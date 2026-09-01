"""[2026-08-22 신규 - Task #29 Step 2 신뢰도 측정] hcx_stage3_resolver.py의
mode(single/period_change/item_diff) 판단이 실제로 얼마나 정확한지 재려면
실측된(정답을 아는) claim이 필요한데, 이번 세션에 정답이 확정된 실제 claim은
몇 건 안 된다(C018, C003/C004 재구성 정도) - 사용자가 "data augmentation으로
늘려도 괜찮다"고 확인해줘서, 여기서 ~90건의 합성 claim + 정답 라벨을
생성한다.

## 이게 "실측 우선 원칙" 위반이 아닌 이유

이 원칙은 "KOSIS API 응답 스키마/필드명을 실측 없이 추측해서 만들지 않는다"는
것이다. 이 스크립트는 KOSIS 스키마를 전혀 다루지 않는다 - 순수하게 "이
문장을 사람이 읽으면 어떤 비교 모드인가"라는 자연어 이해 문제의 정답 라벨을
**내가 직접 문장을 쓰면서 동시에 정의**하는 것뿐이다.

## [2026-08-22 갱신 - 실측 실행 후 발견된 평가셋 자체의 버그 수정]

1차 실행(README "스물다섯 번째"/"스물여덟 번째" 항목) 결과를 분석하다가
평가셋 생성 로직 자체의 결함 두 가지를 발견해 고쳤다 - HCX의 실패가 아니라
내 테스트 문장이 애초에 비문이거나 의미가 안 통했던 사례들이었다:

1. **조사(은/는, 이/가) 하드코딩 버그**: 한국어 조사는 앞 글자 받침
   유무로 결정되는데("전국은"/"경기는"), 템플릿에 "은"/"는"/"이"를
   고정 문자열로 박아놔서 "전국는"(비문) 같은 문장이 그대로 생성됐다
   (item_diff-084 실패 원인으로 의심됨). `_has_batchim`/`_josa`로
   유니코드 한글 분해(종성 유무)를 직접 계산해서 항상 문법에 맞는 조사를
   고르도록 고쳤다.
2. **단위-도메인 불일치 버그**: unit 결정 로직이 "인구"/"혼인 건수" 같은
   개수형 지표에 "수"/"건수" 부분 문자열 매칭이 안 걸려 "%"로 잘못
   배정됐다("경기 인구이 26.2%로 집계됐다" - 의미 자체가 성립 안 됨,
   README "스물다섯 번째" 항목에서 이미 지적됨). DOMAINS에 매 항목마다
   measure_kind("rate"|"count")를 명시적으로 붙여 이 추론 자체를 없앴다.
   덤으로 "경기"(지역명 vs "경기"=경제상황의 동음이의어 - item_diff-075/084
   같은 빈 응답 실패의 원인일 가능성)를 "경기도"로 바꿔 모호성을 줄였다.

## 구성 (총 90건 목표)

- single(25건): 비교/파생 없이 그 시점 값 자체만 필요.
- period_change_explicit(25건): "OOOO년 O월 대비"/"N년 전보다"처럼
  기준시점이 문장에 명시된 경우.
- period_change_yoy(20건): "전년동월비"/"전년대비"/"전년동분기비"처럼
  1년 전이 암묵적으로 고정된 경우.
- item_diff(15건): "이 항목의 등락률"과 "전체/총지수의 등락률" 차이를
  말하는 경우(C003/C004류).
- ambiguous(5건): 정답이 사실상 None(확신 없음)이어야 하는 경우.

각 항목은 {"id", "claim_text", "target_period", "expected_mode",
"expected_reference_period"} 형식으로 stage3_eval_set.jsonl에 한 줄씩
저장된다. 완전히 결정적(랜덤 시드 없음) - 네트워크/API 불필요.

사용법: python generate_stage3_eval_set.py (이 폴더에서 실행,
stage3_eval_set.jsonl 생성)
"""

import json

