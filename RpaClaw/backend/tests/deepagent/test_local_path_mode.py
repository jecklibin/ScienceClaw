import unittest

from backend.deepagent.local_path_mode import (
    adapt_local_backend_for_platform,
    local_mode_uses_windows_paths,
)


class _StubBackend:
    pass


class LocalPathModeTest(unittest.TestCase):
    def test_uses_windows_paths_only_for_local_windows_mode(self) -> None:
        self.assertTrue(local_mode_uses_windows_paths("local", system_name="Windows"))
        self.assertFalse(local_mode_uses_windows_paths("local", system_name="Darwin"))
        self.assertFalse(local_mode_uses_windows_paths("docker", system_name="Windows"))

    def test_adapt_local_backend_passthroughs_on_non_windows(self) -> None:
        backend = _StubBackend()
        passthrough = adapt_local_backend_for_platform(
            backend,
            storage_backend="local",
            system_name="Darwin",
        )

        self.assertIs(passthrough, backend)


if __name__ == "__main__":
    unittest.main()
