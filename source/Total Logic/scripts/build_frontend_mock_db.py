"""Build the frontend Mock DB by joining Article, Task1 Claim, and verdict JSONL.

The mapper never creates claims, verdicts, values, or evidence. It only joins
records by exact article_id/claim_id and derives UI presentation fields.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTICLES = ROOT / "static/data/articles_clean.jsonl"
DEFAULT_CLAIMS = ROOT / "pipeline_260826/run01_result.jsonl"
DEFAULT_VERDICTS = ROOT / "static/data/verdict_output_examples.jsonl"
DEFAULT_OUTPUT = ROOT / "static/data/frontend_mock_db.json"
CLAIM_ID_PATTERN = re.compile(r"^(?P<article_id>.+)-C(?P<sequence>\d+)$")

VERDICT_UI = {
    "VERIFIED": ("MATCH", "일치", "verified"),
    "MISMATCH": ("MISMATCH", "불일치", "mismatch"),
    "UNVERIFIED_NOT_FOUND": ("UNVERIFIED", "판단 불가", "unverified"),
    "UNVERIFIED_UNRESOLVED": ("UNVERIFIED", "판단 불가", "unverified"),
    "UNVERIFIED_DERIVED_NEEDED": ("UNVERIFIED", "판단 불가", "unverified"),
    "UNVERIFIED_RECORD_CLAIM": ("UNVERIFIED", "판단 불가", "unverified"),
    "NOT_ELIGIBLE": ("NOT_ELIGIBLE", "검증 대상 아님", "notEligible"),
    "ERROR": ("ERROR", "검증 오류", "error"),
    "RAW_ONLY": ("RAW_ONLY", "원자료 확인", "raw"),
}
FULL_RESULT_FIELDS = (
    "claimed_value", "actual_value", "hedge_type", "mode", "ai_used", "ai_note", "evidence"
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as source:
        for line_number, raw_line in enumerate(source, start=1):
            if not raw_line.strip():
                continue
            try:
                rows.append(json.loads(raw_line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number} JSON 오류: {exc}") from exc
    return rows


def extract_article_id(claim_id: str) -> Optional[str]:
    match = CLAIM_ID_PATTERN.fullmatch(str(claim_id or ""))
    return match.group("article_id") if match else None


def claim_sequence(claim_id: str) -> int:
    match = CLAIM_ID_PATTERN.fullmatch(str(claim_id or ""))
    return int(match.group("sequence")) if match else 10**9


def map_backend_verdict_to_frontend(raw_verdict: str) -> dict[str, str]:
    try:
        ui_verdict, label, group = VERDICT_UI[raw_verdict]
    except KeyError as exc:
        raise ValueError(f"알 수 없는 backend verdict: {raw_verdict}") from exc
    return {
        "raw_verdict": raw_verdict,
        "ui_verdict": ui_verdict,
        "verdict_label": label,
        "verdict_group": group,
    }


def display_verdict(result: dict[str, Any]) -> dict[str, Any]:
    """Return the flat MinimalResult or the handoff's tolerance ModeVerdict."""
    if "modes" not in result:
        return result
    tolerance = result.get("modes", {}).get("tolerance")
    if not isinstance(tolerance, dict):
        raise ValueError(f"{result.get('claim_id')}: modes.tolerance가 없습니다.")
    return {**result, **tolerance, "evidence": result.get("evidence")}


def map_backend_claim(verdict: dict[str, Any], task1: dict[str, Any], article: dict[str, Any]) -> dict[str, Any]:
    claim_id = verdict["claim_id"]
    display = display_verdict(verdict)
    mapped: dict[str, Any] = {
        "claim_id": claim_id,
        "article_id": task1["article_id"],
        "sent_id": task1.get("sent_id"),
        "claim": verdict["claim"],
        **map_backend_verdict_to_frontend(display["verdict"]),
        "explanation": display["explanation"],
        "metric": task1.get("metric"),
        "metric_normalized": task1.get("metric_normalized"),
        "value": task1.get("value"),
        "value_num": task1.get("value_num"),
        "unit": task1.get("unit"),
        "period": task1.get("period"),
        "kosis_eligible": task1.get("kosis_eligible"),
        "exclusion_code": task1.get("exclusion_code"),
        "raw_backend": verdict,
    }
    if display["verdict"] not in {"NOT_ELIGIBLE", "ERROR"}:
        for field in FULL_RESULT_FIELDS:
            mapped[field] = display.get(field)
        mapped["mode"] = "tolerance"
        mapped["backend_modes"] = verdict.get("modes")

    start = str(article.get("text") or "").find(verdict["claim"])
    mapped["start_offset"] = start if start >= 0 else None
    mapped["end_offset"] = start + len(verdict["claim"]) if start >= 0 else None
    return mapped


