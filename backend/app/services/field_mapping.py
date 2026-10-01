"""Rule-based + content-profiling field mapping engine.

The engine produces, for every detected source column, a ranked list of
canonical field suggestions with confidence scores in ``[0, 1]``. Confidence is
a weighted blend of:

  * **name signal**    - dictionary + fuzzy match of the column header
  * **value signal**   - what fraction of sampled cells validate as that type
  * **uniqueness**     - cardinality ratio (ids/usernames are near-unique)
  * **prior**          - static per-type bonus

The result is intentionally *explainable*: ``reason`` explains, in one line,
why a suggestion scored what it did, which is what the mapping preview UI shows.
"""
from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from app.services.normalize import (
    CANONICAL_FIELDS,
    is_null,
    normalize_field,
    normalize_name,
    normalize_username,
)

# --------------------------------------------------------------------------- #
# dictionaries
# --------------------------------------------------------------------------- #
EXACT_ALIASES: dict[str, set[str]] = {
    "email": {
        "email", "emailaddress", "email_address", "emailaddress", "mail",
        "emailaddress", "mailaddress", "mail_address", "e_mail", "emailid",
        "email_address_", "work_email", "business_email", "contact_email",
        "user_email", "primary_email", "id", "email1", "contactemail",
    },
    "phone": {
        "phone", "phonenumber", "phone_number", "mobile", "mobilenumber",
        "mobile_number", "cell", "cellphone", "cell_phone", "telephone",
        "tel", "telephone_number", "contactno", "contact_no", "contact",
        "contactnumber", "contact_number", "msisdn", "mob", "mobileno",
        "phone_no", "phoneno", "work_phone", "primary_phone", "number",
    },
    "name": {
        "name", "fullname", "full_name", "firstname", "first_name", "lastname",
        "last_name", "lastname", "displayname", "display_name", "customername",
        "customer_name", "employeename", "employee_name", "contactname",
        "contact_name", "person", "personname", "person_name", "username_name",
        "givenname", "familyname", "surname", "membername", "member_name",
    },
    "username": {
        "username", "user_name", "user", "login", "loginname", "login_name",
        "handle", "screenname", "screen_name", "nickname", "nick",
        "accountname", "account_name", "userid", "user_id", "userhandle",
        "alias", "uname", "loginid", "login_id",
    },
    "member_id": {
        "memberid", "member_id", "memberidnumber", "customerid", "customer_id",
        "userid", "user_id", "accountid", "account_id", "personid",
        "person_id", "clientid", "client_id", "subscriberid", "subscriber_id",
        "empid", "emp_id", "employeeid", "employee_id", "staffid", "staff_id",
        "ref", "refid", "ref_id", "reference", "referenceno", "recordid",
        "record_id", "uuid", "guid", "pk", "primarykey", "primary_key", "id",
    },
    "address": {
        "address", "addressline1", "address_line_1", "address1", "street",
        "streetaddress", "street_address", "addr", "addressline", "location",
        "residentialaddress", "mailingaddress", "city_address", "fulladdress",
    },
    "company": {
        "company", "companyname", "company_name", "organisation",
        "organization", "organizationname", "org", "employer", "business",
        "businessname", "firm", "startup", "startupname", "workplace",
    },
}

#: substrings that strongly hint at a type (matched against a squashed header)
HINTS: list[tuple[str, str, float]] = [
    ("email", r"e?mail", 0.80),
    ("email", r"emailaddr", 0.85),
    ("phone", r"phone|mobile|cell|tel|contactno|msisdn", 0.80),
    ("phone", r"mob(no|ile)?num", 0.85),
    ("member_id", r"memberid|member_id|customerid|customer_id|accountid|account_id", 0.90),
    ("member_id", r"userid|user_id|employeeid|empid|personid|clientid|uuid|guid", 0.75),
    ("member_id", r"^id$|^pk$|primary_?key|^ref(erence)?(no|id)?$", 0.60),
    ("username", r"user(name)?|login|handle|nick|alias|uname", 0.75),
    ("username", r"screen_?name|account_?name", 0.70),
    ("name", r"full_?name|display_?name|customer_?name|person_?name", 0.80),
    ("name", r"first_?name|last_?name|given_?name|family_?name|surname", 0.78),
    ("name", r"name", 0.55),
    ("address", r"address|addr|street|street_?line|location", 0.75),
    ("company", r"company|organisation|organization|employer|business|firm", 0.75),
]

