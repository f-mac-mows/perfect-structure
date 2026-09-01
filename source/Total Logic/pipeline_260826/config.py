# -*- coding: utf-8 -*-
"""공용 설정 — `.env` 기반 비밀키·경로 관리.

**팀 통합 기준(2026-08-13 결정)**: 비밀키는 OS 영구 환경변수가 아니라 저장소 루트의
`.env` 파일로 관리한다. 변수명은 `.env.example`이 정본이다.

    NCP_CLOVASTUDIO_API_KEY   CLOVA Studio(HCX) API 키   — 1번(Stage B) 사용
    KOSIS_API_KEY             KOSIS Open API 키          — 2~5번 사용

설계 메모
- **외부 의존성 없음**: `python-dotenv`를 쓰지 않고 직접 파싱한다. 팀원이 dotenv를
  써도 같은 파일을 읽으므로 충돌하지 않는다.
- **값의 출처는 `.env` 파일 하나뿐이다**(2026-08-14 변경). OS 환경변수는 **읽지도
  쓰지도 않는다.** 옛 이름(`NCP_API_KEY` 등)이 OS에 남아 있으면 `.env`를 못 찾은
  실행이 그 값으로 조용히 성공해 "`.env`를 고쳤는데 왜 안 바뀌지"라는 디버깅 불가능한
  상태가 된다(실측 재현). 이제는 폴백 없이 예외로 멈춘다.
- **`.env` 위치는 유연하다**: 소스와 **같은 폴더**에 둬도, 저장소 루트에 둬도, 상위
  통합 저장소에 둬도 찾는다(`_candidates()` 순서). 어느 파일이 적용됐는지는
  `env_file()`로 확인한다 — 통합 과정에서 배치가 바뀌어도 "어디를 읽었나"가 보인다.
- 키는 **절대 로그·예외 메시지에 값이 실리지 않는다**(§7 — 키 외부 공유 금지).
"""
from __future__ import annotations

from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SRC_DIR.parent

# --- 정본 변수명 (.env.example과 반드시 일치) --------------------------------
ENV_HCX_KEY = "NCP_CLOVASTUDIO_API_KEY"
ENV_KOSIS_KEY = "KOSIS_API_KEY"
ENV_HCX_ENDPOINT = "HCX_ENDPOINT"        # 선택 — 기본 CLOVA Studio v3
ENV_HCX_MODEL = "HCX_MODEL"              # 선택 — 기본 HCX-005
ENV_PART1_DIR = "PART1_DIR"              # 선택 — 골든셋·원본 데이터 폴더(평가 전용)

# 구 변수명 — 팀 통합 전 쓰던 이름. **같은 `.env` 안에** 정본이 없을 때만 폴백한다.
LEGACY_HCX_KEYS = ("NCP_API_KEY", "HCX_API_KEY")

PARENT_SEARCH_DEPTH = 3   # 상위 탐색 상한 — 드라이브 루트까지 올라가 남의 .env를 집지 않게

_env: dict[str, str] = {}      # `.env`에서 읽은 값 — 유일한 출처(OS 환경변수 미사용)
_source: Path | None = None    # 실제로 읽은 파일
_is_loaded = False
_warned: set[str] = set()


def _parse_env_file(path: Path) -> dict[str, str]:
    """최소 dotenv 파서. `KEY=VALUE`, `export KEY=VALUE`, `#` 주석, 따옴표를 지원."""
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]            # 따옴표 안은 주석 제거를 하지 않는다
        else:
            val = val.split(" #", 1)[0].rstrip()
        out[key] = val
    return out