# [2026-08-22 갱신] (item, metric, measure_kind) 3튜플 - measure_kind는
# "rate"(%로 표현하는 게 자연스러운 지표 - 물가지수/실업률/고용률 등) 또는
# "count"(개수/금액형 - 취업자 수/인구/건수 등, % 단위를 붙이면 의미가
# 안 통함). 부분 문자열 추론(예전 버그의 원인)이 아니라 도메인마다 직접
# 명시한다.
DOMAINS = [
    ("건설업", "취업자 수", "count"), ("제조업", "생산자물가지수", "rate"),
    ("서울", "아파트 매매가격지수", "rate"), ("반도체", "수출액", "count"),
    ("자동차", "생산량", "count"), ("전체 산업", "생산지수", "rate"),
    ("음식료품", "소비자물가지수", "rate"), ("의류 및 신발", "소비자물가지수", "rate"),
    ("교통", "소비자물가지수", "rate"), ("보건", "소비자물가지수", "rate"),
    ("교육", "소비자물가지수", "rate"), ("농림어업", "취업자 수", "count"),
    ("도소매업", "취업자 수", "count"), ("숙박 및 음식점업", "취업자 수", "count"),
    ("정보통신업", "취업자 수", "count"), ("금융 및 보험업", "취업자 수", "count"),
    # [2026-08-22 갱신] "경기"(지역명) -> "경기도"로 변경 - "경기"(economy,
    # 경제상황)와의 동음이의어 모호성이 일부 빈 응답 실패(item_diff-075/084)
    # 의 원인일 가능성이 있어 제거.
    ("경기도", "인구", "count"), ("부산", "인구", "count"),
    ("대구", "인구", "count"), ("광주", "인구", "count"),
    ("전국", "실업률", "rate"), ("청년층", "고용률", "rate"),
    ("여성", "경제활동참가율", "rate"), ("수도권", "출생아 수", "count"),
    ("전국", "혼인 건수", "count"), ("전국", "사망자 수", "count"),
    ("석유류", "소비자물가지수", "rate"), ("전기·가스·수도", "소비자물가지수", "rate"),
    ("가정용품 및 가사서비스", "소비자물가지수", "rate"), ("음식 서비스", "소비자물가지수", "rate"),
]

MONTHS_KR = ["1월", "2월", "3월", "4월", "5월", "6월", "7월", "8월", "9월", "10월", "11월", "12월"]


def _has_batchim(word: str) -> bool:
    """word의 마지막 글자가 받침(종성)이 있는지 확인한다 - 한글 유니코드는
    코드 = 0xAC00 + (초성*21+중성)*28+종성으로 조합되므로, (코드-0xAC00)%28
    이 0이면 받침 없음, 아니면 있음이다. 한글 완성형 범위(0xAC00~0xD7A3)
    밖 문자(숫자/영문 등)로 끝나면 안전하게 받침 없음으로 취급한다(이
    프로젝트 claim 텍스트엔 실제로 그런 경우가 없음 - 추측 최소화)."""
    if not word:
        return False
    code = ord(word[-1])
    if 0xAC00 <= code <= 0xD7A3:
        return (code - 0xAC00) % 28 != 0
    return False


def _josa(word: str, with_batchim: str, without_batchim: str) -> str:
    """word 끝 글자의 받침 유무에 따라 조사(은/는, 이/가 등) 중 맞는 쪽을
    고른다 - [2026-08-22 신규] 하드코딩된 조사가 "전국는" 같은 비문을
    만들던 버그(item_diff-084)의 수정."""
    return with_batchim if _has_batchim(word) else without_batchim


def _target_period(idx: int) -> str:
    """2023~2026년, 1~12월 사이를 순환하며 결정적으로 target_period를 만든다."""
    year = 2023 + (idx % 4)
    month = 1 + (idx % 12)
    return f"{year}{month:02d}"


def _yoy_reference(target_period: str) -> str:
    year = int(target_period[:4]) - 1
    return f"{year}{target_period[4:]}"


def _explicit_reference_and_phrase(idx: int, target_period: str):
    """"N년 전"/"YYYY년 M월 대비" 두 가지 명시적 기준시점 문구 패턴을
    번갈아 만든다 - reference_period는 target_period에서 직접 계산한다."""
    year = int(target_period[:4])
    month = int(target_period[4:])
    if idx % 2 == 0:
        n_years = 1 + (idx % 5)  # 1~5년 전
        ref_year = year - n_years
        ref_period = f"{ref_year}{month:02d}"
        phrase = f"{n_years}년 전보다"
    else:
        ref_year = year - (5 + (idx % 6))
        ref_month = 1 + ((idx * 3) % 12)
        ref_period = f"{ref_year}{ref_month:02d}"
        phrase = f"{ref_year}년 {ref_month}월에 비해"
    return ref_period, phrase


