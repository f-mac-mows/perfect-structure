"""[2026-08-22 신규 - Task #29 Step 2 신뢰도 측정] generate_stage3_eval_set.py
로 만든 stage3_eval_set.jsonl(합성 90건, 정답 라벨 포함)에 대해 실제
HCX-007을 호출해 hcx_stage3_resolver.resolve_comparison_mode_with_hcx007의
mode/reference_period 판단이 얼마나 정확한지 잰다.

CLAUDE.md "샌드박스에서 직접 실행 금지" 규칙에 따라 이 세션에서 직접
실행하지 않는다 - 사용자가 실제 네트워크 + NCP_CLOVASTUDIO_API_KEY가 설정된
로컬 환경에서 직접 실행해야 한다.

## 비용 안내

90건 × (1콜, 재시도 시 최대 2콜) = 최대 180콜. HCX-007 thinking_effort="low"
로 호출하므로(hcx_stage3_resolver.py 기본값) 콜당 비용/시간은 크지 않을
것으로 예상되지만, 실제 비용은 이 스크립트가 api_usage_logger로 그대로
기록한다(다른 probe들과 동일).

## 무엇을 측정하는가

각 claim에 대해:
1. mode가 정답과 정확히 일치하는가(ambiguous 건은 expected_mode=None이므로
   "HCX도 None을 반환했는가"로 확인 - 무리하게 추측하지 않는지가 핵심).
2. mode가 일치하는 period_change/item_diff 건에 한해, reference_period
   숫자까지 정확히 일치하는가(YoY 자동 계산, 명시적 기준시점 파싱 둘 다).

집계: 전체 정확도, 카테고리(single/period_change_explicit/period_change_yoy/
item_diff/ambiguous)별 정확도, 틀린 케이스 전체 목록(claim_text/기대값/
실제값 나란히) - 어떤 문형에서 특히 약한지 눈으로 바로 보이게.

사용법: python generate_stage3_eval_set.py 먼저 실행해 stage3_eval_set.jsonl을
만든 뒤, python probe_hcx_stage3_reliability.py (.env에 NCP_CLOVASTUDIO_API_KEY
설정된 이 폴더에서)

[2026-08-22 신규 - temperature=0.0 재검증용] 카테고리 접두어를 인자로 주면
그 카테고리만 돌린다(비용 절감) - 예: `python probe_hcx_stage3_reliability.py
item_diff`는 item_diff 15건만 돈다. 인자 없으면 기존처럼 90건 전부.
hcx_stage3_resolver.py가 이미 temperature=0.0을 고정으로 쓰도록 바뀌었으므로
(README "스물여덟 번째" 항목) 이 스크립트 자체는 그대로 두고 재실행만 하면
temperature=0.0 기준 정확도가 나온다."""

import json
import sys

from hcx_stage3_resolver import Stage3ParseError, resolve_comparison_mode_with_hcx007
from hcx_client import HCXClientError

EVAL_SET_PATH = "stage3_eval_set.jsonl"
RESULT_PATH = "stage3_eval_result.json"


def _category(row):
    """id 접두어로 원래 5개 카테고리(single/period_explicit/period_yoy/
    item_diff/ambiguous) 중 어디였는지 되돌린다 - 집계용."""
    return row["id"].rsplit("-", 1)[0]


