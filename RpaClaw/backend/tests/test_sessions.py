import importlib.util
import json
import unittest
import tempfile
import shutil
from pathlib import Path
from pathlib import Path as _Path
from types import SimpleNamespace
from typing import Any, Optional, List
from unittest.mock import AsyncMock, MagicMock

from backend.recording.models import RecordingArtifact
from backend.recording.orchestrator import RecordingOrchestrator

# Load sessions module directly to avoid import issues
SESSIONS_PATH = Path(__file__).resolve().parents[1] / "route" / "sessions.py"
SPEC = importlib.util.spec_from_file_location("sessions_module", SESSIONS_PATH)
SESSIONS_MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(SESSIONS_MODULE)
SESSIONS_MODULE.Any = Any
SESSIONS_MODULE.ApiResponse.model_rebuild(_types_namespace={"Any": Any})
SESSIONS_MODULE.ChatRequest.model_rebuild(_types_namespace={"Optional": Optional, "List": List})
SESSIONS_MODULE.CompleteRecordingSegmentRequest.model_rebuild(
    _types_namespace={"Optional": Optional, "List": List, "Dict": dict, "Any": Any}
)
SESSIONS_MODULE.UpdateRecordingSegmentBindingsRequest.model_rebuild(
    _types_namespace={"List": List, "Dict": dict, "Any": Any}
)
SESSIONS_MODULE.PromoteRecordingSegmentLocatorRequest.model_rebuild(
    _types_namespace={"Optional": Optional}
)


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


