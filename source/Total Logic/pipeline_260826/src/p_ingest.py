# -*- coding: utf-8 -*-
"""서비스 진입점 — URL 하나 → 문단·문장 구조 본문 (89차).

웹 서비스는 사용자가 입력한 URL을 즉시 처리해야 한다. 배치 경로(`p_crawl.crawl_urls`)는
파일에 쓰는 구조라 서비스에 맞지 않으므로, **파일 IO 없이 메모리에서** 같은 체인을 돈다:

    URL → [P-1 크롤] → [P0 표준화] → [P1 정제] → [P2 문장화] → dict

배치와 **같은 함수**를 호출하므로 산출 형식이 배치 결과와 동일하다(골든 채점·튜닝 결과가
서비스에 그대로 적용된다). 다른 것은 입출력 매체뿐이다.

Claim 추출(Stage B~E)은 LLM 호출이라 이 모듈 밖이다 — 여기 산출(sentences)이 그 입력이다.

사용:
    from src.p_ingest import ingest_url
    doc = ingest_url("https://www.chosun.com/...")
    doc["article"]["paragraphs"]   # 화면 렌더링용 문단 배열
    doc["sentences"]               # 하이라이트용 문장 + 오프셋
"""
from __future__ import annotations

from src.p0_load import standardize_article
from src.p1_clean import clean_text, PIPELINE_VERSION as CLEAN_VERSION
from src.p2_split import split_spans, PIPELINE_VERSION as SPLIT_VERSION
from src.p_crawl import fetch_html, parse_article


class IngestError(RuntimeError):
    """크롤·파싱 실패 — 서비스 계층이 사용자에게 보여줄 수 있는 단일 예외."""


def crawl_one(url: str, timeout: int = 30) -> dict:
    """URL 하나 → 크롤 산출 dict (파일 없음). 배치의 한 행과 같은 형태."""
    url = (url or "").strip()
    if not url.lower().startswith("http"):
        raise IngestError(f"URL 형식이 아닙니다: {url!r}")
    try:
        html = fetch_html(url, timeout=timeout)
    except Exception as e:                       # 네트워크·HTTP 실패
        raise IngestError(f"기사를 가져오지 못했습니다: {type(e).__name__}") from e
    try:
        return parse_article(html, url).to_dict()
    except ValueError as e:                      # 미지 구조·비기사 페이지
        raise IngestError(f"기사 본문을 찾지 못했습니다: {e}") from e


def ingest_url(url: str, timeout: int = 30) -> dict:
    """URL → 서비스가 바로 쓰는 문서 구조.

    반환:
      article    — article_id·title·posted_date·url·publisher·subtitle·text·paragraphs
                   (text·paragraphs는 **정제 후** 기준 — 화면에 보이는 본문)
      sentences  — [{sent_id, text, start, end, para, para_start, para_end}]
                   start/end는 정제 본문 전체 기준, para_start/para_end는 그 문단 안 기준
                   (프런트가 문단을 렌더링하고 그 안에서 Claim을 하이라이트하기 위함)
      removed    — 정제로 제거된 스팬(감사용 — 무엇을 왜 지웠는지)
      versions   — 재현 추적용 파이프라인 버전 3튜플
    """
    raw = crawl_one(url, timeout=timeout)
    art = standardize_article(raw)               # 검증·표준화(article_id·날짜 정규화)

    # 재크롤 산출이므로 구조화 모드 — 명시 노이즈만 제거(88차)
    clean, spans = clean_text(art["text"], art["title"], structured=True)
    paragraphs = [p for p in clean.split("\n") if p.strip()]

    # 문단 시작 오프셋(정제 본문 기준) — 문단 상대 좌표 계산용
    para_starts, pos = [], 0
    for p in paragraphs:
        pos = clean.index(p, pos)
        para_starts.append(pos)
        pos += len(p)

    sentences = []
    for i, (s, e) in enumerate(split_spans(clean), start=1):
        pidx = max((k for k, st in enumerate(para_starts) if st <= s), default=0)
        base = para_starts[pidx] if para_starts else 0
        sentences.append({
            "sent_id": f"s{i:03d}", "text": clean[s:e],
            "start": s, "end": e,
            "para": pidx + 1 if paragraphs else None,
            "para_start": s - base, "para_end": e - base,
        })

    article = {k: art[k] for k in ("article_id", "title", "posted_date", "url") if k in art}
    for opt in ("publisher", "subtitle"):
        if raw.get(opt):
            article[opt] = raw[opt]
    article["text"] = clean
    article["paragraphs"] = paragraphs
    return {
        "article": article,
        "sentences": sentences,
        "removed": spans,
        "versions": {"crawl": "p_crawl_v1", "clean": CLEAN_VERSION, "split": SPLIT_VERSION},
    }


if __name__ == "__main__":
    import argparse, json, sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="URL 하나를 서비스 형식으로 수집")
    ap.add_argument("url")
    ap.add_argument("--json", action="store_true", help="전체 JSON 출력")
    a = ap.parse_args()
    doc = ingest_url(a.url)
    if a.json:
        print(json.dumps(doc, ensure_ascii=False, indent=1))
    else:
        art = doc["article"]
        print(f"{art['article_id']} | {art.get('publisher', '?')} | {art['posted_date']}")
        print(f"{art['title']}")
        print(f"문단 {len(art['paragraphs'])} · 문장 {len(doc['sentences'])} · 제거 {len(doc['removed'])}")
        for s in doc["sentences"][:3]:
            print(f"  [{s['sent_id']} p{s['para']} {s['start']}:{s['end']}] {s['text'][:60]}")