def main():
    try:
        with open(EVAL_SET_PATH, "r", encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        print(f"[중단] {EVAL_SET_PATH}가 없습니다 - 먼저 python generate_stage3_eval_set.py를 실행하세요.")
        sys.exit(1)

    category_filter = sys.argv[1] if len(sys.argv) > 1 else None
    if category_filter:
        rows = [r for r in rows if _category(r) == category_filter]
        if not rows:
            print(f"[중단] 카테고리 '{category_filter}'에 해당하는 건이 없습니다.")
            sys.exit(1)

    print(f"[시작] {len(rows)}건 평가" + (f"(카테고리={category_filter})" if category_filter else "") + " - HCX-007 실 API 호출 (시간이 걸릴 수 있습니다)")

    results = []
    for i, row in enumerate(rows):
        claim_text = row["claim_text"]
        target_period = row["target_period"]
        expected_mode = row["expected_mode"]
        expected_ref = row["expected_reference_period"]

        error = None
        actual = None
        try:
            actual = resolve_comparison_mode_with_hcx007(claim_text, target_period)
        except (Stage3ParseError, HCXClientError) as e:
            error = str(e)

        actual_mode = actual["mode"] if actual else None
        actual_ref = actual["reference_period"] if actual else None

        mode_correct = actual_mode == expected_mode
        # reference_period는 mode가 애초에 맞았을 때만 의미 있게 비교한다.
        ref_correct = (actual_ref == expected_ref) if mode_correct else None

        results.append({
            "id": row["id"], "category": _category(row), "claim_text": claim_text,
            "target_period": target_period,
            "expected_mode": expected_mode, "expected_reference_period": expected_ref,
            "actual_mode": actual_mode, "actual_reference_period": actual_ref,
            "mode_correct": mode_correct, "reference_period_correct": ref_correct,
            "error": error,
        })
        status = "OK" if mode_correct and (ref_correct is not False) else "MISS"
        print(f"  [{i+1}/{len(rows)}] [{status}] {row['id']}: expected={expected_mode}/{expected_ref} actual={actual_mode}/{actual_ref}" + (f" ERROR={error}" if error else ""))

    # ---- 집계 ----
    by_category = {}
    for r in results:
        cat = r["category"]
        by_category.setdefault(cat, {"total": 0, "mode_correct": 0, "ref_correct": 0, "ref_applicable": 0})
        by_category[cat]["total"] += 1
        if r["mode_correct"]:
            by_category[cat]["mode_correct"] += 1
        if r["reference_period_correct"] is not None:
            by_category[cat]["ref_applicable"] += 1
            if r["reference_period_correct"]:
                by_category[cat]["ref_correct"] += 1

    total = len(results)
    total_mode_correct = sum(1 for r in results if r["mode_correct"])
    total_ref_applicable = sum(1 for r in results if r["reference_period_correct"] is not None)
    total_ref_correct = sum(1 for r in results if r["reference_period_correct"])

    print("\n=== 카테고리별 정확도 ===")
    for cat, agg in sorted(by_category.items()):
        mode_acc = agg["mode_correct"] / agg["total"] * 100
        ref_acc = (agg["ref_correct"] / agg["ref_applicable"] * 100) if agg["ref_applicable"] else None
        line = f"  {cat}: mode 정확도 {agg['mode_correct']}/{agg['total']} ({mode_acc:.0f}%)"
        if ref_acc is not None:
            line += f", reference_period 정확도(mode 맞은 것 중) {agg['ref_correct']}/{agg['ref_applicable']} ({ref_acc:.0f}%)"
        print(line)

    print(f"\n=== 전체 ===")
    print(f"  mode 정확도: {total_mode_correct}/{total} ({total_mode_correct/total*100:.0f}%)")
    if total_ref_applicable:
        print(f"  reference_period 정확도(mode 맞은 것 중): {total_ref_correct}/{total_ref_applicable} ({total_ref_correct/total_ref_applicable*100:.0f}%)")

    misses = [r for r in results if not r["mode_correct"] or r["reference_period_correct"] is False]
    print(f"\n=== 틀린 케이스 {len(misses)}건 ===")
    for r in misses:
        print(f"  [{r['id']}] {r['claim_text']}")
        print(f"    기대: mode={r['expected_mode']} ref={r['expected_reference_period']}")
        print(f"    실제: mode={r['actual_mode']} ref={r['actual_reference_period']}" + (f" (오류: {r['error']})" if r["error"] else ""))

    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump({"results": results, "by_category": by_category}, f, ensure_ascii=False, indent=2)
    print(f"\n전체 결과를 {RESULT_PATH}에 저장했습니다 - 이 파일을(또는 위 요약을) 공유해주세요.")


if __name__ == "__main__":
    main()
