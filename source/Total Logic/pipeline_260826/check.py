"""KOSIS 실제 엔드포인트 원본 응답을 그대로 눈으로 확인하기 위한 실험용 스크립트.

목적: getList(실데이터 조회) 응답에 축 의미(국가/기준단위/급여기간 같은 라벨)가
전혀 없고 C1~C8 위치값+이름만 온다는 것, 그리고 getMeta(type=ITM) 쪽에만
OBJ_ID_SN(축 번호)이 있어서 그 둘을 사람이 직접 대조해야 한다는 걸 실제 응답으로
검증한다. client.py의 KosisApiClient를 그대로 재사용하므로(같은 캐싱·재시도
로직) 여기서 본 것이 실제 파이프라인이 보는 것과 동일하다.

터미널은 넓어도 잘리니, 결과는 항상 파일로 남긴다:
- kosis_check.txt  : 잘림 없는 전체 텍스트(diff·grep하기 좋음)
- kosis_check.html : 브라우저로 열면 넓은 표를 스크롤/정렬해서 보기 좋음

사용:
    python3 check.py                                   # 기본값(최저임금 예시)
    python3 check.py --org-id 101 --tbl-id DT_1DA7001S --start 2024 --end 2025
    python3 check.py --fix 1=1005 --fix 3=T205          # objL1/objL3을 서버 필터로 고정
    python3 check.py --out-name wage_check              # wage_check.txt/.html로 저장
"""

import argparse
import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from client import KosisApiClient

PROJECT_ROOT = Path(__file__).resolve().parent

# 터미널에도 최대한 안 잘리게는 해두되, 진짜 원본은 파일로 봐야 한다.
pd.set_option("display.max_columns", None)
pd.set_option("display.max_colwidth", None)
pd.set_option("display.width", None)
pd.set_option("display.expand_frame_repr", False)


def parse_fix(pairs: Optional[List[str]]) -> Optional[Dict[int, str]]:
    """--fix 1=1005 --fix 3=T205 같은 인수를 {1: "1005", 3: "T205"}로 변환."""
    if not pairs:
        return None
    fixed: Dict[int, str] = {}
    for pair in pairs:
        axis_str, _, code = pair.partition("=")
        fixed[int(axis_str)] = code
    return fixed


def fetch_item_meta(client: KosisApiClient, org_id: str, tbl_id: str) -> pd.DataFrame:
    """getMeta(type=ITM) 원본 - OBJ_ID_SN이 몇 번째 objL/C 축인지 알려주는 유일한 곳."""
    meta = client.get_itm_meta_list(org_id, tbl_id)
    return pd.DataFrame(meta) if meta else pd.DataFrame()


# 지금 코드(_row_name 등)가 실제로 읽는 필드들 - 이 목록 밖에 있는 컬럼이
# non-null로 나오면, 우리가 놓치고 있는 정보(예: CD_NM 같은 단위/이름 후보
# 필드)일 수 있다는 신호다.
_KNOWN_META_FIELDS = {
    "ORG_ID", "TBL_ID", "TBL_NM",
    "OBJ_ID", "OBJ_ID_SN", "OBJ_NM", "OBJ_NM_ENG",
    "ITM_ID", "ITM_NM", "ITM_NM_ENG",
    "UP_ITM_ID", "ITM_LVL",
    # [2026-08-15 추가] ITM 행에만 붙는 단위 표준분류코드 - 실측(DT_1DA7001S)
    # 으로 CD_NM이 그 항목의 단위("천명"/"%")라는 게 확인돼서
    # resolve_dimensions_from_sentence의 _row_unit_hint가 이제 읽는다.
    # 모든 표에 있는 건 아니다(DT_2OEEM1012 국제비교표는 필드 자체가 없음).
    "CD_ID", "CD_NM", "CD_ENG_NM",
}


