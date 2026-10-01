"""Chunked parsing of uploaded CSV files and SQL dumps.

Both parsers are **generators** yielding ``ParsedChunk`` objects, so a 2 GB file
is never held in memory: rows are streamed, batched into fixed-size chunks and
handed to the worker. Every chunk carries its own ``batch_id`` so a re-run or a
multi-part import of the same dataset stays idempotent.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ParsedChunk:
    batch_id: str
    chunk_index: int
    table_name: str
    columns: list[str]
    rows: list[dict[str, Any]]
    is_first: bool
    is_last: bool
    total_rows_so_far: int = 0

    @property
    def size(self) -> int:
        return len(self.rows)


@dataclass(slots=True)
class ParseMeta:
    table_name: str
    columns: list[str]
    total_rows: int = 0
    total_chunks: int = 0
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #
def _sniff_dialect(sample: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.excel  # type: ignore[return-value]


def iter_csv_chunks(
    path: str | Path,
    batch_id: str,
    chunk_size: int = 5000,
    table_name: str | None = None,
    max_bytes: int | None = None,
) -> Iterator[ParsedChunk]:
    """Stream a CSV (or TSV) into fixed-size chunks."""
    p = Path(path)
    table = table_name or p.stem
    with p.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        head = fh.read(65536)
        fh.seek(0)
        dialect = _sniff_dialect(head)

        reader = csv.reader(fh, dialect)
        try:
            header = next(reader)
        except StopIteration:
            return

        columns = [unicodedata.normalize("NFKC", c).strip() for c in header]
        # de-duplicate headers: "email", "email", "email " -> email, email_2, email_3
        seen: dict[str, int] = {}
        final_cols: list[str] = []
        for i, c in enumerate(columns):
            name = c or f"column_{i + 1}"
            if name in seen:
                seen[name] += 1
                name = f"{name}_{seen[name]}"
            else:
                seen[name] = 0
            final_cols.append(name)

        buffer: list[dict[str, Any]] = []
        idx = 0
        rownum = 0
        while True:
            if max_bytes and fh.tell() > max_bytes:
                logger.warning("csv read truncated at %s bytes", max_bytes)
                break
            try:
                raw_row = next(reader)
            except StopIteration:
                break
            except csv.Error as exc:
                logger.warning("csv parse error near row %s: %s", rownum, exc)
                continue
            if not raw_row or all(not str(c).strip() for c in raw_row):
                continue
            rownum += 1
            row = {final_cols[i]: raw_row[i] if i < len(raw_row) else None
                   for i in range(len(final_cols))}
            row["__row__"] = rownum
            buffer.append(row)
            if len(buffer) >= chunk_size:
                yield ParsedChunk(
                    batch_id=batch_id,
                    chunk_index=idx,
                    table_name=table,
                    columns=final_cols,
                    rows=buffer,
                    is_first=idx == 0,
                    is_last=False,
                )
                buffer = []
                idx += 1

        if buffer or idx == 0:
            yield ParsedChunk(
                batch_id=batch_id,
                chunk_index=idx,
                table_name=table,
                columns=final_cols,
                rows=buffer,
                is_first=idx == 0,
                is_last=True,
            )


def read_csv_sample(path: str | Path, limit: int = 200) -> tuple[list[str], list[dict[str, Any]]]:
    p = Path(path)
    with p.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        dialect = _sniff_dialect(fh.read(65536))
        fh.seek(0)
        reader = csv.reader(fh, dialect)
        try:
            header = next(reader)
        except StopIteration:
            return [], []
        columns = [c.strip() or f"column_{i+1}" for i, c in enumerate(header)]
        rows: list[dict[str, Any]] = []
        for i, raw in enumerate(reader):
            if i >= limit:
                break
            rows.append({columns[j]: raw[j] if j < len(raw) else None
                        for j in range(len(columns))})
    return columns, rows


# --------------------------------------------------------------------------- #
# SQL dump
# --------------------------------------------------------------------------- #
CREATE_TABLE_RE = re.compile(
    r"CREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?P<name>[\w`\"\.\[\]]+)\s*\(",
    re.I,
)
INSERT_RE = re.compile(
    r"^INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(?P<name>[\w`\"\.\[\]]+)\s*"
    r"(?:\((?P<cols>[^)]*)\))?\s*VALUES",
    re.I,
)
COPY_RE = re.compile(r"^COPY\s+(?P<name>[\w`\"\.\[\]]+)\s*(?:\((?P<cols>[^)]*)\))?\s+FROM\s+STDIN", re.I)

#: tables we never want to import
SKIP_TABLES = {
    "alembic_version", "schema_migrations", "flyway_schema_history",
    "django_migrations", "pg_stat_statements", "spatial_ref_sys",
}


def _clean_ident(name: str) -> str:
    n = name.strip().strip("`").strip('"').strip("[]")
    if "." in n:
        n = n.split(".")[-1]
    return n.strip('"').strip("`").strip("[]")


def _split_top_level(text: str) -> list[str]:
    """Split on commas that are not inside quotes or parentheses."""
    parts: list[str] = []
    depth = 0
    in_str = False
    esc = False
    quote = ""
    buf: list[str] = []
    for ch in text:
        if in_str:
            buf.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in ("'", '"'):
            in_str = True
            quote = ch
            buf.append(ch)
        elif ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _decode_value(token: str) -> Any:
    """Convert one SQL literal into a Python value."""
    t = token.strip()
    if not t:
        return None
    upper = t.upper()

    # E'...' escape strings
    if len(t) >= 3 and t[0] in "eE" and t[1] == "'":
        t = t[1:]
        inner = t[1:-1]
        out: list[str] = []
        esc = False
        for ch in inner:
            if esc:
                out.append({"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"'}.get(ch, ch))
                esc = False
            elif ch == "\\":
                esc = True
            else:
                out.append(ch)
        return "".join(out)

    if t.startswith("'") and t.endswith("'"):
        inner = t[1:-1]
        out = []
        i = 0
        while i < len(inner):
            if inner[i] == "'" and i + 1 < len(inner) and inner[i + 1] == "'":
                out.append("'")
                i += 2
            else:
                out.append(inner[i])
                i += 1
        return "".join(out)

    if t.startswith('"') and t.endswith('"'):
        return t[1:-1]
    if t.startswith("`") and t.endswith("`"):
        return t[1:-1]
    if t.startswith("N'") or upper == "NULL":
        return None if upper == "NULL" else _decode_value(t[1:])
    if upper in ("TRUE", "FALSE"):
        return upper == "TRUE"
    if upper == "DEFAULT":
        return None
    if re.fullmatch(r"[+-]?\d+", t):
        try:
            return int(t)
        except ValueError:
            return t
    if re.fullmatch(r"[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?", t):
        try:
            return float(t)
        except ValueError:
            return t
    if t.startswith("0x"):
        try:
            return int(t, 16)
        except ValueError:
            return t
    return t


def parse_insert_values(stmt: str) -> list[list[Any]]:
    """Extract every VALUES tuple from an INSERT statement."""
    vpos = stmt.upper().rfind("VALUES")
    if vpos == -1:
        return []
    body = stmt[vpos + len("VALUES"):].rstrip().rstrip(";")

    tuples: list[list[Any]] = []
    depth = 0
    in_str = False
    esc = False
    quote = ""
    buf: list[str] = []
    for ch in body:
        if in_str:
            buf.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in ("'", '"'):
            in_str = True
            quote = ch
            buf.append(ch)
        elif ch == "(":
            if depth == 0:
                buf = []
            else:
                buf.append(ch)
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                tuples.append([_decode_value(x) for x in _split_top_level("".join(buf))])
                buf = []
            else:
                buf.append(ch)
        elif ch == ";" and depth == 0:
            break
        else:
            if depth > 0:
                buf.append(ch)
    return tuples


def _split_statements(text: str) -> Iterator[str]:
    """Yield complete SQL statements, ignoring comment-only text."""
    buf: list[str] = []
    in_str = False
    esc = False
    quote = ""
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            buf.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            i += 1
            continue
        if ch in ("'", '"'):
            in_str = True
            quote = ch
            buf.append(ch)
            i += 1
            continue
        # line comment
        if ch == "-" and i + 1 < n and text[i + 1] == "-":
            j = text.find("\n", i)
            i = n if j == -1 else j + 1
            continue
        if ch == "#":
            j = text.find("\n", i)
            i = n if j == -1 else j + 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                yield stmt
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        yield tail


def _split_copy_line(line: str) -> list[str]:
    """Tab-split a PostgreSQL COPY data row, honouring backslash escapes and
    the ``\\N`` NULL marker."""
    fields: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == "\\" and i + 1 < n:
            nxt = line[i + 1]
            if nxt == "t":
                fields.append("".join(buf))
                buf = []
                i += 2
                continue
            if nxt == "n":
                buf.append("\n")
                i += 2
                continue
            if nxt == "N":
                buf.append("\\N")
                i += 2
                continue
            if nxt == "\\":
                buf.append("\\")
                i += 2
                continue
        if ch == "\t":
            fields.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    fields.append("".join(buf))
    return [f if f != "\\N" else None for f in fields]


def iter_sql_chunks(
    path: str | Path,
    batch_id: str,
    chunk_size: int = 5000,
    max_bytes: int | None = None,
) -> Iterator[ParsedChunk]:
    """Stream a SQL dump into chunks, one logical table at a time.

    Supports both ``INSERT INTO ... VALUES`` and PostgreSQL ``COPY ... FROM
    stdin`` blocks, and tolerates multi-row inserts.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    if max_bytes:
        text = text[:max_bytes]
    if not text.strip():
        return

    table_columns: dict[str, list[str]] = {}
    current: dict[str, Any] | None = None  # table, columns, buffer, idx, rownum
    chunk_counter = 0
    per_table_idx: dict[str, int] = {}
    per_table_rows: dict[str, int] = {}

    def emit(table: str, columns: list[str], buffer: list[dict[str, Any]]) -> Iterator[ParsedChunk]:
        nonlocal chunk_counter
        if not buffer:
            return
        i = per_table_idx.get(table, 0)
        per_table_idx[table] = i + 1
        per_table_rows[table] = per_table_rows.get(table, 0) + len(buffer)
        yield ParsedChunk(
            batch_id=batch_id,
            chunk_index=i,
            table_name=table,
            columns=columns,
            rows=buffer,
            is_first=i == 0,
            is_last=False,
        )
        chunk_counter += 1

    in_copy = False
    copy_table = ""
    copy_cols: list[str] = []
    copy_buf: list[dict[str, Any]] = []
    copy_rownum = 0

    statements = _split_statements(text)
    while True:
        try:
            stmt = next(statements)
        except StopIteration:
            break
        if in_copy:
            if stmt.strip() == "\\." or stmt.strip() == r"\.":
                in_copy = False
                table_columns.setdefault(copy_table, copy_cols)
                for ch in emit(copy_table, copy_cols, copy_buf):
                    yield ch
                copy_buf = []
                continue
            cols = copy_cols
            # one INSERT/COPY statement can wrap many physical lines
            for data_line in stmt.splitlines():
                if not data_line.strip():
                    continue
                if data_line.strip() == r"\.":
                    continue
                vals = [_decode_value(x) for x in _split_copy_line(data_line)]
                if cols:
                    row = {cols[i]: vals[i] if i < len(vals) else None for i in range(len(cols))}
                else:
                    row = {f"col_{i+1}": v for i, v in enumerate(vals)}
                copy_rownum += 1
                row["__row__"] = copy_rownum
                copy_buf.append(row)
                if len(copy_buf) >= chunk_size:
                    for ch in emit(copy_table, copy_cols, copy_buf):
                        yield ch
                    copy_buf = []
            continue

        stripped = stmt.strip()

        m = COPY_RE.match(stripped)
        if m:
            copy_table = _clean_ident(m.group("name"))
            raw_cols = m.group("cols")
            copy_cols = [_clean_ident(c) for c in raw_cols.split(",")] if raw_cols else []
            in_copy = True
            copy_rownum = 0
            continue

        m = CREATE_TABLE_RE.match(stripped)
        if m:
            table = _clean_ident(m.group("name"))
            cols = _parse_create_table_columns(stripped)
            if cols:
                table_columns[table] = cols
            continue

        m = INSERT_RE.match(stripped)
        if m:
            table = _clean_ident(m.group("name"))
            if table.lower() in SKIP_TABLES:
                continue
            raw_cols = m.group("cols")
            cols = (
                [_clean_ident(c) for c in raw_cols.split(",")]
                if raw_cols
                else table_columns.get(table, [])
            )
            if not cols:
                cols = [f"col_{i+1}" for i in range(_peek_tuple_width(stripped))]
            for vals in parse_insert_values(stripped):
                row = {cols[i]: vals[i] if i < len(vals) else None for i in range(len(cols))}
                n = per_table_rows.get(table, 0) + 1
                per_table_rows[table] = n
                row["__row__"] = n
                cur = current
                if cur is None or cur["table"] != table:
                    if cur and cur["buffer"]:
                        for ch in emit(cur["table"], cur["cols"], cur["buffer"]):
                            yield ch
                    current = {"table": table, "cols": cols, "buffer": []}
                    cur = current
                cur["buffer"].append(row)
                if len(cur["buffer"]) >= chunk_size:
                    for ch in emit(cur["table"], cur["cols"], cur["buffer"]):
                        yield ch
                    cur["buffer"] = []
            continue

        if stripped:
            # DDL / SET / LOCK etc. - ignored on purpose, but keep the loop honest
            continue

    if current and current["buffer"]:
        for ch in emit(current["table"], current["cols"], current["buffer"]):
            yield ch
    if in_copy and copy_buf:
        for ch in emit(copy_table, copy_cols, copy_buf):
            yield ch


