from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


class Cache:
    def __init__(self, root: Path, enabled: bool = True) -> None:
        self.root = root
        self.enabled = enabled

    @staticmethod
    def key(*parts: Any) -> str:
        payload = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _path(self, namespace: str, key: str) -> Path:
        return self.root / "cache" / namespace / f"{key}.json"

    def get(self, namespace: str, key: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        path = self._path(namespace, key)
        if not path.is_file():
            return None
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return loaded if isinstance(loaded, dict) else None

    def put(self, namespace: str, key: str, value: dict[str, Any]) -> None:
        if not self.enabled:
            return
        path = self._path(namespace, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)

    def append_log(self, name: str, record: dict[str, Any]) -> None:
        path = self.root / f"{name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
