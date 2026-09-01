"""[2026-08-17 신규] client.KosisApiClient.get_statistics_list()를 실제
API 키로 3번 호출해서 raw 응답을 그대로 파일에 남기는 실측용 스크립트 -
CLAUDE.md "실측 우선 원칙"에 따라, VDB용 catalog_titles 스키마를 확정하기
전에 반드시 거쳐야 하는 단계다.

이 스크립트는 코딩 샌드박스가 아니라 **사용자가 실제 네트워크+API 키가
있는 로컬 환경에서 직접 실행**해야 한다(샌드박스는 KOSIS 포함 모든 외부
네트워크가 막혀 있음, 2026-08-17 확인).

3번 호출하는 이유 - 이번 세션에 MCP(kosis_list)로 이미 실측해본 것과 정확히
같은 파라미터 조합을 그대로 재현해서, "한글 라벨(MCP)"과 "진짜 JSON
필드명(이 스크립트)"을 나란히 비교할 수 있게 하기 위함이다:
  1. top_level: parentListId 없음 -> 최상위 대분류 노드들(카테고리)
  2. P2: parentListId="P2"(물가) -> 그 하위 카테고리들(여전히 카테고리 노드)
  3. P2_6: parentListId="P2_6"(소비자물가조사) -> 리프(실제 표) 노드 15개
     (MCP로 이미 확인한 것: DT_1J22003/DT_1J22041/DT_1J22001 등)

각 호출의 (a) 완전히 가공 안 된 raw HTTP 응답 텍스트와 (b)
get_statistics_list()가 파싱한 결과를 둘 다 statistics_list_probe.json에
남긴다 - fix_and_parse_kosis_json이 혹시 필드명을 건드리는 경우까지
대비해서, raw 텍스트도 같이 봐야 완전히 확신할 수 있다.

사용법: python probe_statistics_list.py (이 폴더에서, config.py에
KOSIS_API_KEY가 이미 설정돼 있어야 함 - seed_ingest.py 등 기존 스크립트와
동일한 전제)
"""

import json
import sys

import requests

from client import KosisApiClient, fix_and_parse_kosis_json


def _raw_call(client: KosisApiClient, vw_cd: str, parent_list_id):
    """get_statistics_list()와 완전히 같은 요청을 한 번 더 날려서, 파싱 전
    raw 텍스트를 그대로 확보한다(get_statistics_list()는 파싱된 결과만
    반환하므로, 원본 텍스트를 따로 보고 싶으면 별도 호출이 필요하다)."""
    params = {
        "method": "getList",
        "apiKey": client.api_key,
        "vwCd": vw_cd,
        "format": "json",
    }
    if parent_list_id:
        params["parentListId"] = parent_list_id
    res = requests.get(
        "https://kosis.kr/openapi/statisticsList.do", params=params, timeout=10
    )
    return res.text.strip()


def probe(client: KosisApiClient, label: str, vw_cd: str, parent_list_id):
    print(f"\n=== [{label}] vw_cd={vw_cd!r} parent_list_id={parent_list_id!r} ===")
    try:
        raw_text = _raw_call(client, vw_cd, parent_list_id)
    except Exception as e:
        print(f"  raw 호출 예외: {e}")
        return {"raw_text": None, "parsed": None, "error": str(e)}

    print(f"  raw 응답(앞 500자): {raw_text[:500]!r}")

    try:
        parsed = fix_and_parse_kosis_json(raw_text)
    except Exception as e:
        print(f"  파싱 예외: {e}")
        parsed = None

    if isinstance(parsed, list) and parsed:
        print(f"  파싱 결과: {len(parsed)}건, 첫 번째 원소의 키: {sorted(parsed[0].keys())}")
        print(f"  첫 번째 원소 전체: {json.dumps(parsed[0], ensure_ascii=False, indent=2)}")
    else:
        print(f"  파싱 결과: {parsed!r}")

    return {"raw_text": raw_text, "parsed": parsed}


def main():
    client = KosisApiClient()
    results = {
        "top_level": probe(client, "top_level (parentListId 없음)", "MT_ZTITLE", None),
        "P2": probe(client, "P2 (물가 하위 카테고리)", "MT_ZTITLE", "P2"),
        "P2_6": probe(client, "P2_6 (소비자물가조사 하위 리프 표)", "MT_ZTITLE", "P2_6"),
    }

    out_path = "statistics_list_probe.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n전체 결과를 {out_path}에 저장했습니다 - 이 파일을 그대로 공유해주세요.")


if __name__ == "__main__":
    sys.exit(main())
