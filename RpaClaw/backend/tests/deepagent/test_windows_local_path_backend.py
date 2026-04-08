import unittest

from backend.deepagent.windows_local_path_backend import WindowsLocalPathBackend


class StubBackend:
    def __init__(self) -> None:
        self.last_path = None

    def read(self, file_path: str, offset: int = 0, limit: int = 100):
        self.last_path = file_path
        return "ok"

    def ls_info(self, path: str):
        self.last_path = path
        return [{"path": r"D:\work\demo.txt", "is_dir": False, "size": 1, "modified_at": ""}]


class WindowsLocalPathBackendTest(unittest.TestCase):
    def test_backend_normalizes_incoming_read_path(self) -> None:
        backend = WindowsLocalPathBackend(StubBackend())
        backend.read(r"D:\work\demo.txt")
        self.assertEqual(backend._inner.last_path, "D:/work/demo.txt")

    def test_backend_normalizes_outgoing_ls_paths(self) -> None:
        backend = WindowsLocalPathBackend(StubBackend())
        result = backend.ls_info(r"D:\work")
        self.assertEqual(result[0]["path"], "D:/work/demo.txt")


if __name__ == "__main__":
    unittest.main()