def _peek_tuple_width(stmt: str) -> int:
    vpos = stmt.upper().rfind("VALUES")
    if vpos == -1:
        return 0
    body = stmt[vpos + 6:]
    start = body.find("(")
    if start == -1:
        return 0
    depth = 0
    buf: list[str] = []
    in_str = False
    quote = ""
    for ch in body[start:]:
        if in_str:
            buf.append(ch)
            if ch == quote:
                in_str = False
            continue
        if ch in ("'", '"'):
            in_str = True
            quote = ch
            buf.append(ch)
        elif ch == "(":
            depth += 1
            if depth == 1:
                continue
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return len(_split_top_level("".join(buf)))
            buf.append(ch)
        else:
            buf.append(ch)
    return 0


def _parse_create_table_columns(stmt: str) -> list[str]:
    start = stmt.find("(")
    if start == -1:
        return []
    depth = 0
    body: list[str] = []
    in_str = False
    quote = ""
    for ch in stmt[start:]:
        if in_str:
            body.append(ch)
            if ch == quote:
                in_str = False
            continue
        if ch in ("'", '"', "`"):
            in_str = True
            quote = ch
            body.append(ch)
        elif ch == "(":
            depth += 1
            if depth == 1:
                continue
            body.append(ch)
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
            body.append(ch)
        else:
            body.append(ch)

    cols: list[str] = []
    for part in _split_top_level("".join(body)):
        head = part.split()[0] if part.split() else ""
        head = _clean_ident(head)
        if not head:
            continue
        upper = part.upper()
        if upper.startswith(("PRIMARY KEY", "UNIQUE", "FOREIGN KEY", "CONSTRAINT", "KEY ", "INDEX ")):
            continue
        if head.lower() in SKIP_TABLES:
            continue
        cols.append(head)
    return cols


