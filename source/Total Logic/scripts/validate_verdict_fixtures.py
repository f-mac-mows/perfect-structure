"""Lightweight validation for frontend verdict fixtures."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "static/data/verdict_output_examples.jsonl"
ALLOWED = {
    "VERIFIED", "MISMATCH", "UNVERIFIED_NOT_FOUND", "UNVERIFIED_UNRESOLVED",
    "UNVERIFIED_DERIVED_NEEDED", "UNVERIFIED_RECORD_CLAIM", "RAW_ONLY",
    "NOT_ELIGIBLE", "ERROR",
}
COMMON = {"claim_id", "claim"}
MINIMAL = {"claim_id", "claim", "verdict", "explanation"}
MODE = {"verdict", "explanation", "claimed_value", "actual_value", "hedge_type", "ai_used", "ai_note"}
EVIDENCE = {"table_org_id", "table_tbl_id", "table_nm", "retrieval_status"}


def main() -> None:
    results = [json.loads(line) for line in FIXTURE.read_text().splitlines() if line.strip()]
    for line_number, result in enumerate(results, 1):
        missing = COMMON - result.keys()
        assert not missing, f"line {line_number}: missing {sorted(missing)}"
        if "verdict" in result:
            missing = MINIMAL - result.keys()
            assert not missing, f"line {line_number}: missing MinimalResult fields {sorted(missing)}"
            assert result["verdict"] in {"NOT_ELIGIBLE", "ERROR"}, f"line {line_number}: invalid flat verdict"
            continue
        assert set(result.get("modes") or {}) == {"strict", "tolerance", "raw_only"}, f"line {line_number}: invalid modes"
        missing = EVIDENCE - (result.get("evidence") or {}).keys()
        assert not missing, f"line {line_number}: missing evidence fields {sorted(missing)}"
        for mode_name, mode_result in result["modes"].items():
            missing = MODE - mode_result.keys()
            assert not missing, f"line {line_number}/{mode_name}: missing {sorted(missing)}"
            assert mode_result["verdict"] in ALLOWED, f"line {line_number}/{mode_name}: unknown verdict"
    print(f"OK: {len(results)} verdict fixtures")


if __name__ == "__main__":
    main()
