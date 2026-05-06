from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def summarize_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(cases)
    passed = sum(1 for case in cases if case.get("passed"))
    failed = total - passed
    latencies = [case["latency_ms"] for case in cases if isinstance(case.get("latency_ms"), int)]
    repaired = sum(1 for case in cases if case.get("passed") and case.get("attempts", 1) > 1)
    phase_names = ("record", "compile", "replay")
    phase_rates = {
        f"{phase}_pass_rate": (
            sum(
                1
                for case in cases
                if ((case.get("phase_results") or {}).get(phase) or {}).get("status") == "passed"
            )
            / total
            if total
            else 0.0
        )
        for phase in phase_names
    }
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": passed / total if total else 0.0,
        "first_pass_rate": sum(1 for case in cases if case.get("passed") and case.get("attempts", 1) == 1) / total
        if total
        else 0.0,
        "repair_pass_rate": repaired / total if total else 0.0,
        "average_latency_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
        **phase_rates,
    }


def write_reports(report: dict[str, Any], report_dir: str | Path) -> dict[str, Path]:
    root = Path(report_dir)
    runs = root / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    run_id = report["run_id"]

    latest_json = root / "latest-report.json"
    latest_md = root / "latest-report.md"
    run_json = runs / f"{run_id}-report.json"
    run_md = runs / f"{run_id}-report.md"

    serialized = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    latest_json.write_text(serialized + "\n", encoding="utf-8")
    run_json.write_text(serialized + "\n", encoding="utf-8")
    markdown = render_markdown(report)
    latest_md.write_text(markdown, encoding="utf-8")
    run_md.write_text(markdown, encoding="utf-8")
    return {"latest_json": latest_json, "latest_md": latest_md, "run_json": run_json, "run_md": run_md}


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        f"# RPA Eval Report {report['run_id']}",
        "",
        "## Summary",
        "",
        f"- Total: {summary['total']}",
        f"- Passed: {summary['passed']}",
        f"- Failed: {summary['failed']}",
        f"- Pass rate: {summary['pass_rate']:.1%}",
        f"- First-pass rate: {summary['first_pass_rate']:.1%}",
        f"- Repair-pass rate: {summary['repair_pass_rate']:.1%}",
        f"- Record-pass rate: {summary.get('record_pass_rate', 0):.1%}",
        f"- Compile-pass rate: {summary.get('compile_pass_rate', 0):.1%}",
        f"- Replay-pass rate: {summary.get('replay_pass_rate', 0):.1%}",
        f"- Average latency: {summary['average_latency_ms']} ms",
        "",
        "## Cases",
        "",
        "| Case | Result | Record | Compile | Replay | Failure stage | Latency | Message |",
        "| --- | --- | --- | --- | --- | --- | ---: | --- |",
    ]
    for case in report["cases"]:
        result = "PASS" if case.get("passed") else "FAIL"
        message = str(case.get("failure_message") or "").replace("|", "\\|")
        phases = case.get("phase_results") or {}
        record_status = (phases.get("record") or {}).get("status", "")
        compile_status = (phases.get("compile") or {}).get("status", "")
        replay_status = (phases.get("replay") or {}).get("status", "")
        lines.append(
            f"| {case['id']} | {result} | {record_status} | {compile_status} | {replay_status} | "
            f"{case.get('failure_stage') or ''} | "
            f"{case.get('latency_ms') or 0} | {message} |"
        )

    lines.extend(["", "## Expected Artifacts", ""])
    for case in report["cases"]:
        lines.append(f"### {case['id']}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(case.get("expected", {}), ensure_ascii=False, indent=2, default=str))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)