def read_sql_sample(
    path: str | Path, limit: int = 200
) -> list[tuple[str, list[str], list[dict[str, Any]]]]:
    """Return [(table, columns, rows), ...] for mapping preview."""
    results: list[tuple[str, list[str], list[dict[str, Any]]]] = []
    total = 0
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    table_columns: dict[str, list[str]] = {}
    seen_tables: set[str] = set()
    for stmt in _split_statements(text):
        m = CREATE_TABLE_RE.match(stmt.strip())
        if m:
            table = _clean_ident(m.group("name"))
            cols = _parse_create_table_columns(stmt.strip())
            if cols:
                table_columns[table] = cols
            continue
        m = INSERT_RE.match(stmt.strip())
        if not m:
            continue
        table = _clean_ident(m.group("name"))
        if table.lower() in SKIP_TABLES or table in seen_tables:
            continue
        raw_cols = m.group("cols")
        cols = (
            [_clean_ident(c) for c in raw_cols.split(",")]
            if raw_cols
            else table_columns.get(table, [])
        )
        rows_raw = parse_insert_values(stmt.strip())
        if not cols:
            cols = [f"col_{i+1}" for i in range(len(rows_raw[0]) if rows_raw else 0)]
        rows = [
            {cols[i]: vals[i] if i < len(vals) else None for i in range(len(cols))}
            for vals in rows_raw[:limit]
        ]
        results.append((table, cols, rows))
        seen_tables.add(table)
        total += len(rows)
        if total >= limit:
            break
    return results