SAMPLE_SIZE = 200
WEIGHT_NAME = 0.45
WEIGHT_VALUE = 0.30
WEIGHT_UNIQUE = 0.15
WEIGHT_PRIOR = 0.10
TYPE_PRIOR: dict[str, float] = {
    "email": 0.9, "phone": 0.9, "name": 0.8, "username": 0.7,
    "member_id": 0.7, "address": 0.6, "company": 0.6,
}


# --------------------------------------------------------------------------- #
# data model
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Suggestion:
    canonical_type: str
    confidence: float
    reason: str


@dataclass(slots=True)
class ColumnProfile:
    column: str
    dtype: str
    total: int
    non_null: int
    null_ratio: float
    distinct: int
    uniqueness: float
    sample_values: list[str]
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    inferred_type: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _squash(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _categorical_guard_passes(values: Sequence[Any], value_ratio: float) -> bool:
    """Try to stop free-text columns (titles, departments) masquerading as
    usernames. A true username store is *mostly* single-token lowercase text;
    categorical columns are usually title-cased multi-word entries."""
    present = [v for v in values if not is_null(v)]
    if len(present) < 4:
        return value_ratio >= 0.9
    single_token = 0
    lower_preferred = 0
    for v in present:
        s = str(v).strip()
        if " " not in s:
            single_token += 1
        if re.fullmatch(r"[\w.-]{1,40}", s):
            lower_preferred += 1
    single_frac = single_token / len(present)
    token_frac = lower_preferred / len(present)
    # title-case, space-separated free text (e.g. "Staff Engineer") fails both
    return single_frac >= 0.7 and token_frac >= 0.7


def _similarity(a: str, b: str) -> float:
    """Ratio similarity in [0,1] - cheap, no external dependency."""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    # Levenshtein-based ratio
    la, lb = len(a), len(b)
    if abs(la - lb) / max(la, lb) > 0.5:
        return 0.0
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(
                min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            )
        prev = cur
    dist = prev[lb]
    return max(0.0, 1.0 - dist / max(la, lb))


def name_signal(column: str) -> dict[str, float]:
    """Score every canonical type purely from the column header."""
    squashed = _squash(column)
    scores: dict[str, float] = {}

    for ctype, aliases in EXACT_ALIASES.items():
        if squashed in aliases:
            scores[ctype] = max(scores.get(ctype, 0.0), 0.97)
        else:
            best = max((_similarity(squashed, a) for a in aliases), default=0.0)
            if best > 0.82:
                scores[ctype] = max(scores.get(ctype, 0.0), 0.60 + (best - 0.82) * 1.5)

    for ctype, pattern, base in HINTS:
        if re.search(pattern, squashed):
            scores[ctype] = max(scores.get(ctype, 0.0), base)

    return scores


def value_signal(values: Sequence[Any]) -> dict[str, float]:
    """Fraction of sampled cells that normalize successfully per type."""
    present = [v for v in values if not is_null(v)]
    if not present:
        return {}
    scores: dict[str, float] = {}
    for ctype in CANONICAL_FIELDS:
        hits = sum(1 for v in present if normalize_field(ctype, v).normalized)
        ratio = hits / len(present)
        if ratio >= 0.6:
            scores[ctype] = ratio
    return scores


def uniqueness_signal(values: Sequence[Any]) -> float:
    present = [str(v) for v in values if not is_null(v)]
    if not present:
        return 0.0
    return len(set(present)) / len(present)


def dtype_of(values: Sequence[Any]) -> str:
    present = [v for v in values if not is_null(v)][:50]
    if not present:
        return "empty"
    if all(isinstance(v, bool) for v in present):
        return "bool"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in present):
        return "int"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in present):
        return "float"
    return "str"


