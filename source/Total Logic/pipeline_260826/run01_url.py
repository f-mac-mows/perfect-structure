# -*- coding: utf-8 -*-
"""단일 URL 서비스 진입점 — 사용자가 URL 하나를 넣으면 끝까지.

    URL ──► 크롤 ──► 정제·문장화 ──► Claim 추출(17필드) ──► 검색·판정(run04) ──► 게시글 JSON
         p_crawl      p_ingest       p3 (HCX-007)         run04_local.py       data/posts/

이 파일은 1번 파트 소유다 — 팀장 코드(adapter.py·run04_local.py 등)는 한 줄도 건드리지
않고 **subprocess로 호출**만 한다(구성·all_modes 등은 run04_local.py의 것을 그대로 씀 —
그쪽이 바뀌면 자동으로 따라간다).

산출물(게시글): `data/posts/{article_id}.json` 하나에 프론트가 기사 화면을 그리는 데
필요한 전부를 담는다 —
    article    기사 메타 + 정제 본문 + 문단 목록 (화면에 보이는 본문 그대로)
    sentences  문장 오프셋 (문단 안 좌표 포함 — Claim 하이라이트용)
    claims     17필드 계약 + 각 Claim에 verdict(STRICT/TOLERANCE/RAW_ONLY 3-mode) 내장
    excluded   검증하지 않은 수치 문장과 그 사유 (리포트의 "왜 검증 안 했는지")
    summary    집계 (claim 수·eligible 수·판정 분포)
`data/posts/index.json`은 게시글 목록(프론트 목록 화면용) — 같은 URL을 다시 넣으면
article_id(URL 해시)가 같으므로 같은 게시글이 갱신된다(재분석). LLM 캐시가 성공분을
재생하므로 재분석의 재과금은 0이다.

사용:
    venv\\Scripts\\python.exe run01_url.py https://www.chosun.com/...기사URL...
    venv\\Scripts\\python.exe run01_url.py <URL> --no-verdict     # 검색·판정 생략(1번 구간만)
    venv\\Scripts\\python.exe run01_url.py <URL> --json           # 게시글 JSON을 stdout에도 출력

검색·판정은 `kosis_warehouse.db`가 프로젝트 루트에 있어야 돈다 — 없으면 그 단계만
건너뛰고 게시글에 `verdict_status: "skipped_no_db"`로 남긴다(db가 오면 같은 명령 재실행).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src import config, llm_meter
from src.p_ingest import ingest_url, IngestError
from src.p3_emit import AccountingError

POSTS_DIR_DEFAULT = "posts"            # data/posts/
RUN04 = ROOT / "run04_local.py"
WAREHOUSE_DB = ROOT / "kosis_warehouse.db"


# ── 1) URL → P3 입력 파일 ────────────────────────────────────
def write_p3_inputs(doc: dict, outdir: Path) -> None:
    """p_ingest 결과를 P3가 읽는 파일 형식으로 기록.

    P3(collect_candidates)는 sentences 각 행에 article_id가 있어야 articles와 조인한다 —
    p_ingest의 문장 행에는 없으므로 여기서 붙인다(배치 경로의 p2_split 산출과 동형).
    """
    outdir.mkdir(parents=True, exist_ok=True)
    art = doc["article"]
    with open(outdir / "articles_clean.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps(art, ensure_ascii=False) + "\n")
    with open(outdir / "sentences.jsonl", "w", encoding="utf-8") as f:
        for s in doc["sentences"]:
            f.write(json.dumps({"article_id": art["article_id"], **s},
                               ensure_ascii=False) + "\n")


# ── 2) P3 — Claim 추출 (run01.py와 같은 동결 구성) ──────────
def run_p3(outdir: Path, meter) -> dict:
    from run01 import build_extractor            # 동결 구성(HCX-007·low·캐시)을 한 곳만 두기
    from src.p3_pipeline import run as pipeline_run

    cache_path = config.cache_dir() / "replay_extract_v1.jsonl"
    extractor = build_extractor(outdir, cache_path, stub=False, fresh=False, meter=meter)
    return pipeline_run(extractor, outdir,
                        sentences_path=outdir / "sentences.jsonl",
                        articles_path=outdir / "articles_clean.jsonl",
                        breaker_rate=1.0)        # 단일 기사 — 문장 몇 개 실패로 전체를 죽이지 않는다


# ── 3) 검색·판정 — 팀장 파이프라인 호출 ──────────────────────
def run_verdicts(claims_path: Path, out_path: Path) -> tuple[str, list[dict]]:
    """run04_local.py를 subprocess로 호출. 반환: (상태, 결과 목록).

    상태: "ok" | "skipped_no_db" | "failed: ..." — 실패해도 1번 구간 산출물은 이미
    완성돼 있으므로 게시글은 verdict 없이 나간다(부분 실패를 전체 실패로 만들지 않는다).
    """
    if not WAREHOUSE_DB.is_file():
        return "skipped_no_db", []
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    proc = subprocess.run(
        [sys.executable, str(RUN04), str(claims_path), str(out_path)],
        cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        return "failed: " + " / ".join(tail), []
    try:
        return "ok", json.loads(out_path.read_text(encoding="utf-8"))
    except Exception as e:
        return f"failed: 결과 파싱 불가({e})", []


# ── 4) 게시글 조립 ───────────────────────────────────────────
def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def build_post(doc: dict, outdir: Path, p3_summary: dict,
               verdict_status: str, verdicts: list[dict]) -> dict:
    claims = _read_jsonl(outdir / "claims.jsonl")          # 17필드 계약본
    excluded = _read_jsonl(outdir / "excluded.jsonl")
    by_id = {v.get("claim_id"): v for v in verdicts}
    for c in claims:
        c["verdict"] = by_id.get(c["claim_id"])            # 없으면 null — 프론트가 "판정 대기" 표시

    # 판정 분포 — run04_local.py 콘솔 요약과 같은 관례(tolerance 기준)
    counts: dict[str, int] = {}
    for v in verdicts:
        key = v["verdict"] if "verdict" in v else v.get("modes", {}).get("tolerance", {}).get("verdict", "?")
        counts[key] = counts.get(key, 0) + 1

    return {
        "article": doc["article"],
        "sentences": doc["sentences"],
        "claims": claims,
        "excluded": [{k: e.get(k) for k in ("sent_id", "sentence", "exclusion_code", "note")}
                     for e in excluded],
        "summary": {
            "n_sentences": len(doc["sentences"]),
            "n_numeric_sentences": p3_summary.get("numeric_sentences"),
            "n_claims": len(claims),
            "n_eligible": p3_summary.get("eligible_true"),
            "n_excluded": len(excluded),
            "verdict_status": verdict_status,
            "verdict_counts": counts or None,
        },
        "versions": {**doc.get("versions", {}),
                     "pipeline": "p3_v1", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
    }


def update_index(posts_dir: Path, post: dict) -> Path:
    """게시글 목록(index.json) upsert — 프론트 목록 화면용 요약만 담는다."""
    idx_path = posts_dir / "index.json"
    entries = []
    if idx_path.is_file():
        try:
            entries = json.loads(idx_path.read_text(encoding="utf-8"))
        except Exception:
            entries = []
    art, s = post["article"], post["summary"]
    row = {"article_id": art["article_id"], "title": art.get("title"),
           "posted_date": art.get("posted_date"), "publisher": art.get("publisher"),
           "url": art.get("url"), "n_claims": s["n_claims"], "n_eligible": s["n_eligible"],
           "verdict_status": s["verdict_status"], "verdict_counts": s["verdict_counts"],
           "analyzed_at": post["versions"]["generated_at"]}
    entries = [e for e in entries if e.get("article_id") != art["article_id"]]
    entries.insert(0, row)                                  # 최신이 앞
    idx_path.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    return idx_path


# ── 오케스트레이션 ───────────────────────────────────────────
def analyze_url(url: str, *, posts_dir: Path | None = None, verdict: bool = True,
                meter=None) -> dict:
    """URL 하나를 끝까지 — 서비스(백엔드 API)에서는 이 함수를 그대로 부르면 된다.

    반환: 게시글 dict (data/posts/{article_id}.json에 저장된 것과 동일).
    """
    posts_dir = posts_dir or (config.data_dir() / POSTS_DIR_DEFAULT)

    print("── 크롤·전처리 ─────────────────────────────")
    doc = ingest_url(url)
    art = doc["article"]
    outdir = posts_dir / art["article_id"]                  # 작업 산출물(재개·감사용)
    write_p3_inputs(doc, outdir)
    print(f"{art['article_id']} | {art.get('publisher', '?')} | {art.get('posted_date')}")
    print(f"{art.get('title')}")
    print(f"문단 {len(art.get('paragraphs', []))} · 문장 {len(doc['sentences'])}")

    print("\n── Claim 추출 (P3) ─────────────────────────")
    summary = run_p3(outdir, meter)
    print(f"숫자 문장 {summary['numeric_sentences']} → Claim {summary['claims']} · "
          f"제외 {summary['excluded']} · eligible {summary['eligible_true']}")

    # 인수인계 계약 사본(배치 경로와 동일 관례 — 다음 단계가 이름 하나만 보면 된다)
    claims_path = Path(summary["paths"]["claims"])
    try:
        import shutil

        import interface
        shutil.copyfile(claims_path, interface.PIPELINE01_PATH)
    except Exception as e:
        print(f"[안내] interface 사본 생성 실패(무시하고 계속): {e}")

    verdict_status, verdicts = "skipped", []
    if verdict:
        print("\n── 검색·판정 (run04_local.py) ──────────────")
        verdict_status, verdicts = run_verdicts(claims_path, outdir / "verdicts.json")
        print({"ok": f"판정 {len(verdicts)}건",
               "skipped_no_db": "kosis_warehouse.db 없음 — 판정 생략(1번 구간만 저장)"}
              .get(verdict_status, f"판정 실패 — {verdict_status}"))

    post = build_post(doc, outdir, summary, verdict_status, verdicts)
    post_path = posts_dir / f"{art['article_id']}.json"
    post_path.write_text(json.dumps(post, ensure_ascii=False, indent=1), encoding="utf-8")
    idx_path = update_index(posts_dir, post)
    print(f"\n게시글 → {post_path}")
    print(f"목록   → {idx_path}")
    return post


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        description="단일 URL 분석 — 크롤 → Claim 추출 → 검색·판정 → 게시글 JSON")
    ap.add_argument("url", help="기사 URL")
    ap.add_argument("--posts-dir", type=Path, default=None,
                    help="게시글 저장 폴더 (기본: data/posts)")
    ap.add_argument("--no-verdict", action="store_true",
                    help="검색·판정(run04) 생략 — 1번 구간(Claim 추출)까지만")
    ap.add_argument("--json", action="store_true", help="게시글 JSON을 stdout에도 출력")
    ap.add_argument("--no-meter", action="store_true", help="사용량 기록 끄기")
    args = ap.parse_args(argv)

    from src.p3_stage_b import PROMPT_VERSION
    meter = None if args.no_meter else llm_meter.UsageMeter(None, prompt_version=PROMPT_VERSION)

    try:
        post = analyze_url(args.url, posts_dir=args.posts_dir,
                           verdict=not args.no_verdict, meter=meter)
    except IngestError as e:
        print(f"\n[크롤 실패] {e}", file=sys.stderr)
        return 2
    except (ValueError, RuntimeError, FileNotFoundError) as e:
        kind = "전수 회계 위반" if isinstance(e, AccountingError) else "실행 중단"
        print(f"\n[{kind}] {e}", file=sys.stderr)
        return 3 if isinstance(e, AccountingError) else 2

    if meter is not None and meter.records:
        s = meter.summary()
        print(f"[사용량] API {s['calls_api']}콜 · 캐시 재생 {s['calls_cached']} · "
              f"{s['cost_krw']:,.2f}원(VAT 별도)")
    if args.json:
        print(json.dumps(post, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
