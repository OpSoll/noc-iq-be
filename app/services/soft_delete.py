from datetime import datetime, timezone
from typing import Optional, Dict, Any, List


class SoftDeleteMixin:
    """
    Mixin providing soft-delete capabilities for in-memory stores.
    Records with a non-null `deleted_at` are considered soft-deleted
    and excluded from queries by default.
    """

    def soft_delete(self, record_id: str) -> Optional[Any]:
        record = self._data.get(record_id)
        if record is None:
            return None
        updated = record.model_copy(update={"deleted_at": datetime.now(timezone.utc)})
        self._data[record_id] = updated
        return updated

    def restore(self, record_id: str) -> Optional[Any]:
        record = self._data.get(record_id)
        if record is None:
            return None
        updated = record.model_copy(update={"deleted_at": None})
        self._data[record_id] = updated
        return updated

    def hard_delete(self, record_id: str) -> bool:
        if record_id in self._data:
            del self._data[record_id]
            return True
        return False

    def _is_deleted(self, record: Any) -> bool:
        return getattr(record, "deleted_at", None) is not None

    def _filter_deleted(self, items: List[Any], include_deleted: bool = False) -> List[Any]:
        if include_deleted:
            return items
        return [item for item in items if not self._is_deleted(item)]

    def list_deleted(self) -> List[Any]:
        return [item for item in self._data.values() if self._is_deleted(item)]