class TestRecordingRoutes(unittest.IsolatedAsyncioTestCase):
    async def test_create_recording_run_route(self):
        session = SimpleNamespace(user_id="u1")
        request_model = SESSIONS_MODULE.CreateRecordingRunRequest(
            message="帮我录一个下载流程",
            kind="mixed",
            publish_target="skill",
            requires_workbench=True,
        )
        current_user = SimpleNamespace(id="u1")

        with (
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "async_get_science_session",
                AsyncMock(return_value=session),
            ),
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "recording_orchestrator",
            ) as orchestrator,
        ):
            orchestrator.create_run.return_value = SimpleNamespace(id="run-1")
            orchestrator.start_segment.return_value = SimpleNamespace(id="seg-1")

            data = await SESSIONS_MODULE.create_recording_run(
                "session-1",
                request_model,
                current_user=current_user,
            )

        self.assertEqual(data.data["run_id"], "run-1")
        self.assertEqual(data.data["segment_id"], "seg-1")
        self.assertTrue(data.data["open_workbench"])
        orchestrator.create_run.assert_called_once_with(
            session_id="session-1",
            user_id="u1",
            kind="mixed",
        )
        orchestrator.start_segment.assert_called_once_with(
            orchestrator.create_run.return_value,
            kind="mixed",
            intent="帮我录一个下载流程",
            requires_workbench=True,
        )

    async def test_chat_with_session_does_not_short_circuit_recording_requests(self):
        events = []

        async def _save():
            return None

        def _fake_create_task(coro):
            coro.close()
            return SimpleNamespace(done=lambda: False)

        session = SimpleNamespace(
            user_id="u1",
            status=SESSIONS_MODULE.SessionStatus.PENDING,
            events=events,
            save=_save,
            model_config=None,
        )

        current_user = SimpleNamespace(id="u1")
        request = SimpleNamespace()
        body = SESSIONS_MODULE.ChatRequest(message="帮我录一个下载流程")

        with (
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "async_get_science_session",
                AsyncMock(return_value=session),
            ),
            unittest.mock.patch.object(
                SESSIONS_MODULE._asyncio,
                "create_task",
                side_effect=_fake_create_task,
            ) as create_task,
        ):
            response = await SESSIONS_MODULE.chat_with_session(
                "session-1",
                body,
                request,
                current_user=current_user,
            )

        self.assertIsInstance(response, SESSIONS_MODULE.EventSourceResponse)
        create_task.assert_called_once()
        self.assertFalse(any(event["event"] == "recording_run_started" for event in events))

    async def test_agent_background_worker_generates_fresh_user_event_id(self):
        events = []

        async def _save():
            return None

        async def _empty_stream(**kwargs):
            if False:
                yield kwargs

        session = SimpleNamespace(
            user_id="u1",
            session_id="session-1",
            status=SESSIONS_MODULE.SessionStatus.PENDING,
            events=events,
            save=AsyncMock(side_effect=_save),
            model_config=None,
            vm_root_dir="D:/tmp/workspace",
            reset_cancel=MagicMock(),
            is_cancelled=MagicMock(return_value=False),
        )

        repo = SimpleNamespace(find_many=AsyncMock(return_value=[]))

        with (
            unittest.mock.patch.object(SESSIONS_MODULE, "arun_science_task_stream", _empty_stream),
            unittest.mock.patch.object(SESSIONS_MODULE, "_get_repo", return_value=repo),
            unittest.mock.patch.object(SESSIONS_MODULE, "_snapshot_workspace_files", return_value={}),
            unittest.mock.patch.object(SESSIONS_MODULE, "_diff_workspace_files", return_value=[]),
            unittest.mock.patch.object(SESSIONS_MODULE, "_emit_to_sse"),
        ):
            await SESSIONS_MODULE._agent_background_worker(
                session=session,
                session_id="session-1",
                message="第二轮用户消息",
                attachments=[],
                event_id="cursor-evt-123",
                timestamp=1710000000,
                language="zh",
            )

        self.assertEqual(events[0]["event"], "message")
        self.assertEqual(events[0]["data"]["role"], "user")
        self.assertNotEqual(events[0]["data"]["event_id"], "cursor-evt-123")
        self.assertEqual(events[0]["data"]["content"], "第二轮用户消息")

    async def test_complete_recording_segment_route_appends_completion_event(self):
        events = []

        async def _save():
            return None

        session = SimpleNamespace(
            user_id="u1",
            events=events,
            save=_save,
        )
        current_user = SimpleNamespace(id="u1")
        orchestrator = RecordingOrchestrator()
        run = orchestrator.create_run(session_id="session-1", user_id="u1", kind="rpa")
        segment = orchestrator.start_segment(run, kind="rpa", intent="下载 PDF", requires_workbench=True)

        with (
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "async_get_science_session",
                AsyncMock(return_value=session),
            ),
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "recording_orchestrator",
                orchestrator,
            ),
        ):
            data = await SESSIONS_MODULE.complete_recording_segment(
                "session-1",
                run.id,
                segment.id,
                SESSIONS_MODULE.CompleteRecordingSegmentRequest(
                    rpa_session_id="rpa-session-1",
                    steps=[
                        {
                            "id": "step-1",
                            "step_index": 0,
                            "action": "extract_text",
                            "description": "提取项目名称",
                            "result_key": "project_name",
                            "locator_candidates": [{"kind": "role", "selected": True}],
                            "validation": {"status": "ok"},
                        },
                        {
                            "id": "step-2",
                            "step_index": 1,
                            "action": "fill",
                            "description": "输入搜索词",
                            "value": "paper",
                        }
                    ],
                    artifacts=[
                        {
                            "name": "downloaded_pdf",
                            "type": "file",
                            "path": "/tmp/paper.pdf",
                            "labels": ["download", "pdf"],
                        }
                    ],
                    params={
                        "keyword": {"original_value": "paper", "sensitive": False},
                        "password": {"original_value": "secret", "sensitive": True, "credential_id": "cred-1"},
                    },
                    auth_config={"credential_ids": ["cred-1"]},
                    description="下载 GitHub 趋势项目详情",
                    testing_status="passed",
                ),
                current_user=current_user,
            )

        self.assertEqual(data.data["segment"]["status"], "completed")
        self.assertEqual(data.data["summary"]["session_id"], "rpa-session-1")
        self.assertEqual(data.data["summary"]["steps"][0]["step_index"], 0)
        self.assertEqual(data.data["summary"]["params"]["keyword"]["original_value"], "paper")
        self.assertEqual(data.data["summary"]["auth_config"]["credential_ids"], ["cred-1"])
        self.assertEqual(data.data["summary"]["description"], "下载 GitHub 趋势项目详情")
        self.assertEqual(data.data["summary"]["testing_status"], "passed")
        self.assertEqual(data.data["summary"]["inputs"][0]["name"], "keyword")
        self.assertEqual(data.data["summary"]["outputs"][0]["name"], "project_name")
        self.assertEqual(events[0]["event"], "recording_segment_completed")
        self.assertEqual(events[0]["data"]["summary"]["artifacts"][0]["name"], "downloaded_pdf")

    async def test_complete_recording_segment_route_keeps_explicit_inputs_and_outputs(self):
        events = []

        async def _save():
            return None

        session = SimpleNamespace(
            user_id="u1",
            events=events,
            save=_save,
        )
        current_user = SimpleNamespace(id="u1")
        orchestrator = RecordingOrchestrator()
        run = orchestrator.create_run(session_id="session-1", user_id="u1", kind="rpa")
        segment = orchestrator.start_segment(run, kind="rpa", intent="下载 PDF", requires_workbench=True)

        with (
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "async_get_science_session",
                AsyncMock(return_value=session),
            ),
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "recording_orchestrator",
                orchestrator,
            ),
        ):
            data = await SESSIONS_MODULE.complete_recording_segment(
                "session-1",
                run.id,
                segment.id,
                SESSIONS_MODULE.CompleteRecordingSegmentRequest(
                    steps=[],
                    artifacts=[],
                    params={"keyword": {"original_value": "paper", "sensitive": False}},
                    inputs=[{"name": "query", "type": "string", "source": "segment_output", "source_ref": "seg-1.outputs.project_name"}],
                    outputs=[{"name": "project_name", "type": "string"}],
                ),
                current_user=current_user,
            )

        self.assertEqual(data.data["summary"]["inputs"][0]["name"], "query")
        self.assertEqual(data.data["summary"]["inputs"][0]["source_ref"], "seg-1.outputs.project_name")
        self.assertEqual(data.data["summary"]["outputs"][0]["name"], "project_name")

    async def test_promote_recording_segment_step_locator_persists_selected_candidate(self):
        async def _save():
            return None

        session = SimpleNamespace(user_id="u1", events=[], save=_save)
        current_user = SimpleNamespace(id="u1")
        orchestrator = RecordingOrchestrator()
        run = orchestrator.create_run(session_id="session-1", user_id="u1", kind="rpa")
        segment = orchestrator.start_segment(run, kind="rpa", intent="点击保存", requires_workbench=True)
        segment.exports["rpa_session_id"] = "rpa-session-1"
        segment.steps = [
            {
                "id": "step-1",
                "action": "click",
                "target": '{"method":"role","role":"button","name":"Save"}',
                "locator_candidates": [
                    {
                        "kind": "role",
                        "selected": True,
                        "locator": {"method": "role", "role": "button", "name": "Save"},
                        "status": "fallback",
                    },
                    {
                        "kind": "css",
                        "selected": False,
                        "locator": {"method": "css", "value": "button.save"},
                        "status": "ok",
                    },
                ],
                "validation": {"status": "fallback", "selected_candidate_index": 0},
            }
        ]
        orchestrator.complete_segment(run, segment)

        with (
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "async_get_science_session",
                AsyncMock(return_value=session),
            ),
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "recording_orchestrator",
                orchestrator,
            ),
        ):
            data = await SESSIONS_MODULE.promote_recording_segment_step_locator(
                "session-1",
                run.id,
                segment.id,
                0,
                SESSIONS_MODULE.PromoteRecordingSegmentLocatorRequest(candidate_index=1),
                current_user=current_user,
            )

        updated_step = data.data["step"]
        self.assertEqual(updated_step["step_index"], 0)
        self.assertEqual(json.loads(updated_step["target"]), {"method": "css", "value": "button.save"})
        self.assertFalse(updated_step["locator_candidates"][0]["selected"])
        self.assertTrue(updated_step["locator_candidates"][1]["selected"])
        self.assertEqual(segment.steps[0]["target"], updated_step["target"])
        self.assertEqual(data.data["summary"]["steps"][0]["step_index"], 0)
        self.assertEqual(data.data["summary"]["rpa_session_id"], "rpa-session-1")
        self.assertEqual(session.events[0]["event"], "recording_segment_updated")

    async def test_get_recording_segment_mapping_sources_route_returns_historical_pool(self):
        async def _save():
            return None

        session = SimpleNamespace(user_id="u1", events=[], save=_save)
        current_user = SimpleNamespace(id="u1")
        orchestrator = RecordingOrchestrator()
        run = orchestrator.create_run(session_id="session-1", user_id="u1", kind="mixed")

        first = orchestrator.start_segment(run, kind="rpa", intent="获取项目名称", requires_workbench=True)
        first.steps = [
            {
                "id": "step-1",
                "action": "extract_text",
                "description": "提取项目名称",
                "result_key": "project_name",
            }
        ]
        first.exports["title"] = "获取项目名称"
        orchestrator.complete_segment(run, first)

        second = orchestrator.start_segment(run, kind="script", intent="转换文件", requires_workbench=False)
        second.exports["outputs"] = [{"name": "normalized_csv", "type": "file"}]
        second.artifacts.append(
            RecordingArtifact(
                id="artifact-1",
                run_id=run.id,
                segment_id=second.id,
                name="normalized_csv",
                type="file",
                path="/tmp/normalized.csv",
            )
        )
        orchestrator.complete_segment(run, second)

        current = orchestrator.start_segment(run, kind="rpa", intent="搜索项目", requires_workbench=True)

        with (
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "async_get_science_session",
                AsyncMock(return_value=session),
            ),
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "recording_orchestrator",
                orchestrator,
            ),
        ):
            data = await SESSIONS_MODULE.get_recording_segment_mapping_sources(
                "session-1",
                run.id,
                current.id,
                current_user=current_user,
            )

        self.assertEqual(data.data["segment_id"], current.id)
        self.assertEqual(data.data["source_pool"]["segment_outputs"][0]["source_ref"], f"{first.id}.outputs.project_name")
        self.assertEqual(data.data["source_pool"]["artifacts"][0]["source_ref"], "artifact:artifact-1")
        self.assertEqual(data.data["summary"]["segment_id"], current.id)

    async def test_update_recording_segment_bindings_route_appends_segment_updated_event(self):
        events = []

        async def _save():
            return None

        session = SimpleNamespace(user_id="u1", events=events, save=_save)
        current_user = SimpleNamespace(id="u1")
        orchestrator = RecordingOrchestrator()
        run = orchestrator.create_run(session_id="session-1", user_id="u1", kind="mixed")

        source = orchestrator.start_segment(run, kind="rpa", intent="提取标题", requires_workbench=True)
        source.steps = [
            {
                "id": "step-1",
                "action": "extract_text",
                "description": "提取标题",
                "result_key": "issue_title",
            }
        ]
        orchestrator.complete_segment(run, source)

        target = orchestrator.start_segment(run, kind="rpa", intent="搜索标题", requires_workbench=True)
        target.steps = [
            {
                "id": "step-2",
                "action": "fill",
                "description": "输入搜索词",
                "value": "test",
            }
        ]
        target.exports["params"] = {}
        orchestrator.complete_segment(run, target)

        with (
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "async_get_science_session",
                AsyncMock(return_value=session),
            ),
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "recording_orchestrator",
                orchestrator,
            ),
        ):
            data = await SESSIONS_MODULE.update_recording_segment_bindings(
                "session-1",
                run.id,
                target.id,
                SESSIONS_MODULE.UpdateRecordingSegmentBindingsRequest(
                    inputs=[
                        {
                            "name": "search",
                            "type": "string",
                            "required": True,
                            "source": "segment_output",
                            "source_ref": f"{source.id}.outputs.issue_title",
                            "description": "来自上一段的标题",
                        }
                    ]
                ),
                current_user=current_user,
            )

        self.assertEqual(data.data["summary"]["inputs"][0]["source_ref"], f"{source.id}.outputs.issue_title")
        self.assertEqual(data.data["segment"]["id"], target.id)
        self.assertEqual(events[0]["event"], "recording_segment_updated")
        self.assertEqual(events[0]["data"]["summary"]["segment_id"], target.id)

    async def test_resolve_recording_segment_steps_prefers_raw_rpa_session_steps(self):
        from backend.rpa.manager import RPASession, RPAStep, rpa_manager

        rpa_manager.sessions["rpa-session-raw"] = RPASession(
            id="rpa-session-raw",
            user_id="u1",
            sandbox_session_id="sandbox-1",
            steps=[
                RPAStep(
                    id="raw-step-1",
                    action="navigate",
                    target="",
                    value="",
                    url="https://github.com/trending",
                    description="导航到 https://github.com/trending",
                ),
                RPAStep(
                    id="raw-step-2",
                    action="fill",
                    target='{"method":"role","role":"textbox","name":"搜索……"}',
                    value="test",
                    url="https://www.runoob.com",
                    description='输入 "test" 到 textbox("搜索……")',
                ),
            ],
        )
        try:
            resolved = await SESSIONS_MODULE._resolve_recording_segment_steps(
                SESSIONS_MODULE.CompleteRecordingSegmentRequest(
                    rpa_session_id="rpa-session-raw",
                    steps=[
                        {
                            "id": "lossy-step-1",
                            "action": "navigate",
                            "description": "导航到 https://github.com/trending",
                        }
                    ],
                    artifacts=[],
                )
            )
        finally:
            rpa_manager.sessions.pop("rpa-session-raw", None)

        self.assertEqual(resolved[0]["url"], "https://github.com/trending")
        self.assertEqual(resolved[1]["value"], "test")

    async def test_create_script_segment_route_appends_completion_event(self):
        events = []

        async def _save():
            return None

        session = SimpleNamespace(user_id="u1", events=events, save=_save)
        current_user = SimpleNamespace(id="u1")
        orchestrator = RecordingOrchestrator()
        run = orchestrator.create_run(session_id="session-1", user_id="u1", kind="mixed")

        with (
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "async_get_science_session",
                AsyncMock(return_value=session),
            ),
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "recording_orchestrator",
                orchestrator,
            ),
        ):
            data = await SESSIONS_MODULE.create_recording_script_segment(
                "session-1",
                run.id,
                SESSIONS_MODULE.CreateScriptSegmentRequest(
                    title="转换报表",
                    purpose="将下载文件转换为 CSV",
                    script="def run(context):\n    return {'converted_csv': 'output.csv'}\n",
                    params={"report_date": {"original_value": "2026-04-21", "sensitive": False}},
                    inputs=[{"name": "source_file", "type": "file", "description": "下载文件"}],
                    outputs=[{"name": "converted_csv", "type": "file", "description": "CSV 文件"}],
                ),
                current_user=current_user,
            )

        self.assertEqual(data.data["summary"]["kind"], "script")
        self.assertEqual(data.data["summary"]["title"], "转换报表")
        self.assertEqual(data.data["summary"]["inputs"][0]["name"], "source_file")
        self.assertEqual(events[0]["event"], "recording_segment_completed")
        self.assertEqual(run.segments[0].kind, "script")

    async def test_begin_recording_test_route_executes_single_segment_run(self):
        async def _save():
            return None

        session = SimpleNamespace(user_id="u1", events=[], save=_save)
        current_user = SimpleNamespace(id="u1")
        orchestrator = RecordingOrchestrator()
        run = orchestrator.create_run(session_id="session-1", user_id="u1", kind="rpa")
        segment = orchestrator.start_segment(run, kind="rpa", intent="下载 PDF", requires_workbench=True)
        segment.exports["rpa_session_id"] = "rpa-1"
        segment.exports["title"] = "下载 PDF"
        segment.exports["description"] = "下载并检查 PDF 文件"
        segment.exports["params"] = {"file_name": {"original_value": "paper.pdf"}}
        orchestrator.complete_segment(run, segment)
        execution = {
            "skill_dir": "/tmp/skill",
            "workflow_path": "/tmp/skill/workflow.json",
            "segments": [],
            "result": {"success": True, "logs": ["segment ok"], "result": {"outputs": {}}},
        }

        with (
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "async_get_science_session",
                AsyncMock(return_value=session),
            ),
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "recording_orchestrator",
                orchestrator,
            ),
            unittest.mock.patch.object(
                SESSIONS_MODULE.recording_testing,
                "execute_recording_workflow_test",
                AsyncMock(return_value=execution),
            ) as execute_test,
        ):
            data = await SESSIONS_MODULE.test_recording_run(
                "session-1",
                run.id,
                current_user=current_user,
            )

        execute_test.assert_awaited_once()
        self.assertEqual(data.data["run"]["status"], "ready_to_publish")
        self.assertEqual(data.data["run"]["testing"]["status"], "passed")
        self.assertEqual(data.data["test_payload"]["run_id"], run.id)
        self.assertEqual(data.data["test_payload"]["execution"]["result"]["logs"], ["segment ok"])
        self.assertEqual(data.data["test_payload"]["rpa_session_id"], "rpa-1")
        self.assertEqual(data.data["test_payload"]["title"], "下载 PDF")

    async def test_recording_test_route_can_restart_after_publish_is_prepared(self):
        async def _save():
            return None

        session = SimpleNamespace(user_id="u1", events=[], save=_save)
        current_user = SimpleNamespace(id="u1")
        orchestrator = RecordingOrchestrator()
        run = orchestrator.create_run(session_id="session-1", user_id="u1", kind="rpa")
        segment = orchestrator.start_segment(run, kind="rpa", intent="download PDF", requires_workbench=True)
        segment.exports["rpa_session_id"] = "rpa-1"
        orchestrator.complete_segment(run, segment)
        orchestrator.mark_ready_to_publish(run, "skill")
        execution = {
            "skill_dir": "/tmp/skill",
            "workflow_path": "/tmp/skill/workflow.json",
            "segments": [],
            "result": {"success": True, "logs": ["segment ok"], "result": {"outputs": {}}},
        }

        with (
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "async_get_science_session",
                AsyncMock(return_value=session),
            ),
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "recording_orchestrator",
                orchestrator,
            ),
            unittest.mock.patch.object(
                SESSIONS_MODULE.recording_testing,
                "execute_recording_workflow_test",
                AsyncMock(return_value=execution),
            ) as execute_test,
        ):
            data = await SESSIONS_MODULE.test_recording_run(
                "session-1",
                run.id,
                current_user=current_user,
            )

        execute_test.assert_awaited_once()
        self.assertEqual(data.data["run"]["status"], "ready_to_publish")
        self.assertEqual(data.data["run"]["testing"]["status"], "passed")

    async def test_begin_recording_test_route_executes_multi_segment_workflow(self):
        async def _save():
            return None

        session = SimpleNamespace(user_id="u1", events=[], save=_save)
        current_user = SimpleNamespace(id="u1")
        orchestrator = RecordingOrchestrator()
        run = orchestrator.create_run(session_id="session-1", user_id="u1", kind="mixed")
        first = orchestrator.start_segment(run, kind="rpa", intent="get project", requires_workbench=True)
        first.exports["testing_status"] = "passed"
        orchestrator.complete_segment(run, first)
        second = orchestrator.start_segment(run, kind="rpa", intent="search project", requires_workbench=True)
        second.exports["testing_status"] = "passed"
        orchestrator.complete_segment(run, second)
        execution = {
            "skill_dir": "/tmp/skill",
            "workflow_path": "/tmp/skill/workflow.json",
            "segments": [],
            "result": {"success": True, "logs": ["workflow ok"], "result": {"outputs": {}}},
        }

        with (
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "async_get_science_session",
                AsyncMock(return_value=session),
            ),
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "recording_orchestrator",
                orchestrator,
            ),
            unittest.mock.patch.object(
                SESSIONS_MODULE.recording_testing,
                "execute_recording_workflow_test",
                AsyncMock(return_value=execution),
            ) as execute_test,
        ):
            data = await SESSIONS_MODULE.test_recording_run(
                "session-1",
                run.id,
                current_user=current_user,
            )

        execute_test.assert_awaited_once()
        self.assertEqual(data.data["test_payload"]["mode"], "workflow")
        self.assertEqual(data.data["test_payload"]["execution"]["result"]["logs"], ["workflow ok"])
        self.assertTrue(session.events[0]["data"]["test_payload"]["execution"]["result"]["success"])
        self.assertEqual(data.data["run"]["status"], "ready_to_publish")
        self.assertEqual(data.data["run"]["testing"]["status"], "passed")

    async def test_begin_recording_test_route_marks_multi_segment_workflow_failed(self):
        async def _save():
            return None

        session = SimpleNamespace(user_id="u1", events=[], save=_save)
        current_user = SimpleNamespace(id="u1")
        orchestrator = RecordingOrchestrator()
        run = orchestrator.create_run(session_id="session-1", user_id="u1", kind="mixed")
        run.publish_target = "tool"
        first = orchestrator.start_segment(run, kind="rpa", intent="get project", requires_workbench=True)
        first.exports["testing_status"] = "passed"
        orchestrator.complete_segment(run, first)
        second = orchestrator.start_segment(run, kind="script", intent="transform data", requires_workbench=False)
        second.exports["testing_status"] = "passed"
        second.exports["script"] = "def run(context):\n    return {'ok': True}\n"
        second.exports["entry"] = "segments/seg2.py"
        orchestrator.complete_segment(run, second)
        execution = {
            "skill_dir": "/tmp/skill",
            "workflow_path": "/tmp/skill/workflow.json",
            "segments": [],
            "result": {
                "success": False,
                "logs": ["workflow failed"],
                "stderr": "boom",
                "result": {},
            },
        }

        with (
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "async_get_science_session",
                AsyncMock(return_value=session),
            ),
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "recording_orchestrator",
                orchestrator,
            ),
            unittest.mock.patch.object(
                SESSIONS_MODULE.recording_testing,
                "execute_recording_workflow_test",
                AsyncMock(return_value=execution),
            ) as execute_test,
        ):
            data = await SESSIONS_MODULE.test_recording_run(
                "session-1",
                run.id,
                current_user=current_user,
            )

        execute_test.assert_awaited_once()
        self.assertEqual(data.data["run"]["status"], "needs_repair")
        self.assertEqual(data.data["run"]["testing"]["status"], "failed")
        self.assertIn("workflow failed", data.data["run"]["testing"]["error"])

    async def test_execute_workflow_test_route_returns_segments_and_result(self):
        async def _save():
            return None

        session = SimpleNamespace(user_id="u1", events=[], save=_save)
        current_user = SimpleNamespace(id="u1")
        orchestrator = RecordingOrchestrator()
        run = orchestrator.create_run(session_id="session-1", user_id="u1", kind="mixed")
        first = orchestrator.start_segment(run, kind="rpa", intent="download pdf", requires_workbench=True)
        first.exports["rpa_session_id"] = "rpa-1"
        first.exports["title"] = "下载 PDF"
        first.exports["description"] = "下载并检查 PDF 文件"
        first.exports["testing_status"] = "passed"
        orchestrator.complete_segment(run, first)
        second = orchestrator.start_segment(run, kind="script", intent="parse pdf", requires_workbench=False)
        second.exports["title"] = "解析 PDF"
        second.exports["description"] = "解析下载文件"
        second.exports["script"] = "def run(context):\n    return {'parsed': True}\n"
        second.exports["entry"] = "segments/segment_2_script.py"
        second.exports["testing_status"] = "passed"
        orchestrator.complete_segment(run, second)

        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                unittest.mock.patch.object(
                    SESSIONS_MODULE,
                    "async_get_science_session",
                    AsyncMock(return_value=session),
                ),
                unittest.mock.patch.object(
                    SESSIONS_MODULE,
                    "recording_orchestrator",
                    orchestrator,
                ),
                unittest.mock.patch.object(SESSIONS_MODULE, "_WORKSPACE_DIR", tmp_dir),
                unittest.mock.patch.object(
                    SESSIONS_MODULE.recording_testing,
                    "execute_workflow_test",
                    AsyncMock(return_value={"success": True, "logs": ["ok"], "result": {"outputs": {"seg-1": {}}}}),
                ),
            ):
                data = await SESSIONS_MODULE.execute_recording_workflow_test(
                    "session-1",
                    run.id,
                    current_user=current_user,
                )

        self.assertEqual(data.data["run"]["id"], run.id)
        self.assertEqual(len(data.data["segments"]), 2)
        self.assertTrue(data.data["result"]["success"])
        self.assertEqual(data.data["result"]["logs"], ["ok"])

    async def test_publish_recording_run_returns_prompt_kind(self):
        async def _save():
            return None

        session = SimpleNamespace(user_id="u1", events=[], save=_save)
        current_user = SimpleNamespace(id="u1")
        orchestrator = RecordingOrchestrator()
        run = orchestrator.create_run(session_id="session-1", user_id="u1", kind="rpa")
        segment = orchestrator.start_segment(run, kind="rpa", intent="下载 PDF", requires_workbench=True)
        orchestrator.complete_segment(run, segment)
        orchestrator.begin_testing(run)

        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                unittest.mock.patch.object(
                    SESSIONS_MODULE,
                    "async_get_science_session",
                    AsyncMock(return_value=session),
                ),
                unittest.mock.patch.object(
                    SESSIONS_MODULE,
                    "recording_orchestrator",
                    orchestrator,
                ),
                unittest.mock.patch.object(SESSIONS_MODULE, "_WORKSPACE_DIR", tmp_dir),
            ):
                data = await SESSIONS_MODULE.publish_recording_run(
                    "session-1",
                    run.id,
                    SESSIONS_MODULE.PublishRecordingRunRequest(publish_target="skill"),
                    current_user=current_user,
                )

        self.assertEqual(data.data["prompt_kind"], "skill")
        self.assertEqual(data.data["run"]["status"], "ready_to_publish")
        self.assertTrue(data.data["staging_paths"])

    async def test_publish_recording_run_accepts_string_workspace_dir_for_draft_publish(self):
        async def _save():
            return None

        session = SimpleNamespace(user_id="u1", events=[], save=_save)
        current_user = SimpleNamespace(id="u1")
        orchestrator = RecordingOrchestrator()
        run = orchestrator.create_run(session_id="session-1", user_id="u1", kind="rpa")
        segment = orchestrator.start_segment(run, kind="rpa", intent="下载 PDF", requires_workbench=True)
        segment.exports["rpa_session_id"] = "rpa-1"
        segment.exports["title"] = "下载 PDF"
        segment.exports["description"] = "下载并检查 PDF 文件"
        orchestrator.complete_segment(run, segment)
        orchestrator.begin_testing(run)

        from backend.workflow.publishing import build_publish_draft
        from backend.workflow.recording_adapter import recording_run_to_workflow

        draft = build_publish_draft(recording_run_to_workflow(run), publish_target="skill")

        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                unittest.mock.patch.object(
                    SESSIONS_MODULE,
                    "async_get_science_session",
                    AsyncMock(return_value=session),
                ),
                unittest.mock.patch.object(
                    SESSIONS_MODULE,
                    "recording_orchestrator",
                    orchestrator,
                ),
                unittest.mock.patch.object(SESSIONS_MODULE, "_WORKSPACE_DIR", tmp_dir),
            ):
                data = await SESSIONS_MODULE.publish_recording_run(
                    "session-1",
                    run.id,
                    SESSIONS_MODULE.PublishRecordingRunRequest(
                        publish_target="skill",
                        draft=draft.model_dump(mode="json"),
                    ),
                    current_user=current_user,
                )

        self.assertEqual(data.data["prompt_kind"], "skill")
        self.assertTrue(data.data["staging_paths"])

    async def test_publish_recording_run_allows_publish_after_failed_test(self):
        async def _save():
            return None

        session = SimpleNamespace(user_id="u1", events=[], save=_save)
        current_user = SimpleNamespace(id="u1")
        orchestrator = RecordingOrchestrator()
        run = orchestrator.create_run(session_id="session-1", user_id="u1", kind="rpa")
        segment = orchestrator.start_segment(run, kind="rpa", intent="下载 PDF", requires_workbench=True)
        segment.exports["rpa_session_id"] = "rpa-1"
        segment.exports["title"] = "下载 PDF"
        segment.exports["description"] = "下载并检查 PDF 文件"
        orchestrator.complete_segment(run, segment)
        orchestrator.begin_testing(run)
        orchestrator.mark_needs_repair(run, "workflow failed")

        from backend.workflow.publishing import build_publish_draft
        from backend.workflow.recording_adapter import recording_run_to_workflow

        draft = build_publish_draft(recording_run_to_workflow(run), publish_target="skill")

        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                unittest.mock.patch.object(
                    SESSIONS_MODULE,
                    "async_get_science_session",
                    AsyncMock(return_value=session),
                ),
                unittest.mock.patch.object(
                    SESSIONS_MODULE,
                    "recording_orchestrator",
                    orchestrator,
                ),
                unittest.mock.patch.object(SESSIONS_MODULE, "_WORKSPACE_DIR", tmp_dir),
            ):
                data = await SESSIONS_MODULE.publish_recording_run(
                    "session-1",
                    run.id,
                    SESSIONS_MODULE.PublishRecordingRunRequest(
                        publish_target="skill",
                        draft=draft.model_dump(mode="json"),
                    ),
                    current_user=current_user,
                )

        self.assertEqual(data.data["prompt_kind"], "skill")
        self.assertEqual(data.data["run"]["status"], "ready_to_publish")
        self.assertTrue(data.data["staging_paths"])

    async def test_publish_recording_run_with_tool_draft_returns_mcp_tool_prompt(self):
        async def _save():
            return None

        session = SimpleNamespace(user_id="u1", events=[], save=_save)
        current_user = SimpleNamespace(id="u1")
        orchestrator = RecordingOrchestrator()
        run = orchestrator.create_run(session_id="session-1", user_id="u1", kind="rpa")
        run.publish_target = "tool"
        segment = orchestrator.start_segment(run, kind="rpa", intent="录制工具", requires_workbench=True)
        segment.steps = [{"action": "navigate", "url": "https://example.com"}]
        segment.exports["rpa_session_id"] = "rpa-1"
        orchestrator.complete_segment(run, segment)
        orchestrator.begin_testing(run)

        from backend.workflow.publishing import build_publish_draft
        from backend.workflow.recording_adapter import recording_run_to_workflow

        draft = build_publish_draft(recording_run_to_workflow(run), publish_target="tool")
        prepared = SESSIONS_MODULE.recording_publishing.PublishPreparation(
            prompt_kind="tool",
            staging_paths=["rpa-mcp:tool-1"],
            summary={
                "name": "recorded_tool",
                "title": "Recorded Tool",
                "run_id": run.id,
                "session_id": run.session_id,
                "target": "mcp_tool",
                "saved": True,
                "tool_id": "tool-1",
                "draft": draft.model_dump(mode="json"),
            },
        )

        with (
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "async_get_science_session",
                AsyncMock(return_value=session),
            ),
            unittest.mock.patch.object(
                SESSIONS_MODULE,
                "recording_orchestrator",
                orchestrator,
            ),
            unittest.mock.patch.object(
                SESSIONS_MODULE.recording_publishing,
                "build_mcp_tool_artifacts",
                AsyncMock(return_value=prepared),
            ) as build_mcp,
        ):
            data = await SESSIONS_MODULE.publish_recording_run(
                "session-1",
                run.id,
                SESSIONS_MODULE.PublishRecordingRunRequest(
                    publish_target="tool",
                    draft=draft.model_dump(mode="json"),
                ),
                current_user=current_user,
            )

        self.assertEqual(data.data["prompt_kind"], "tool")
        self.assertEqual(data.data["summary"]["target"], "mcp_tool")
        self.assertTrue(data.data["summary"]["saved"])
        build_mcp.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
