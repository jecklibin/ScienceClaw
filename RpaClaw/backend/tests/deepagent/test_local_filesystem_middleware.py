import unittest
from unittest.mock import patch

from backend.deepagent.local_filesystem_middleware import validate_local_tool_path


class LocalFilesystemMiddlewareTest(unittest.TestCase):
    def test_validate_local_tool_path_accepts_posix_absolute_path(self) -> None:
        with patch("backend.deepagent.local_filesystem_middleware.settings.local_path_style", "posix"):
            self.assertEqual(validate_local_tool_path("/workspace/demo.txt"), "/workspace/demo.txt")

    def test_validate_local_tool_path_accepts_backslash_windows_path(self) -> None:
        with patch("backend.deepagent.local_filesystem_middleware.settings.local_path_style", "windows"):
            self.assertEqual(validate_local_tool_path(r"D:\work\demo.txt"), "D:/work/demo.txt")

    def test_validate_local_tool_path_accepts_forward_slash_windows_path(self) -> None:
        with patch("backend.deepagent.local_filesystem_middleware.settings.local_path_style", "windows"):
            self.assertEqual(validate_local_tool_path("D:/work/demo.txt"), "D:/work/demo.txt")

    def test_validate_local_tool_path_rejects_wrong_style(self) -> None:
        with patch("backend.deepagent.local_filesystem_middleware.settings.local_path_style", "windows"):
            with self.assertRaisesRegex(ValueError, "Windows absolute path"):
                validate_local_tool_path("/workspace/demo.txt")


if __name__ == "__main__":
    unittest.main()