def build_eval_set():
    rows = []
    rid = 0

    # ---- single(25건) ----
    single_templates = [
        "{item} {metric}{topic_josa} {period_kr} {value}{unit}였다.",
        "{item} {metric}{subj_josa} {value}{unit}로 집계됐다.",
        "{item} {metric}{topic_josa} {value}{unit}다.",
        "{item} {metric}{subj_josa} {value}{unit}에 그쳤다.",
        "{item} {metric}{topic_josa} {period_kr} 기준 {value}{unit}로 나타났다.",
    ]
    for i in range(25):
        item, metric, kind = DOMAINS[i % len(DOMAINS)]
        target = _target_period(i)
        year, month = target[:4], int(target[4:])
        period_kr = f"{year}년 {month}월"
        value = round(10 + (i % 40) + 0.1 * (i % 7), 1)
        unit = "%" if kind == "rate" else "만 명"
        template = single_templates[i % len(single_templates)]
        claim_text = template.format(
            item=item, metric=metric, period_kr=period_kr, value=value, unit=unit,
            topic_josa=_josa(metric, "은", "는"), subj_josa=_josa(metric, "이", "가"),
        )
        rows.append({
            "id": f"single-{rid:03d}", "claim_text": claim_text, "target_period": target,
            "expected_mode": "single", "expected_reference_period": None,
        })
        rid += 1

    # ---- period_change_explicit(25건) ----
    explicit_templates = [
        "{item} {metric}{topic_josa} {ref_phrase} {value}{unit} 올랐다.",
        "{item} {metric}{topic_josa} {ref_phrase} {value}{unit} 상승했다.",
        "{item} {metric}{topic_josa} {ref_phrase} {value}{unit} 감소했다.",
        "{item} {metric}{topic_josa} {ref_phrase} {value}{unit} 줄었다.",
        "{item} {metric}{topic_josa} {ref_phrase} {value}{unit} 높아졌다.",
    ]
    for i in range(25):
        item, metric, kind = DOMAINS[i % len(DOMAINS)]
        target = _target_period(i + 3)
        ref_period, ref_phrase = _explicit_reference_and_phrase(i, target)
        value = round(1 + (i % 30) + 0.1 * (i % 9), 1)
        template = explicit_templates[i % len(explicit_templates)]
        claim_text = template.format(
            item=item, metric=metric, ref_phrase=ref_phrase, value=value, unit="%",
            topic_josa=_josa(metric, "은", "는"),
        )
        rows.append({
            "id": f"period_explicit-{rid:03d}", "claim_text": claim_text, "target_period": target,
            "expected_mode": "period_change", "expected_reference_period": ref_period,
        })
        rid += 1

    # ---- period_change_yoy(20건) ----
    yoy_phrases = ["전년동월비", "전년 대비", "지난해 같은 달보다", "전년동분기비", "1년 전보다"]
    yoy_templates = [
        "{item} {metric}{topic_josa} {ref_phrase} {value}{unit} 상승했다.",
        "{item} {metric}{subj_josa} {ref_phrase} {value}{unit} 올랐다.",
        "{item} {metric}{topic_josa} {ref_phrase} {value}{unit} 하락했다.",
        "{item} {metric}{subj_josa} {ref_phrase} {value}{unit} 낮아졌다.",
    ]
    for i in range(20):
        item, metric, kind = DOMAINS[(i + 5) % len(DOMAINS)]
        target = _target_period(i + 7)
        ref_period = _yoy_reference(target)
        ref_phrase = yoy_phrases[i % len(yoy_phrases)]
        value = round(0.5 + (i % 15) + 0.1 * (i % 5), 1)
        template = yoy_templates[i % len(yoy_templates)]
        claim_text = template.format(
            item=item, metric=metric, ref_phrase=ref_phrase, value=value, unit="%",
            topic_josa=_josa(metric, "은", "는"), subj_josa=_josa(metric, "이", "가"),
        )
        rows.append({
            "id": f"period_yoy-{rid:03d}", "claim_text": claim_text, "target_period": target,
            "expected_mode": "period_change", "expected_reference_period": ref_period,
        })
        rid += 1

    # ---- item_diff(15건) ----
    # [2026-08-22 실측 발견 - 사용자 지적] 예전 템플릿 3은 {item}만 쓰고
    # {metric}을 아예 안 썼다("여성은... 올라", "전국은... 올라" - 뭐가
    # 오른다는 건지 목적어가 없는 비문 아닌 이상한 문장). "총지수"도
    # 물가지수류 표에서만 자연스러운 용어라 "여성"/"전국" 같은 인구·지역
    # 도메인엔 안 어울렸다 - 세 템플릿 다 반드시 {item}+{metric}을 함께
    # 쓰고, 비교 대상도 "총지수"(도메인 특정 용어) 대신 "전체 {metric}"/
    # "전체 평균"(도메인 무관하게 항상 자연스러움)으로 통일한다.
    item_diff_templates = [
        "{item} {metric}{metric_josa} {ref_phrase} {value_a}{unit} 올랐다. 같은 기간 전체 {metric}({value_b}{unit})보다 {diff}{unit}포인트 높은 수치다.",
        "{item} {metric} 상승률이 {ref_phrase} {value_a}{unit}로, 전체 평균({value_b}{unit})을 {diff}{unit}포인트 웃돌았다.",
        "{item} {metric}{metric_josa} {ref_phrase} {value_a}{unit} 올라, 전체 {metric} 상승률({value_b}{unit})보다 {diff}{unit}포인트 앞섰다.",
    ]
    for i in range(15):
        item, metric, kind = DOMAINS[(i + 11) % len(DOMAINS)]
        target = _target_period(i + 2)
        ref_period, ref_phrase = _explicit_reference_and_phrase(i + 1, target)
        value_b = round(3 + (i % 10), 1)
        diff = round(1 + (i % 8) + 0.1 * (i % 3), 1)
        value_a = round(value_b + diff, 1)
        template = item_diff_templates[i % len(item_diff_templates)]
        claim_text = template.format(
            item=item, metric=metric, ref_phrase=ref_phrase,
            value_a=value_a, value_b=value_b, diff=diff, unit="%",
            metric_josa=_josa(metric, "은", "는"),
        )
        rows.append({
            "id": f"item_diff-{rid:03d}", "claim_text": claim_text, "target_period": target,
            "expected_mode": "item_diff", "expected_reference_period": ref_period,
        })
        rid += 1

    # ---- ambiguous(5건) - 정답은 사실상 None(확신 없음)이어야 함 ----
    ambiguous_claims = [
        "건설업 취업자 수는 최근 다소 늘어난 것으로 보인다.",
        "소비자물가는 여러 요인으로 등락을 반복하고 있다.",
        "전국 인구는 앞으로도 비슷한 흐름을 이어갈 전망이다.",
        "수출액은 업계 상황에 따라 변동성이 큰 편이다.",
        "실업률은 계절에 따라 다르게 나타나는 경향이 있다.",
    ]
    for i, claim_text in enumerate(ambiguous_claims):
        target = _target_period(i + 1)
        rows.append({
            "id": f"ambiguous-{rid:03d}", "claim_text": claim_text, "target_period": target,
            "expected_mode": None, "expected_reference_period": None,
        })
        rid += 1

    return rows


def main():
    rows = build_eval_set()
    out_path = "stage3_eval_set.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    counts = {}
    for row in rows:
        key = row["expected_mode"] or "ambiguous(None 기대)"
        counts[key] = counts.get(key, 0) + 1
    print(f"[생성 완료] 총 {len(rows)}건 -> {out_path}")
    for k, v in counts.items():
        print(f"  - {k}: {v}건")

    # [2026-08-22 신규] 조사 버그 회귀 확인용 - 생성된 문장 중 눈으로 바로
    # 비문을 잡아낼 수 있게 item_diff 15건 전체를 출력한다(가장 최근에
    # 실패가 몰렸던 카테고리).
    print("\n[item_diff 15건 미리보기 - 비문 없는지 눈으로 확인]")
    for row in rows:
        if row["id"].startswith("item_diff"):
            print(f"  {row['id']}: {row['claim_text']}")


if __name__ == "__main__":
    main()
