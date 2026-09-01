# -*- coding: utf-8 -*-
"""P1 정제: articles.jsonl → articles_clean.jsonl (룰 기반 노이즈 제거 + removed_spans 기록).

규칙은 골든셋(cleaned_articles_ex.xlsx) 역산 관찰로 도출했다 (52건 중 50건이 선두/말미 노이즈만).
- 선두: '입력 <ts> [업데이트 <ts>] [댓글수]' 앵커 절단 (짧은 헤더·긴 포털 내비 공통)
        + 뉴스레터형('N호 YYYY.MM.DD HH:MM') + 타임스탬프 없는 기사용 섹션+제목 폴백
- 말미: UI 전용 앵커(가장 이른 위치)부터 끝까지 절단
- v2: 말미 잔여 꼬리(해시 없는 태그 키워드·기자명) 휴리스틱
- v4: 본문 중간은 건드리지 않는다 — [칼럼 전문 링크]·<사진>·[편집자주] 같은
      기사 고유 마커는 특정 기사의 특수 사례라 규칙화하지 않고 보존 (후속 단계에서 처리)

원칙: 패턴이 없으면 그대로 통과(과잉 삭제 금지) · 제거는 삭제가 아니라 removed_spans 기록
      · 정제본+제거분으로 원문 복원 가능해야 함(보존 인바리언트).

산출은 2파일로 분리한다 (실사용 파일의 용량 절감):
  - articles_clean.jsonl        코어 5필드만 (article_id·title·posted_date·url·text) — P2·다운스트림 입력
  - articles_clean_trace.jsonl  감사 사이드카 (article_id·pipeline_version·removed_spans)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PIPELINE_VERSION = "clean_v5"   # 88차: 구조화 입력(재크롤)은 명시 노이즈 패턴만 적용

# ── 선두(프리픽스) 규칙 ──────────────────────────────────────────
# R1: '입력 2025.06.23. 09:03 업데이트 2025.06.23. 17:40 0 ' — 업데이트·댓글수는 옵션
_TS = r"\d{4}\.\s?\d{1,2}\.\s?\d{1,2}\.?\s*\d{1,2}:\d{2}"
RE_HEADER = re.compile(
    rf"입력\s*{_TS}(?:\s*업데이트\s*{_TS})?(?:\s+\d{{1,4}}(?=\s))?\s*"
)
# R2: 뉴스레터형 '604호 2025.09.15 11:00 '
RE_NEWSLETTER = re.compile(rf"\d{{1,4}}호\s+{_TS}\s*")
PREFIX_SEARCH_LIMIT = 1200  # 앵커가 이 위치 안에서 시작해야 헤더로 인정 (긴 포털 내비 ~850자)

# ── 말미(서픽스) 앵커 — 전부 기사 본문에 나올 수 없는 UI 전용 문구 ──
SUFFIX_ANCHORS = [
    ("english_button", re.compile(r"English\s*기사보기")),
    ("hashtag_block", re.compile(r"#(?!\d+(?:\s|$))[^\s#]{2,}")),
    ("reporter_profile", re.compile(r"[가-힣]{2,4}\s*기자(?:\(조선비즈\))?\s+\d{4}년\s*조선일보에\s*입사")),
    ("staff_writer", re.compile(r"[가-힣]{2,4}\s*기자\s+staff\s+writer")),
    ("comment_widget", re.compile(r"(?:코스피\s+코스닥\s+증권\s+)?100자평\s+도움말\s+삭제기준")),
    ("video_player", re.compile(r"Video\s+Player\s+is\s+loading")),
    ("video_time", re.compile(r"\d{1,2}:\d{2}\s*/\s*(?:Duration\s*)?\d{1,2}:\d{2}")),
    ("ads_close", re.compile(r"close\s+Advertisements")),
    ("hot_news", re.compile(r"오늘의\s*핫뉴스")),
    ("most_viewed", re.compile(r"많이\s*본\s*뉴스")),
    ("recommend", re.compile(r"당신이\s*좋아할\s*만한\s*콘텐츠")),
    ("taboola", re.compile(r"By\s+Taboola")),
    ("newsletter_promo", re.compile(r"매일\s*조선일보에\s*실린\s*칼럼")),
]

# v2 — 말미 잔여 꼬리(태그 키워드·기자명): 마지막 문장 종결 이후의 짧은 무종결 꼬리
RE_SENT_END = re.compile(r"[.!?…”\"』」)]\s")
RE_TRAILING_REPORTER = re.compile(r"(?:[가-힣]{1,6}=)?[가-힣]{2,4}\s*기자\s*$")
TAIL_MAX_LEN = 40

# ── 구조화 입력(재크롤)용 정밀 꼬리 패턴 (clean_v5 — 88차) ──────────────
# 크롤러는 본문 타입 화이트리스트로 파싱하므로 UI 노이즈가 **들어올 수 없다**.
# 그 결과 길이 기반 휴리스틱(TAIL_MAX_LEN)은 참 양성이 없어지고 오발화만 남는다 —
# 전량 실측(2,696건)에서 인사 기사의 '▲회사▷직위 이름' 목록, 날씨 기사의 '서울·광주
# 낮 최고 36도', 사진 캡션 본문이 잘려나갔다. 구조화 입력에는 **명시 노이즈 패턴만**
# 적용한다(기자 이메일·영상 링크·취재팀 바이라인·타 매체 유도 문구).
# 꼬리가 '노이즈임을 스스로 드러내는' 표지 — 이메일·URL·바이라인·타 매체 유도.
# 실측 참 양성(기자 이메일 14·영상 링크 14·취재팀 바이라인 3·주간조선 유도 1)은 전부
# 이 중 하나를 포함하고, 오발화(인사 ▲▷ 목록·날씨 요약·가격 관측 문장)는 하나도 없다.
RE_TAIL_NOISE_MARK = re.compile(r"@|https?://|기자|취재팀|더\s*많은\s*기사는")


def _collapse(s: str) -> tuple[str, list[int]]:
    """공백 전부 제거 + 원본 인덱스 매핑 (공백 표기 차이를 무시한 비교용)"""
    out, m = [], []
    for i, ch in enumerate(s):
        if not ch.isspace():
            out.append(ch)
            m.append(i)
    return "".join(out), m


def find_prefix_end(text: str, title: str) -> tuple[int, str] | None:
    """선두 노이즈의 끝 위치. (끝 오프셋, 규칙명) 또는 None(없으면 통과)."""
    m = RE_HEADER.search(text)
    if m and m.start() < PREFIX_SEARCH_LIMIT:
        return m.end(), "prefix_header"
    m = RE_NEWSLETTER.search(text)
    if m and m.start() < PREFIX_SEARCH_LIMIT:
        return m.end(), "prefix_newsletter"
    # 폴백: 본문이 (짧은 섹션명 +) 제목 반복으로 시작하면 제목 끝까지 절단.
    # 다중 문장형 제목("헤드라인… 부제")은 첫 세그먼트('…'까지)만 헤드라인 반복으로 본다
    # — 부제는 본문 리드로 남는 경우가 있음 (골든셋 관찰).
    seg = title
    for delim in ("…", "..."):
        if delim in title:
            seg = title.split(delim, 1)[0] + delim
            break
    t_c, _ = _collapse(seg)
    if t_c:
        x_c, x_map = _collapse(text)
        pos = x_c.find(t_c)
        if 0 <= pos <= 20:  # 섹션명 정도만 앞에 허용
            end_c = pos + len(t_c) - 1
            end = x_map[end_c] + 1
            # 제목 뒤 공백까지 포함
            while end < len(text) and text[end].isspace():
                end += 1
            return end, "prefix_title_repeat"
    return None


def find_suffix_start(text: str, from_pos: int) -> tuple[int, str] | None:
    """말미 노이즈의 시작 위치. 앵커 중 가장 이른 매치. 없으면 None."""
    best = None
    for name, pat in SUFFIX_ANCHORS:
        m = pat.search(text, from_pos)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), name)
    return best


def find_tail_residue(text: str, start: int, end: int,
                      require_marker: bool = False) -> tuple[int, str] | None:
    """v2: [start,end) 구간 끝의 잔여 꼬리(태그 키워드·기자명) 시작 위치.

    require_marker=True (구조화 입력 — 88차): 짧은 무종결 꼬리라는 것만으로는 자르지 않고
    **노이즈 표지(RE_TAIL_NOISE_MARK)를 포함할 때만** 자른다. 재크롤 본문에는 UI 노이즈가
    없어서 길이 조건만 남으면 인사 기사의 '▲회사▷직위' 목록, 날씨 기사의 '서울·광주 낮
    최고 36도' 같은 **진짜 본문**이 잘려나간다(전량 실측).
    """
    seg = text[start:end]
    if not seg.strip():
        return None
    # 마지막 문장 종결 위치
    last = None
    for m in RE_SENT_END.finditer(seg):
        last = m.end()
    if last is None:
        # 종결 없음 — 기자명 단독 꼬리만 검사
        m = RE_TRAILING_REPORTER.search(seg)
        if m and m.start() > 0:
            return start + m.start(), "tail_reporter"
        return None
    tail = seg[last:]
    if not tail.strip():
        return None
    if (len(tail.strip()) <= TAIL_MAX_LEN and not RE_SENT_END.search(tail + " ")
            and (not require_marker or RE_TAIL_NOISE_MARK.search(tail))):
        return start + last, "tail_keywords"
    m = RE_TRAILING_REPORTER.search(seg)
    if m and m.start() >= last:
        return start + m.start(), "tail_reporter"
    return None


def clean_text(text: str, title: str, structured: bool = False) -> tuple[str, list[dict]]:
    """본문에서 노이즈를 제거하고 (정제본, removed_spans)를 반환.

    removed_spans: [{start, end, rule, text}] — 원본 오프셋 기준, 겹침 없음, 정렬됨.
    보존 인바리언트: 정제본 + 제거분을 오프셋 순서로 이으면 원문과 동일.

    structured=True (재크롤 산출 — 88차): 화이트리스트 파싱이라 UI 노이즈가 구조적으로
    들어올 수 없으므로 **명시 노이즈 패턴만** 적용한다. 길이 휴리스틱(tail_keywords)·
    제목 반복(prefix_title_repeat)·해시태그 앵커는 참 양성이 사라지고 오발화만 남아
    본문을 파괴한다(전량 실측: 오발화 9/47건, 최악은 제품명 '영웅문 S#'로 본문 과반 삭제).
    """
    spans: list[dict] = []
    body_start = 0
    body_end = len(text)

    if structured:
        # 선두 헤더·UI 서픽스 앵커는 **구조적으로 존재할 수 없다**(화이트리스트 파싱) —
        # 적용하면 오발화만 남는다(제목 반복=사진 캡션 본문, hashtag=제품명 '영웅문 S#').
        # 말미 꼬리만, 그것도 노이즈 표지가 있을 때만 자른다.
        t = find_tail_residue(text, 0, len(text), require_marker=True)
        if t:
            spans.append({"start": t[0], "end": len(text), "rule": t[1]})
    else:
        p = find_prefix_end(text, title)
        if p:
            body_start = p[0]
            spans.append({"start": 0, "end": p[0], "rule": p[1]})

        # 앵커가 본문 시작과 정확히 겹치면 본문이 통째로 UI 블록인 기사([속보] 등) — 빈 본문 허용
        s = find_suffix_start(text, body_start)
        if s and s[0] >= body_start:
            body_end = s[0]
            spans.append({"start": s[0], "end": len(text), "rule": s[1]})

        # v2: 말미 잔여 꼬리 (서픽스 절단 후 남은 본문 구간의 끝)
        t = find_tail_residue(text, body_start, body_end)
        if t and all(not (sp["start"] <= t[0] < sp["end"]) for sp in spans):
            spans.append({"start": t[0], "end": body_end, "rule": t[1]})

    spans.sort(key=lambda x: x["start"])
    # 겹침 방지(안전망): 앞 스팬과 겹치면 뒤 스팬을 버림
    dedup: list[dict] = []
    for sp in spans:
        if dedup and sp["start"] < dedup[-1]["end"]:
            continue
        dedup.append(sp)
    spans = dedup

    kept: list[str] = []
    cursor = 0
    for sp in spans:
        kept.append(text[cursor:sp["start"]])
        sp["text"] = text[sp["start"]:sp["end"]]
        cursor = sp["end"]
    kept.append(text[cursor:])
    clean = "".join(kept)

    # 보존 인바리언트: 정제본+제거분 = 원문
    rebuilt, cursor, ki = [], 0, 0
    for sp in spans:
        rebuilt.append(kept[ki]); ki += 1
        rebuilt.append(sp["text"])
    rebuilt.append(kept[ki])
    assert "".join(rebuilt) == text, "보존 인바리언트 위반: 정제본+제거분 ≠ 원문"

    return clean.strip(), spans


def main(argv=None) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="P1 정제: articles.jsonl → articles_clean.jsonl (+trace 사이드카)")
    ap.add_argument("--input", type=Path, default=Path("data/articles.jsonl"))
    ap.add_argument("--output", type=Path, default=Path("data/articles_clean.jsonl"))
    ap.add_argument("--trace", type=Path, default=Path("data/articles_clean_trace.jsonl"))
    args = ap.parse_args(argv)

    n = {"prefix": 0, "suffix": 0, "tail": 0, "untouched": 0}
    out_lines = []
    trace_lines = []
    with open(args.input, encoding="utf-8") as f:
        for line in f:
            a = json.loads(line)
            # 88차: 재크롤 산출(paragraphs 보유)은 구조화 입력 — 명시 노이즈만 제거
            clean, spans = clean_text(a["text"], a["title"],
                                      structured="paragraphs" in a)
            rules = {sp["rule"] for sp in spans}
            if any(r.startswith("prefix") for r in rules):
                n["prefix"] += 1
            if any(r in ("english_button", "hashtag_block", "reporter_profile", "staff_writer",
                         "comment_widget", "video_player", "video_time", "ads_close", "hot_news",
                         "most_viewed", "recommend", "taboola", "newsletter_promo") for r in rules):
                n["suffix"] += 1
            if any(r.startswith("tail") for r in rules):
                n["tail"] += 1
            if not spans:
                n["untouched"] += 1
            row_out = {
                "article_id": a["article_id"],
                "title": a["title"],
                "posted_date": a["posted_date"],
                "url": a["url"],
                "text": clean,
            }
            # 84차: 재크롤 구조 필드 통과. paragraphs는 원본 리스트를 옮기지 않고
            # **정제된 text에서 재파생**한다("\n"이 문단 경계 정본) — 스팬 제거로
            # 리스트와 text가 어긋나는 desync를 원천 차단. 구 데이터(개행 없음)는 미기재
            if "paragraphs" in a:
                row_out["paragraphs"] = [p for p in clean.split("\n") if p.strip()]
            for opt in ("publisher", "subtitle"):
                if a.get(opt):
                    row_out[opt] = a[opt]
            out_lines.append(json.dumps(row_out, ensure_ascii=False))
            trace_lines.append(json.dumps({
                "article_id": a["article_id"],
                "pipeline_version": PIPELINE_VERSION,
                "removed_spans": spans,
            }, ensure_ascii=False))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    args.trace.parent.mkdir(parents=True, exist_ok=True)
    args.trace.write_text("\n".join(trace_lines) + "\n", encoding="utf-8")
    print(f"{PIPELINE_VERSION}: {len(out_lines)}건 정제 — 선두 {n['prefix']} · 말미 {n['suffix']} · "
          f"꼬리 {n['tail']} · 무변경 {n['untouched']}")
    print(f"본문(코어 5필드): {args.output}")
    print(f"감사 사이드카(removed_spans): {args.trace}")


if __name__ == "__main__":
    main()
