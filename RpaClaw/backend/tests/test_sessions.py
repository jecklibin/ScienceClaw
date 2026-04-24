import importlib.util
import json
import unittest
import tempfile
import shutil
from pathlib import Path
from pathlib import Path as _Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock


# Load sessions module directly to avoid import issues
SESSIONS_PATH = Path(__file__).resolve().parents[1] / "route" / "sessions.py"
SPEC = importlib.util.spec_from_file_location("sessions_module", SESSIONS_PATH)
SESSIONS_MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(SESSIONS_MODULE)
SESSIONS_MODULE.Any = Any
SESSIONS_MODULE.ApiResponse.model_rebuild(_types_namespace={"Any": Any})


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

    def test_extract_tool_meta_preserves_nested_mcp_metadata(self):
        """Should keep nested MCP metadata alongside the base tool fields."""
        extract_tool_meta = SESSIONS_MODULE._extract_tool_meta
        payload = {
            "tool_meta": {
                "icon": "🔧",
                "category": "execution",
                "description": "PubMed search",
                "sandbox": True,
                "mcp": {
                    "source": "mcp",
                    "server_id": "pubmed",
                    "nested": {"tool": "search"},
                },
            }
        }

        self.assertEqual(
            extract_tool_meta(payload),
            {
                "icon": "🔧",
                "category": "execution",
                "description": "PubMed search",
                "sandbox": True,
                "mcp": {
                    "source": "mcp",
                    "server_id": "pubmed",
                    "nested": {"tool": "search"},
                },
            },
        )


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


class TestListSkillDirs(unittest.TestCase):
    """Test that _list_skill_dirs applies the filter correctly."""

    def setUp(self):
        """Create a temporary skill directory."""
        self.temp_dir = tempfile.mkdtemp()
        self.skill_dir = _Path(self.temp_dir) / "test_skill"
        self.skill_dir.mkdir()

        # Create SKILL.md with frontmatter
        (self.skill_dir / "SKILL.md").write_text("""---
name: test_skill
description: Test skill
---
# Test Skill""")
        (self.skill_dir / "skill.py").write_text("def run(): pass")
        (self.skill_dir / "params.json").write_text("{}")

        # Create files that should be filtered
        pycache_dir = self.skill_dir / "__pycache__"
        pycache_dir.mkdir()
        (pycache_dir / "skill.cpython-313.pyc").write_bytes(b"fake")
        (self.skill_dir / "module.pyc").write_text("fake")

    def tearDown(self):
        """Clean up temporary directory."""
        shutil.rmtree(self.temp_dir)

    def test_list_skill_dirs_filters_files(self):
        """Test that _list_skill_dirs filters cache files."""
        _list_skill_dirs = SESSIONS_MODULE._list_skill_dirs

        skills = _list_skill_dirs(self.temp_dir, builtin=False)

        self.assertEqual(len(skills), 1)
        skill = skills[0]

        # Should only have 3 normal files
        self.assertEqual(len(skill["files"]), 3)

        file_names = [_Path(f).name for f in skill["files"]]
        self.assertIn("SKILL.md", file_names)
        self.assertIn("skill.py", file_names)
        self.assertIn("params.json", file_names)

        # Verify filtered files are NOT in the list
        self.assertNotIn("skill.cpython-313.pyc", file_names)
        self.assertNotIn("module.pyc", file_names)


class TestSaveToolFromSession(unittest.IsolatedAsyncioTestCase):
    """Regression coverage for configured tools_dir writes."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_root = _Path(self.temp_dir) / "workspace"
        self.tools_root = _Path(self.temp_dir) / "configured-tools"
        self.session_id = "session-123"
        self.tool_name = "demo_tool"
        staging_dir = self.workspace_root / self.session_id / "tools_staging"
        staging_dir.mkdir(parents=True)
        (staging_dir / f"{self.tool_name}.py").write_text(
            '@tool\ndef demo_tool():\n    """demo"""\n    return "ok"\n',
            encoding="utf-8",
        )

        self.original_workspace_dir = getattr(SESSIONS_MODULE, "_WORKSPACE_DIR", None)
        self.original_tools_dir = SESSIONS_MODULE.settings.tools_dir
        self.original_get_session = SESSIONS_MODULE.async_get_science_session

        SESSIONS_MODULE._WORKSPACE_DIR = str(self.workspace_root)
        SESSIONS_MODULE.settings.tools_dir = str(self.tools_root)
        SESSIONS_MODULE.async_get_science_session = AsyncMock(
            return_value=SimpleNamespace(user_id="user-1")
        )

    def tearDown(self):
        SESSIONS_MODULE.async_get_science_session = self.original_get_session
        SESSIONS_MODULE.settings.tools_dir = self.original_tools_dir
        if self.original_workspace_dir is not None:
            SESSIONS_MODULE._WORKSPACE_DIR = self.original_workspace_dir
        shutil.rmtree(self.temp_dir)

    async def test_save_tool_from_session_creates_configured_tools_dir(self):
        body = SESSIONS_MODULE.SaveToolRequest(tool_name=self.tool_name, replaces="")
        current_user = SimpleNamespace(id="user-1")

        response = await SESSIONS_MODULE.save_tool_from_session(
            self.session_id,
            body,
            current_user,
        )

        saved_tool = self.tools_root / f"{self.tool_name}.py"
        self.assertTrue(self.tools_root.is_dir())
        self.assertTrue(saved_tool.is_file())
        self.assertIn("@tool", saved_tool.read_text(encoding="utf-8"))
        self.assertEqual(response.data["tool_name"], self.tool_name)
        self.assertTrue(response.data["saved"])


class TestRecordedSkillDetail(unittest.IsolatedAsyncioTestCase):
    async def test_recorded_skill_detail_reads_skill_meta_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_dir = _Path(temp_dir) / "recorded_skill"
            skill_dir.mkdir()
            (skill_dir / "skill.meta.json").write_text(
                json.dumps({
                    "version": 1,
                    "kind": "rpa-recording",
                    "name": "recorded_skill",
                    "description": "Recorded flow",
                    "entry_script": "skill.py",
                    "generated_at": "2026-04-24T12:00:00+08:00",
                    "params": {},
                    "steps": [{"id": "step_1", "action": "goto"}],
                    "artifacts": ["SKILL.md", "skill.py", "params.json"],
                }),
                encoding="utf-8",
            )
            (skill_dir / "SKILL.md").write_text(
                "---\nname: recorded_skill\ndescription: Recorded flow\n---",
                encoding="utf-8",
            )
            (skill_dir / "skill.py").write_text("print('ok')\n", encoding="utf-8")

            original_backend = SESSIONS_MODULE.settings.storage_backend
            original_dir = SESSIONS_MODULE.settings.external_skills_dir
            SESSIONS_MODULE.settings.storage_backend = "local"
            SESSIONS_MODULE.settings.external_skills_dir = temp_dir
            try:
                response = await SESSIONS_MODULE.get_skill_detail(
                    "recorded_skill",
                    SimpleNamespace(id="user-1"),
                )
            finally:
                SESSIONS_MODULE.settings.storage_backend = original_backend
                SESSIONS_MODULE.settings.external_skills_dir = original_dir

            self.assertEqual(response.data["mode"], "recorded-overview")
            self.assertEqual(response.data["name"], "recorded_skill")
            self.assertEqual(response.data["steps"][0]["action"], "goto")
            self.assertTrue(response.data["can_use_overview"])


if __name__ == "__main__":
    unittest.main()
