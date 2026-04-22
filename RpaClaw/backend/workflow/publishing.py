from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from backend.config import settings
from backend.rpa.generator import (
    PlaywrightGenerator,
    RPA_NAVIGATION_TIMEOUT_MS,
    RPA_PLAYWRIGHT_TIMEOUT_MS,
)
from backend.rpa.playwright_security import get_chromium_launch_kwargs, get_context_kwargs

from .models import (
    CredentialRequirement,
    PublishInput,
    PublishOutput,
    PublishSegmentSummary,
    PublishWarning,
    SkillPublishDraft,
    WorkflowRun,
    WorkflowSegment,
)
from .runner_template import render_workflow_runner


def safe_skill_name(value: str, fallback: str) -> str:
    candidate = re.sub(r"[^a-zA-Z0-9_\-]+", "_", value).strip("_").lower()
    return candidate or fallback


def build_publish_draft(
    run: WorkflowRun,
    publish_target: Literal["skill", "tool", "mcp"] = "skill",
) -> SkillPublishDraft:
    ordered = run.ordered_segments()
    title = run.intent.strip() or "recorded_workflow"
    segment_titles = "、".join(segment.title for segment in ordered[:3]) or title
    warnings: list[PublishWarning] = []
    for segment in ordered:
        if segment.status not in {"tested", "configured"}:
            warnings.append(
                PublishWarning(
                    code="segment_not_tested",
                    message=f"片段「{segment.title}」尚未测试通过。",
                    segment_id=segment.id,
                )
            )
        for item in segment.inputs:
            if item.source in {"segment_output", "artifact", "workflow_param"} and not item.source_ref:
                warnings.append(
                    PublishWarning(
                        code="segment_input_unbound",
                        message=f"片段「{segment.title}」的输入「{item.name}」尚未绑定来源。",
                        segment_id=segment.id,
                    )
                )

    return SkillPublishDraft(
        id=f"draft_{run.id}",
        run_id=run.id,
        publish_target=publish_target,
        skill_name=title,
        display_title=title,
        description=f"自动执行以下工作流片段：{segment_titles}。",
        trigger_examples=[title],
        inputs=_collect_publish_inputs(ordered),
        outputs=_collect_publish_outputs(ordered),
        credentials=_collect_credentials(ordered),
        segments=[
            PublishSegmentSummary(
                id=segment.id,
                kind=segment.kind,
                title=segment.title,
                purpose=segment.purpose,
                status=segment.status,
                input_count=len(segment.inputs),
                output_count=len(segment.outputs),
            )
            for segment in ordered
        ],
        warnings=warnings,
    )