def summarize_meta_columns(meta_df: pd.DataFrame) -> str:
    """[2026-08-15 추가] getMeta(type=ITM) 응답에 우리가 지금 안 읽고 있는
    필드(예: 사용자가 실제로 본 "CD_NM")가 섞여 있는지 한눈에 보려고 만든
    요약. OBJ_ID(항목 vs 축1/축2/...)별로 그룹을 나눠서, 그룹마다 "실제로
    값이 채워진(non-null) 컬럼"만 보여준다 - KOSIS가 항목 행과 분류축
    행에 서로 다른 컬럼 집합을 채워 보내는 경우(축마다도 다를 수 있음),
    DataFrame 전체를 한꺼번에 보면 다른 그룹 값이 없어서 NaN으로만 보이는
    컬럼을 놓치기 쉽다. _KNOWN_META_FIELDS 밖의 컬럼은 "⚠️ 미확인 필드"로
    따로 표시한다.
    """
    if meta_df.empty:
        return "(메타 없음)"
    lines: List[str] = []
    lines.append(f"전체 컬럼: {list(meta_df.columns)}")
    unknown_cols = sorted(set(meta_df.columns) - _KNOWN_META_FIELDS)
    if unknown_cols:
        lines.append(f"⚠️ 미확인 필드(_row_name 등 현재 코드가 안 읽는 컬럼): {unknown_cols}")
    else:
        lines.append("미확인 필드 없음 - 전부 이미 코드가 읽는 필드.")
    lines.append("")

    if "OBJ_ID" not in meta_df.columns:
        return "\n".join(lines)

    for obj_id, group in meta_df.groupby("OBJ_ID", dropna=False):
        non_null_cols = [c for c in group.columns if group[c].notna().any()]
        obj_nm = group["OBJ_NM"].dropna().iloc[0] if "OBJ_NM" in group.columns and group["OBJ_NM"].notna().any() else ""
        lines.append(f"[OBJ_ID={obj_id!r} ({obj_nm}), {len(group)}건] non-null 컬럼: {non_null_cols}")
        sample = group.iloc[0]
        for col in non_null_cols:
            if col in ("OBJ_ID",):
                continue
            lines.append(f"    예시 1행 - {col}: {sample.get(col)!r}")
    return "\n".join(lines)


def fetch_raw_data(
    client: KosisApiClient,
    org_id: str,
    tbl_id: str,
    start: str,
    end: str,
    prd_se: str,
    itm_id: str,
    fixed: Optional[Dict[int, str]],
) -> Tuple[pd.DataFrame, int]:
    """getList(실데이터) 원본 - raw_dict를 그대로 펼쳐서 어떤 필드가 실제로 오는지 본다."""
    refined = client.fetch_actual_statistics_bounded_retry(
        org_id=org_id,
        tbl_id=tbl_id,
        start_year=start,
        end_year=end,
        itm_id=itm_id,
        current_dim=0,
        max_dim=8,
        prd_se=prd_se,
        objl_fixed=fixed,
    )
    if not refined:
        return pd.DataFrame(), 0
    # raw_dict가 KOSIS가 실제로 준 JSON 그대로다 - refined 쪽 요약(date/indicator/...)
    # 말고 이걸 펼쳐야 "C1/C1_NM에 축 이름이 안 실려있다"가 눈으로 확인된다.
    raw_rows = [item["raw_dict"] for item in refined]
    return pd.DataFrame(raw_rows), len(refined)


