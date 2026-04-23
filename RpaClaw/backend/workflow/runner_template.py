from __future__ import annotations


WORKFLOW_RUNNER_TEMPLATE_LOCAL = '''"""Generated workflow skill runner."""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import sys
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright


BASE_DIR = Path(__file__).resolve().parent


class WorkflowContext:
    def __init__(self, params: dict[str, Any]):
        self.params = params
        self.outputs: dict[str, dict[str, Any]] = {}
        self.artifacts: dict[str, Any] = {}
        self.runtime: dict[str, Any] = {}
        self.output_artifact_refs: dict[str, str] = {}

    def register_workflow(self, workflow: dict[str, Any]) -> None:
        for segment in workflow.get("segments", []):
            segment_id = str(segment.get("id") or "")
            if not segment_id:
                continue
            for output_spec in segment.get("outputs", []):
                if not isinstance(output_spec, dict):
                    continue
                output_name = str(output_spec.get("name") or "")
                artifact_ref = str(output_spec.get("artifact_ref") or "")
                if output_name and artifact_ref:
                    self.output_artifact_refs[f"{segment_id}.outputs.{output_name}"] = artifact_ref

    def resolve(self, source_ref: str | None) -> Any:
        if not source_ref:
            return None
        if source_ref.startswith("params."):
            return self.params.get(source_ref.removeprefix("params."))
        if source_ref.startswith("artifact:"):
            value = self.artifacts.get(source_ref.removeprefix("artifact:"))
            if isinstance(value, dict) and value.get("path"):
                return value.get("path")
            return value
        if ".outputs." in source_ref:
            segment_id, output_name = source_ref.split(".outputs.", 1)
            value = self.outputs.get(segment_id, {}).get(output_name)
            if isinstance(value, dict) and value.get("path"):
                return value.get("path")
            if value is not None:
                return value
            artifact_ref = self.output_artifact_refs.get(source_ref)
            if artifact_ref:
                return self.resolve(f"artifact:{artifact_ref}")
        return None

    def store_segment_outputs(self, segment: dict[str, Any], values: dict[str, Any]) -> None:
        segment_id = str(segment.get("id") or "")
        if segment_id:
            self.outputs[segment_id] = values
        self._store_artifact_outputs(segment, values)

    def _store_artifact_outputs(self, segment: dict[str, Any], values: dict[str, Any]) -> None:
        for output_spec in segment.get("outputs", []):
            artifact_ref = output_spec.get("artifact_ref") if isinstance(output_spec, dict) else None
            if not artifact_ref:
                continue
            value = values.get(str(output_spec.get("name") or ""))
            if value is None:
                value = self._first_file_like_output(values)
            if value is not None:
                self.artifacts[str(artifact_ref)] = value

    @staticmethod
    def _first_file_like_output(values: dict[str, Any]) -> Any:
        for value in values.values():
            if isinstance(value, dict) and value.get("path"):
                return value
        for name, value in values.items():
            if str(name).startswith("download_"):
                return value
        return None

    def final_outputs(self) -> dict[str, Any]:
        return {
            "status": "success",
            "outputs": self.outputs,
            "artifacts": self.artifacts,
        }


def load_json(name: str) -> dict[str, Any]:
    return json.loads((BASE_DIR / name).read_text(encoding="utf-8"))


def load_params(kwargs: dict[str, Any]) -> dict[str, Any]:
    config = load_json("params.json")
    params: dict[str, Any] = {}
    for name, spec in config.items():
        if not isinstance(spec, dict):
            continue
        original = spec.get("original_value", "")
        if original and original != "{{credential}}":
            params[name] = original
    params.update(kwargs)
    return params


def build_runtime_context(page, kwargs: dict[str, Any]) -> dict[str, Any]:
    downloads_dir = Path(str(kwargs.get("_downloads_dir") or (BASE_DIR / "downloads"))).resolve()
    workspace_dir = Path(str(kwargs.get("_workspace_dir") or BASE_DIR)).resolve()
    skill_dir = Path(str(kwargs.get("_skill_dir") or BASE_DIR)).resolve()
    return {
        "current_page": page,
        "skill_dir": str(skill_dir),
        "_skill_dir": str(skill_dir),
        "workspace_dir": str(workspace_dir),
        "_workspace_dir": str(workspace_dir),
        "downloads_dir": str(downloads_dir),
        "_downloads_dir": str(downloads_dir),
        "workflow_path": str((BASE_DIR / "workflow.json").resolve()),
        "segments_dir": str((BASE_DIR / "segments").resolve()),
    }


def load_module(relative_path: str, module_name: str):
    script_path = BASE_DIR / relative_path
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load workflow segment: {relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_segment_kwargs(segment: dict[str, Any], context: WorkflowContext) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for input_spec in segment.get("inputs", []):
        name = input_spec.get("name")
        if not name:
            continue
        source_ref = input_spec.get("source_ref")
        value = context.resolve(source_ref) if source_ref else context.params.get(name, input_spec.get("default"))
        if value is not None:
            resolved[name] = value
    return resolved


def normalize_segment_result(result: Any) -> dict[str, Any]:
    if result is None:
        return {}
    if isinstance(result, dict):
        return result
    return {"result": result}


def invoke_segment_callable(func, *, context: WorkflowContext, kwargs: dict[str, Any]) -> Any:
    signature = inspect.signature(func)
    parameters = signature.parameters

    call_args: list[Any] = []
    call_kwargs: dict[str, Any] = {}

    if "context" in parameters:
        context_param = parameters["context"]
        if context_param.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}:
            call_args.append(context)
        else:
            call_kwargs["context"] = context

    accepts_var_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if accepts_var_kwargs:
        call_kwargs.update(kwargs)
    else:
        for name, value in kwargs.items():
            if name in parameters:
                call_kwargs[name] = value

    return func(*call_args, **call_kwargs)


async def run_rpa_segment(segment: dict[str, Any], context: WorkflowContext, current_page) -> dict[str, Any]:
    module = load_module(segment["entry"], f"workflow_segment_{segment['id']}")
    execute_segment = getattr(module, "execute_segment", None)
    if not callable(execute_segment):
        raise RuntimeError(f"RPA segment {segment['id']} must define execute_segment(page, workflow_context, **kwargs)")

    kwargs = build_segment_kwargs(segment, context)
    result = await execute_segment(current_page, context.runtime, **kwargs)
    return normalize_segment_result(result)


async def run_script_segment(segment: dict[str, Any], context: WorkflowContext) -> dict[str, Any]:
    module = load_module(segment["entry"], f"workflow_segment_{segment['id']}")
    run = getattr(module, "run", None)
    main = getattr(module, "main", None)
    kwargs = build_segment_kwargs(segment, context)

    if callable(run):
        result = invoke_segment_callable(run, context=context, kwargs=kwargs)
    elif callable(main):
        result = invoke_segment_callable(main, context=context, kwargs=kwargs)
    else:
        raise RuntimeError(f"Script segment {segment['id']} must define run(context) or main(**inputs)")

    if inspect.isawaitable(result):
        result = await result
    return normalize_segment_result(result)


async def run_mcp_segment(segment: dict[str, Any], context: WorkflowContext) -> dict[str, Any]:
    return {
        "status": "metadata_only",
        "tool": segment.get("tool"),
    }


async def run_llm_segment(segment: dict[str, Any], context: WorkflowContext) -> dict[str, Any]:
    return {
        "status": "metadata_only",
        "schema": segment.get("schema"),
    }


async def _run_workflow(page, **kwargs):
    workflow = load_json("workflow.json")
    params = load_params(kwargs)
    context = WorkflowContext(params=params)
    context.register_workflow(workflow)
    context.runtime.update(build_runtime_context(page, kwargs))

    for segment in workflow.get("segments", []):
        kind = segment.get("kind")
        current_page = context.runtime.get("current_page", page)
        if kind == "rpa":
            result = await run_rpa_segment(segment, context, current_page)
        elif kind == "script":
            result = await run_script_segment(segment, context)
        elif kind == "mcp":
            result = await run_mcp_segment(segment, context)
        elif kind == "llm":
            result = await run_llm_segment(segment, context)
        else:
            raise ValueError(f"Unsupported segment kind: {kind}")
        context.store_segment_outputs(segment, result)

    return context.final_outputs()


async def execute_skill(page, **kwargs):
    return await _run_workflow(page, **kwargs)


def run(**kwargs):
    workflow = load_json("workflow.json")
    if any(segment.get("kind") == "rpa" for segment in workflow.get("segments", [])):
        raise RuntimeError("run() is not supported for workflows containing RPA segments; use execute_skill(page, **kwargs) or python skill.py")
    return asyncio.run(_run_workflow(None, **kwargs))


async def main():
    kwargs = {}
    for arg in sys.argv[1:]:
        if arg.startswith("--") and "=" in arg:
            key, value = arg[2:].split("=", 1)
            kwargs[key] = value

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(**__LAUNCH_KWARGS__)
    context = await browser.new_context(**__CONTEXT_KWARGS__)
    page = await context.new_page()
    page.set_default_timeout(__DEFAULT_TIMEOUT_MS__)
    page.set_default_navigation_timeout(__NAVIGATION_TIMEOUT_MS__)
    try:
        result = await execute_skill(page, **kwargs)
        if result:
            print("SKILL_DATA:" + json.dumps(result, ensure_ascii=False, default=str))
        print("SKILL_SUCCESS")
    except Exception as exc:
        print(f"SKILL_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        await context.close()
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
'''


