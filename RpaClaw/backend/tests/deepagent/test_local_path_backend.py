import unittest
from unittest.mock import patch

from backend.deepagent.local_path_backend import LocalPathBackend


class StubBackend:
    def __init__(self) -> None:
        self.last_path = None

    def read(self, file_path: str, offset: int = 0, limit: int = 100):
        self.last_path = file_path
        return "ok"

    def ls_info(self, path: str):
        self.last_path = path
        return [{"path": r"D:\work\demo.txt", "is_dir": False, "size": 1, "modified_at": ""}]


class LocalPathBackendTest(unittest.TestCase):
    def test_backend_preserves_posix_read_path(self) -> None:
        with patch("backend.deepagent.local_path_backend.settings.local_path_style", "posix"):
            backend = LocalPathBackend(StubBackend())
            backend.read("/workspace/demo.txt")
            self.assertEqual(backend._inner.last_path, "/workspace/demo.txt")

    def test_backend_normalizes_incoming_read_path(self) -> None:
        with patch("backend.deepagent.local_path_backend.settings.local_path_style", "windows"):
            backend = LocalPathBackend(StubBackend())
            backend.read(r"D:\work\demo.txt")
            self.assertEqual(backend._inner.last_path, "D:/work/demo.txt")

    def test_backend_normalizes_outgoing_ls_paths(self) -> None:
        with patch("backend.deepagent.local_path_backend.settings.local_path_style", "windows"):
            backend = LocalPathBackend(StubBackend())
            result = backend.ls_info(r"D:\work")
            self.assertEqual(result[0]["path"], "D:/work/demo.txt")

    def test_backend_rejects_path_with_wrong_style(self) -> None:
        with patch("backend.deepagent.local_path_backend.settings.local_path_style", "windows"):
            backend = LocalPathBackend(StubBackend())
            with self.assertRaisesRegex(ValueError, "Windows absolute path"):
                backend.read("/workspace/demo.txt")


if __name__ == "__main__":
    unittest.main()
