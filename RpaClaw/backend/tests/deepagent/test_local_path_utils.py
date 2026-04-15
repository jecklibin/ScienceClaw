import unittest

from backend.deepagent.local_path_utils import canonicalize_local_agent_path


class LocalPathUtilsTest(unittest.TestCase):
    def test_accepts_posix_absolute_path(self) -> None:
        self.assertEqual(
            canonicalize_local_agent_path("/workspace/project/file.txt", path_style="posix"),
            "/workspace/project/file.txt",
        )

    def test_accepts_windows_backslash_absolute_path(self) -> None:
        self.assertEqual(canonicalize_local_agent_path(r"D:\work\foo.txt", path_style="windows"), "D:/work/foo.txt")

    def test_accepts_windows_forward_slash_absolute_path(self) -> None:
        self.assertEqual(canonicalize_local_agent_path("D:/work/foo.txt", path_style="windows"), "D:/work/foo.txt")

    def test_rejects_relative_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "absolute"):
            canonicalize_local_agent_path("foo/bar.txt", path_style="windows")

    def test_rejects_parent_traversal(self) -> None:
        with self.assertRaisesRegex(ValueError, "traversal"):
            canonicalize_local_agent_path(r"D:\work\..\secret.txt", path_style="windows")

    def test_rejects_posix_parent_traversal(self) -> None:
        with self.assertRaisesRegex(ValueError, "traversal"):
            canonicalize_local_agent_path("/workspace/../secret.txt", path_style="posix")

    def test_windows_mode_rejects_posix_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "Windows absolute path"):
            canonicalize_local_agent_path("/workspace/project/file.txt", path_style="windows")

    def test_posix_mode_rejects_windows_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "absolute path"):
            canonicalize_local_agent_path(r"D:\work\foo.txt", path_style="posix")


if __name__ == "__main__":
    unittest.main()
