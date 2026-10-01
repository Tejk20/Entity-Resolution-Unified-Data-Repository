"""Data cleaning & normalization engine.

Every function here is *pure* and deterministic: given the same raw value it
always returns the same ``(raw, normalized, ok)`` triple. That property is what
makes multi-part imports idempotent and lets the resolution engine rely on
equality of normalized values for joining.

Contract per field:
    raw        -> the untouched original string (persisted for audit)
    normalized -> the canonical join key, or None when the value is unusable
    ok         -> False when the value was present but malformed
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable

# --------------------------------------------------------------------------- #
# regex vocabularies
# --------------------------------------------------------------------------- #
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
EMAIL_LOOSE_RE = re.compile(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+")
# display-name style: John Doe <john@example.com>
ANGLE_EMAIL_RE = re.compile(r"<([^>]+@[^>]+)>")
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{1,63}$")
PHONE_DIGITS_RE = re.compile(r"\d+")
NULL_TOKENS = {
    "", "-", "--", "n/a", "na", "null", "none", "nil", "nan", "undefined",
    "unknown", "not available", "not provided", "no data", "?", "0",
}
# country dial codes we can confidently strip to reach a 10-digit NANP number
COUNTRY_CODES = {
    "1": "US/CA NANP", "44": "UK", "91": "IN", "61": "AU", "49": "DE",
    "33": "FR", "86": "CN", "81": "JP", "55": "BR", "52": "MX", "7": "RU/KZ",
}

CANONICAL_FIELDS = (
    "email", "phone", "name", "username", "member_id", "address", "company",
)

#: canonical types that are emitted into `entity_identifiers` and may be used to
#: transitively join records. `address`/`company` are descriptive only: they are
#: normalized and stored on the master entity but never used as a match key.
IDENTIFIABLE_TYPES = ("email", "phone", "name", "username", "member_id")


@dataclass(slots=True)
class Normalized:
    raw: Any
    normalized: str | None
    ok: bool
    note: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_tuple(self) -> tuple[Any, str | None, bool]:
        return self.raw, self.normalized, self.ok


def is_null(value: Any) -> bool:
    """True for None, empty strings, and the usual placeholder tokens."""
    if value is None:
        return True
    if isinstance(value, float) and value != value:  # NaN
        return True
    if not isinstance(value, str):
        value = str(value)
    return value.strip().lower() in NULL_TOKENS


def _squash_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


# --------------------------------------------------------------------------- #
# field normalizers
# --------------------------------------------------------------------------- #
def normalize_email(value: Any) -> Normalized:
    """Lowercase + trim, unwrap `Name <addr>` forms, strip surrounding noise."""
    if is_null(value):
        return Normalized(value, None, True)
    raw = value
    text = str(value).strip()
    # "John Doe <john@example.com>" -> "john@example.com"
    m = ANGLE_EMAIL_RE.search(text)
    if m:
        text = m.group(1)
    text = text.strip().strip("<>").strip()
    # take the first of several comma/semicolon separated addresses
    text = re.split(r"[,;]", text)[0].strip()
    text = text.strip('"').strip().lower()
    text = re.sub(r"^mailto:", "", text)
    text = re.sub(r"\s+", "", text)
    if not text:
        return Normalized(raw, None, False, "empty after cleaning")
    if EMAIL_RE.fullmatch(text):
        return Normalized(raw, text, True)
    # multi-value cells ("a@b.com, c@d.com", "a@b.com; junk") - scan the whole
    # string rather than trusting the split above
    loose = EMAIL_LOOSE_RE.search(text)
    if loose:
        candidate = loose.group(0).lower().strip(".")
        if EMAIL_RE.fullmatch(candidate):
            return Normalized(
                raw, candidate, True, "extracted from multi-value field"
            )
        return Normalized(
            raw, candidate, True, "recovered from malformed value"
        )
    return Normalized(raw, None, False, "no valid email found")


def normalize_phone(value: Any, default_region: str = "US") -> Normalized:
    """Strip non-numerics, handle `+CC`/`00CC`/leading 1, emit E.164-ish key.

    Preference order for the stored key:
      1. explicit international  -> ``+<digits>``  (unambiguous, globally safe)
      2. 10-digit NANP            -> ``+1<digits>`` (canonical for US/CA)
      3. other 7-15 digit local   -> ``+<digits>`` (best effort, flagged)
    """
    if is_null(value):
        return Normalized(value, None, True)
    raw = value
    text = str(value).strip()
    ext = None
    # split extensions: "555-123-4567 ext. 22" / "x22"
    ext_match = re.search(r"(?:ext|x|ext\.?|#)\s*\.?\s*(\d{1,6})\s*$", text, re.I)
    if ext_match:
        ext = ext_match.group(1)
        text = text[: ext_match.start()]

    digits = "".join(PHONE_DIGITS_RE.findall(text))
    if not digits:
        return Normalized(raw, None, False, "no digits present")

    stripped = text.lstrip()
    intl = stripped.startswith("+") or stripped.startswith("00")
    note: str | None = None
    region = "UNKNOWN"

    if intl:
        # explicit international dialling: the digits *are* the E.164 payload
        digits = digits.lstrip("0") or digits
        region = "INTL"
        if not 7 <= len(digits) <= 15:
            return Normalized(
                raw, None, False, f"implausible international length ({len(digits)} digits)"
            )
        return Normalized(
            raw, f"+{digits}", True, None,
            {"region": "INTL", "ext": ext, "digits": digits},
        )

    # --- no country marker: infer from length ---------------------------
    if len(digits) == 10:
        return Normalized(
            raw, f"+1{digits}", True, None,
            {"region": "US/CA", "ext": ext, "digits": digits},
        )
    if len(digits) == 11 and digits.startswith("1"):
        return Normalized(
            raw, f"+{digits}", True, "leading NANP country code",
            {"region": "US/CA", "ext": ext, "digits": digits[1:]},
        )

    # trunk-0 national formats (e.g. UK 020 xxxx xxxx): peel the leading 0 and
    # assign the +44 country code when the remainder is 10 digits
    if digits.startswith("0") and len(digits) == 11 and digits[1].isdigit():
        return Normalized(
            raw, f"+44{digits[1:]}", True, "inferred +44 from trunk-0 number",
            {"region": "UK", "ext": ext, "digits": digits[1:]},
        )

    # longer, no marker: peel a known country code if that leaves a plausible
    # national number, otherwise treat the whole thing as international
    for cc in ("1", "44", "61", "49", "33", "86", "81", "55", "52", "91", "7"):
        if digits.startswith(cc):
            national = digits[len(cc):]
            if 9 <= len(national) <= 10:
                note = f"inferred country code +{cc}"
                region = COUNTRY_CODES.get(cc, "INTL")
                return Normalized(
                    raw, f"+{cc}{national}", True, note,
                    {"region": region, "ext": ext, "digits": national},
                )

    if 10 <= len(digits) <= 15:
        return Normalized(
            raw, f"+{digits}", True, "assumed international format",
            {"region": "INTL", "ext": ext, "digits": digits},
        )
    if 7 <= len(digits) < 10:
        return Normalized(
            raw, None, False, f"too short ({len(digits)} digits)",
            {"ext": ext, "region": default_region},
        )
    return Normalized(raw, None, False, f"implausible length ({len(digits)} digits)")


def normalize_username(value: Any) -> Normalized:
    """Lowercase, strip a leading `@`, convert spaces to underscores."""
    if is_null(value):
        return Normalized(value, None, True)
    raw = value
    text = str(value).strip().lower()
    text = text.lstrip("@").strip()
    text = re.sub(r"[\s.]+", "_", text)
    text = re.sub(r"[^\w\-.]+", "", text, flags=re.ASCII)
    text = text.strip("_")
    if not text:
        return Normalized(raw, None, False, "empty after cleaning")
    if not USERNAME_RE.match(text):
        return Normalized(raw, text, True, "unusual username characters")
    return Normalized(raw, text, True)


def normalize_name(value: Any) -> Normalized:
    """Collapse whitespace, strip trailing punctuation, NFKC-fold unicode."""
    if is_null(value):
        return Normalized(value, None, True)
    raw = value
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\t", " ").replace("\n", " ")
    text = _squash_ws(text)
    text = re.sub(r"[,\s]+$", "", text)
    if not text:
        return Normalized(raw, None, False, "empty after cleaning")
    return Normalized(raw, text, True)


def normalize_member_id(value: Any) -> Normalized:
    """Uppercase and strip separators so `AB-1234` == `ab1234`."""
    if is_null(value):
        return Normalized(value, None, True)
    raw = value
    text = str(value).strip().upper()
    text = re.sub(r"[\s\-_./]+", "", text)
    text = re.sub(r"[^A-Z0-9]", "", text)
    if not text:
        return Normalized(raw, None, False, "no alphanumerics present")
    return Normalized(raw, text, True)


def normalize_address(value: Any) -> Normalized:
    """Whitespace-squash + street-suffix standardization (comparison only)."""
    if is_null(value):
        return Normalized(value, None, True)
    raw = value
    text = unicodedata.normalize("NFKC", str(value))
    text = _squash_ws(text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(
        r"\b(street|str)\b\.?", "St", text, flags=re.I,
    )
    text = re.sub(
        r"\b(avenue|ave)\b\.?", "Ave", text, flags=re.I,
    )
    text = re.sub(r"\b(road|rd)\b\.?", "Rd", text, flags=re.I,
    )
    text = re.sub(
        r"\b(suite|apt|apartment|unit)\b\.?", lambda m: m.group(0)[:3].title(), text, flags=re.I
    )
    if not text:
        return Normalized(raw, None, False, "empty after cleaning")
    return Normalized(raw, text, True)


def normalize_company(value: Any) -> Normalized:
    """Strip legal suffixes for comparison while keeping a tidy display form."""
    if is_null(value):
        return Normalized(value, None, True)
    raw = value
    text = unicodedata.normalize("NFKC", str(value))
    text = _squash_ws(text)
    if not text:
        return Normalized(raw, None, False, "empty after cleaning")
    display = text
    key = re.sub(
        r"[.,]?\s*\b(inc|llc|ltd|limited|corp|corporation|co|company|gmbh|plc|llp)\b\.?",
        "",
        text,
        flags=re.I,
    )
    key = _squash_ws(key) or text
    return Normalized(raw, display, True, None, {"match_key": key.lower()})


def normalize_generic(value: Any) -> Normalized:
    if is_null(value):
        return Normalized(value, None, True)
    raw = value
    text = _squash_ws(unicodedata.normalize("NFKC", str(value)))
    if not text:
        return Normalized(raw, None, False, "empty after cleaning")
    return Normalized(raw, text, True)


NORMALIZERS = {
    "email": normalize_email,
    "phone": normalize_phone,
    "name": normalize_name,
    "username": normalize_username,
    "member_id": normalize_member_id,
    "address": normalize_address,
    "company": normalize_company,
}


def normalize_field(canonical_type: str, value: Any) -> Normalized:
    fn = NORMALIZERS.get(canonical_type, normalize_generic)
    return fn(value)


# --------------------------------------------------------------------------- #
# detection helpers (used by the field-mapping engine)
# --------------------------------------------------------------------------- #
def value_looks_like(canonical_type: str, value: Any) -> bool:
    return normalize_field(canonical_type, value).normalized is not None


def detect_value_type(value: Any) -> str | None:
    """Cheap single-value type sniff used for column profiling."""
    if is_null(value):
        return None
    if normalize_email(value).normalized:
        return "email"
    digits = "".join(PHONE_DIGITS_RE.findall(str(value)))
    if digits and len(digits) >= 7 and len(digits) <= 15:
        return "phone"
    if normalize_username(value).normalized and len(str(value).strip()) <= 64:
        return "username"
    return None


# --------------------------------------------------------------------------- #
# record-level normalization
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class CleanedRecord:
    raw: dict[str, Any]
    clean: dict[str, Any]
    identifiers: list[dict[str, Any]]
    invalid_cells: list[dict[str, Any]]


def clean_record(
    row: dict[str, Any],
    mapping: dict[str, str],
    *,
    weights: dict[str, float] | None = None,
    record_pk: str | None = None,
) -> CleanedRecord:
    """Normalize one raw row.

    ``mapping`` maps canonical_type -> source column name. Only mapped columns
    are touched; the original row is returned untouched alongside the result so
    the caller can persist both for audit.
    """
    clean: dict[str, Any] = {}
    identifiers: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []

    for canonical_type, column in mapping.items():
        if not column or column not in row:
            clean[canonical_type] = None
            continue
        raw_value = row.get(column)
        result = normalize_field(canonical_type, raw_value)
        clean[canonical_type] = result.normalized
        clean[f"{canonical_type}__raw"] = (
            None if is_null(raw_value) else str(raw_value)[:1000]
        )
        if result.normalized and canonical_type in IDENTIFIABLE_TYPES:
            identifiers.append(
                {
                    "canonical_type": canonical_type,
                    "normalized_value": result.normalized,
                    "raw_value": None if is_null(raw_value) else str(raw_value)[:1000],
                    "confidence": weights.get(canonical_type, 1.0),
                    "note": result.note,
                }
            )
        if not result.ok:
            invalid.append(
                {
                    "column": column,
                    "canonical_type": canonical_type,
                    "raw_value": None if is_null(raw_value) else str(raw_value)[:200],
                    "reason": result.note or "malformed value",
                }
            )

    return CleanedRecord(
        raw=row, clean=clean, identifiers=identifiers, invalid_cells=invalid
    )


def dedupe_identifiers(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse identical (type, value) pairs, keeping the first occurrence."""
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = (item["canonical_type"], item["normalized_value"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