def write_skill_artifacts(run: WorkflowRun, draft: SkillPublishDraft, base_dir: Path) -> dict[str, Any]:
    safe_name = safe_skill_name(draft.skill_name, f"workflow_{run.id[:8]}")
    skill_dir = base_dir / safe_name
    segments_dir = skill_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)

    workflow = _build_workflow_json(run, draft)
    (skill_dir / "workflow.json").write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (skill_dir / "params.json").write_text(
        json.dumps(_build_params_config(run, draft), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(_build_skill_md(draft, safe_name), encoding="utf-8")
    (skill_dir / "skill.py").write_text(
        render_workflow_runner(
            is_local=settings.storage_backend == "local",
            launch_kwargs=repr(get_chromium_launch_kwargs(headless=False)),
            context_kwargs=repr(get_context_kwargs()),
            default_timeout_ms=RPA_PLAYWRIGHT_TIMEOUT_MS,
            navigation_timeout_ms=RPA_NAVIGATION_TIMEOUT_MS,
        ),
        encoding="utf-8",
    )

    for segment in run.ordered_segments():
        if segment.kind == "rpa":
            _write_rpa_segment(segments_dir, segment)
        elif segment.kind == "script":
            _write_script_segment(segments_dir, segment)
        else:
            _write_metadata_segment(segments_dir, segment)

    return {
        "name": safe_name,
        "skill_dir": str(skill_dir),
        "workflow_path": str(skill_dir / "workflow.json"),
    }


def _collect_publish_inputs(segments: list[WorkflowSegment]) -> list[PublishInput]:
    collected: dict[str, PublishInput] = {}
    for segment in segments:
        for item in segment.inputs:
            if item.source != "user":
                continue
            if item.type == "secret":
                continue
            collected.setdefault(
                item.name,
                PublishInput(
                    name=item.name,
                    type=item.type,
                    required=item.required,
                    description=item.description,
                    default=item.default,
                ),
            )
    return list(collected.values())


def _collect_publish_outputs(segments: list[WorkflowSegment]) -> list[PublishOutput]:
    outputs: dict[str, PublishOutput] = {}
    for segment in segments:
        for item in segment.outputs:
            outputs[item.name] = PublishOutput(
                name=item.name,
                type=item.type,
                description=item.description,
            )
    return list(outputs.values())


def _collect_credentials(segments: list[WorkflowSegment]) -> list[CredentialRequirement]:
    credentials: dict[str, CredentialRequirement] = {}
    for segment in segments:
        auth_config = segment.config.get("auth_config")
        if isinstance(auth_config, dict):
            for name in auth_config.get("credential_ids", []):
                credentials[str(name)] = CredentialRequirement(
                    name=str(name),
                    type="browser_session",
                    description=f"片段「{segment.title}」需要的浏览器登录态。",
                )

        segment_params = segment.config.get("params", {})
        if not isinstance(segment_params, dict):
            continue
        for param_name, config in segment_params.items():
            if not isinstance(config, dict):
                continue
            credential_id = config.get("credential_id")
            if not credential_id:
                continue
            credentials.setdefault(
                str(credential_id),
                CredentialRequirement(
                    name=str(param_name),
                    type="secret",
                    description=str(config.get("description") or f"片段「{segment.title}」需要的凭证参数。"),
                ),
            )
    return list(credentials.values())


def _build_skill_md(draft: SkillPublishDraft, safe_name: str) -> str:
    segment_lines = [
        f"{index}. {segment.title}: {segment.purpose}"
        for index, segment in enumerate(draft.segments, start=1)
    ]
    input_lines = [
        f"- `{item.name}`: {item.description or item.type}"
        for item in draft.inputs
    ] or ["- 无需用户显式输入。"]
    credential_lines = [
        f"- `{item.name}`: {item.description}"
        for item in draft.credentials
    ] or ["- 无需额外认证，或使用当前浏览器登录态。"]
    output_lines = [
        f"- `{item.name}`: {item.description or item.type}"
        for item in draft.outputs
    ] or ["- 返回每个片段的执行状态和产物。"]
    trigger_lines = [f"- {example}" for example in draft.trigger_examples] or [f"- {draft.display_title}"]

    return "\n".join(
        [
            "---",
            f"name: {safe_name}",
            f'description: "{draft.description}"',
            "---",
            "",
            f"# {draft.display_title}",
            "",
            "## 何时使用",
            "",
            draft.description,
            "",
            "## 触发示例",
            "",
            *trigger_lines,
            "",
            "## 使用方式",
            "",
            "执行该技能时应进入技能目录并运行：",
            "",
            "```bash",
            "python skill.py",
            "```",
            "",
            "在应用内执行时，技能会复用同目录 `params.json` 中的默认值和已绑定凭证；用户显式传入的参数会覆盖这些默认配置。",
            "",
            "如需在命令行覆盖普通输入参数，请使用 `--参数名=参数值` 的形式传入。命令行直接运行不会自行解密凭证，敏感凭证应由应用执行链路注入，或在调试时手动传参。",
            "",
            "## 输入参数",
            "",
            *input_lines,
            "",
            "## 认证要求",
            "",
            *credential_lines,
            "",
            "## 工作流片段",
            "",
            *segment_lines,
            "",
            "## 输出",
            "",
            *output_lines,
            "",
            "## 失败处理",
            "",
            "如果片段执行失败，技能应返回失败片段、错误信息和已生成产物，便于用户重新录制或修复该片段。",
            "",
        ]
    )


def _build_workflow_json(run: WorkflowRun, draft: SkillPublishDraft) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "name": safe_skill_name(draft.skill_name, f"workflow_{run.id[:8]}"),
        "title": draft.display_title,
        "description": draft.description,
        "segments": [_segment_to_workflow_json(segment) for segment in run.ordered_segments()],
    }


def _segment_to_workflow_json(segment: WorkflowSegment) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": segment.id,
        "kind": segment.kind,
        "title": segment.title,
        "purpose": segment.purpose,
        "inputs": [item.model_dump(mode="json") for item in segment.inputs],
        "outputs": [item.model_dump(mode="json") for item in segment.outputs],
    }
    if segment.kind == "rpa":
        base["entry"] = f"segments/{segment.id}_rpa.py"
    elif segment.kind == "script":
        base["entry"] = segment.config.get("entry") or f"segments/{segment.id}_script.py"
    else:
        base["config_path"] = f"segments/{segment.id}_{segment.kind}.json"
    return base


