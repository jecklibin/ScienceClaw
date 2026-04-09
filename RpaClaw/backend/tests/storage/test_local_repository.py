import tempfile
import unittest
from datetime import datetime

from backend.config import settings
from backend.storage.local.repository import FileRepository


class FileRepositorySortTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._original_local_data_dir = settings.local_data_dir
        settings.local_data_dir = self._tmpdir.name
        self.addCleanup(self._restore_local_data_dir)

    def _restore_local_data_dir(self) -> None:
        settings.local_data_dir = self._original_local_data_dir

    async def test_find_many_sorts_mixed_datetime_and_string_values(self):
        repo = FileRepository("credentials")
        await repo.insert_one(
            {
                "_id": "old",
                "user_id": "user-1",
                "created_at": datetime(2024, 1, 1, 9, 0, 0),
            }
        )

        reloaded_repo = FileRepository("credentials")
        await reloaded_repo.insert_one(
            {
                "_id": "new",
                "user_id": "user-1",
                "created_at": datetime(2024, 1, 2, 9, 0, 0),
            }
        )

        docs = await reloaded_repo.find_many(
            {"user_id": "user-1"},
            sort=[("created_at", -1)],
        )

        self.assertEqual([doc["_id"] for doc in docs], ["new", "old"])