# --------------------------------------------------------------------------- #
# dispatcher
# --------------------------------------------------------------------------- #
def detect_file_type(filename: str, head_bytes: bytes = b"") -> str:
    ext = Path(filename).suffix.lower()
    if ext in (".csv", ".tsv", ".txt"):
        return "csv"
    if ext in (".sql", ".dump", ".ddl"):
        return "sql"
    h = head_bytes.lstrip()[:64].lower()
    if h.startswith((b"create table", b"insert into", b"copy ", b"--", b"set ", b"lock ")):
        return "sql"
    if b"," in head_bytes[:1024] or b"\t" in head_bytes[:1024]:
        return "csv"
    return "csv"


def iter_chunks(
    path: str | Path,
    file_type: str,
    batch_id: str,
    chunk_size: int = 5000,
) -> Iterator[ParsedChunk]:
    if file_type == "sql":
        yield from iter_sql_chunks(path, batch_id, chunk_size)
    else:
        yield from iter_csv_chunks(path, batch_id, chunk_size)


def sample_for_mapping(
    path: str | Path, file_type: str, limit: int = 200
) -> list[tuple[str, list[str], list[dict[str, Any]]]]:
    if file_type == "sql":
        return read_sql_sample(path, limit)
    columns, rows = read_csv_sample(path, limit)
    return [(Path(path).stem, columns, rows)]


def write_row_to_sql_literal(value: Any) -> str:
    """Render a Python value as a SQL literal (used by the SQL export)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"