def write_txt(
    path: Path,
    meta_df: pd.DataFrame,
    raw_df: pd.DataFrame,
    raw_count: int,
    args: argparse.Namespace,
    fixed: Optional[Dict[int, str]],
) -> None:
    lines = [
        f"KOSIS 원본 응답 확인 — {datetime.datetime.now().isoformat(timespec='seconds')}",
        f"org_id={args.org_id} tbl_id={args.tbl_id} start={args.start} end={args.end}"
        f" prd_se={args.prd_se} itm_id={args.itm_id} fix={fixed}",
        "",
        "=" * 70,
        "[0] getMeta(type=ITM) 필드 요약 - OBJ_ID(항목/축)별 non-null 컬럼",
        "    (CD_NM처럼 지금 코드가 안 읽는 필드가 있는지 여기서 바로 확인)",
        "=" * 70,
        summarize_meta_columns(meta_df),
        "",
        "=" * 70,
        f"[1] getMeta(type=ITM) 원본 — {len(meta_df)}건 (축 의미는 여기만 나옴)",
        "=" * 70,
        meta_df.to_string() if not meta_df.empty else "(메타 없음)",
        "",
        "=" * 70,
        f"[2] getList(실데이터) 원본 — {raw_count}행 (objl_fixed={fixed})",
        "=" * 70,
        raw_df.to_string() if not raw_df.empty else "(데이터 없음 - 표가 이 조합/주기를 지원 안 하거나 축 코드가 안 맞을 수 있음)",
    ]
    if not raw_df.empty:
        lines.append(f"\n실제로 온 컬럼 목록: {list(raw_df.columns)}")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_html(
    path: Path,
    meta_df: pd.DataFrame,
    raw_df: pd.DataFrame,
    raw_count: int,
    args: argparse.Namespace,
    fixed: Optional[Dict[int, str]],
) -> None:
    style = """
    <style>
      body { font-family: -apple-system, sans-serif; margin: 24px; color: #222; }
      h2 { border-bottom: 2px solid #333; padding-bottom: 4px; }
      table { border-collapse: collapse; margin-bottom: 32px; }
      th, td { border: 1px solid #ccc; padding: 4px 10px; font-size: 13px; white-space: nowrap; }
      th { background: #f0f0f0; position: sticky; top: 0; }
      tr:nth-child(even) { background: #fafafa; }
      .meta { color: #555; font-size: 13px; margin-bottom: 16px; }
    </style>
    """
    meta_html = (
        meta_df.to_html(index=False)
        if not meta_df.empty
        else "<p>(메타 없음)</p>"
    )
    raw_html = (
        raw_df.to_html(index=False)
        if not raw_df.empty
        else "<p>(데이터 없음 - 표가 이 조합/주기를 지원 안 하거나 축 코드가 안 맞을 수 있음)</p>"
    )
    summary_text = summarize_meta_columns(meta_df)
    summary_html = f"<pre>{summary_text}</pre>"
    body = f"""
    <html><head><meta charset="utf-8"><title>KOSIS 원본 응답 확인</title>{style}</head>
    <body>
      <div class="meta">
        {datetime.datetime.now().isoformat(timespec='seconds')}<br>
        org_id={args.org_id} · tbl_id={args.tbl_id} · start={args.start} · end={args.end}
        · prd_se={args.prd_se} · itm_id={args.itm_id} · fix={fixed}
      </div>
      <h2>[0] getMeta(type=ITM) 필드 요약 - OBJ_ID(항목/축)별 non-null 컬럼</h2>
      <p class="meta">CD_NM처럼 지금 코드가 안 읽는 필드가 있는지 여기서 바로 확인 - "⚠️ 미확인 필드"에 뭐가 뜨는지가 핵심.</p>
      {summary_html}
      <h2>[1] getMeta(type=ITM) 원본 — {len(meta_df)}건 (축 의미는 여기만 나옴)</h2>
      {meta_html}
      <h2>[2] getList(실데이터) 원본 — {raw_count}행 (objl_fixed={fixed})</h2>
      {raw_html}
    </body></html>
    """
    path.write_text(body, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-id", default="101")
    parser.add_argument("--tbl-id", default="DT_2OEEM1012", help="기본: OECD 최저임금 국제비교표")
    parser.add_argument("--start", default="2024")
    parser.add_argument("--end", default="2025")
    parser.add_argument("--prd-se", default="Y")
    parser.add_argument("--itm-id", default="all")
    parser.add_argument(
        "--fix",
        action="append",
        metavar="AXIS=CODE",
        help="objL{AXIS}를 all 대신 CODE로 서버 필터 고정 (여러 번 지정 가능, 예: --fix 1=1005)",
    )
    parser.add_argument(
        "--out-name",
        default="kosis_check",
        help="저장 파일 이름(확장자 제외) — 루트에 <이름>.txt/.html로 저장 (기본: kosis_check)",
    )
    args = parser.parse_args()

    client = KosisApiClient()
    fixed = parse_fix(args.fix)

    meta_df = fetch_item_meta(client, args.org_id, args.tbl_id)
    raw_df, raw_count = fetch_raw_data(
        client, args.org_id, args.tbl_id, args.start, args.end, args.prd_se, args.itm_id, fixed
    )

    txt_path = PROJECT_ROOT / f"{args.out_name}.txt"
    html_path = PROJECT_ROOT / f"{args.out_name}.html"
    write_txt(txt_path, meta_df, raw_df, raw_count, args, fixed)
    write_html(html_path, meta_df, raw_df, raw_count, args, fixed)

    print(f"meta {len(meta_df)}건 · raw data {raw_count}행")
    if not meta_df.empty:
        unknown_cols = sorted(set(meta_df.columns) - _KNOWN_META_FIELDS)
        if unknown_cols:
            print(f"⚠️ 미확인 meta 필드(코드가 안 읽는 컬럼): {unknown_cols}")
        else:
            print("meta 필드는 전부 이미 코드가 읽는 필드뿐.")
    print(f"저장 완료:\n  {txt_path}\n  {html_path}  (더블클릭해서 브라우저로 열면 넓은 표도 편함)")


if __name__ == "__main__":
    main()
