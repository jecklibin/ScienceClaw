WORKFLOW_RUNNER_TEMPLATE = '''"""Generated workflow skill runner."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent


class WorkflowContext:
    def __init__(self, params: dict[str, Any]):
        self.params = params
        self.outputs: dict[str, dict[str, Any]] = {}

    def resolve(self, source_ref: str | None) -> Any:
        if not source_ref:
            return None
        if source_ref.startswith("params."):
            return self.params.get(source_ref.removeprefix("params."))
        if ".outputs." in source_ref:
            segment_id, output_name = source_ref.split(".outputs.", 1)
            return self.outputs.get(segment_id, {}).get(output_name)
        return None

    def store_segment_outputs(self, segment_id: str, values: dict[str, Any]) -> None:
        self.outputs[segment_id] = values

    def final_outputs(self) -> dict[str, Any]:
        return {
            "status": "success",
            "outputs": self.outputs,
        }


def load_json(name: str) -> dict[str, Any]:
    return json.loads((BASE_DIR / name).read_text(encoding="utf-8"))


def load_params(kwargs: dict[str, Any]) -> dict[str, Any]:
    schema = load_json("params.schema.json")
    params: dict[str, Any] = {}
    for name, spec in schema.get("properties", {}).items():
        if "default" in spec:
            params[name] = spec["default"]
    params.update(kwargs)
    return params


def run_rpa_segment(segment: dict[str, Any], context: WorkflowContext) -> dict[str, Any]:
    config_path = segment.get("config_path")
    config = load_json(config_path) if config_path else segment.get("config", {})
    return {
        output.get("name"): output.get("artifact_ref") or config.get("last_output")
        for output in segment.get("outputs", [])
        if output.get("name")
    }


def run_script_segment(segment: dict[str, Any], context: WorkflowContext) -> dict[str, Any]:
    entry = segment["entry"]
    script_path = BASE_DIR / entry
    spec = importlib.util.spec_from_file_location(f"workflow_segment_{segment['id']}", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load script segment: {entry}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "run"):
        raise RuntimeError(f"Script segment {entry} must define run(context)")
    return module.run(context)


def run_mcp_segment(segment: dict[str, Any], context: WorkflowContext) -> dict[str, Any]:
    return {
        "status": "metadata_only",
        "tool": segment.get("tool"),
    }


def run_llm_segment(segment: dict[str, Any], context: WorkflowContext) -> dict[str, Any]:
    return {
        "status": "metadata_only",
        "schema": segment.get("schema"),
    }


def run(**kwargs):
    workflow = load_json("workflow.json")
    params = load_params(kwargs)
    context = WorkflowContext(params=params)

    for segment in workflow.get("segments", []):
        kind = segment.get("kind")
        if kind == "rpa":
            result = run_rpa_segment(segment, context)
        elif kind == "script":
            result = run_script_segment(segment, context)
        elif kind == "mcp":
            result = run_mcp_segment(segment, context)
        elif kind == "llm":
            result = run_llm_segment(segment, context)
        else:
            raise ValueError(f"Unsupported segment kind: {kind}")
        context.store_segment_outputs(segment["id"], result)

    return context.final_outputs()
'''
