import unittest

from backend.deepagent.windows_filesystem_middleware import validate_local_tool_path


class WindowsFilesystemMiddlewareTest(unittest.TestCase):
    def test_validate_local_tool_path_accepts_backslash_windows_path(self) -> None:
        self.assertEqual(validate_local_tool_path(r"D:\work\demo.txt"), "D:/work/demo.txt")

    def test_validate_local_tool_path_accepts_forward_slash_windows_path(self) -> None:
        self.assertEqual(validate_local_tool_path("D:/work/demo.txt"), "D:/work/demo.txt")


if __name__ == "__main__":
    unittest.main()
