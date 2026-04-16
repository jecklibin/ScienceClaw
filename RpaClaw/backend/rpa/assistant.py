import json
import logging
import re
import asyncio
import ast
from typing import Dict, List, Any, AsyncGenerator, Optional, Callable

from playwright.async_api import Page
from backend.deepagent.engine import get_llm_model
from backend.rpa.segment_models import SegmentSpec
from backend.rpa.segment_runner import run_segment
from backend.rpa.segment_validator import validate_segment_result
from backend.rpa.intent_planner import plan_compilable_intent
from backend.rpa.segment_router import route_intent
from backend.rpa.segment_compiler_atomic import compile_atomic_segment
from backend.rpa.segment_compiler_selection import compile_selection_segment
from backend.rpa.assistant_runtime import (
    build_frame_path_from_frame,
    build_page_snapshot,
    execute_structured_intent,
    resolve_structured_intent,
    resolve_collection_target,
)

# Active ReAct agent instances keyed by session_id
_active_agents: Dict[str, "RPAReActAgent"] = {}

logger = logging.getLogger(__name__)

ELEMENT_EXTRACTION_TIMEOUT_S = 5.0
EXECUTION_TIMEOUT_S = 60.0
THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
THINK_CONTENT_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)
CONTROL_FLOW_HINT_RE = re.compile(r"\b(if|until|retry|repeat|otherwise)\b|refresh until", re.IGNORECASE)
DYNAMIC_SELECTION_HINT_RE = re.compile(
    r"\b(latest|earliest|highest|lowest)\b|\b(most|least)\b|status\s*(?:is|=)|\bfilter\b|\bcontains\b",
    re.IGNORECASE,
)
JAVASCRIPT_CODE_HINT_RE = re.compile(
    r"\b(const|let|var)\b|document\.|querySelector(?:All)?\(|=>|===|!==|\[\.\.\.",
    re.IGNORECASE,
)
READ_GOAL_VERB_RE = re.compile(
    r"获取|提取|读取|返回|输出|告诉我|extract|read|get\b|return\b|output\b|tell me|show\b",
    re.IGNORECASE,
)
READ_GOAL_OBJECT_RE = re.compile(
    r"标题|名称|文本|内容|结果|链接|地址|title|name|text|content|value|url",
    re.IGNORECASE,
)
REPO_NAME_LITERAL_RE = re.compile(r"^[A-Za-z0-9_.-]+\s*/\s*[A-Za-z0-9_.-]+$")

SYSTEM_PROMPT = "Legacy RPAAssistant prompt placeholder. Runtime entrypoint is segment mode."


async def _execute_on_page(page: Page, code: str) -> Dict[str, Any]:
    """Execute AI-generated code directly on the page object."""
    try:
        await page.evaluate("window.__rpa_paused = true")
    except Exception:
        pass
    try:
        namespace: Dict[str, Any] = {"page": page}
        exec(compile(code, "<rpa_assistant>", "exec"), namespace)
        if "run" in namespace and callable(namespace["run"]):
            ret = await asyncio.wait_for(namespace["run"](page), timeout=EXECUTION_TIMEOUT_S)
            if isinstance(ret, dict):
                output = str(ret.get("output", "") or "")
                if not output:
                    output = json.dumps(ret, ensure_ascii=False)
                return {
                    "success": True,
                    "output": output,
                    "error": None,
                    "selected_artifacts": ret,
                    "page_changed": bool(ret.get("page_changed", False)),
                }
            return {"success": True, "output": str(ret) if ret else "ok", "error": None}
        else:
            return {"success": False, "output": "", "error": "No run(page) function defined"}
    except asyncio.TimeoutError:
        return {"success": False, "output": "", "error": f"Command execution timed out ({EXECUTION_TIMEOUT_S:.0f}s)"}
    except Exception:
        import traceback
        return {"success": False, "output": "", "error": traceback.format_exc()}
    finally:
        try:
            await page.evaluate("window.__rpa_paused = false")
        except Exception:
            pass


def _extract_llm_response_text(response: Any) -> str:
    """Normalize LangChain AIMessage content into a plain text response."""
    content = getattr(response, "content", "")
    additional_kwargs = getattr(response, "additional_kwargs", {}) or {}

    reasoning = additional_kwargs.get("reasoning_content", "")
    fallback_text = reasoning.strip() if isinstance(reasoning, str) else ""

    if isinstance(content, list):
        text_parts: List[str] = []
        thinking_parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type", "")
                if block_type == "thinking":
                    thinking_parts.append(str(block.get("thinking", "")).strip())
                    continue
                text = block.get("text") or block.get("content")
                if text:
                    text_parts.append(str(text))
            elif isinstance(block, str):
                text_parts.append(block)
            elif block is not None:
                text_parts.append(str(block))
        clean = "\n".join(part.strip() for part in text_parts if str(part).strip()).strip()
        if clean:
            return clean
        thoughts = "\n".join(part for part in thinking_parts if part).strip()
        return thoughts or fallback_text

    if isinstance(content, str):
        clean = THINK_TAG_RE.sub("", content).strip()
        if clean:
            return clean
        if not fallback_text:
            matches = THINK_CONTENT_RE.findall(content)
            fallback_text = "\n".join(match.strip() for match in matches if match.strip()).strip()
        return fallback_text

    if content is None:
        return fallback_text

    text = str(content).strip()
    return text or fallback_text


def _extract_llm_chunk_text(chunk: Any) -> str:
    """Extract displayable text from a streamed chunk."""
    content = getattr(chunk, "content", "")
    if isinstance(content, list):
        text_parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type", "")
                if block_type == "thinking":
                    continue
                text = block.get("text") or block.get("content")
                if text:
                    text_parts.append(str(text))
            elif isinstance(block, str):
                text_parts.append(block)
            elif block is not None:
                text_parts.append(str(block))
        return "".join(text_parts)
    if isinstance(content, str):
        return THINK_TAG_RE.sub("", content)
    return ""


