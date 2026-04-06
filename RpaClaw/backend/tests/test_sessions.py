import importlib.util
import unittest
import tempfile
import shutil
from pathlib import Path
from pathlib import Path as _Path
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


class TestListSkillFilesFiltering(unittest.TestCase):
    """Test that list_skill_files applies the filter correctly."""

    def setUp(self):
        """Create a temporary skill directory with test files."""
        self.temp_dir = tempfile.mkdtemp()
        self.skill_dir = _Path(self.temp_dir) / "test_skill"
        self.skill_dir.mkdir()

        # Create normal files
        (self.skill_dir / "SKILL.md").write_text("# Test Skill")
        (self.skill_dir / "skill.py").write_text("def run(): pass")
        (self.skill_dir / "params.json").write_text("{}")

        # Create files that should be filtered
        pycache_dir = self.skill_dir / "__pycache__"
        pycache_dir.mkdir()
        (pycache_dir / "skill.cpython-313.pyc").write_bytes(b"fake bytecode")
        (self.skill_dir / "module.pyc").write_text("fake")
        (self.skill_dir / ".DS_Store").write_text("fake")
        (self.skill_dir / "Thumbs.db").write_text("fake")
        (self.skill_dir / ".gitignore").write_text("*.pyc")

        vscode_dir = self.skill_dir / ".vscode"
        vscode_dir.mkdir()
        (vscode_dir / "settings.json").write_text("{}")

    def tearDown(self):
        """Clean up temporary directory."""
        shutil.rmtree(self.temp_dir)

    def test_filtered_file_list(self):
        """Test that rglob with filter returns only normal files."""
        should_skip_file = SESSIONS_MODULE.should_skip_file

        items = []
        for file_path in sorted(self.skill_dir.rglob("*")):
            if should_skip_file(file_path):
                continue
            if file_path.is_file():
                rel_path = str(file_path.relative_to(self.skill_dir))
                items.append({
                    "name": file_path.name,
                    "path": rel_path,
                    "type": "file",
                })

        # Debug: print what we got
        if len(items) != 3:
            print(f"Expected 3 files, got {len(items)}: {[item['name'] for item in items]}")

        # Should only have 3 normal files
        self.assertEqual(len(items), 3)

        # Extract just the names for easier assertion
        names = {item["name"] for item in items}
        self.assertEqual(names, {"SKILL.md", "skill.py", "params.json"})

        # Verify filtered files are NOT in the list
        self.assertNotIn("skill.cpython-313.pyc", names)
        self.assertNotIn("module.pyc", names)
        self.assertNotIn(".DS_Store", names)
        self.assertNotIn("Thumbs.db", names)
        self.assertNotIn(".gitignore", names)
        self.assertNotIn("settings.json", names)


if __name__ == "__main__":
    unittest.main()