def profile_column(column: str, values: Sequence[Any], total_rows: int) -> ColumnProfile:
    sample = list(values[:SAMPLE_SIZE])
    non_null = sum(1 for v in sample if not is_null(v))
    uniq = uniqueness_signal(sample)
    dtype = dtype_of(sample)

    ns = name_signal(column)
    vs = value_signal(sample)
    uq = uniq

    combined: dict[str, float] = {}
    for ctype in CANONICAL_FIELDS:
        n = ns.get(ctype, 0.0)
        v = vs.get(ctype, 0.0)
        prior = TYPE_PRIOR.get(ctype, 0.5)

        # A column with no header match but a very strong value match is still
        # a legitimate candidate (e.g. header "col_7" holding emails).
        score = (
            WEIGHT_NAME * n
            + WEIGHT_VALUE * v
            + WEIGHT_UNIQUE * (uq if ctype in ("email", "username", "member_id") else 0.0)
            + WEIGHT_PRIOR * prior
        )

        # discriminator: if a *different* type scores much better on values,
        # heavily penalize this one to avoid every column looking like a name.
        best_other = max(
            (val for k, val in vs.items() if k != ctype), default=0.0
        )
        if v > 0 and best_other - v > 0.4:
            score *= 0.45
        if ctype == "name" and vs.get("email", 0) > 0.5:
            score *= 0.2
        if ctype in ("email", "username", "member_id") and uq < 0.02:
            score *= 0.3  # near-constant column cannot be an identifier

        # free-text/categorical columns (titles, departments, dates, prices)
        # routinely pass the *username* normalizer; require the header signal or
        # a very strong value match before letting them rank as identifiers.
        if ctype == "username" and n < 0.4 and v > 0:
            if not _categorical_guard_passes(sample, v):
                score *= 0.12

        if score > 0.18:
            combined[ctype] = min(score, 0.99)

    ranked = sorted(combined.items(), key=lambda kv: kv[1], reverse=True)
    suggestions: list[dict[str, Any]] = []
    for ctype, score in ranked[:4]:
        bits = []
        if ns.get(ctype, 0) >= 0.7:
            bits.append(f"header '{column}' matches {ctype}")
        if vs.get(ctype, 0) >= 0.6:
            bits.append(f"{int(vs[ctype] * 100)}% of sampled values validate as {ctype}")
        if ctype in ("email", "username", "member_id") and uq >= 0.9 and non_null > 3:
            bits.append(f"values are {int(uq * 100)}% unique")
        if not bits:
            bits.append(f"weak {ctype} signal")
        suggestions.append(
            {
                "canonical_type": ctype,
                "confidence": round(score, 4),
                "reason": "; ".join(bits),
            }
        )

    inferred = suggestions[0]["canonical_type"] if suggestions and suggestions[0]["confidence"] >= 0.4 else None

    return ColumnProfile(
        column=column,
        dtype=dtype,
        total=total_rows,
        non_null=non_null,
        null_ratio=round(1 - (non_null / max(len(sample), 1)), 4),
        distinct=len({str(v) for v in sample if not is_null(v)}),
        uniqueness=round(uq, 4),
        sample_values=[str(v)[:120] for v in sample[:8] if not is_null(v)],
        suggestions=suggestions,
        inferred_type=inferred,
    )


# --------------------------------------------------------------------------- #
# dataset-level mapping
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class MappingResult:
    columns: list[ColumnProfile]
    mapping: dict[str, str]          # canonical_type -> column
    confidence: float
    unmapped_columns: list[str]
    ambiguous: dict[str, list[dict[str, Any]]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "columns": [c.as_dict() for c in self.columns],
            "mapping": self.mapping,
            "confidence": round(self.confidence, 4),
            "unmapped_columns": self.unmapped_columns,
            "ambiguous": self.ambiguous,
        }


