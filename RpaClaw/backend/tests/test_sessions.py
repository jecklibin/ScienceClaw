import importlib.util
import unittest
from pathlib import Path
from unittest.mock import MagicMock


# Load sessions module directly to avoid import issues
SESSIONS_PATH = Path(__file__).resolve().parents[1] / "route" / "sessions.py"
SPEC = importlib.util.spec_from_file_location("sessions_module", SESSIONS_PATH)
SESSIONS_MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(SESSIONS_MODULE)


class TestShouldSkipFile(unittest.TestCase):
    """Test the should_skip_file helper function."""

    def test_skip_pycache_directory(self):
        """Should skip __pycache__ directories."""
        should_skip_file = SESSIONS_MODULE.should_skip_file
        path = Path("/some/skill/__pycache__")
        self.assertTrue(should_skip_file(path))

    def test_skip_pyc_files(self):
        """Should skip .pyc bytecode files."""
        should_skip_file = SESSIONS_MODULE.should_skip_file
        path = Path("/some/skill/module.pyc")
        self.assertTrue(should_skip_file(path))

    def test_skip_pyo_files(self):
        """Should skip .pyo bytecode files."""
        should_skip_file = SESSIONS_MODULE.should_skip_file
        path = Path("/some/skill/module.pyo")
        self.assertTrue(should_skip_file(path))

    def test_skip_pyd_files(self):
        """Should skip .pyd bytecode files."""
        should_skip_file = SESSIONS_MODULE.should_skip_file
        path = Path("/some/skill/module.pyd")
        self.assertTrue(should_skip_file(path))

    def test_skip_ds_store(self):
        """Should skip macOS .DS_Store files."""
        should_skip_file = SESSIONS_MODULE.should_skip_file
        path = Path("/some/skill/.DS_Store")
        self.assertTrue(should_skip_file(path))

    def test_skip_thumbs_db(self):
        """Should skip Windows Thumbs.db files."""
        should_skip_file = SESSIONS_MODULE.should_skip_file
        path = Path("/some/skill/Thumbs.db")
        self.assertTrue(should_skip_file(path))

    def test_skip_desktop_ini(self):
        """Should skip Windows desktop.ini files."""
        should_skip_file = SESSIONS_MODULE.should_skip_file
        path = Path("/some/skill/desktop.ini")
        self.assertTrue(should_skip_file(path))

    def test_skip_gitignore(self):
        """Should skip .gitignore files."""
        should_skip_file = SESSIONS_MODULE.should_skip_file
        path = Path("/some/skill/.gitignore")
        self.assertTrue(should_skip_file(path))

    def test_skip_git_directory(self):
        """Should skip .git directories."""
        should_skip_file = SESSIONS_MODULE.should_skip_file
        path = Path("/some/skill/.git")
        self.assertTrue(should_skip_file(path))

    def test_skip_svn_directory(self):
        """Should skip .svn directories."""
        should_skip_file = SESSIONS_MODULE.should_skip_file
        path = Path("/some/skill/.svn")
        self.assertTrue(should_skip_file(path))

    def test_skip_vscode_directory(self):
        """Should skip .vscode directories."""
        should_skip_file = SESSIONS_MODULE.should_skip_file
        path = Path("/some/skill/.vscode")
        self.assertTrue(should_skip_file(path))

    def test_skip_idea_directory(self):
        """Should skip .idea directories."""
        should_skip_file = SESSIONS_MODULE.should_skip_file
        path = Path("/some/skill/.idea")
        self.assertTrue(should_skip_file(path))

    def test_skip_vs_directory(self):
        """Should skip .vs directories."""
        should_skip_file = SESSIONS_MODULE.should_skip_file
        path = Path("/some/skill/.vs")
        self.assertTrue(should_skip_file(path))

    def test_allow_normal_python_file(self):
        """Should NOT skip normal .py files."""
        should_skip_file = SESSIONS_MODULE.should_skip_file
        path = Path("/some/skill/skill.py")
        self.assertFalse(should_skip_file(path))

    def test_allow_skill_md(self):
        """Should NOT skip SKILL.md files."""
        should_skip_file = SESSIONS_MODULE.should_skip_file
        path = Path("/some/skill/SKILL.md")
        self.assertFalse(should_skip_file(path))

    def test_allow_params_json(self):
        """Should NOT skip params.json files."""
        should_skip_file = SESSIONS_MODULE.should_skip_file
        path = Path("/some/skill/params.json")
        self.assertFalse(should_skip_file(path))

    def test_allow_normal_directory(self):
        """Should NOT skip normal directories."""
        should_skip_file = SESSIONS_MODULE.should_skip_file
        path = Path("/some/skill/utils")
        self.assertFalse(should_skip_file(path))


if __name__ == "__main__":
    unittest.main()
