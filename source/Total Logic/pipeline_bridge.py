from __future__ import annotations

import json
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode


PROJECT_ROOT = Path(__file__).resolve().parent
BACKEND_DIR = PROJECT_ROOT / "pipeline_260826"
CLAIMS_FIXTURE_PATH = BACKEND_DIR / "run01_result.jsonl"
ARTICLES_FIXTURE_PATH = PROJECT_ROOT / "static" / "data" / "articles_clean.jsonl"
KOSIS_DB_PATH = BACKEND_DIR / "kosis_warehouse.db"
VERDICT_FIXTURE_PATH = BACKEND_DIR / "verdict_output_examples.jsonl"
POSTS_DIR = PROJECT_ROOT / "data" / "posts"
BUNDLED_POSTS_DIR = BACKEND_DIR / "data" / "posts"

_pipeline_lock = threading.Lock()


class PipelineConfigurationError(RuntimeError):
    pass


def _post_path(article_id: str) -> Path:
    """Resolve an article JSON path without allowing directory traversal."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", article_id or ""):
        raise ValueError("article_id 형식이 올바르지 않습니다.")
    path = (POSTS_DIR / f"{article_id}.json").resolve()
    if path.parent != POSTS_DIR.resolve():
        raise ValueError("article_id 경로가 올바르지 않습니다.")
    return path


def _selected_verdict(claim: dict[str, Any]) -> Optional[str]:
    payload = claim.get("verdict")
    if not isinstance(payload, dict):
        return None
    strict = (payload.get("modes") or {}).get("strict")
    if isinstance(strict, dict):
        verdict = strict.get("verdict")
        return verdict if isinstance(verdict, str) else None
    verdict = payload.get("verdict")
    return verdict if isinstance(verdict, str) else None


def _normalize_verdict(value: Optional[str]) -> str:
    if value is None:
        return "UNVERIFIED"
    normalized = str(value).strip().upper()
    if normalized == "VERIFIED":
        return "VERIFIED"
    if normalized == "MISMATCH":
        return "MISMATCH"
    if normalized.startswith("UNVERIFIED_") or normalized in {
        "UNVERIFIED",
        "PENDING",
        "WAITING",
        "NOT_VERIFIED",
    }:
        return "UNVERIFIED"
    if normalized == "NOT_ELIGIBLE":
        return "UNVERIFIED"
    if normalized.startswith("ERROR"):
        return "ERROR"
    return "UNVERIFIED"


def _strict_values_match(claimed: Any, actual: Any, hedge_type: Any) -> Optional[bool]:
    if not isinstance(claimed, (int, float)) or not isinstance(actual, (int, float)):
        return None
    return actual == claimed


def _refresh_legacy_strict_modes(post: dict[str, Any]) -> None:
    """Apply the exact STRICT rule to reports saved before that rule changed."""
    for claim in post.get("claims") or []:
        payload = claim.get("verdict")
        if not isinstance(payload, dict):
            continue
        strict = (payload.get("modes") or {}).get("strict")
        if not isinstance(strict, dict):
            continue
        matched = _strict_values_match(
            strict.get("claimed_value"),
            strict.get("actual_value"),
            strict.get("hedge_type"),
        )
        if matched is None:
            continue
        strict["verdict"] = "VERIFIED" if matched else "MISMATCH"
        strict["explanation"] = (
            f"{strict.get('explanation', '').split(' - strict 기준')[0]}"
            f" - strict 기준 허용 오차 없이 "
            f"{'일치합니다.' if matched else '일치하지 않습니다.'}"
        )


def _sentence_key(claim: dict[str, Any]) -> tuple[str, str]:
    """Return the stable source-sentence identity available in pipeline output."""
    for field in ("sent_id", "sentence_id", "source_sentence_id"):
        value = claim.get(field)
        if isinstance(value, str) and value.strip():
            return field, value.strip()

    for field in (
        "original_sentence",
        "source_sentence",
        "sentence",
        "original_claim",
        "claim",
    ):
        value = claim.get(field)
        if isinstance(value, str) and value.strip():
            return field, " ".join(value.split())

    # A Claim without any sentence identity must not be merged with unrelated Claims.
    claim_id = claim.get("claim_id")
    return "unidentified_claim", str(claim_id or id(claim))


def _sentence_verdict(values: list[Optional[str]]) -> Optional[str]:
    """Select one report verdict for all numeric Claims in one source sentence."""
    normalized_values = [_normalize_verdict(value) for value in values]
    if "ERROR" in normalized_values:
        return "ERROR"
    if "MISMATCH" in normalized_values:
        return "MISMATCH"
    if "UNVERIFIED" in normalized_values:
        return "UNVERIFIED"
    if normalized_values and all(value == "NOT_ELIGIBLE" for value in normalized_values):
        return "NOT_ELIGIBLE"
    if normalized_values and all(value == "VERIFIED" for value in normalized_values):
        return "VERIFIED"
    return "UNVERIFIED"


def _sentence_verdicts(values: list[Optional[str]]) -> set[str]:
    """Return verdict categories present in one source sentence.

    A sentence can contain multiple numeric Claims with different outcomes.
    Report badges count each category by unique source sentence, so a mixed
    sentence can contribute once to both VERIFIED and MISMATCH, but never more
    than once to either category.
    """
    return {_normalize_verdict(value) for value in values}


def _frontend_summary(post: dict[str, Any]) -> dict[str, Any]:
    summary = dict(post.get("summary") or {})
    article_id = str((post.get("article") or {}).get("article_id") or "")
    claim_verdicts: dict[str, Optional[str]] = {}
    sentence_claims: dict[tuple[str, str], list[Optional[str]]] = {}
    for claim in post.get("claims") or []:
        claim_article_id = str(claim.get("article_id") or "")
        if article_id and claim_article_id and claim_article_id != article_id:
            continue
        claim_id = claim.get("claim_id")
        if not isinstance(claim_id, str) or claim_id in claim_verdicts:
            continue
        value = _selected_verdict(claim)
        claim_verdicts[claim_id] = value
        sentence_claims.setdefault(_sentence_key(claim), []).append(value)

    # Count each verdict category by unique source sentence, not by numeric
    # Claim. Mixed outcomes in one sentence remain visible in every applicable
    # category while duplicate Claims cannot inflate a category count.
    sentence_verdicts = [
        _sentence_verdicts(values) for values in sentence_claims.values()
    ]
    n_verified = sum("VERIFIED" in values for values in sentence_verdicts)
    n_mismatch = sum("MISMATCH" in values for values in sentence_verdicts)
    n_unverified = sum("UNVERIFIED" in values for values in sentence_verdicts)
    n_not_eligible = sum("NOT_ELIGIBLE" in values for values in sentence_verdicts)
    n_error = sum("ERROR" in values for values in sentence_verdicts)
    summary.update(
        n_claims=len(claim_verdicts),
        n_sentences=len(sentence_claims),
        n_pending=0,
        n_verified_claims=n_verified + n_mismatch + n_unverified,
        n_processed_sentences=len(sentence_verdicts),
        n_verified=n_verified,
        n_mismatch=n_mismatch,
        n_unverified=n_unverified,
        n_not_eligible=n_not_eligible,
        n_error=n_error,
    )
    return summary


def _add_kosis_urls(post: dict[str, Any]) -> None:
    for claim in post.get("claims") or []:
        verdict = claim.get("verdict")
        if not isinstance(verdict, dict):
            continue
        evidence = verdict.get("evidence")
        if not isinstance(evidence, dict):
            continue
        org_id = evidence.get("table_org_id")
        table_id = evidence.get("table_tbl_id")
        evidence["kosis_url"] = (
            "https://kosis.kr/statHtml/statHtml.do?"
            + urlencode({"orgId": org_id, "tblId": table_id})
            if org_id and table_id
            else None
        )


def load_frontend_post(article_id: str) -> Optional[dict[str, Any]]:
    """Load and enrich one final pipeline JSON without modifying the file."""
    path = _post_path(article_id)
    if not path.is_file():
        return None
    try:
        post = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name}의 JSON 형식이 올바르지 않습니다.") from exc
    if not isinstance(post, dict):
        raise ValueError(f"{path.name}의 최상위 값은 객체여야 합니다.")
    _refresh_legacy_strict_modes(post)
    _add_kosis_urls(post)
    post["summary"] = _frontend_summary(post)
    return post


def list_frontend_posts() -> list[dict[str, Any]]:
    if not POSTS_DIR.is_dir():
        return []
    posts: list[dict[str, Any]] = []
    for path in POSTS_DIR.glob("*.json"):
        if path.name == "index.json":
            continue
        post = load_frontend_post(path.stem)
        if post is not None:
            posts.append(post)
    posts.sort(key=lambda post: str((post.get("versions") or {}).get("generated_at") or ""), reverse=True)
    return posts


def find_frontend_post(*, url: Optional[str] = None, title: Optional[str] = None) -> Optional[dict[str, Any]]:
    normalized_url = (url or "").strip().rstrip("/")
    normalized_title = " ".join((title or "").split()).casefold()
    for post in list_frontend_posts():
        article = post.get("article") or {}
        same_url = normalized_url and str(article.get("url") or "").strip().rstrip("/") == normalized_url
        saved_title = " ".join(str(article.get("title") or "").split()).casefold()
        if same_url or (normalized_title and normalized_title == saved_title):
            return post
    return None


def find_bundled_post(*, url: Optional[str] = None, title: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Find a backend-produced sample post without mixing it into the public board."""
    normalized_url = (url or "").strip().rstrip("/")
    normalized_title = " ".join((title or "").split()).casefold()
    if not BUNDLED_POSTS_DIR.is_dir():
        return None
    for path in BUNDLED_POSTS_DIR.glob("A*.json"):
        try:
            post = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        # Bundled reports are snapshots. If the warehouse was expanded after
        # a report was generated, reusing that report would keep stale
        # NOT_FOUND/UNRESOLVED verdicts even though the new table now exists.
        generated_at = str((post.get("versions") or {}).get("generated_at") or "")
        try:
            generated_timestamp = datetime.fromisoformat(generated_at).timestamp()
        except ValueError:
            generated_timestamp = 0.0
        if KOSIS_DB_PATH.is_file() and generated_timestamp < KOSIS_DB_PATH.stat().st_mtime:
            continue
        article = post.get("article") or {}
        same_url = normalized_url and str(article.get("url") or "").strip().rstrip("/") == normalized_url
        saved_title = " ".join(str(article.get("title") or "").split()).casefold()
        if same_url or (normalized_title and normalized_title == saved_title):
            return post
    return None