def _candidates() -> list[Path]:
    """`.env` 탐색 순서 — 소스 옆 → 저장소 루트 → 상위(통합 저장소) → 실행 위치.

    **소스와 같은 폴더를 맨 앞에 두는 이유**: 통합 과정에서 모듈을 다른 저장소로 복사·평탄화해
    합치는 배치가 실재하고, 그때 `.env`는 소스 옆에 놓인다. 저장소 루트만 보면 한 칸 위를
    가리켜 못 찾는다(실측). 상위 탐색은 `PARENT_SEARCH_DEPTH`까지만 — 무한정 거슬러 올라가면
    관계없는 상위 폴더의 `.env`를 집는다.
    """
    paths = [SRC_DIR / ".env", REPO_ROOT / ".env"]
    paths += [p / ".env" for p in list(REPO_ROOT.parents)[:PARENT_SEARCH_DEPTH]]
    paths.append(Path.cwd() / ".env")
    seen: set[Path] = set()
    out: list[Path] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def load_env(path: str | Path | None = None, reload: bool = False) -> dict[str, str]:
    """`.env`를 찾아 파싱한다. 반환: 파일에서 읽은 항목(값 포함, 로그 금지).

    프로세스당 한 번만 읽는다(`reload=True`로 강제 재적재 — 테스트·경로 변경용).
    **os.environ에 쓰지 않는다** — 값은 이 모듈 안에만 두고, 자식 프로세스로 새지 않는다.
    파일을 못 찾으면 빈 dict이고, 조회 시점에 예외로 드러난다(조용한 폴백 금지).
    """
    global _env, _source, _is_loaded
    if path is not None:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f".env 파일이 없습니다: {p}")
        _env, _source, _is_loaded = _parse_env_file(p), p.resolve(), True
        return dict(_env)
    if _is_loaded and not reload:
        return dict(_env)
    for p in _candidates():
        if p.is_file():
            _env, _source, _is_loaded = _parse_env_file(p), p.resolve(), True
            return dict(_env)
    _env, _source, _is_loaded = {}, None, True
    return {}


def env_file() -> Path | None:
    """적용된 `.env` 경로(없으면 None). "어느 파일을 읽었나"를 눈으로 확인하는 용도."""
    load_env()
    return _source


def get_env(name: str, default: str | None = None) -> str | None:
    """`.env` 값 조회. **OS 환경변수는 보지 않는다** — 출처는 `.env` 하나뿐이다."""
    load_env()
    val = _env.get(name)
    return val if val not in (None, "") else default


def get_secret(name: str, *fallbacks: str, required: bool = True) -> str:
    """비밀키 조회. 값은 절대 로그·예외 메시지에 넣지 않는다.

    fallbacks는 구 변수명 호환용 — **같은 `.env` 안에서** 정본이 비어 있을 때만 쓰이며,
    쓰이면 1회 안내한다. OS 환경변수로는 폴백하지 않는다(§설계 메모).
    """
    load_env()
    val = (_env.get(name) or "").strip()
    if val:
        return val
    for alt in fallbacks:
        alt_val = (_env.get(alt) or "").strip()
        if alt_val:
            if alt not in _warned:
                _warned.add(alt)
                print(f"[config] 구 변수명 {alt} 사용 중 — .env에서 {name} 로 바꾸세요 "
                      f"(.env.example 참조)")
            return alt_val
    if not required:
        return ""
    where = (f"읽은 .env: {_source}" if _source else
             "읽은 .env 없음 — 탐색 경로: " + " · ".join(str(p) for p in _candidates()))
    raise RuntimeError(
        f"{name} 값이 .env 에 없습니다. .env.example 을 복사해 값을 채우세요 "
        f"({where}). OS 환경변수는 사용하지 않습니다. 키는 커밋 금지(§7)."
    )


def get_hcx_api_key(required: bool = True) -> str:
    """CLOVA Studio(HCX) API 키 — Stage B 전용."""
    return get_secret(ENV_HCX_KEY, *LEGACY_HCX_KEYS, required=required)


def get_kosis_api_key(required: bool = True) -> str:
    """KOSIS Open API 키 — 2~5번 Task용. 1번 파이프라인은 사용하지 않는다."""
    return get_secret(ENV_KOSIS_KEY, required=required)


# --- 경로 ---------------------------------------------------------------------
def data_dir() -> Path:
    """산출물 기본 디렉터리(`data/`). 저장소 루트 기준 — 실행 위치에 의존하지 않는다."""
    return Path(get_env("DATA_DIR") or (REPO_ROOT / "data"))


