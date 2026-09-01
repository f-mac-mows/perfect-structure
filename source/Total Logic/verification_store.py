from __future__ import annotations

import json
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional


STORE_PATH = Path(__file__).resolve().parent / "data" / "verification_records.json"
_lock = threading.RLock()


def _read() -> list[dict[str, Any]]:
    if not STORE_PATH.is_file():
        return []
    try:
        payload = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return payload if isinstance(payload, list) else []


def _write(records: list[dict[str, Any]]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = STORE_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(STORE_PATH)


def list_records() -> list[dict[str, Any]]:
    with _lock:
        return deepcopy(_read())


def get_record(article_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        return next((deepcopy(item) for item in _read() if item.get("article_id") == article_id), None)


def save_record(record: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        records = _read()
        index = next((i for i, item in enumerate(records) if item.get("article_id") == record.get("article_id")), None)
        if index is None:
            records.append(deepcopy(record))
        else:
            records[index] = deepcopy(record)
        _write(records)
        return deepcopy(record)
