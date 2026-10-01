from app.services.field_mapping import build_mapping
from app.services.seed_datasets import DATASETS

for spec in DATASETS:
    res = build_mapping(spec.rows, spec.columns)
    print(f"{spec.system_key} {spec.label}")
    print("   mapping:", {k: v for k, v in res.mapping.items()}, "conf=", round(res.confidence, 3))
    for c in res.columns:
        top = c.suggestions[0] if c.suggestions else None
        nt = top['canonical_type'] if top else None
        nc = top['confidence'] if top else 0
        print(f"      {c.column!r:18} -> {nt} ({nc:.2f})")