def build_mapping(
    rows: Sequence[dict[str, Any]],
    columns: Sequence[str] | None = None,
    *,
    lock_columns: dict[str, str] | None = None,
) -> MappingResult:
    """Suggest a canonical mapping for a dataset.

    Assignment is greedy by descending confidence so a strong ``email`` column
    cannot be stolen by a weak ``name`` claim on the same header.
    """
    rows = list(rows)
    if columns is None:
        seen: list[str] = []
        for r in rows[:50]:
            for k in r.keys():
                if k not in seen:
                    seen.append(k)
        columns = seen

    profiles = [profile_column(col, [r.get(col) for r in rows], len(rows)) for col in columns]

    # greedy global assignment. Low-confidence suggestions stay visible in the
    # preview (operators can nudge them), but the *auto* mapping only trusts
    # fields with a strong score so categorical columns (titles, departments,
    # dates, prices) never leak into the identifier index.
    pairs: list[tuple[float, str, str]] = []
    for prof in profiles:
        for s in prof.suggestions:
            pairs.append((s["confidence"], prof.column, s["canonical_type"]))
    pairs.sort(key=lambda p: p[0], reverse=True)
    AUTOPICK_CONFIDENCE = 0.58

    assigned: dict[str, str] = {}       # canonical_type -> column
    used_columns: set[str] = set()
    ambiguous: dict[str, list[dict[str, Any]]] = {}
    top_by_column: dict[str, list[dict[str, Any]]] = {}
    for prof in profiles:
        top_by_column[prof.column] = prof.suggestions

    for conf, column, ctype in pairs:
        if ctype in assigned or column in used_columns:
            if conf >= 0.30:
                ambiguous.setdefault(ctype, []).append(
                    {
                        "column": column,
                        "confidence": round(conf, 4),
                        "reason": next(
                            (s["reason"] for s in top_by_column.get(column, []) if s["canonical_type"] == ctype),
                            "competing match",
                        ),
                    }
                )
            continue
        if conf < AUTOPICK_CONFIDENCE:
            continue
        assigned[ctype] = column
        used_columns.add(column)

    # explicit user locks always win
    for ctype, column in (lock_columns or {}).items():
        if ctype in CANONICAL_FIELDS:
            prev = assigned.get(ctype)
            if prev and prev != column:
                used_columns.discard(prev)
            assigned[ctype] = column
            used_columns.add(column)

    confidences = []
    for ctype, column in assigned.items():
        prof = next((p for p in profiles if p.column == column), None)
        best = 0.0
        if prof:
            best = max(
                (s["confidence"] for s in prof.suggestions if s["canonical_type"] == ctype),
                default=0.5,
            )
        confidences.append(best)

    overall = (sum(confidences) / len(confidences)) if confidences else 0.0
    # a dataset with no identifier-bearing field cannot be resolved at all
    if not ({"email", "phone", "username", "member_id"} & set(assigned)):
        overall = min(overall, 0.25)
    # flag weak-but-present identifier coverage in the confidence
    idents_pct = len({"email", "phone", "username", "member_id"} & set(assigned))
    if idents_pct < 2:
        overall = overall * (0.75 + 0.25 * idents_pct)

    unmapped = [c for c in columns if c not in used_columns]

    return MappingResult(
        columns=profiles,
        mapping=assigned,
        confidence=overall,
        unmapped_columns=unmapped,
        ambiguous=ambiguous,
    )


def mapping_summary(mapping: MappingResult) -> str:
    if not mapping.mapping:
        return "no canonical fields could be identified"
    return ", ".join(f"{c}<-{col}" for c, col in sorted(mapping.mapping.items()))


def normalize_column_for_display(column: str) -> str:
    return normalize_name(column).normalized or column


def looks_like_username(value: str) -> bool:
    return normalize_username(value).normalized is not None


def entropy_score(values: Sequence[Any]) -> float:
    """Shannon entropy - useful signal for id vs free-text columns."""
    present = [str(v) for v in values if not is_null(v)]
    if not present:
        return 0.0
    counts: dict[str, int] = {}
    for v in present:
        counts[v] = counts.get(v, 0) + 1
    n = len(present)
    h = -sum((c / n) * math.log2(c / n) for c in counts.values())
    return h / math.log2(n) if n > 1 and h > 0 else 0.0