WORKFLOW_RUNNER_TEMPLATE_DOCKER = '''"""Generated workflow skill runner."""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import sys
from pathlib import Path
from typing import Any

import httpx
from playwright.async_api import async_playwright


BASE_DIR = Path(__file__).resolve().parent


class WorkflowContext:
    def __init__(self, params: dict[str, Any]):
        self.params = params
        self.outputs: dict[str, dict[str, Any]] = {}
        self.artifacts: dict[str, Any] = {}
        self.runtime: dict[str, Any] = {}
        self.output_artifact_refs: dict[str, str] = {}

    def register_workflow(self, workflow: dict[str, Any]) -> None:
        for segment in workflow.get("segments", []):
            segment_id = str(segment.get("id") or "")
            if not segment_id:
                continue
            for output_spec in segment.get("outputs", []):
                if not isinstance(output_spec, dict):
                    continue
                output_name = str(output_spec.get("name") or "")
                artifact_ref = str(output_spec.get("artifact_ref") or "")
                if output_name and artifact_ref:
                    self.output_artifact_refs[f"{segment_id}.outputs.{output_name}"] = artifact_ref

    def resolve(self, source_ref: str | None) -> Any:
        if not source_ref:
            return None
        if source_ref.startswith("params."):
            return self.params.get(source_ref.removeprefix("params."))
        if source_ref.startswith("artifact:"):
            value = self.artifacts.get(source_ref.removeprefix("artifact:"))
            if isinstance(value, dict) and value.get("path"):
                return value.get("path")
            return value
        if ".outputs." in source_ref:
            segment_id, output_name = source_ref.split(".outputs.", 1)
            value = self.outputs.get(segment_id, {}).get(output_name)
            if isinstance(value, dict) and value.get("path"):
                return value.get("path")
            if value is not None:
                return value
            artifact_ref = self.output_artifact_refs.get(source_ref)
            if artifact_ref:
                return self.resolve(f"artifact:{artifact_ref}")
        return None

    def store_segment_outputs(self, segment: dict[str, Any], values: dict[str, Any]) -> None:
        segment_id = str(segment.get("id") or "")
        if segment_id:
            self.outputs[segment_id] = values
        self._store_artifact_outputs(segment, values)

    def _store_artifact_outputs(self, segment: dict[str, Any], values: dict[str, Any]) -> None:
        for output_spec in segment.get("outputs", []):
            artifact_ref = output_spec.get("artifact_ref") if isinstance(output_spec, dict) else None
            if not artifact_ref:
                continue
            value = values.get(str(output_spec.get("name") or ""))
            if value is None:
                value = self._first_file_like_output(values)
            if value is not None:
                self.artifacts[str(artifact_ref)] = value

    @staticmethod
    def _first_file_like_output(values: dict[str, Any]) -> Any:
        for value in values.values():
            if isinstance(value, dict) and value.get("path"):
                return value
        for name, value in values.items():
            if str(name).startswith("download_"):
                return value
        return None

    def final_outputs(self) -> dict[str, Any]:
        return {
            "status": "success",
            "outputs": self.outputs,
            "artifacts": self.artifacts,
        }


def load_json(name: str) -> dict[str, Any]:
    return json.loads((BASE_DIR / name).read_text(encoding="utf-8"))


def load_params(kwargs: dict[str, Any]) -> dict[str, Any]:
    config = load_json("params.json")
    params: dict[str, Any] = {}
    for name, spec in config.items():
        if not isinstance(spec, dict):
            continue
        original = spec.get("original_value", "")
        if original and original != "{{credential}}":
            params[name] = original
    params.update(kwargs)
    return params


def build_runtime_context(page, kwargs: dict[str, Any]) -> dict[str, Any]:
    downloads_dir = Path(str(kwargs.get("_downloads_dir") or (BASE_DIR / "downloads"))).resolve()
    workspace_dir = Path(str(kwargs.get("_workspace_dir") or BASE_DIR)).resolve()
    skill_dir = Path(str(kwargs.get("_skill_dir") or BASE_DIR)).resolve()
    return {
        "current_page": page,
        "skill_dir": str(skill_dir),
        "_skill_dir": str(skill_dir),
        "workspace_dir": str(workspace_dir),
        "_workspace_dir": str(workspace_dir),
        "downloads_dir": str(downloads_dir),
        "_downloads_dir": str(downloads_dir),
        "workflow_path": str((BASE_DIR / "workflow.json").resolve()),
        "segments_dir": str((BASE_DIR / "segments").resolve()),
    }


def load_module(relative_path: str, module_name: str):
    script_path = BASE_DIR / relative_path
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load workflow segment: {relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_segment_kwargs(segment: dict[str, Any], context: WorkflowContext) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for input_spec in segment.get("inputs", []):
        name = input_spec.get("name")
        if not name:
            continue
        source_ref = input_spec.get("source_ref")
        value = context.resolve(source_ref) if source_ref else context.params.get(name, input_spec.get("default"))
        if value is not None:
            resolved[name] = value
    return resolved


def normalize_segment_result(result: Any) -> dict[str, Any]:
    if result is None:
        return {}
    if isinstance(result, dict):
        return result
    return {"result": result}


def invoke_segment_callable(func, *, context: WorkflowContext, kwargs: dict[str, Any]) -> Any:
    signature = inspect.signature(func)
    parameters = signature.parameters

    call_args: list[Any] = []
    call_kwargs: dict[str, Any] = {}

    if "context" in parameters:
        context_param = parameters["context"]
        if context_param.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}:
            call_args.append(context)
        else:
            call_kwargs["context"] = context

    accepts_var_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if accepts_var_kwargs:
        call_kwargs.update(kwargs)
    else:
        for name, value in kwargs.items():
            if name in parameters:
                call_kwargs[name] = value

    return func(*call_args, **call_kwargs)


async def run_rpa_segment(segment: dict[str, Any], context: WorkflowContext, current_page) -> dict[str, Any]:
    module = load_module(segment["entry"], f"workflow_segment_{segment['id']}")
    execute_segment = getattr(module, "execute_segment", None)
    if not callable(execute_segment):
        raise RuntimeError(f"RPA segment {segment['id']} must define execute_segment(page, workflow_context, **kwargs)")

    kwargs = build_segment_kwargs(segment, context)
    result = await execute_segment(current_page, context.runtime, **kwargs)
    return normalize_segment_result(result)


async def run_script_segment(segment: dict[str, Any], context: WorkflowContext) -> dict[str, Any]:
    module = load_module(segment["entry"], f"workflow_segment_{segment['id']}")
    run = getattr(module, "run", None)
    main = getattr(module, "main", None)
    kwargs = build_segment_kwargs(segment, context)

    if callable(run):
        result = invoke_segment_callable(run, context=context, kwargs=kwargs)
    elif callable(main):
        result = invoke_segment_callable(main, context=context, kwargs=kwargs)
    else:
        raise RuntimeError(f"Script segment {segment['id']} must define run(context) or main(**inputs)")

    if inspect.isawaitable(result):
        result = await result
    return normalize_segment_result(result)


async def run_mcp_segment(segment: dict[str, Any], context: WorkflowContext) -> dict[str, Any]:
    return {
        "status": "metadata_only",
        "tool": segment.get("tool"),
    }


async def run_llm_segment(segment: dict[str, Any], context: WorkflowContext) -> dict[str, Any]:
    return {
        "status": "metadata_only",
        "schema": segment.get("schema"),
    }


async def _run_workflow(page, **kwargs):
    workflow = load_json("workflow.json")
    params = load_params(kwargs)
    context = WorkflowContext(params=params)
    context.register_workflow(workflow)
    context.runtime.update(build_runtime_context(page, kwargs))

    for segment in workflow.get("segments", []):
        kind = segment.get("kind")
        current_page = context.runtime.get("current_page", page)
        if kind == "rpa":
            result = await run_rpa_segment(segment, context, current_page)
        elif kind == "script":
            result = await run_script_segment(segment, context)
        elif kind == "mcp":
            result = await run_mcp_segment(segment, context)
        elif kind == "llm":
            result = await run_llm_segment(segment, context)
        else:
            raise ValueError(f"Unsupported segment kind: {kind}")
        context.store_segment_outputs(segment, result)

    return context.final_outputs()


async def execute_skill(page, **kwargs):
    return await _run_workflow(page, **kwargs)


def run(**kwargs):
    workflow = load_json("workflow.json")
    if any(segment.get("kind") == "rpa" for segment in workflow.get("segments", [])):
        raise RuntimeError("run() is not supported for workflows containing RPA segments; use execute_skill(page, **kwargs) or python skill.py")
    return asyncio.run(_run_workflow(None, **kwargs))


async def _get_cdp_url() -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get("http://127.0.0.1:8080/v1/browser/info")
        resp.raise_for_status()
        return resp.json()["data"]["cdp_url"]


async def main():
    kwargs = {}
    for arg in sys.argv[1:]:
        if arg.startswith("--") and "=" in arg:
            key, value = arg[2:].split("=", 1)
            kwargs[key] = value

    cdp_url = await _get_cdp_url()
    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(cdp_url)
    context = await browser.new_context(**__CONTEXT_KWARGS__)
    page = await context.new_page()
    page.set_default_timeout(__DEFAULT_TIMEOUT_MS__)
    page.set_default_navigation_timeout(__NAVIGATION_TIMEOUT_MS__)
    try:
        result = await execute_skill(page, **kwargs)
        if result:
            print("SKILL_DATA:" + json.dumps(result, ensure_ascii=False, default=str))
        print("SKILL_SUCCESS")
    except Exception as exc:
        print(f"SKILL_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        await context.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
'''


def render_workflow_runner(
    *,
    is_local: bool,
    launch_kwargs: str,
    context_kwargs: str,
    default_timeout_ms: int,
    navigation_timeout_ms: int,
) -> str:
    template = WORKFLOW_RUNNER_TEMPLATE_LOCAL if is_local else WORKFLOW_RUNNER_TEMPLATE_DOCKER
    return (
        template
        .replace("__LAUNCH_KWARGS__", launch_kwargs)
        .replace("__CONTEXT_KWARGS__", context_kwargs)
        .replace("__DEFAULT_TIMEOUT_MS__", str(default_timeout_ms))
        .replace("__NAVIGATION_TIMEOUT_MS__", str(navigation_timeout_ms))
    )
