# -*- coding: utf-8 -*-
"""P3 record-replay 캐시 (§5.6) — LLM 호출의 녹화·재생.

키 = (prompt_version, model, params 해시, payload 해시) — 프롬프트 버전이 키에 승격돼
버전별 replay 코퍼스가 분리 보관된다(리뷰: 전문 해시는 공백 변경에도 전부 무효).
수리(repair) 시퀀스는 원 payload 키 아래 대화 체인째 저장해 재생 시 통째로 재현한다.

용도 ①중단-재개: 실행이 끊겨도 성공분은 캐시에서 재생돼 재실행이 사실상 재개가 된다
    ②회귀 테스트: 동결된 LLM 원출력을 입력으로 Stage C 이후 룰만 재검증(HCX 0콜)
    ③비용: dev 튜닝 반복에서 동일 프롬프트 재호출 차단.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


class ReplayCache:
    """키에는 프롬프트 **버전 라벨**만 들어가고 본문은 들어가지 않는다.

    그래서 프롬프트를 고치고 `PROMPT_VERSION`을 안 올리면 옛 응답이 조용히 재생된다 —
    실험이 거짓말을 하게 되는 경로다. 막을 수 없으니(본문을 키에 넣으면 공백 한 칸에도
    코퍼스가 통째로 무효) **본문 해시를 행에 같이 적고 로드 시 불일치를 경고**한다.
    """

    def __init__(self, path: Path | str, prompt_version: str, model: str,
                 params: dict | None = None, prompt_text: str | None = None):
        self.path = Path(path)
        self.meta = {"prompt_version": prompt_version, "model": model,
                     "params": params or {}}
        self.prompt_sha = (hashlib.sha1(prompt_text.encode("utf-8")).hexdigest()[:8]
                           if prompt_text is not None else None)
        self._index: dict[str, dict] = {}
        drifted = set()
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    row = json.loads(line)
                    if row.get("meta") == self.meta:   # 다른 버전 행은 무시(파일 공유 허용)
                        self._index[row["key"]] = row
                        sha = row.get("prompt_sha")
                        if self.prompt_sha and sha and sha != self.prompt_sha:
                            drifted.add(sha)
        if drifted:
            print(f"[cache] ⚠ 프롬프트 본문이 바뀌었는데 버전은 그대로다 "
                  f"({self.meta['prompt_version']}: 캐시 {sorted(drifted)} ≠ 현재 {self.prompt_sha}). "
                  f"의도한 변경이면 PROMPT_VERSION을 올리세요 — 안 올리면 옛 응답이 재생됩니다.")

    def key(self, payload: str) -> str:
        blob = json.dumps({"meta": self.meta, "payload": payload},
                          ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()

    def get(self, payload: str) -> dict | None:
        """→ {response, chain} | None. response = 최종(파싱 대상) 응답 텍스트."""
        return self._index.get(self.key(payload))

    def put(self, payload: str, response: str, chain: list | None = None) -> None:
        row = {"key": self.key(payload), "meta": self.meta,
               "response": response, "chain": chain or []}
        if self.prompt_sha:
            row["prompt_sha"] = self.prompt_sha
        self._index[row["key"]] = row
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def __len__(self) -> int:
        return len(self._index)
