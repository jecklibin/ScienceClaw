"""FileRepository — JSON-file-backed storage with in-memory index."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from backend.config import settings
from backend.storage.base import Repository
from backend.storage.local.query_engine import (
    apply_projection,
    apply_update,
    match_filter,
)


class FileRepository(Repository):
    """Each collection is a directory; each document is {_id}.json."""

    def __init__(self, collection_name: str):
        super().__init__(collection_name)
        self._dir = Path(settings.local_data_dir) / collection_name
        self._data: dict[str, dict] = {}
        self._loaded = False

    async def load(self) -> None:
        """Scan directory and load all JSON files into memory."""
        if self._loaded:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        for f in self._dir.glob("*.json"):
            try:
                raw = f.read_text(encoding="utf-8")
                doc = json.loads(raw)
                _id = doc.get("_id", f.stem)
                doc["_id"] = _id
                self._data[str(_id)] = doc
            except Exception as exc:
                logger.warning(f"Failed to load {f}: {exc}")
        self._loaded = True
        logger.info(
            f"[FileRepo] Loaded {len(self._data)} docs for '{self.collection_name}'"
        )

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._dir.mkdir(parents=True, exist_ok=True)
            for f in self._dir.glob("*.json"):
                try:
                    raw = f.read_text(encoding="utf-8")
                    doc = json.loads(raw)
                    _id = doc.get("_id", f.stem)
                    doc["_id"] = _id
                    self._data[str(_id)] = doc
                except Exception:
                    pass
            self._loaded = True

    def _safe_filename(self, _id: str) -> str:
        return _id.replace("/", "%2F").replace("\\", "%5C")

    def _write_doc(self, doc: dict) -> None:
        _id = str(doc["_id"])
        fname = self._safe_filename(_id)
        target = self._dir / f"{fname}.json"
        tmp = self._dir / f"{fname}.tmp"
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps(doc, ensure_ascii=False, default=str), encoding="utf-8"
        )
        tmp.replace(target)

    @staticmethod
    def _normalize_sort_value(value):
        if value is None:
            return (1, "", "")

        if isinstance(value, bool):
            return (0, "bool", int(value))

        if isinstance(value, (int, float)):
            return (0, "number", value)

        if isinstance(value, datetime):
            return (0, "datetime", value.isoformat())

        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                return (0, "string", value)
            return (0, "datetime", parsed.isoformat())

        return (0, value.__class__.__name__, str(value))

    def _delete_doc(self, _id: str) -> None:
        fname = self._safe_filename(_id)
        target = self._dir / f"{fname}.json"
        target.unlink(missing_ok=True)

    async def find_one(
        self, filter: dict, projection: dict | None = None
    ) -> Optional[dict]:
        self._ensure_loaded()
        for doc in self._data.values():
            if match_filter(doc, filter):
                return apply_projection(doc, projection)
        return None

    async def find_many(
        self,
        filter: dict,
        projection: dict | None = None,
        sort: list[tuple[str, int]] | None = None,
        skip: int = 0,
        limit: int = 0,
    ) -> list[dict]:
        self._ensure_loaded()
        results = [doc for doc in self._data.values() if match_filter(doc, filter)]
        if sort:
            for key, direction in reversed(sort):
                results.sort(
                    key=lambda d, k=key: self._normalize_sort_value(d.get(k)),
                    reverse=(direction == -1),
                )
        if skip:
            results = results[skip:]
        if limit:
            results = results[:limit]
        if projection:
            results = [apply_projection(doc, projection) for doc in results]
        return results

    async def insert_one(self, document: dict) -> str:
        self._ensure_loaded()
        if "_id" not in document:
            document["_id"] = str(uuid.uuid4())
        _id = str(document["_id"])
        self._write_doc(document)
        self._data[_id] = document
        return _id

    async def update_one(
        self, filter: dict, update: dict, upsert: bool = False
    ) -> int:
        self._ensure_loaded()
        for _id, doc in self._data.items():
            if match_filter(doc, filter):
                updated = apply_update(doc, update)
                self._data[_id] = updated
                self._write_doc(updated)
                return 1
        if upsert:
            new_doc = {}
            for k, v in filter.items():
                if not k.startswith("$") and not isinstance(v, dict):
                    new_doc[k] = v
            if "_id" not in new_doc:
                new_doc["_id"] = str(uuid.uuid4())
            new_doc = apply_update(new_doc, update, is_upsert_insert=True)
            _id = str(new_doc["_id"])
            self._data[_id] = new_doc
            self._write_doc(new_doc)
            return 1
        return 0

    async def update_many(self, filter: dict, update: dict) -> int:
        self._ensure_loaded()
        count = 0
        for _id, doc in list(self._data.items()):
            if match_filter(doc, filter):
                updated = apply_update(doc, update)
                self._data[_id] = updated
                self._write_doc(updated)
                count += 1
        return count

    async def delete_one(self, filter: dict) -> int:
        self._ensure_loaded()
        for _id, doc in list(self._data.items()):
            if match_filter(doc, filter):
                del self._data[_id]
                self._delete_doc(_id)
                return 1
        return 0

    async def delete_many(self, filter: dict) -> int:
        self._ensure_loaded()
        to_delete = [
            _id for _id, doc in self._data.items() if match_filter(doc, filter)
        ]
        for _id in to_delete:
            del self._data[_id]
            self._delete_doc(_id)
        return len(to_delete)

    async def count(self, filter: dict) -> int:
        self._ensure_loaded()
        return sum(1 for doc in self._data.values() if match_filter(doc, filter))
