# -*- coding: utf-8 -*-
"""1번 Task 통합 실행기 — 기사 원본 한 개(파일) → claims.jsonl 한 번에.

    기사 원본  ──►  P0 적재  ──►  P1 정제  ──►  P2 문장화  ──►  P3 Claim 추출  ──►  claims.jsonl
    xlsx/csv/json    articles     articles_clean    sentences      A→B(LLM)→C→D→E      (2번 Task 입력)

기존 모듈을 그대로 호출한다 — 이 파일에는 파이프라인 로직이 없다(배선만 한다).
P0·P1·P2는 각 모듈의 CLI 진입점을 그대로 부르고(문서화된 명령과 동일 경로),
P3만 Stage B 추출기(HCX 클라이언트 + record-replay 캐시)를 조립해 오케스트레이터에 주입한다.

산출물은 **한 디렉터리**에 모인다(기본 `data/run`) — 중간 산출물과 최종 계약 파일이 같은
실행 단위로 묶여야 "이 claims.jsonl이 어느 sentences.jsonl에서 나왔나"를 되짚을 수 있다.

사용:
    # 선별 기사 60건(xlsx) 전 구간
    venv\\Scripts\\python.exe run.py --input D:/part1/articles.xlsx --outdir data/run

    # 크롤링 기사 1건 (실서비스 경로)
    venv\\Scripts\\python.exe run.py --input crawled.json --outdir data/one

    # LLM 호출 없이 배선만 확인 (P2까지)
    venv\\Scripts\\python.exe run.py --input D:/part1/articles.xlsx --outdir data/run --to p2

    # 중단 후 재개 — 캐시가 성공분을 재생하므로 재과금 0
    venv\\Scripts\\python.exe run.py --outdir data/run --from p3
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # 어느 cwd에서 불러도 src를 찾게

import config, llm_meter, p0_load, p1_clean, p2_split
from p3_emit import AccountingError
from p3_pipeline import CIRCUIT_BREAKER_RATE

STAGES = ("p0", "p1", "p2", "p3")

# 실측 단가(CODE_GUIDE §6.5 — dev 8기사 104문장 전량 실호출, extract_v1.8).
# 기사당·문장당 둘 다 두고 **큰 쪽**을 추정치로 쓴다: 나열형 기사("2025년 달라지는 것")는
# 기사당 숫자 문장이 표본(약 13개)의 2배라 기사 기준만 쓰면 실제의 절반으로 과소 추정된다.
# 어느 쪽도 정확하지 않은 추정이며, 캐시 재생분은 과금 0이라 그만큼 과대 추정된다.
COST_PER_ARTICLE_KRW = 82.27
COST_PER_SENTENCE_KRW = 6.33
DEFAULT_BUDGET_KRW = 5000.0    # 추정 요금이 이 값을 넘으면 --yes 없이는 실행하지 않는다


# ── 단계 배선 ────────────────────────────────────────────────
def stage_p0(input_path: Path, outdir: Path, fmt: str, policy: str,
             encoding: str | None, limit: int) -> None:
    """P0 적재 — 입력 형식을 아는 유일한 층. outdir/articles.jsonl 등을 쓴다."""
    argv = ["--input", str(input_path), "--outdir", str(outdir),
            "--format", fmt, "--policy", policy]
    if encoding:
        argv += ["--encoding", encoding]
    if limit:
        argv += ["--limit", str(limit)]
    p0_load.main(argv)


def stage_p1(outdir: Path) -> None:
    """P1 정제 — articles.jsonl → articles_clean.jsonl (+ 감사 사이드카)."""
    p1_clean.main(["--input", str(outdir / "articles.jsonl"),
                   "--output", str(outdir / "articles_clean.jsonl"),
                   "--trace", str(outdir / "articles_clean_trace.jsonl")])


def stage_p2(outdir: Path) -> None:
    """P2 문장화 — articles_clean.jsonl → sentences.jsonl (오프셋 보존)."""
    p2_split.main(["--input", str(outdir / "articles_clean.jsonl"),
                   "--output", str(outdir / "sentences.jsonl")])


def build_extractor(outdir: Path, cache_path: Path, *, stub: bool, fresh: bool, meter):
    """Stage B 추출기 조립 — 파이프라인에 주입할 콜러블 하나를 만든다.

    stub=True: 골든 라벨을 되돌려주는 무-LLM 추출기(§6 스모크 ③). 골든셋 파일이 필요하다.
    """
    if stub:
        from p3_pipeline import make_golden_stub_extractor
        return make_golden_stub_extractor()

    import p3_stage_b as sb
    from p3_cache import ReplayCache

    client = sb.HCXClient(meter=meter)                      # 키는 .env 소관(src/config.py)
    cache = ReplayCache(cache_path, sb.PROMPT_VERSION, client.model)
    sent_index = sb.build_sentence_index(outdir / "sentences.jsonl")
    system_prompt = sb.PROMPT_V1.read_text(encoding="utf-8")
    print(f"Stage B: {client.model} · {sb.PROMPT_VERSION} · 캐시 {len(cache)}건"
          f"{' (재생 끔 — 전량 실호출)' if fresh else ''}")
    return sb.make_hcx_extractor(client, cache, system_prompt, sent_index,
                                 meter=meter, use_cache=not fresh)


def stage_p3(outdir: Path, cache_path: Path, *, stub: bool, fresh: bool, meter,
             article_filter: set | None, breaker_rate: float) -> dict:
    """P3 — A(숫자 문장 필터) → B(주입) → C(검증·시점 해소) → D(표준명) → E(산출·전수 회계)."""
    from p3_pipeline import run as pipeline_run

    extractor = build_extractor(outdir, cache_path, stub=stub, fresh=fresh, meter=meter)
    return pipeline_run(extractor, outdir,
                        sentences_path=outdir / "sentences.jsonl",
                        articles_path=outdir / "articles_clean.jsonl",
                        article_filter=article_filter,
                        breaker_rate=breaker_rate)


# ── 오케스트레이션 ───────────────────────────────────────────
def run_pipeline(input_path: Path | str | None, outdir: Path | str, *,
                 fmt: str = "auto", policy: str = "auto", encoding: str | None = None,
                 limit: int = 0, start: str = "p0", end: str = "p3",
                 stub: bool = False, fresh: bool = False, meter=None,
                 cache_path: Path | str | None = None,
                 article_filter: set | None = None,
                 breaker_rate: float = CIRCUIT_BREAKER_RATE,
                 budget_krw: float | None = DEFAULT_BUDGET_KRW) -> dict | None:
    """입력부터 claims.jsonl까지 한 번에. 반환: P3 요약 dict(P3를 돌지 않았으면 None).

    서비스에서 부를 때는 이 함수를 그대로 쓰면 된다 — CLI는 이 함수의 얇은 껍데기다.
    budget_krw: 추정 요금 상한. 초과하면 실행하지 않고 RuntimeError(None이면 검사 생략).
    """
    outdir = Path(outdir)
    cache_path = Path(cache_path) if cache_path else config.cache_dir() / "replay_extract_v1.jsonl"
    todo = [s for s in STAGES if STAGES.index(start) <= STAGES.index(s) <= STAGES.index(end)]
    summary = None

    for stage in todo:
        t0 = time.perf_counter()
        print(f"\n── {stage.upper()} ──────────────────────────────")
        if stage == "p0":
            if input_path is None:
                raise ValueError("P0부터 실행하려면 --input 이 필요합니다 "
                                 "(중간부터 재개하려면 --from p1|p2|p3)")
            stage_p0(Path(input_path), outdir, fmt, policy, encoding, limit)
        elif stage == "p1":
            stage_p1(outdir)
        elif stage == "p2":
            stage_p2(outdir)
        else:
            _preflight_cost(outdir, stub=stub, budget_krw=budget_krw,
                            article_filter=article_filter)
            summary = stage_p3(outdir, cache_path, stub=stub, fresh=fresh, meter=meter,
                               article_filter=article_filter, breaker_rate=breaker_rate)
        print(f"({time.perf_counter() - t0:.1f}초)")
    return summary


def _count_records(path: Path, article_filter: set | None = None,
                   numeric_only: bool = False) -> int:
    """jsonl 레코드 수 — article_filter가 있으면 그 기사분만 센다(추정 규모를 실행 규모에 맞춘다).

    numeric_only: 숫자 문장만 — Stage A가 고르는 **실제 LLM 호출 대상**과 같은 기준.
    """
    import json

    from p3_stage_a import is_numeric_sentence

    n = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            if article_filter and rec.get("article_id") not in article_filter:
                continue
            if numeric_only and not is_numeric_sentence(rec.get("text")):
                continue
            n += 1
    return n


def _preflight_cost(outdir: Path, *, stub: bool, budget_krw: float | None,
                    article_filter: set | None = None) -> None:
    """LLM을 부르기 직전에 규모·추정 요금을 먼저 보여준다 — 과금은 되돌릴 수 없다."""
    if stub:
        print("Stage B: 골든 stub — HCX 0콜")
        return
    n_articles = _count_records(outdir / "articles_clean.jsonl", article_filter)
    n_sents = _count_records(outdir / "sentences.jsonl", article_filter, numeric_only=True)
    est = max(n_articles * COST_PER_ARTICLE_KRW, n_sents * COST_PER_SENTENCE_KRW)
    print(f"규모: 기사 {n_articles}건 · 숫자 문장 {n_sents}개(Stage B 호출 대상) → "
          f"추정 요금 약 {est:,.0f}원 (VAT 별도, 캐시 재생분 제외)")
    if budget_krw is not None and est > budget_krw:
        raise RuntimeError(
            f"추정 요금 {est:,.0f}원이 상한 {budget_krw:,.0f}원을 넘습니다 — "
            f"확인 후 --yes(상한 해제) 또는 --budget 으로 상한을 올리세요. "
            f"규모를 줄이려면 --limit N.")


def _print_summary(summary: dict, meter) -> None:
    n = summary["numeric_sentences"]
    err_rate = f"({summary['errors'] / n:.1%})" if n else ""
    print("\n── 결과 ──────────────────────────────────────")
    print(f"숫자 문장 {n} → Claim {summary['claims']} · 제외 {summary['excluded']} · "
          f"오류 {summary['errors']}{err_rate} · eligible {summary['eligible_true']}")
    print(f"Stage D: 사전 {summary['dictionary_version'] or '없음(verbatim 복사)'} {summary['stage_d']}")
    print(f"\n인수인계 계약 파일 → {summary['paths']['claims']}")
    for name, path in summary["paths"].items():
        if name != "claims":
            print(f"  {name}: {path}")
    if meter is not None and meter.records:
        s = meter.summary()
        print(f"\n[사용량] API {s['calls_api']}콜 · 캐시 재생 {s['calls_cached']} · "
              f"토큰 입력 {s['input_tokens']:,}/출력 {s['output_tokens']:,} · "
              f"요금 {s['cost_krw']:,.2f}원(VAT 별도) → 로그 {meter.path}")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")   # 중단 사유가 콘솔에서 깨지면 못 읽는다
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        description="1번 Task 통합 실행 — 기사 원본(xlsx·csv·json) → claims.jsonl",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--input", type=Path, default=None,
                    help="입력 파일 (xlsx · csv/tsv · json/jsonl). P0부터 돌 때 필수")
    ap.add_argument("--outdir", type=Path, default=config.data_dir() / "run",
                    help="산출물 디렉터리 — 중간·최종 산출물이 전부 여기에 모인다")
    ap.add_argument("--format", choices=("auto", "xlsx", "csv", "json"), default="auto")
    ap.add_argument("--policy", choices=("auto", "strict", "bulk"), default="auto",
                    help="auto: xlsx=strict(불량 1행이면 전체 실패), csv/json=bulk(격리 후 계속)")
    ap.add_argument("--encoding", default=None, help="csv/json 인코딩 (기본 utf-8-sig → cp949 폴백)")
    ap.add_argument("--limit", type=int, default=0, help="선두 N행만 적재 (대량 원본 시험용)")
    ap.add_argument("--from", dest="start", choices=STAGES, default="p0",
                    help="이 단계부터 실행 (재개용 — 앞 단계 산출물이 outdir에 있어야 한다)")
    ap.add_argument("--to", dest="end", choices=STAGES, default="p3",
                    help="이 단계까지 실행 (--to p2 면 LLM 호출 없이 문장화까지)")
    ap.add_argument("--articles", default="", help="기사 ID 부분집합, 쉼표 구분 (P3만 적용)")
    ap.add_argument("--stub", action="store_true",
                    help="LLM 대신 골든 라벨 stub으로 P3 실행 — 배선 검증용(HCX 0콜, 골든셋 필요)")
    ap.add_argument("--cache", type=Path, default=config.cache_dir() / "replay_extract_v1.jsonl",
                    help="record-replay 캐시 경로 — 재실행 시 성공분을 재생(재과금 0)")
    ap.add_argument("--fresh", action="store_true",
                    help="캐시 재생을 끄고 전량 실호출 — 사용량 실측용. 과금 발생")
    ap.add_argument("--breaker-rate", type=float, default=CIRCUIT_BREAKER_RATE,
                    help="서킷브레이커 임계 — 오류 문장 비율이 넘으면 계통 결함으로 보고 중단")
    ap.add_argument("--budget", type=float, default=DEFAULT_BUDGET_KRW,
                    help="추정 요금 상한(원). 초과하면 실행 전에 멈춘다")
    ap.add_argument("--yes", action="store_true", help="추정 요금 상한 검사를 건너뛴다")
    ap.add_argument("--meter", type=Path, default=None,
                    help=f"사용량 로그 경로 (기본: data/{llm_meter.USAGE_LOG_DEFAULT})")
    ap.add_argument("--no-meter", action="store_true", help="사용량 기록 끄기")
    args = ap.parse_args(argv)

    if STAGES.index(args.start) > STAGES.index(args.end):
        ap.error(f"--from {args.start} 이 --to {args.end} 보다 뒤입니다")

    from p3_stage_b import PROMPT_VERSION
    meter = None if (args.no_meter or args.stub) else llm_meter.UsageMeter(
        args.meter, prompt_version=PROMPT_VERSION)

    print(f"파이프라인 {args.start.upper()} → {args.end.upper()} · 출력 {args.outdir}")
    # 어느 .env가 적용됐는지 먼저 보여준다 — 통합 과정에서 배치가 바뀌면 여기서 바로 드러난다
    print(f".env: {config.env_file() or '없음 — 키가 필요한 단계에서 멈춘다'}")
    try:
        summary = run_pipeline(
            args.input, args.outdir,
            fmt=args.format, policy=args.policy, encoding=args.encoding, limit=args.limit,
            start=args.start, end=args.end, stub=args.stub, fresh=args.fresh, meter=meter,
            cache_path=args.cache,
            article_filter={a.strip() for a in args.articles.split(",") if a.strip()} or None,
            breaker_rate=args.breaker_rate,
            budget_krw=None if args.yes else args.budget)
    except (ValueError, RuntimeError, FileNotFoundError) as e:
        # 적재 검증 실패·예산 초과·회계 위반 — 사유는 예외 메시지가 이미 담고 있다
        kind = "전수 회계 위반" if isinstance(e, AccountingError) else "실행 중단"
        sys.stdout.flush()          # 진행 로그와 중단 사유의 순서가 뒤집히지 않게
        print(f"\n[{kind}] {e}", file=sys.stderr)
        return 3 if isinstance(e, AccountingError) else 2

    if summary is not None:
        _print_summary(summary, meter)
        _handoff_claims(summary)
    else:
        print(f"\n{args.end.upper()}까지 완료 — 이어서 돌리려면 "
              f"`run.py --outdir {args.outdir} --from {STAGES[STAGES.index(args.end) + 1]}`")
    return 0


def _handoff_claims(summary: dict) -> None:
    """P3까지 끝나 claims.jsonl이 나오면, 2번(run02.py)이 --input 없이 바로
    받을 수 있게 interface.PIPELINE01 이름으로 루트에도 복사해 둔다
    (2026-08-14 — interface.py 중앙화). --outdir 자체의 재개·회계 구조는
    건드리지 않고, 마지막에 인수인계용 사본 하나만 더 남기는 것뿐이다."""
    claims_path = summary.get("paths", {}).get("claims")
    if not claims_path:
        return
    try:
        import shutil

        import interface

        dest = interface.PIPELINE01_PATH
        shutil.copyfile(claims_path, dest)
        print(f"인수인계 사본 → {dest}")
    except Exception as e:
        print(f"[안내] interface.PIPELINE01 사본 생성 실패(무시하고 계속): {e}")


if __name__ == "__main__":
    raise SystemExit(main())