def _extract_llm_chunk_fallback_text(chunk: Any) -> str:
    """Extract reasoning/thinking fallback text from a streamed chunk."""
    additional_kwargs = getattr(chunk, "additional_kwargs", {}) or {}
    reasoning = additional_kwargs.get("reasoning_content", "")
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning.strip()

    content = getattr(chunk, "content", "")
    if isinstance(content, list):
        thoughts: List[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                thought = str(block.get("thinking", "")).strip()
                if thought:
                    thoughts.append(thought)
        return "\n".join(thoughts).strip()

    if isinstance(content, str):
        matches = THINK_CONTENT_RE.findall(content)
        return "\n".join(match.strip() for match in matches if match.strip()).strip()

    return ""


def _snapshot_frame_lines(snapshot: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    for container in snapshot.get("containers", []):
        lines.append(
            "Container: "
            f"{container.get('container_kind', 'container')} "
            f"{container.get('name', '')} "
            f"(actionable={len(container.get('child_actionable_ids') or [])}, "
            f"content={len(container.get('child_content_ids') or [])})"
        )
    for frame in snapshot.get("frames", []):
        lines.append(f"Frame: {frame.get('frame_hint', 'main document')}")
        for collection in frame.get("collections", []):
            lines.append(
                f"  Collection: {collection.get('kind', 'collection')} ({collection.get('item_count', 0)} items)"
            )
        for element in frame.get("elements", []):
            parts = [f"[{element.get('index', '?')}]"]
            if element.get("role"):
                parts.append(element["role"])
            parts.append(element.get("tag", "element"))
            if element.get("name"):
                parts.append(f'"{element["name"]}"')
            if element.get("placeholder"):
                parts.append(f'placeholder="{element["placeholder"]}"')
            if element.get("href"):
                parts.append(f'href="{element["href"]}"')
            if element.get("type"):
                parts.append(f'type={element["type"]}')
            lines.append("  " + " ".join(parts))
    return lines


REACT_SYSTEM_PROMPT = """You are an RPA automation planner.

You receive a user goal and the latest page snapshot. Plan exactly one ai_script segment at a time.

Return exactly one JSON object per turn, not wrapped in markdown.

Preferred format:
{
  "thought": "brief reasoning about the current page",
  "action": "execute|done|abort",
  "segment_goal": "what this segment will achieve on the current page",
  "segment_kind": "read_only|state_changing",
  "stop_reason": "goal_reached|before_state_change|after_state_change",
  "expected_outcome": {
    "type": "observation|page_state_changed|download_started",
    "summary": "what should be true after this segment"
  },
  "completion_check": {
    "url_not_same": true,
    "selected_target_key": "selected_repo_name",
    "page_contains_selected_target": true
  },
  "notes": "optional short note",
  "code": "required Python code that defines async def run(page): ..."
}

Rules:
1. Never return structured browser actions. Always plan one ai_script segment.
2. A segment may extract, compare, filter, branch, poll, and locate targets on the current page.
3. If the segment will execute any state-changing action such as click, press, submit, opening a link, switching tab, or expanding a panel, stop immediately after the first state-changing action.
4. Do not split a dynamic task into "extract a concrete name first, then click that fixed name later". Keep the dynamic selection and the action in the same segment.
5. For action goals such as click, open, enter, download, or submit, do not claim success with a read-only segment that only extracts text.
6. Code must be Python and define async def run(page): using the Playwright async API.
7. When the segment dynamically chooses a target, return the chosen values in a dict so the runtime can validate the result. For example: return {"selected_repo_name": "owner / repo"}.
8. Only output action=done when the user goal is actually complete after the previous segment.
9. For read/extraction goals, do not output action=done until a prior read-only segment has returned the requested value in output.
10. For read-only extraction segments, return the exact visible value from the page in output or a named field.
11. Do not use selected_target_key/page_contains_selected_target for read-only extraction unless you need an explicit post-segment visibility check.
12. Use the current page snapshot to derive adaptive locators and comparisons.
13. Generated code must stay adaptive. Do not hard-code volatile values observed on the page or in previous segments, such as badge counts, issue titles, repository names, or full accessible names containing changing numbers. Prefer semantic matching, prefixes, contains, regex, or page structure.
"""




class RPAReActAgent:
    """ReAct-based autonomous agent: Observe → Think → Act loop."""

    MAX_STEPS = 20
    MAX_RETRYABLE_SEGMENT_FAILURES = 2

    @staticmethod
    def _classify_code_execution_error(error_message: str) -> tuple[str, bool]:
        text = str(error_message or "").lower()
        if "appears to be javascript" in text or "requires python with async def run(page)" in text:
            return "invalid_generated_code", True
        if "syntaxerror" in text or "indentationerror" in text or "invalid syntax" in text:
            return "invalid_generated_code", True
        if "no run(page) function defined" in text:
            return "invalid_generated_code", True
        if "timed out" in text:
            return "execution_timeout", True
        if "traceback" in text or "exception" in text or "error" in text:
            return "code_execution_failed", True
        return "code_execution_failed", True

    @staticmethod
    def _looks_like_javascript(code: str) -> bool:
        return bool(JAVASCRIPT_CODE_HINT_RE.search(str(code or "")))

    @staticmethod
    def _has_meaningful_output(output: Any) -> bool:
        text = str(output or "").strip()
        return bool(text and text not in {"ok", "None"})

    @staticmethod
    def _goal_requires_observable_output(goal: str) -> bool:
        text = str(goal or "")
        return bool(READ_GOAL_VERB_RE.search(text) and READ_GOAL_OBJECT_RE.search(text))

    @staticmethod
    def _text_variants(value: str) -> set[str]:
        normalized = " ".join(str(value or "").strip().lower().split())
        if not normalized:
            return set()

        variants = {normalized, re.sub(r"\s+", "", normalized)}
        slash_compact = re.sub(r"\s*/\s*", "/", normalized)
        variants.add(slash_compact)
        variants.add(re.sub(r"\s+", "", slash_compact))
        return {variant for variant in variants if variant}

    @staticmethod
    def _snapshot_texts(snapshot: Dict[str, Any]) -> list[str]:
        texts = [
            str(snapshot.get("url", "") or ""),
            str(snapshot.get("title", "") or ""),
        ]
        for frame in snapshot.get("frames", []) or []:
            texts.append(str(frame.get("frame_hint", "") or ""))
            for element in frame.get("elements", []) or []:
                texts.append(str(element.get("name", "") or ""))
                texts.append(str(element.get("href", "") or ""))
            for collection in frame.get("collections", []) or []:
                for item in collection.get("items", []) or []:
                    texts.append(str(item.get("name", "") or ""))
        for node in snapshot.get("actionable_nodes", []) or []:
            texts.append(str(node.get("name", "") or ""))
        for node in snapshot.get("content_nodes", []) or []:
                texts.append(str(node.get("text", "") or ""))
        return [text for text in texts if text]

    @staticmethod
    def _snapshot_visible_texts(snapshot: Dict[str, Any]) -> list[str]:
        texts = [str(snapshot.get("title", "") or "")]
        for frame in snapshot.get("frames", []) or []:
            texts.append(str(frame.get("frame_hint", "") or ""))
            for element in frame.get("elements", []) or []:
                texts.append(str(element.get("name", "") or ""))
            for collection in frame.get("collections", []) or []:
                for item in collection.get("items", []) or []:
                    texts.append(str(item.get("name", "") or ""))
        for node in snapshot.get("actionable_nodes", []) or []:
            texts.append(str(node.get("name", "") or ""))
        for node in snapshot.get("content_nodes", []) or []:
            texts.append(str(node.get("text", "") or ""))
        for container in snapshot.get("containers", []) or []:
            texts.append(str(container.get("name", "") or ""))
            texts.append(str(container.get("summary", "") or ""))
        return [text for text in texts if text]

    @staticmethod
    def _extract_python_string_literals(code: str) -> list[str]:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []

        literals: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value.strip()
                if text:
                    literals.append(text)
        return literals

    @classmethod
    def _find_non_adaptive_literal(cls, code: str, snapshot: Dict[str, Any], goal: str) -> str:
        goal_variants = cls._text_variants(goal)
        visible_snapshot_variants = set()
        for text in cls._snapshot_visible_texts(snapshot):
            visible_snapshot_variants.update(cls._text_variants(text))

        for literal in cls._extract_python_string_literals(code):
            literal_variants = cls._text_variants(literal)
            if not literal_variants:
                continue

            normalized_literal = " ".join(literal.strip().split())
            is_volatile = any(char.isdigit() for char in normalized_literal) or bool(
                REPO_NAME_LITERAL_RE.fullmatch(normalized_literal)
            )
            if not is_volatile:
                continue

            if any(
                literal_variant and any(literal_variant in goal_variant for goal_variant in goal_variants)
                for literal_variant in literal_variants
            ):
                continue

            if any(
                literal_variant and any(
                    literal_variant in snapshot_variant or snapshot_variant in literal_variant
                    for snapshot_variant in visible_snapshot_variants
                )
                for literal_variant in literal_variants
            ):
                return literal

        return ""

    def __init__(self):
        self._aborted: bool = False
        self._history: List[Dict[str, str]] = []  # persists across turns

    def abort(self) -> None:
        self._aborted = True

    async def run(
        self,
        session_id: str,
        page: Page,
        goal: str,
        existing_steps: List[Dict[str, Any]],
        model_config: Optional[Dict[str, Any]] = None,
        page_provider: Optional[Callable[[], Optional[Page]]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        async for event in self._run_segment_loop(
            session_id=session_id,
            page=page,
            goal=goal,
            existing_steps=existing_steps,
            model_config=model_config,
            page_provider=page_provider,
        ):
            yield event
        return

        self._aborted = False
        steps_done = 0
        structured_retry_attempts: Dict[str, int] = {}

        # Append new user goal to persistent history
        steps_summary = ""
        if existing_steps:
            lines = [f"{i+1}. {s.get('description', s.get('action', ''))}" for i, s in enumerate(existing_steps)]
            steps_summary = "\nExisting steps:\n" + "\n".join(lines) + "\n"
        self._history.append({"role": "user", "content": f"Goal: {goal}{steps_summary}"})

        for iteration in range(self.MAX_STEPS):
            if self._aborted:
                yield {"event": "agent_aborted", "data": {"reason": "用户中止"}}
                return

            # Observe
            current_page = page_provider() if page_provider else page
            if current_page is None:
                yield {"event": "agent_aborted", "data": {"reason": "No active page available"}}
                return
            snapshot = await build_page_snapshot(current_page, build_frame_path_from_frame)
            obs = self._build_observation(snapshot, steps_done)
            self._history.append({"role": "user", "content": obs})

            # Think — stream LLM response
            full_response = ""
            async for chunk in self._stream_llm(self._history, model_config):
                full_response += chunk

            self._history.append({"role": "assistant", "content": full_response})

            # Parse JSON
            parsed = self._parse_json(full_response)
            if not parsed:
                yield {"event": "agent_aborted", "data": {"reason": f"Unable to parse agent response: {full_response[:200]}"}}
                return

            thought = parsed.get("thought", "")
            action = parsed.get("action", "execute")
            structured_intent = self._extract_structured_execute_intent(parsed, goal)
            execution_mode = self._normalize_execution_mode(parsed, goal)
            code = parsed.get("code", "")
            description = parsed.get("description", "Execute step")
            subgoal = self._normalize_subgoal(parsed, description)
            upgrade_reason = str(parsed.get("upgrade_reason", "") or "").strip()
            risk = parsed.get("risk", "none")
            risk_reason = parsed.get("risk_reason", "")
            retry_key = self._build_retry_key(
                execution_mode=execution_mode,
                structured_intent=structured_intent,
                description=description,
                goal=goal,
            )
            executable_code = self._normalize_run_function(code) if execution_mode == "code" and code else ""
            action_payload = executable_code or ""
            if execution_mode == "structured" and structured_intent:
                action_payload = json.dumps(structured_intent, ensure_ascii=False)

            yield {"event": "agent_thought", "data": {"text": thought}}

            if action == "done":
                yield {"event": "agent_done", "data": {"total_steps": steps_done}}
                return

            if action == "abort":
                yield {"event": "agent_aborted", "data": {"reason": thought}}
                return

            if execution_mode == "structured" and not structured_intent:
                yield {"event": "agent_aborted", "data": {"reason": "Structured execution requested without a valid atomic action"}}
                return

            if execution_mode == "code" and not executable_code:
                yield {"event": "agent_aborted", "data": {"reason": "Code execution requested without executable code"}}
                return

            # High-risk confirmation
            if risk == "high":
                self._confirm_event = asyncio.Event()
                self._confirm_approved = False
                yield {"event": "confirm_required", "data": {
                    "description": description,
                    "risk_reason": risk_reason,
                    "code": action_payload,
                }}
                await self._confirm_event.wait()
                self._confirm_event = None
                if self._aborted:
                    yield {"event": "agent_aborted", "data": {"reason": "User aborted"}}
                    return
                if not self._confirm_approved:
                    self._history.append({"role": "user", "content": "User rejected that step. Continue with a safer next step or finish."})
                    continue

            # Act
            yield self._attempt_event(
                description=description,
                subgoal=subgoal,
                execution_mode=execution_mode,
                code=action_payload,
            )
            yield {
                "event": "agent_action",
                "data": {
                    "description": description,
                    "code": action_payload,
                },
            }
            current_page = page_provider() if page_provider else page
            if current_page is None:
                yield {"event": "agent_aborted", "data": {"reason": "No active page available"}}
                return
            if execution_mode == "structured":
                result = await run_structured_intent(current_page, snapshot, structured_intent)
            else:
                if self._looks_like_javascript(code):
                    result = {
                        "success": False,
                        "output": "",
                        "error": (
                            "Generated code appears to be JavaScript. "
                            "Code mode requires Python with async def run(page): ..."
                        ),
                    }
                else:
                    result = await _execute_on_page(current_page, executable_code)
            if result["success"]:
                if execution_mode == "code":
                    recovery_attempts = structured_retry_attempts.pop(retry_key, 0)
                    step_data = self._build_ai_script_step(
                        goal=goal,
                        description=description,
                        code=executable_code,
                        upgrade_reason=upgrade_reason,
                        recovery_attempts=recovery_attempts,
                    )
                    yield {
                        "event": "agent_escalated",
                        "data": {
                            "description": description,
                            "upgrade_reason": upgrade_reason or "custom_code",
                        },
                    }
                    validated = True
                else:
                    structured_retry_attempts.pop(retry_key, None)
                    step_data = result.get("step") or {
                        "action": "ai_script",
                        "source": "ai",
                        "value": executable_code,
                        "description": description,
                        "prompt": goal,
                    }
                    validated = await self._post_check_subgoal(
                        current_page=current_page,
                        snapshot=snapshot,
                        subgoal=subgoal,
                        step_data=step_data,
                        output=result.get("output", ""),
                    )
                if not validated:
                    self._history.append({
                        "role": "user",
                        "content": f"Attempt did not complete the subgoal: {subgoal}. Try a different approach.",
                    })
                    continue
                steps_done += 1
                output = result.get("output", "")
                # If there's meaningful output, append to description for visibility
                if output and output != "ok" and output != "None":
                    yield {"event": "agent_step_committed", "data": {"step": step_data, "output": output}}
                    self._history.append({"role": "user", "content": f"Step committed: {description}\nOutput: {output}"})
                else:
                    yield {"event": "agent_step_committed", "data": {"step": step_data}}
                    self._history.append({"role": "user", "content": f"Step committed: {description}"})
            else:
                error_msg = result.get("error", "Unknown error")
                if execution_mode == "structured":
                    error_code = str(result.get("error_code", "") or "")
                    if result.get("retryable"):
                        attempt = structured_retry_attempts.get(retry_key, 0) + 1
                        structured_retry_attempts[retry_key] = attempt
                        if attempt > self.MAX_RETRYABLE_STRUCTURED_FAILURES:
                            self._history.append({
                                "role": "user",
                                "content": f"Structured execution failed: {error_msg[:500]}\nError code: {error_code}\nRetry budget exhausted.",
                            })
                            yield {
                                "event": "agent_aborted",
                                "data": {
                                    "description": description,
                                    "error_code": error_code,
                                    "message": error_msg,
                                    "reason": error_msg,
                                },
                            }
                            return
                        if attempt == self.MAX_RETRYABLE_STRUCTURED_FAILURES:
                            yield {
                                "event": "agent_warning",
                                "data": {
                                    "description": description,
                                    "error_code": error_code,
                                    "message": "Retry budget nearly exhausted",
                                },
                            }
                        yield {
                            "event": "agent_recovering",
                            "data": {
                                "description": description,
                                "error_code": error_code,
                                "message": error_msg,
                                "attempt": attempt,
                            },
                        }
                        self._history.append({
                            "role": "user",
                            "content": f"Structured execution failed: {error_msg[:500]}\nError code: {error_code}\nRetry with a safer or more precise next atomic step.",
                        })
                        continue
                    self._history.append({
                        "role": "user",
                        "content": f"Structured execution failed: {error_msg[:500]}\nError code: {error_code}\nAbort the current plan.",
                    })
                    yield {
                        "event": "agent_aborted",
                        "data": {
                            "description": description,
                            "error_code": error_code,
                            "message": error_msg,
                            "reason": error_msg,
                        },
                    }
                    return
                error_code, retryable = self._classify_code_execution_error(error_msg)
                if retryable:
                    attempt = structured_retry_attempts.get(retry_key, 0) + 1
                    structured_retry_attempts[retry_key] = attempt
                    if attempt > self.MAX_RETRYABLE_STRUCTURED_FAILURES:
                        yield {
                            "event": "agent_aborted",
                            "data": {
                                "description": description,
                                "error_code": error_code,
                                "message": error_msg,
                                "reason": error_msg,
                            },
                        }
                        return
                    if attempt == self.MAX_RETRYABLE_STRUCTURED_FAILURES:
                        yield {
                            "event": "agent_warning",
                            "data": {
                                "description": description,
                                "error_code": error_code,
                                "message": "Retry budget nearly exhausted",
                            },
                        }
                    yield {
                        "event": "agent_recovering",
                        "data": {
                            "description": description,
                            "error_code": error_code,
                            "message": error_msg,
                            "attempt": attempt,
                        },
                    }
                    self._history.append({
                        "role": "user",
                        "content": (
                            f"Code execution failed: {error_msg[:500]}\n"
                            f"Error code: {error_code}\n"
                            "Generate corrected Python async def run(page) code for the same subgoal."
                        ),
                    })
                    continue
                yield {
                    "event": "agent_aborted",
                    "data": {
                        "description": description,
                        "error_code": error_code,
                        "message": error_msg,
                        "reason": error_msg,
                    },
                }
                return

        yield {"event": "agent_done", "data": {"total_steps": steps_done}}

    @staticmethod
    def _build_observation(snapshot: Dict[str, Any], steps_done: int) -> str:
        frame_lines = _snapshot_frame_lines(snapshot)
        return f"""Current page state:
URL: {snapshot.get('url', '')}
Title: {snapshot.get('title', '')}
Completed steps: {steps_done}

Current page snapshot:
{chr(10).join(frame_lines) or "(no observable elements)"}

Return the next JSON action."""

    @staticmethod
    def _compile_deterministic_segment(goal: str, snapshot: Dict[str, Any]) -> tuple[Optional[SegmentSpec], str, str]:
        intent = plan_compilable_intent(goal, snapshot)
        if intent is None:
            return None, "", ""

        route = route_intent(intent)
        if route == "selection":
            spec = compile_selection_segment(intent, goal, snapshot)
        elif route == "atomic":
            spec = compile_atomic_segment(intent, goal, snapshot)
        else:
            spec = None

        if spec is None:
            return None, "", ""

        if intent.goal_type == "read":
            thought = "Current page already contains the requested list context. Deterministically read the target item from the visible collection."
        elif intent.selection.mode in {"first", "last", "nth"}:
            thought = "Current page exposes a stable repeated-item collection. Deterministically select the requested ordinal item and complete the action in one segment."
        else:
            thought = "Current page exposes a stable repeated-item collection. Deterministically compute the requested selection on the visible list and complete the action in one segment."
        return spec, thought, route

    async def _run_segment_loop(
        self,
        *,
        session_id: str,
        page: Page,
        goal: str,
        existing_steps: List[Dict[str, Any]],
        model_config: Optional[Dict[str, Any]],
        page_provider: Optional[Callable[[], Optional[Page]]],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        del session_id
        self._aborted = False
        steps_done = 0
        segment_retry_attempts: Dict[str, int] = {}
        read_result_committed = False
        deterministic_disabled = False

        steps_summary = ""
        if existing_steps:
            lines = [f"{i + 1}. {s.get('description', s.get('action', ''))}" for i, s in enumerate(existing_steps)]
            steps_summary = "\nExisting steps:\n" + "\n".join(lines) + "\n"
        self._history.append({"role": "user", "content": f"Goal: {goal}{steps_summary}"})

        for _iteration in range(self.MAX_STEPS):
            if self._aborted:
                yield {"event": "recording_aborted", "data": {"reason": "User aborted"}}
                return

            current_page = page_provider() if page_provider else page
            if current_page is None:
                yield {"event": "recording_aborted", "data": {"reason": "No active page available"}}
                return

            snapshot = await build_page_snapshot(current_page, build_frame_path_from_frame)
            compiled_spec: Optional[SegmentSpec] = None
            compiled_route = ""
            thought = ""
            if not deterministic_disabled:
                compiled_spec, thought, compiled_route = self._compile_deterministic_segment(goal, snapshot)

            if compiled_spec is not None:
                spec = compiled_spec
                retry_key = f"compiled:{compiled_route}:{spec.segment_goal.strip() or goal.strip() or 'segment'}"
                if thought:
                    yield {"event": "segment_planned", "data": {"thought": thought}}
            else:
                observation = self._build_observation(snapshot, steps_done).replace("Return the next JSON action.", "Return the next JSON segment spec.")
                self._history.append({"role": "user", "content": observation})

                full_response = ""
                async for chunk in self._stream_llm(self._history, model_config):
                    full_response += chunk
                self._history.append({"role": "assistant", "content": full_response})

                parsed = self._parse_json(full_response)
                if not parsed:
                    retry_key = "__planner_parse__"
                    error_code = "invalid_planner_response"
                    error_msg = f"Unable to parse planner response: {full_response[:200]}"
                    attempt = segment_retry_attempts.get(retry_key, 0) + 1
                    segment_retry_attempts[retry_key] = attempt
                    if attempt > self.MAX_RETRYABLE_SEGMENT_FAILURES:
                        yield {"event": "recording_aborted", "data": {"error_code": error_code, "reason": error_msg}}
                        return
                    yield {
                        "event": "segment_recovering",
                        "data": {
                            "segment_goal": goal,
                            "error_code": error_code,
                            "message": error_msg,
                            "attempt": attempt,
                        },
                    }
                    self._history.append({
                        "role": "user",
                        "content": (
                            "Your previous planner response was not valid JSON. Return exactly one complete JSON object only, "
                            "with properly escaped Python code in the code field."
                        ),
                    })
                    continue

                thought = str(parsed.get("thought", "") or "").strip()
                if thought:
                    yield {"event": "segment_planned", "data": {"thought": thought}}

                action = str(parsed.get("action", "execute") or "execute").strip().lower()
                if action == "done":
                    if self._goal_requires_observable_output(goal) and not read_result_committed:
                        retry_key = "__goal_completion__"
                        error_msg = "Read goal is not complete yet. Plan a read-only extraction segment that returns the requested value."
                        attempt = segment_retry_attempts.get(retry_key, 0) + 1
                        segment_retry_attempts[retry_key] = attempt
                        if attempt > self.MAX_RETRYABLE_SEGMENT_FAILURES:
                            yield {"event": "recording_aborted", "data": {"error_code": "goal_not_complete", "reason": error_msg}}
                            return
                        yield {
                            "event": "segment_recovering",
                            "data": {
                                "segment_goal": goal,
                                "error_code": "goal_not_complete",
                                "message": error_msg,
                                "attempt": attempt,
                            },
                        }
                        self._history.append({
                            "role": "user",
                            "content": (
                                "The goal is not complete yet. You must plan one read-only segment that extracts the requested value "
                                "from the current page and return that value in output before using action=done."
                            ),
                        })
                        continue
                    yield {"event": "recording_done", "data": {"total_steps": steps_done}}
                    return
                if action == "abort":
                    yield {"event": "recording_aborted", "data": {"reason": thought or "Planner aborted"}}
                    return

                spec = self._parse_segment_spec(parsed)
                if spec is None:
                    yield {"event": "recording_aborted", "data": {"reason": "Planner did not return a valid segment spec"}}
                    return

                retry_key = spec.segment_goal.strip() or goal.strip() or "segment"
            yield {
                "event": "segment_planned",
                "data": {
                    "segment_goal": spec.segment_goal,
                    "segment_kind": spec.segment_kind,
                    "stop_reason": spec.stop_reason,
                    "code": spec.code,
                    "notes": spec.notes,
                },
            }

            if not spec.code.strip() or self._looks_like_javascript(spec.code):
                error_msg = (
                    "Generated code appears to be JavaScript. Segment mode requires Python with async def run(page): ..."
                    if self._looks_like_javascript(spec.code)
                    else "Segment code is required"
                )
                error_code = "invalid_generated_code"
                attempt = segment_retry_attempts.get(retry_key, 0) + 1
                segment_retry_attempts[retry_key] = attempt
                deterministic_disabled = deterministic_disabled or compiled_spec is not None
                if attempt > self.MAX_RETRYABLE_SEGMENT_FAILURES:
                    yield {"event": "recording_aborted", "data": {"segment_goal": spec.segment_goal, "error_code": error_code, "reason": error_msg}}
                    return
                yield {"event": "segment_recovering", "data": {"segment_goal": spec.segment_goal, "error_code": error_code, "message": error_msg, "attempt": attempt}}
                self._history.append({
                    "role": "user",
                    "content": (
                        f"Segment generation failed: {error_msg}\n"
                        f"Error code: {error_code}\n"
                        "Rewrite the same segment in Python async def run(page)."
                    ),
                })
                continue

            non_adaptive_literal = self._find_non_adaptive_literal(spec.code, snapshot, goal)
            if non_adaptive_literal:
                error_code = "non_adaptive_code"
                error_msg = (
                    f"Generated code hard-codes volatile page data: {non_adaptive_literal!r}. "
                    "Rewrite the same segment using adaptive matching."
                )
                attempt = segment_retry_attempts.get(retry_key, 0) + 1
                segment_retry_attempts[retry_key] = attempt
                deterministic_disabled = deterministic_disabled or compiled_spec is not None
                if attempt > self.MAX_RETRYABLE_SEGMENT_FAILURES:
                    yield {"event": "recording_aborted", "data": {"segment_goal": spec.segment_goal, "error_code": error_code, "reason": error_msg}}
                    return
                yield {
                    "event": "segment_recovering",
                    "data": {
                        "segment_goal": spec.segment_goal,
                        "error_code": error_code,
                        "message": error_msg,
                        "attempt": attempt,
                    },
                }
                self._history.append({
                    "role": "user",
                    "content": (
                        f"Segment generation failed: {error_msg}\n"
                        f"Error code: {error_code}\n"
                        "Rewrite the segment without hard-coding volatile text, counts, titles, or repository names from the page."
                    ),
                })
                continue

            yield {"event": "segment_started", "data": {"segment_goal": spec.segment_goal}}
            current_page = page_provider() if page_provider else page
            if current_page is None:
                yield {"event": "recording_aborted", "data": {"reason": "No active page available"}}
                return

            run_result = await run_segment(
                page=current_page,
                spec=spec,
                executor=_execute_on_page,
                snapshot_builder=lambda current: build_page_snapshot(current, build_frame_path_from_frame),
            )
            after_snapshot = run_result.after_snapshot or {}
            yield {
                "event": "segment_reobserved",
                "data": {
                    "segment_goal": spec.segment_goal,
                    "page_changed": run_result.page_changed,
                    "url": after_snapshot.get("url", ""),
                    "title": after_snapshot.get("title", ""),
                },
            }

            if not run_result.success:
                error_msg = run_result.error or "Unknown segment error"
                error_code, _retryable = self._classify_code_execution_error(error_msg)
                attempt = segment_retry_attempts.get(retry_key, 0) + 1
                segment_retry_attempts[retry_key] = attempt
                deterministic_disabled = deterministic_disabled or compiled_spec is not None
                if attempt > self.MAX_RETRYABLE_SEGMENT_FAILURES:
                    yield {"event": "recording_aborted", "data": {"segment_goal": spec.segment_goal, "error_code": error_code, "reason": error_msg}}
                    return
                yield {"event": "segment_recovering", "data": {"segment_goal": spec.segment_goal, "error_code": error_code, "message": error_msg, "attempt": attempt}}
                self._history.append({
                    "role": "user",
                    "content": (
                        f"Segment execution failed: {error_msg[:500]}\n"
                        f"Error code: {error_code}\n"
                        "Re-plan the same goal as one ai_script segment using the latest page state."
                    ),
                })
                continue

            validation = await validate_segment_result(goal=goal, spec=spec, run_result=run_result)
            if not validation.passed:
                attempt = segment_retry_attempts.get(retry_key, 0) + 1
                segment_retry_attempts[retry_key] = attempt
                deterministic_disabled = deterministic_disabled or compiled_spec is not None
                yield {"event": "segment_validation_failed", "data": {"segment_goal": spec.segment_goal, "reason": validation.reason}}
                if attempt > self.MAX_RETRYABLE_SEGMENT_FAILURES:
                    yield {"event": "recording_aborted", "data": {"segment_goal": spec.segment_goal, "reason": validation.reason}}
                    return
                yield {"event": "segment_recovering", "data": {"segment_goal": spec.segment_goal, "error_code": "validation_failed", "message": validation.reason, "attempt": attempt}}
                self._history.append({
                    "role": "user",
                    "content": (
                        f"Segment validation failed: {validation.reason}\n"
                        "Use the latest page state and plan the next segment without hard-coding dynamic content."
                    ),
                })
                continue

            recovery_attempts = segment_retry_attempts.pop(retry_key, 0)
            step_data = self._build_ai_script_step(
                goal=goal,
                description=spec.segment_goal,
                code=spec.code,
                upgrade_reason=compiled_route or "segment",
                recovery_attempts=recovery_attempts,
            )
            step_data["assistant_diagnostics"].update({
                "execution_mode": "segment",
                "segment_kind": spec.segment_kind,
                "stop_reason": spec.stop_reason,
                "compiled_route": compiled_route or "",
            })
            steps_done += 1
            payload: Dict[str, Any] = {"step": step_data}
            if run_result.output and run_result.output not in {"ok", "None"}:
                payload["output"] = run_result.output
            if spec.segment_kind == "read_only" and self._has_meaningful_output(run_result.output):
                read_result_committed = True
            yield {"event": "segment_committed", "data": payload}
            self._history.append({"role": "user", "content": f"Segment committed: {spec.segment_goal}"})
            if validation.goal_completed:
                yield {"event": "recording_done", "data": {"total_steps": steps_done}}
                return

        yield {"event": "recording_done", "data": {"total_steps": steps_done}}

    @staticmethod
    def _parse_segment_spec(parsed: Dict[str, Any]) -> Optional[SegmentSpec]:
        action = str(parsed.get("action", "execute") or "execute").strip().lower()
        if action in {"done", "abort"}:
            return None

        segment_goal = str(parsed.get("segment_goal", "") or parsed.get("description", "") or "").strip()
        segment_kind = str(parsed.get("segment_kind", "") or "read_only").strip().lower()
        stop_reason = str(parsed.get("stop_reason", "") or "goal_reached").strip().lower()
        expected_outcome = parsed.get("expected_outcome") if isinstance(parsed.get("expected_outcome"), dict) else {}
        completion_check = parsed.get("completion_check") if isinstance(parsed.get("completion_check"), dict) else {}
        code = str(parsed.get("code", "") or "").strip()
        notes = str(parsed.get("notes", "") or "").strip()

        if not segment_goal:
            return None
        if segment_kind not in {"read_only", "state_changing"}:
            segment_kind = "read_only"
        if stop_reason not in {"goal_reached", "before_state_change", "after_state_change"}:
            stop_reason = "goal_reached"

        return SegmentSpec(
            segment_goal=segment_goal,
            segment_kind=segment_kind,
            stop_reason=stop_reason,
            expected_outcome=expected_outcome,
            completion_check=completion_check,
            code=RPAReActAgent._normalize_run_function(code) if code else "",
            notes=notes,
        )

    @staticmethod
    def _normalize_subgoal(parsed: Dict[str, Any], description: str) -> str:
        subgoal = str(parsed.get("subgoal", "") or "").strip()
        return subgoal or description.strip() or "Execute step"

    @staticmethod
    def _attempt_event(*, description: str, subgoal: str, execution_mode: str, code: str = "") -> Dict[str, Any]:
        return {
            "event": "agent_attempted",
            "data": {
                "description": description,
                "subgoal": subgoal,
                "execution_mode": execution_mode,
                "code": code,
            },
        }

    async def _post_check_subgoal(
        self,
        *,
        current_page: Page,
        snapshot: Dict[str, Any],
        subgoal: str,
        step_data: Dict[str, Any],
        output: str = "",
    ) -> bool:
        lowered_subgoal = subgoal.lower()
        action = str(step_data.get("action") or "").lower()

        if "issues" in lowered_subgoal and action in {"click", "navigate"}:
            latest_snapshot = await build_page_snapshot(current_page, build_frame_path_from_frame)
            url = str(latest_snapshot.get("url", "")).lower()
            title = str(latest_snapshot.get("title", "")).lower()
            return "issues" in url or "issues" in title

        if action == "extract_text":
            return bool(str(output or "").strip())

        return True

    @staticmethod
    def _extract_structured_execute_intent(parsed: Dict[str, Any], prompt: str) -> Optional[Dict[str, Any]]:
        action = str(parsed.get("action", "") or "").strip().lower()
        operation = str(parsed.get("operation", "") or "").strip().lower()
        atomic_actions = {"navigate", "click", "fill", "extract_text", "press"}

        if action in atomic_actions:
            operation = action
        if action not in {"", "execute"} and action not in atomic_actions:
            return None
        if operation not in atomic_actions:
            return None

        intent: Dict[str, Any] = {
            "action": operation,
            "description": parsed.get("description", operation),
            "prompt": prompt,
        }
        for key in ("target_hint", "collection_hint", "ordinal", "value", "result_key"):
            value = parsed.get(key)
            if value is not None:
                intent[key] = value
        return intent

    def _normalize_execution_mode(self, parsed: Dict[str, Any], goal: str) -> str:
        raw_mode = str(parsed.get("execution_mode", "") or "").strip().lower()
        if raw_mode in {"structured", "code"}:
            return raw_mode
        description = str(parsed.get("description", "") or "")
        subgoal = str(parsed.get("subgoal", "") or "")
        combined = f"{goal}\n{description}\n{subgoal}"
        if CONTROL_FLOW_HINT_RE.search(combined) or DYNAMIC_SELECTION_HINT_RE.search(combined):
            return "code"
        return "structured"

    def _build_ai_script_step(
        self,
        *,
        goal: str,
        description: str,
        code: str,
        upgrade_reason: str,
        recovery_attempts: int,
    ) -> Dict[str, Any]:
        return {
            "action": "ai_script",
            "source": "ai",
            "value": self._normalize_run_function(code),
            "description": description,
            "prompt": goal,
            "assistant_diagnostics": {
                "execution_mode": "code",
                "upgrade_reason": upgrade_reason or "custom_code",
                "recovery_attempts": recovery_attempts,
            },
        }

    @staticmethod
    def _build_retry_key(
        *,
        execution_mode: str,
        structured_intent: Optional[Dict[str, Any]],
        description: str,
        goal: str,
    ) -> str:
        if execution_mode == "structured" and structured_intent:
            normalized_intent = {
                "action": structured_intent.get("action"),
                "target_hint": structured_intent.get("target_hint") or {},
                "collection_hint": structured_intent.get("collection_hint") or {},
                "ordinal": structured_intent.get("ordinal"),
                "value": structured_intent.get("value"),
                "result_key": structured_intent.get("result_key"),
            }
            return json.dumps(normalized_intent, sort_keys=True, ensure_ascii=False)
        return description.strip() or goal.strip() or "step"

    @staticmethod
    def _normalize_run_function(code: str) -> str:
        stripped = code.strip()
        if stripped.startswith("async def run("):
            body = RPAReActAgent._extract_run_function_body(stripped)
            body = RPAReActAgent._sync_playwright_calls_to_async(body)
            return RPAReActAgent._wrap_code(body)
        if stripped.startswith("def run("):
            body = RPAReActAgent._extract_run_function_body(stripped)
            body = RPAReActAgent._sync_playwright_calls_to_async(body)
            return RPAReActAgent._wrap_code(body)
        return RPAReActAgent._wrap_code(RPAReActAgent._sync_playwright_calls_to_async(stripped))

    @staticmethod
    def _parse_json(text: str) -> Optional[Dict[str, Any]]:
        # Try raw JSON first
        text = text.strip()
        try:
            return json.loads(text)
        except Exception:
            pass
        # Try extracting from code block
        m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except Exception:
                pass
        # Try extracting the first balanced JSON object from noisy text
        candidate = RPAReActAgent._extract_first_balanced_json_object(text)
        if candidate:
            try:
                return json.loads(candidate)
            except Exception:
                pass
        return None

    @staticmethod
    def _extract_first_balanced_json_object(text: str) -> str:
        start = text.find("{")
        if start < 0:
            return ""

        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
                continue
            if char == "{":
                depth += 1
                continue
            if char == "}":
                depth -= 1
                if depth == 0:
                    return text[start:index + 1]

        return ""

    @staticmethod
    def _wrap_code(code: str) -> str:
        """Wrap bare code in async def run(page) if not already wrapped."""
        stripped = code.strip()
        if stripped.startswith("async def run(") or stripped.startswith("def run("):
            return stripped
        indented = "\n".join("    " + line for line in stripped.splitlines())
        return f"async def run(page):\n{indented}"

    @staticmethod
    def _extract_run_function_body(code: str) -> str:
        lines = code.split("\n")
        body_lines = []
        in_body = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("async def run(") or stripped.startswith("def run("):
                in_body = True
                continue
            if not in_body:
                continue
            if line.startswith("    "):
                body_lines.append(line[4:])
            elif stripped == "":
                body_lines.append("")
            else:
                body_lines.append(line)
        return "\n".join(body_lines).strip()

    @staticmethod
    def _sync_playwright_calls_to_async(code: str) -> str:
        locator_builder_methods = {
            "locator", "frame_locator",
            "get_by_role", "get_by_text", "get_by_label", "get_by_placeholder",
            "get_by_alt_text", "get_by_title", "get_by_test_id",
            "nth", "first", "last", "filter",
        }
        assign_pattern = re.compile(
            r"^(?P<lhs>\w+)\s*=\s*(?P<await>await\s+)?(?P<receiver>\w+|page)(?P<chain>(?:\.\w+(?:\([^)]*\))?)+)\s*$"
        )
        call_pattern = re.compile(
            r"^(?P<await>await\s+)?(?P<receiver>\w+|page)(?P<chain>(?:\.\w+(?:\([^)]*\))?)+)\s*$"
        )
        return_pattern = re.compile(
            r"^return\s+(?P<await>await\s+)?(?P<receiver>\w+|page)(?P<chain>(?:\.\w+(?:\([^)]*\))?)+)\s*$"
        )
        generic_assign_pattern = re.compile(r"^(?P<lhs>\w+)\s*=")

        locator_vars = set()
        lines = code.split("\n")
        result = []
        for line in lines:
            stripped = line.lstrip()
            indent = line[:len(line) - len(stripped)]
            if not stripped or stripped.startswith("#") or stripped.startswith("def "):
                result.append(line)
                continue

            generic_assign_match = generic_assign_pattern.match(stripped)
            if generic_assign_match:
                lhs = generic_assign_match.group("lhs")
                assign_match = assign_pattern.match(stripped)
                if not assign_match:
                    locator_vars.discard(lhs)
                    result.append(line)
                    continue

                receiver = assign_match.group("receiver")
                chain = assign_match.group("chain")
                last_call = re.search(r"\.(\w+)\([^)]*\)(?!.*\.\w+\([^)]*\))", chain)
                receiver_is_supported = receiver == "page" or receiver in locator_vars
                if not receiver_is_supported or not last_call:
                    locator_vars.discard(lhs)
                    result.append(line)
                    continue

                if last_call.group(1) in locator_builder_methods:
                    locator_vars.add(lhs)
                    result.append(line)
                    continue

                locator_vars.discard(lhs)
                if assign_match.group("await"):
                    result.append(line)
                else:
                    result.append(
                        f"{indent}{lhs} = await {receiver}{chain}"
                    )
                continue

            call_match = call_pattern.match(stripped)
            if call_match:
                receiver = call_match.group("receiver")
                if receiver != "page" and receiver not in locator_vars:
                    result.append(line)
                    continue

                last_call = re.search(r"\.(\w+)\([^)]*\)(?!.*\.\w+\([^)]*\))", call_match.group("chain"))
                if not last_call or last_call.group(1) in locator_builder_methods or call_match.group("await"):
                    result.append(line)
                    continue

                result.append(f"{indent}await {stripped}")
                continue

            return_match = return_pattern.match(stripped)
            if not return_match:
                result.append(line)
                continue

            receiver = return_match.group("receiver")
            if receiver != "page" and receiver not in locator_vars:
                result.append(line)
                continue

            last_call = re.search(r"\.(\w+)\([^)]*\)(?!.*\.\w+\([^)]*\))", return_match.group("chain"))
            if not last_call or last_call.group(1) in locator_builder_methods or return_match.group("await"):
                result.append(line)
                continue

            result.append(f"{indent}return await {receiver}{return_match.group('chain')}")
        return "\n".join(result)

    @staticmethod
    async def _stream_llm(
        history: List[Dict[str, str]],
        model_config: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[str, None]:
        from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
        model = get_llm_model(config=model_config, streaming=True)
        lc_messages = [SystemMessage(content=REACT_SYSTEM_PROMPT)]
        for m in history:
            if m["role"] == "user":
                lc_messages.append(HumanMessage(content=m["content"]))
            elif m["role"] == "assistant":
                lc_messages.append(AIMessage(content=m["content"]))
        if hasattr(model, "astream"):
            text_parts: List[str] = []
            fallback_parts: List[str] = []
            async for chunk in model.astream(lc_messages):
                text = _extract_llm_chunk_text(chunk)
                if text:
                    text_parts.append(text)
                    continue
                fallback = _extract_llm_chunk_fallback_text(chunk)
                if fallback:
                    fallback_parts.append(fallback)
            full_text = "".join(text_parts)
            if full_text.strip():
                yield full_text
                return
            fallback_text = "\n".join(part for part in fallback_parts if part).strip()
            if fallback_text:
                yield fallback_text
                return

        response = await model.ainvoke(lc_messages)
        yield _extract_llm_response_text(response)


class RPAAssistant:
    """Frame-aware AI recording assistant."""

    def __init__(self):
        self._histories: Dict[str, List[Dict[str, str]]] = {}

    def _get_history(self, session_id: str) -> List[Dict[str, str]]:
        if session_id not in self._histories:
            self._histories[session_id] = []
        return self._histories[session_id]

    def _trim_history(self, session_id: str, max_rounds: int = 10):
        hist = self._get_history(session_id)
        max_msgs = max_rounds * 2
        if len(hist) > max_msgs:
            self._histories[session_id] = hist[-max_msgs:]

    async def chat(
        self,
        session_id: str,
        page: Page,
        message: str,
        steps: List[Dict[str, Any]],
        model_config: Optional[Dict[str, Any]] = None,
        page_provider: Optional[Callable[[], Optional[Page]]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        yield {"event": "message_chunk", "data": {"text": "正在分析当前页面......\n\n"}}
        current_page = page_provider() if page_provider else page
        if current_page is None:
            yield {"event": "error", "data": {"message": "No active page available"}}
            yield {"event": "done", "data": {}}
            return

        snapshot = await build_page_snapshot(current_page, build_frame_path_from_frame)
        history = self._get_history(session_id)
        messages = self._build_messages(message, steps, snapshot, history)

        full_response = ""
        async for chunk_text in self._stream_llm(messages, model_config):
            full_response += chunk_text
            yield {"event": "message_chunk", "data": {"text": chunk_text}}

        yield {"event": "executing", "data": {}}
        result, final_response, code, resolution, retry_notice = await self._execute_with_retry(
            page=page,
            page_provider=page_provider,
            snapshot=snapshot,
            full_response=full_response,
            messages=messages,
            model_config=model_config,
        )

        if retry_notice:
            yield {"event": "message_chunk", "data": {"text": retry_notice}}
        if resolution:
            yield {"event": "resolution", "data": {"intent": resolution}}

        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": final_response})
        self._trim_history(session_id)

        step_data = None
        if result["success"]:
            if result.get("step"):
                step_data = result["step"]
            elif code:
                step_data = {
                    "action": "ai_script",
                    "source": "ai",
                    "value": RPAReActAgent._normalize_run_function(code),
                    "description": message,
                    "prompt": message,
                }

        yield {
            "event": "result",
            "data": {
                "success": result["success"],
                "error": result.get("error"),
                "step": step_data,
                "output": result.get("output"),
            },
        }
        yield {"event": "done", "data": {}}

    async def _execute_with_retry(
        self,
        page: Page,
        page_provider: Optional[Callable[[], Optional[Page]]],
        snapshot: Dict[str, Any],
        full_response: str,
        messages: List[Dict[str, str]],
        model_config: Optional[Dict[str, Any]],
    ) -> tuple[Dict[str, Any], str, Optional[str], Optional[Dict[str, Any]], str]:
        current_page = page_provider() if page_provider else page
        if current_page is None:
            return {"success": False, "error": "No active page available", "output": ""}, full_response, None, None, ""

        try:
            result, code, resolution = await self._execute_single_response(current_page, snapshot, full_response)
            if result["success"]:
                return result, full_response, code, resolution, ""
        except Exception as exc:
            result = {"success": False, "error": str(exc), "output": ""}
            code = None
            resolution = None

        retry_messages = messages + [
            {"role": "assistant", "content": full_response},
            {"role": "user", "content": f"Execution error: {result['error']}\nPlease fix it and retry."},
        ]
        retry_response = ""
        async for chunk_text in self._stream_llm(retry_messages, model_config):
            retry_response += chunk_text

        current_page = page_provider() if page_provider else page
        if current_page is None:
            return {"success": False, "error": "No active page available", "output": ""}, retry_response, None, None, "\n\nExecution failed. Retrying.\n\n"

        retry_snapshot = await build_page_snapshot(current_page, build_frame_path_from_frame)
        try:
            retry_result, retry_code, retry_resolution = await self._execute_single_response(
                current_page,
                retry_snapshot,
                retry_response,
            )
            return retry_result, retry_response, retry_code, retry_resolution, "\n\nExecution failed. Retrying.\n\n"
        except Exception as exc:
            return {"success": False, "error": str(exc), "output": ""}, retry_response, None, None, "\n\nExecution failed. Retrying.\n\n"

    async def _execute_single_response(
        self,
        current_page: Page,
        snapshot: Dict[str, Any],
        full_response: str,
    ) -> tuple[Dict[str, Any], Optional[str], Optional[Dict[str, Any]]]:
        structured_intent = self._extract_structured_intent(full_response)
        if structured_intent:
            resolved_intent = resolve_structured_intent(snapshot, structured_intent)
            result = await execute_structured_intent(current_page, resolved_intent)
            return result, None, resolved_intent

        code = self._extract_code(full_response)
        if not code:
            raise ValueError("Unable to extract structured intent or executable code from assistant response")
        normalized_code = RPAReActAgent._normalize_run_function(code)
        result = await self._execute_on_page(current_page, normalized_code)
        return result, normalized_code, None

    def _build_messages(
        self,
        user_message: str,
        steps: List[Dict[str, Any]],
        snapshot: Dict[str, Any],
        history: List[Dict[str, str]],
    ) -> List[Dict[str, str]]:
        steps_text = ""
        if steps:
            lines = []
            for i, step in enumerate(steps, 1):
                source = step.get("source", "record")
                desc = step.get("description", step.get("action", ""))
                lines.append(f"{i}. [{source}] {desc}")
            steps_text = "\n".join(lines)

        frame_lines = _snapshot_frame_lines(snapshot)

        context = f"""## History Steps
{steps_text or "(none)"}

## Current Page Snapshot
{chr(10).join(frame_lines) or "(no observable elements)"}

## User Instruction
{user_message}"""

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": context})
        return messages

    async def _stream_llm(
        self,
        messages: List[Dict[str, str]],
        model_config: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[str, None]:
        model = get_llm_model(config=model_config, streaming=True)
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        lc_messages = []
        for message in messages:
            if message["role"] == "system":
                lc_messages.append(SystemMessage(content=message["content"]))
            elif message["role"] == "user":
                lc_messages.append(HumanMessage(content=message["content"]))
            elif message["role"] == "assistant":
                lc_messages.append(AIMessage(content=message["content"]))

        async for chunk in model.astream(lc_messages):
            text = _extract_llm_chunk_text(chunk)
            if text:
                yield text
                continue
            fallback = _extract_llm_chunk_fallback_text(chunk)
            if fallback:
                yield fallback

    @staticmethod
    def _extract_structured_intent(text: str) -> Optional[Dict[str, Any]]:
        stripped = text.strip()
        try:
            parsed = json.loads(stripped)
        except Exception:
            parsed = None
        if isinstance(parsed, dict) and parsed.get("action"):
            return parsed

        match = re.search(r"```json\s*\n(.*?)```", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1).strip())
            except Exception:
                return None
            if isinstance(parsed, dict) and parsed.get("action"):
                return parsed
        return None

    @staticmethod
    def _extract_code(text: str) -> Optional[str]:
        pattern = r"```python\s*\n(.*?)```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
        pattern2 = r"(async def run\(page\):.*)"
        match2 = re.search(pattern2, text, re.DOTALL)
        if match2:
            return match2.group(1).strip()
        pattern3 = r"(def run\(page\):.*)"
        match3 = re.search(pattern3, text, re.DOTALL)
        if match3:
            return match3.group(1).strip()
        return None

    async def _get_page_elements(self, page: Page) -> str:
        return await _get_page_elements(page)

    async def _execute_on_page(self, page: Page, code: str) -> Dict[str, Any]:
        return await _execute_on_page(page, code)



