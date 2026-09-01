"""[2026-08-21 신규 - 로컬 실행용] "통계설명자료" 제공 항목이 27개(핵심)/
51개(전체)에서 59개로 늘었다는 KOSIS 공지(2026-08-06)가 실제 API 응답에
반영됐는지 실측으로 확인하는 프로브.

## 배경 (실측 우선 원칙)

이 세션에서 MCP(kosis_meta 도구)로 실시간 조회한 결과, 도구 설명과 실제
응답 둘 다 "핵심 27개 항목(전체 51개)"라고만 나왔다 - 사용자가 받은
메일의 "59개"와 숫자가 안 맞는다. 다만 MCP 도구는 KOSIS 원본 API 응답을
그대로 안 돌려주고 사람이 읽기 좋게 재포맷할 수 있어서, 그 재포맷
과정에서 최신 필드가 누락됐을 가능성을 배제할 수 없다 - 이 프로젝트의
client.py(get_stat_explanation)는 KOSIS statisticsExplData.do를 직접
호출하므로, 원본 raw 응답을 그대로 까서 실제 키 개수를 세면 MCP 도구를
거치지 않고 확인할 수 있다.

주의: 이 스크립트는 "27→59"가 맞다/틀리다를 미리 단정하지 않는다 - 아래
BASELINE_51_FIELDS(이번 세션에 kosis_meta MCP 실측으로 받아 적은 51개
필드 목록)와 실제 원본 응답 키를 비교해서, (1) 지금 몇 개가 오는지,
(2) BASELINE에 없는 새 키가 있는지(있다면 그게 늘어난 부분)를 그대로
보여주기만 한다 - 판단은 이 출력을 보고 사용자가 내린다.

## 왜 로컬에서 돌려야 하는가

이 코딩 샌드박스는 KOSIS 실 API로 나가는 네트워크가 막혀 있다(이번
세션 내내 확인된 제약 - README/CLAUDE.md 참고). 실제 API 키가 있는
로컬 환경(이 프로젝트 폴더, .env에 KOSIS_API_KEY)에서 직접 실행해야
한다.

## DB 관련 없음

이 스크립트는 조회만 하고(GET 요청), kosis_warehouse.db를 열거나
쓰지 않는다 - CLAUDE.md "DB 파일에 직접 쓰기/삭제 금지" 규칙과 무관.

사용법: python3 probe_stat_explanation_field_count.py
"""

import json

from client import KosisApiClient

# 이미 로컬에 적재된 표 중, 성격이 다른 2개를 골랐다(일반 조사 vs 지수류
# 조사) - "지수 작성" 섹션(idxEqua 등 4개)처럼 조사 성격에 따라서만
# 조건부로 나오는 필드가 있어서, 표 하나만 보면 "필드가 줄어든 것"과
# "이 조사엔 원래 해당 없는 것"을 못 가른다.
TABLES_TO_CHECK = [
    ("101", "DT_1J22001", "소비자물가지수(지출목적별) - 지수류 조사"),
    ("101", "DT_1DA7010S", "종사상지위별 취업자 - 일반 조사"),
]