def publish_bundled_post(post: dict[str, Any]) -> dict[str, Any]:
    """Copy one already-produced backend result into the frontend/API post store."""
    article_id = str((post.get("article") or {}).get("article_id") or "")
    path = _post_path(article_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(post, ensure_ascii=False, indent=1), encoding="utf-8")
    return post


def analyze_frontend_url(url: str) -> dict[str, Any]:
    """Run the existing URL pipeline and write its final JSON to the public post store."""
    backend_path = str(BACKEND_DIR)
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)

    # The handoff modules intentionally use flat imports relative to
    # pipeline_260826, so keep its directory intact and import the existing
    # service entry point without recreating any pipeline logic here.
    from run01_url import analyze_url

    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    with _pipeline_lock:
        return analyze_url(url, posts_dir=POSTS_DIR, verdict=True, meter=None)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as source:
        for line_number, raw_line in enumerate(source, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name} {line_number}번째 줄의 JSON 형식이 올바르지 않습니다.") from exc
    return rows


def fixture_articles() -> list[dict[str, Any]]:
    return _read_jsonl(ARTICLES_FIXTURE_PATH)


def find_fixture_article(*, url: Optional[str] = None, title: Optional[str] = None) -> Optional[dict[str, Any]]:
    normalized_url = (url or "").strip().rstrip("/")
    normalized_title = " ".join((title or "").split()).casefold()
    for article in fixture_articles():
        if normalized_url and str(article.get("url") or "").strip().rstrip("/") == normalized_url:
            return article
        candidate_title = " ".join(str(article.get("title") or "").split()).casefold()
        if normalized_title and (normalized_title in candidate_title or candidate_title in normalized_title):
            return article
    return None


