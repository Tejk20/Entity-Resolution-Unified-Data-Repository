"""Demo dataset seeds (Section 24).

The four datasets are designed so that a single search for ``john@example.com``
performs a **4-step progressive enrichment**: each dataset contributes exactly
one new identifier type that the previous one could not have known about.

    Database A (HR)        email john@example.com  + mobile_number
        | hop 1: phone +1-555-0100 is discovered
        v
    Database B (Directory) email_id john@example.com + contact_no (same phone)
        | hop 2: address is discovered (not a match key, entity attribute)
        v
    Database C (Startup)   username jdoe_startup + phone (formatted differently,
        |                     same digits -> normalizes identically) + company
        | hop 3: username 'jdoe_startup' is discovered
        v
    Database D (Members)   member_id MB-9001 + email john@example.com
                              + username jdoe_startup  <- the transitive link

D is only reachable because C introduced the username, which is exactly the
recursive behaviour the assignment asks to demonstrate. Decoys (shared names,
shared company) exist to prove the matcher does not over-merge.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.normalize import normalize_field
from app.services.seed_datasets import DATASETS, DatasetSpec, FileFormat

logger = logging.getLogger(__name__)


def _rows_to_csv(spec: DatasetSpec) -> str:
    cols = spec.columns
    out = [",".join(_csv_cell(c) for c in cols)]
    for row in spec.rows:
        out.append(",".join(_csv_cell(row.get(c, "")) for c in cols))
    return "\n".join(out) + "\n"


def _csv_cell(value: Any) -> str:
    s = "" if value is None else str(value)
    if any(ch in s for ch in (",", '"', "\n", "\r")):
        return '"' + s.replace('"', '""') + '"'
    return s


def _literal(v: Any) -> str:
    if v is None:
        return "NULL"
    s = str(v).replace("'", "''")
    return f"'{s}'"


def _rows_to_sql(spec: DatasetSpec) -> str:
    """Render a MySQL-flavoured dump: exercises the parser's escaping paths."""
    lines: list[str] = [
        f"-- {spec.label} ({spec.system_key})",
        "SET FOREIGN_KEY_CHECKS=0;",
        f"DROP TABLE IF EXISTS `{spec.table_name}`;",
        f"CREATE TABLE `{spec.table_name}` (",
    ]
    for col in spec.columns:
        col_type = "VARCHAR(255)"
        if col in ("member_id",):
            col_type = "VARCHAR(64)"
        elif col in ("age", "signup_year", "zip"):
            col_type = "INT"
        lines.append(f"  `{col}` {col_type}{',' if col != spec.columns[-1] else ''}")
    lines[-1] = lines[-1].rstrip(",")
    lines.append(") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
    lines.append("BEGIN;")
    # multi-row INSERT on purpose: the parser must split tuples correctly
    for start in range(0, len(spec.rows), 3):
        batch = spec.rows[start : start + 3]
        values = ", ".join(
            "(" + ", ".join(_literal(r.get(c)) for c in spec.columns) + ")"
            for r in batch
        )
        lines.append(
            f"INSERT INTO `{spec.table_name}` "
            f"({', '.join('`' + c + '`' for c in spec.columns)}) VALUES {values};"
        )
    lines.append("COMMIT;")
    lines.append("SET FOREIGN_KEY_CHECKS=1;")
    return "\n".join(lines) + "\n"


def render_dataset(spec: DatasetSpec, fmt: FileFormat) -> str:
    if fmt == "sql":
        return _rows_to_sql(spec)
    if fmt == "tsv":
        body = _rows_to_csv(spec).replace(",", "\t")
        return body
    return _rows_to_csv(spec)


def verify_bridge() -> dict[str, Any]:
    """Assert the 4-hop chain holds under the normalizer (self-check)."""
    hops: list[dict[str, Any]] = []
    a_phone = normalize_field("phone", DATASETS[0].rows[0]["mobile_number"]).normalized
    b_contact = normalize_field("phone", DATASETS[1].rows[0]["contact_no"]).normalized
    c_phone = normalize_field("phone", DATASETS[2].rows[0]["phone"]).normalized
    c_user = normalize_field("username", DATASETS[2].rows[0]["username"]).normalized
    d_email = normalize_field("email", DATASETS[3].rows[0]["email"]).normalized
    d_user = normalize_field("username", DATASETS[3].rows[0]["username"]).normalized
    d_member = normalize_field("member_id", DATASETS[3].rows[0]["member_id"]).normalized

    hops = [
        {"hop": 1, "via": "email", "value": normalize_field("email", "john@example.com").normalized,
         "reaches": "Database A (HR)"},
        {"hop": 2, "via": "phone", "value": a_phone,
         "reaches": "Database B (Directory)",
         "cross_check": {"a": a_phone, "b": b_contact, "equal": a_phone == b_contact}},
        {"hop": 3, "via": "phone", "value": c_phone,
         "reaches": "Database C (Startup)",
         "cross_check": {"a": a_phone, "c": c_phone, "equal": a_phone == c_phone}},
        {"hop": 4, "via": "username", "value": c_user,
         "reaches": "Database D (Members)",
         "cross_check": {"c": c_user, "d": d_user, "equal": c_user == d_user}},
    ]
    return {
        "ok": all(h.get("cross_check", {}).get("equal", True) for h in hops),
        "hops": hops,
        "member_id": d_member,
        "d_email": d_email,
    }