# [2026-08-21 실측 - kosis_meta MCP 도구, metaItm="목록" 응답 그대로 받아적음]
# 핵심 27개 + 지정 시에만 나오는 24개 = 51개. 새 키가 이 목록에 없으면
# "59개 확대"가 실제로 이 엔드포인트에도 반영된 것으로 볼 수 있다.
BASELINE_51_FIELDS = {
    # 개요
    "statsNm", "statsKind", "statsEnd", "statsContinue", "basisLaw", "writingPurps",
    # 작성 개요
    "examinPd", "statsPeriod", "writingSystem", "writingTel", "statsField",
    # 조사 대상·항목
    "examinObjrange", "examinObjArea", "josaUnit", "applyGroup", "josaItm",
    # 공표
    "pubPeriod", "pubExtent", "pubDate", "publictMth", "examinTrgetPd",
    # 유의사항 / 용어해설
    "dataUserNote", "mainTermExpl",
    # 수집방법·조사연혁
    "dataCollectMth", "examinHistory",
    # 승인정보
    "confmNo", "confmDt",
    # 조사 실시·품질 (지정 시)
    "enumSampleMng", "exmnTaskFlow", "exmnrScl", "exmnrEduTrng", "grndsExmnGuid",
    "nonresratenonresAct",
    # 표본설계·추정 (지정 시)
    "goalPoplExmnPopl", "sampleFrame", "extrUnit", "sampleExtrMthd", "sampleSclCfml",
    "sampleAprtMthd", "estEqua", "wgtAjmt", "outIdntfProc",
    # 지수 작성 (지정 시)
    "idxEqua", "idxWrtBscsData", "idxWrtBscsField", "origIndex",
    # 자료 제공·참고 (지정 시)
    "pldoc", "md", "totData", "presentnYnPresentnNm", "etcRefData",
}


def main() -> None:
    client = KosisApiClient()
    print(f"[기준선] 이번 세션 실측(kosis_meta MCP) 기준 51개 필드로 비교합니다.\n")

    all_new_keys = set()
    for org_id, tbl_id, label in TABLES_TO_CHECK:
        print(f"=== {label} ({org_id}/{tbl_id}) ===")
        expl = client.get_stat_explanation(org_id, tbl_id)
        if not expl:
            print("  [실패] 빈 응답 - API 키/네트워크/표 ID를 확인하세요.\n")
            continue

        keys = set(expl.keys())
        populated = {k: v for k, v in expl.items() if v not in (None, "", [], {})}
        new_keys = keys - BASELINE_51_FIELDS
        missing_from_baseline = BASELINE_51_FIELDS - keys
        all_new_keys |= new_keys

        print(f"  원본 응답 키 개수: {len(keys)}개 (기준선 51개 대비 {len(keys) - 51:+d})")
        print(f"  값이 채워진 키 개수: {len(populated)}개")
        if new_keys:
            print(f"  [기준선(51개)에 없던 새 키] {len(new_keys)}개: {sorted(new_keys)}")
        else:
            print("  [기준선에 없던 새 키] 없음")
        if missing_from_baseline:
            print(f"  [기준선엔 있는데 이번 응답엔 없는 키] {len(missing_from_baseline)}개: {sorted(missing_from_baseline)}")
        print()

    print("=" * 70)
    if all_new_keys:
        print(f"[결론] 두 표에서 기준선(51개)에 없던 새 필드가 총 {len(all_new_keys)}개 발견됨:")
        print(f"       {sorted(all_new_keys)}")
        print("       -> '27→59개 확대'가 이 엔드포인트(statisticsExplData.do)에도")
        print("          실제로 반영된 것으로 보입니다. 51+새 필드 개수가 59에")
        print("          가까운지 직접 확인하세요.")
    else:
        print("[결론] 새 필드가 발견되지 않았습니다 - 이 엔드포인트는 아직 51개")
        print("       그대로입니다. '59개'는 (1) 아직 이 표/엔드포인트에 반영")
        print("       안 됐거나, (2) 다른 서비스(예: 화면 정보 API)의 항목 수를")
        print("       가리키는 것일 수 있습니다 - 메일 원문에 다른 API명이")
        print("       언급돼 있는지 확인해보시는 걸 권합니다.")

    # 원본 그대로도 파일로 남겨서, 위 요약과 별개로 직접 훑어볼 수 있게 한다.
    dump = {
        f"{org_id}/{tbl_id}": client.get_stat_explanation(org_id, tbl_id)
        for org_id, tbl_id, _ in TABLES_TO_CHECK
    }
    with open("probe_stat_explanation_raw.json", "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=2)
    print("\n[저장] 원본 응답 전체를 probe_stat_explanation_raw.json에 저장했습니다.")


if __name__ == "__main__":
    main()
