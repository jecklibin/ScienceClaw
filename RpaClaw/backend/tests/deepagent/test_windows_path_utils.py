import unittest

from backend.deepagent.windows_path_utils import canonicalize_local_agent_path


class WindowsPathUtilsTest(unittest.TestCase):
    def test_accepts_windows_backslash_absolute_path(self) -> None:
        self.assertEqual(canonicalize_local_agent_path(r"D:\work\foo.txt"), "D:/work/foo.txt")

    def test_accepts_windows_forward_slash_absolute_path(self) -> None:
        self.assertEqual(canonicalize_local_agent_path("D:/work/foo.txt"), "D:/work/foo.txt")

    def test_rejects_relative_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "absolute"):
            canonicalize_local_agent_path("foo/bar.txt")

    def test_rejects_parent_traversal(self) -> None:
        with self.assertRaisesRegex(ValueError, "traversal"):
            canonicalize_local_agent_path(r"D:\work\..\secret.txt")


if __name__ == "__main__":
    unittest.main()