def get_claims_for_verification(article_id: str) -> list[dict[str, Any]]:
    """Temporary Task1 boundary.

    Replace only this function body with the future URL -> Task1 Claim[] call.
    The downstream backend entry point and its input contract stay unchanged.
    """
    return [
        claim
        for claim in _read_jsonl(CLAIMS_FIXTURE_PATH)
        if claim.get("article_id") == article_id
    ]


def pipeline_readiness() -> dict[str, Any]:
    env_candidates = (BACKEND_DIR / ".env", PROJECT_ROOT / ".env")
    return {
        "backend_files": BACKEND_DIR.is_dir(),
        "claims_fixture": CLAIMS_FIXTURE_PATH.is_file(),
        "articles_fixture": ARTICLES_FIXTURE_PATH.is_file(),
        "kosis_db": KOSIS_DB_PATH.is_file(),
        "verdict_fixture": VERDICT_FIXTURE_PATH.is_file(),
        "mode": "live_pipeline" if KOSIS_DB_PATH.is_file() else "jsonl_fixture",
        "ready": KOSIS_DB_PATH.is_file() or VERDICT_FIXTURE_PATH.is_file(),
        "env_file": next((str(path) for path in env_candidates if path.is_file()), None),
    }


def get_existing_results(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only backend-produced results matching the requested Claim IDs."""
    if not VERDICT_FIXTURE_PATH.is_file():
        raise PipelineConfigurationError("백엔드 verdict JSONL 결과 파일이 없습니다.")
    claim_ids = {claim.get("claim_id") for claim in claims}
    results = [
        result for result in _read_jsonl(VERDICT_FIXTURE_PATH)
        if result.get("claim_id") in claim_ids
    ]
    if not results:
        raise PipelineConfigurationError("선택한 기사의 Claim에 대응하는 백엔드 verdict 결과가 없습니다.")
    return results


def run_existing_pipeline(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Call the handoff pipeline with the exact production agent configuration.

    No search, KOSIS, HCX, value comparison, or verdict logic is implemented here.
    """
    if not claims:
        raise PipelineConfigurationError("선택한 기사에 대응하는 run01 Claim 입력이 없습니다.")
    if not KOSIS_DB_PATH.is_file():
        # 현재 1차 연결은 백이 이미 생성한 JSONL 결과를 전달한다. Claim,
        # 판정, 수치, evidence를 여기서 새로 만들거나 다시 계산하지 않는다.
        return get_existing_results(claims)

    backend_path = str(BACKEND_DIR)
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)

    # Imports intentionally remain flat because the handoff modules use imports
    # such as `from judgment import ...` and `from local_db_agent import ...`.
    from adapter import run_search_and_judge
    from client import KosisApiClient
    from hcx_stage1_resolver import resolve_table_with_hcx007
    from hcx_stage2_resolver import resolve_cell_with_hcx007
    from hcx_stage3_resolver import resolve_comparison_mode_with_hcx007
    from hcx_tree_resolver import resolve_axis_codes_with_hcx007
    from local_db_agent import LocalDbAgent

    with _pipeline_lock:
        agent = LocalDbAgent(
            db_path=str(KOSIS_DB_PATH),
            stage1_keywords="llm_table_select",
            hcx_table_resolve_fn=resolve_table_with_hcx007,
            hcx_resolve_fn=resolve_cell_with_hcx007,
            hcx_axis_resolve_fn=resolve_axis_codes_with_hcx007,
            kosis_client=KosisApiClient(),
            hcx_stage3_fn=resolve_comparison_mode_with_hcx007,
        )
        try:
            # run04_local.py의 실사용 계약과 동일하게 세 판정 mode를 모두
            # 반환한다. 검색/증거 조회는 한 번만 수행되며 판정 로직은
            # 기존 backend 함수 내부에서 실행된다.
            return run_search_and_judge(claims, {}, agent=agent, all_modes=True)
        finally:
            agent.close()