def build_frontend_articles(
    articles: list[dict[str, Any]],
    task1_claims: list[dict[str, Any]],
    verdicts: list[dict[str, Any]],
) -> dict[str, Any]:
    article_by_id = {row["article_id"]: row for row in articles}
    task1_by_id: dict[str, dict[str, Any]] = {}
    duplicate_claim_ids: list[str] = []
    for claim in task1_claims:
        claim_id = claim.get("claim_id")
        if claim_id in task1_by_id:
            duplicate_claim_ids.append(claim_id)
            continue
        task1_by_id[claim_id] = claim

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unmatched_claims: list[dict[str, str]] = []
    unmatched_article_ids: set[str] = set()
    id_conflicts: list[dict[str, str]] = []
    duplicate_verdict_ids: list[str] = []
    seen_verdict_ids: set[str] = set()

    for verdict in verdicts:
        claim_id = verdict.get("claim_id")
        if claim_id in seen_verdict_ids:
            duplicate_verdict_ids.append(claim_id)
            continue
        seen_verdict_ids.add(claim_id)
        extracted_article_id = extract_article_id(claim_id)
        if extracted_article_id is None:
            unmatched_claims.append({"claim_id": str(claim_id), "reason": "INVALID_CLAIM_ID"})
            continue
        task1 = task1_by_id.get(claim_id)
        if task1 is None:
            unmatched_claims.append({"claim_id": claim_id, "reason": "MISSING_RUN01_CLAIM"})
            continue
        task1_article_id = task1.get("article_id")
        if task1_article_id != extracted_article_id:
            id_conflicts.append({
                "claim_id": claim_id,
                "extracted_article_id": extracted_article_id,
                "run01_article_id": str(task1_article_id),
            })
            continue
        article = article_by_id.get(task1_article_id)
        if article is None:
            unmatched_article_ids.add(task1_article_id)
            continue
        grouped[task1_article_id].append(map_backend_claim(verdict, task1, article))

    frontend_articles: list[dict[str, Any]] = []
    unmatched_highlights: list[str] = []
    for article_id, claims in grouped.items():
        source = article_by_id[article_id]
        claims.sort(key=lambda row: (
            row["start_offset"] if row["start_offset"] is not None else 10**12,
            row.get("sent_id") or "",
            claim_sequence(row["claim_id"]),
        ))
        unmatched_highlights.extend(row["claim_id"] for row in claims if row["start_offset"] is None)
        counts = Counter(row["raw_verdict"] for row in claims)
        frontend_articles.append({
            "article_id": article_id,
            "input_type": "STORED_ARTICLE",
            "url": source.get("url"),
            "title": source.get("title"),
            "publisher": source.get("publisher"),
            "published_at": f"{source['posted_date']}T00:00:00+09:00" if source.get("posted_date") else None,
            "content": source.get("text") or "",
            "paragraphs": source.get("paragraphs") or [],
            "status": "COMPLETED",
            "claims": claims,
            "summary": {
                "total_claims": len(claims),
                "verified_count": counts["VERIFIED"],
                "mismatch_count": counts["MISMATCH"],
                "unverified_count": sum(count for verdict, count in counts.items() if verdict.startswith("UNVERIFIED_")),
                "not_eligible_count": counts["NOT_ELIGIBLE"],
                "error_count": counts["ERROR"],
                "raw_only_count": counts["RAW_ONLY"],
            },
        })

    frontend_articles.sort(key=lambda row: row["article_id"])
    verdict_counts = Counter(display_verdict(row)["verdict"] for row in verdicts)
    diagnostics = {
        "articles": len(frontend_articles),
        "claims": sum(len(row["claims"]) for row in frontend_articles),
        "verdict_counts": dict(sorted(verdict_counts.items())),
        "ui_counts": {
            "verified": verdict_counts["VERIFIED"],
            "mismatch": verdict_counts["MISMATCH"],
            "unverified": sum(count for verdict, count in verdict_counts.items() if verdict.startswith("UNVERIFIED_")),
            "not_eligible": verdict_counts["NOT_ELIGIBLE"],
            "error": verdict_counts["ERROR"],
            "raw_only": verdict_counts["RAW_ONLY"],
        },
        "unmatched_articles": sorted(unmatched_article_ids),
        "unmatched_claims": unmatched_claims,
        "unmatched_highlights": sorted(unmatched_highlights),
        "duplicate_run01_claim_ids": sorted(set(duplicate_claim_ids)),
        "duplicate_verdict_claim_ids": sorted(set(duplicate_verdict_ids)),
        "article_id_conflicts": id_conflicts,
    }
    return {
        "schema_version": "frontend-mock-db-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "articles": frontend_articles,
        "diagnostics": diagnostics,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--articles", type=Path, default=DEFAULT_ARTICLES)
    parser.add_argument("--claims", type=Path, default=DEFAULT_CLAIMS)
    parser.add_argument("--verdicts", type=Path, default=DEFAULT_VERDICTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build_frontend_articles(
        read_jsonl(args.articles), read_jsonl(args.claims), read_jsonl(args.verdicts)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["diagnostics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