def cache_dir() -> Path:
    """record-replay 캐시 디렉터리(`cache/`)."""
    return Path(get_env("CACHE_DIR") or (REPO_ROOT / "cache"))


def part1_dir() -> Path:
    """골든셋·원본 데이터 폴더. **평가/개발 전용**이며 운영 파이프라인은 참조하지 않는다.

    저장소에 포함되지 않는 자료(기사 원문·골든셋)라 기본값은 개인 경로일 수밖에 없다.
    다른 사람은 `.env`의 PART1_DIR로 자기 경로를 지정한다.
    """
    return Path(get_env(ENV_PART1_DIR) or "D:/part1")


# --- CLOVA Studio(HCX) / KOSIS 엔드포인트 (config05.py 통합, 2026-08-14) -------
# client.py 전용. 원래 config05.py는 python-dotenv로 별도 .env를 다시 로드하고
# 모듈 최상단에서 os.getenv()를 한 번만 호출해 값을 굳혔다 — 이 파일의 "값의
# 출처는 .env 파일 하나뿐" 원칙과 어긋나고(§설계 메모), .env를 나중에 고쳐도
# 반영이 안 된다. 여기서는 이 모듈의 get_hcx_api_key()/get_kosis_api_key()를
# 매 접근마다 다시 불러 그 문제를 없앤다.
HCX_GENERATION_MODEL = "HCX-007"
HCX_EMBEDDING_API_VERSION = "v2"  # 임베딩은 별도 모델명이 아닌 v2 API 엔드포인트 사용
HCX_BASE_URL = "https://clovastudio.stream.ntruss.com"
HCX_REQUEST_ID_HEADER = "X-NCP-CLOVASTUDIO-REQUEST-ID"

KOSIS_SEARCH_URL = "https://kosis.kr/openapi/statisticsSearch.do"
KOSIS_DATA_URL = "https://kosis.kr/openapi/Param/statisticsParameterData.do"


class _ClientConfig:
    """client.py가 기존 `config.NCP_CLOVASTUDIO_API_KEY` 같은 속성 접근을 그대로
    쓸 수 있게 하는 얇은 래퍼(예전 config05.py의 `Config` 클래스 대체). 키는
    property로 두어 매번 get_env 경로를 타게 한다 — import 시점에 굳히지 않는다.
    """

    @property
    def NCP_CLOVASTUDIO_API_KEY(self) -> str:
        return get_hcx_api_key(required=False)

    @property
    def KOSIS_API_KEY(self) -> str:
        return get_kosis_api_key(required=False)

    HCX_GENERATION_MODEL = HCX_GENERATION_MODEL
    HCX_EMBEDDING_API_VERSION = HCX_EMBEDDING_API_VERSION
    HCX_BASE_URL = HCX_BASE_URL
    HCX_REQUEST_ID_HEADER = HCX_REQUEST_ID_HEADER
    KOSIS_SEARCH_URL = KOSIS_SEARCH_URL
    KOSIS_DATA_URL = KOSIS_DATA_URL

    def hcx_headers(self, request_id: str | None = None, stream: bool = False) -> dict:
        """CLOVA Studio API 호출용 공통 요청 헤더 생성(config05.py에서 이전, 현재
        호출부 없음 — client.py는 자체적으로 헤더를 만든다. 이후를 위해 보존)."""
        headers = {
            "Authorization": f"Bearer {self.NCP_CLOVASTUDIO_API_KEY}",
            "Content-Type": "application/json",
        }
        if request_id:
            headers[self.HCX_REQUEST_ID_HEADER] = request_id
        if stream:
            headers["Accept"] = "text/event-stream"
        return headers


# client.py가 `from config import config` 후 `config.NCP_CLOVASTUDIO_API_KEY`
# 식으로 그대로 쓸 수 있도록 하는 싱글톤(예전 config05.py의 `config = Config()`).
config = _ClientConfig()