def _build_params_config(run: WorkflowRun, draft: SkillPublishDraft) -> dict[str, Any]:
    params: dict[str, dict[str, Any]] = {}
    input_descriptions: dict[str, str] = {
        item.name: item.description
        for segment in run.ordered_segments()
        for item in segment.inputs
        if item.description
    }
    input_descriptions.update({
        item.name: item.description
        for item in draft.inputs
        if item.description
    })

    for segment in run.ordered_segments():
        segment_params = segment.config.get("params", {})
        if not isinstance(segment_params, dict):
            continue
        for name, config in segment_params.items():
            if not isinstance(name, str) or not name or not isinstance(config, dict):
                continue
            params[name] = _normalize_param_config(
                name,
                config,
                description=input_descriptions.get(name, ""),
            )

    for item in draft.inputs:
        if item.type == "secret":
            continue
        params.setdefault(
            item.name,
            {
                "type": "string" if item.type == "file" else item.type,
                "description": item.description,
                "original_value": item.default if item.default is not None else "",
                "sensitive": False,
                "required": item.required,
            },
        )

    return params


def _normalize_param_config(name: str, config: dict[str, Any], *, description: str = "") -> dict[str, Any]:
    sensitive = bool(config.get("sensitive"))
    normalized: dict[str, Any] = {
        "type": str(config.get("type") or "string"),
        "description": str(config.get("description") or description or f"参数 {name}"),
        "original_value": "{{credential}}" if sensitive else config.get("original_value", ""),
        "sensitive": sensitive,
        "required": bool(config.get("required", False)),
    }
    credential_id = config.get("credential_id")
    if credential_id:
        normalized["credential_id"] = str(credential_id)
    return normalized


def _write_rpa_segment(segments_dir: Path, segment: WorkflowSegment) -> None:
    generator = PlaywrightGenerator()
    script = generator.generate_script(
        segment.config.get("steps", []),
        params=segment.config.get("params", {}),
        is_local=settings.storage_backend == "local",
        test_mode=False,
    )
    module_source = _convert_rpa_script_to_segment_module(script)
    (segments_dir / f"{segment.id}_rpa.py").write_text(module_source, encoding="utf-8")


def _write_script_segment(segments_dir: Path, segment: WorkflowSegment) -> None:
    entry = segment.config.get("entry") or f"segments/{segment.id}_script.py"
    source = segment.config.get("source") or "def run(context):\n    return {}\n"
    script_name = Path(str(entry)).name
    (segments_dir / script_name).write_text(str(source), encoding="utf-8")


def _write_metadata_segment(segments_dir: Path, segment: WorkflowSegment) -> None:
    payload = segment.model_dump(mode="json")
    (segments_dir / f"{segment.id}_{segment.kind}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _convert_rpa_script_to_segment_module(script: str) -> str:
    marker = "async def execute_skill(page, **kwargs):"
    if marker not in script:
        raise ValueError("generated RPA script is missing execute_skill")

    start = script.index(marker)
    main_marker = "\n\nasync def main():"
    end = script.find(main_marker, start)
    if end == -1:
        raise ValueError("generated RPA script is missing main() wrapper")

    function_block = script[start:end].strip()
    function_block = function_block.replace(
        marker,
        "async def execute_segment(page, workflow_context=None, **kwargs):",
        1,
    )

    if "    return _results" not in function_block:
        raise ValueError("generated RPA script is missing result return")

    function_block = function_block.replace(
        "    return _results",
        "    if workflow_context is not None:\n"
        "        workflow_context['current_page'] = current_page\n"
        "    return _results",
        1,
    )
    return function_block + "\n"
