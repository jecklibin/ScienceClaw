# RPA Locator Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make RPA replay choose unique recorded locator candidates before falling back to ambiguous locators, and only use `.first()` as the last fallback.

**Architecture:** Extend the replay generator with a step-local locator resolver that consumes existing `target`, `locator_candidates`, and `validation` metadata. Keep recorder payloads unchanged, add generator-focused tests first, and emit explicit `.first()` fallback only when all recorded candidates remain ambiguous.

**Tech Stack:** Python, FastAPI backend, Playwright script generator, unittest

---

### Task 1: Lock In Replay Fallback Behavior With Tests

**Files:**
- Modify: `RpaClaw/backend/tests/test_rpa_screencast.py`
- Create: `RpaClaw/backend/tests/test_rpa_generator.py`
- Modify: `RpaClaw/backend/rpa/generator.py`

- [ ] **Step 1: Write a failing test for preferring a unique alternative candidate**

```python
import unittest

from backend.rpa.generator import PlaywrightGenerator


class PlaywrightGeneratorLocatorFallbackTests(unittest.TestCase):
    def test_prefers_unique_candidate_when_primary_locator_is_ambiguous(self):
        generator = PlaywrightGenerator()
        steps = [{
            "action": "click",
            "target": {"method": "title", "value": ".dockerignore"},
            "validation": {"status": "fallback", "details": "strict matches = 2"},
            "locator_candidates": [
                {
                    "kind": "title",
                    "score": 7,
                    "strict_match_count": 2,
                    "visible_match_count": 2,
                    "selected": True,
                    "reason": "strict matches = 2",
                    "locator": {"method": "title", "value": ".dockerignore"},
                },
                {
                    "kind": "nested",
                    "score": 20,
                    "strict_match_count": 1,
                    "visible_match_count": 1,
                    "selected": False,
                    "reason": "strict unique match",
                    "locator": {
                        "method": "nested",
                        "parent": {"method": "css", "value": "div[role='row']"},
                        "child": {"method": "title", "value": ".dockerignore"},
                    },
                },
            ],
            "url": "https://github.com/example/repo",
        }]

        script = generator.generate_script(steps)

        self.assertIn('locator("div[role=\'row\']")', script)
        self.assertNotIn('get_by_title(".dockerignore", exact=True).click()', script)
```

- [ ] **Step 2: Run the test to verify RED**

Run: `python3 -m unittest RpaClaw/backend/tests/test_rpa_generator.py`
Expected: FAIL because the generator still emits the selected ambiguous locator.

- [ ] **Step 3: Add a failing test for last-resort `.first()` fallback**

```python
    def test_uses_first_only_when_all_candidates_are_ambiguous(self):
        generator = PlaywrightGenerator()
        steps = [{
            "action": "click",
            "target": {"method": "title", "value": ".dockerignore"},
            "validation": {"status": "fallback", "details": "strict matches = 2"},
            "locator_candidates": [
                {
                    "kind": "title",
                    "score": 7,
                    "strict_match_count": 2,
                    "visible_match_count": 2,
                    "selected": True,
                    "reason": "strict matches = 2",
                    "locator": {"method": "title", "value": ".dockerignore"},
                },
                {
                    "kind": "text",
                    "score": 6,
                    "strict_match_count": 2,
                    "visible_match_count": 2,
                    "selected": False,
                    "reason": "strict matches = 2",
                    "locator": {"method": "text", "value": ".dockerignore"},
                },
            ],
            "url": "https://github.com/example/repo",
        }]

        script = generator.generate_script(steps)

        self.assertIn('.first.click()', script)
        self.assertIn('Fallback: recorder candidates were ambiguous', script)
```

- [ ] **Step 4: Run the test file again to verify both failures are meaningful**

Run: `python3 -m unittest RpaClaw/backend/tests/test_rpa_generator.py`
Expected: FAIL on script assertions, not import or syntax errors.

### Task 2: Implement Generator Locator Resolution

**Files:**
- Modify: `RpaClaw/backend/rpa/generator.py`
- Test: `RpaClaw/backend/tests/test_rpa_generator.py`

- [ ] **Step 1: Add an internal resolver for replay locator selection**

```python
    def _resolve_replay_locator_payload(self, step: Dict[str, Any]) -> Dict[str, Any]:
        target = step.get("target", "")
        validation = step.get("validation") or {}
        candidates = step.get("locator_candidates") or []
        primary = self._parse_locator_payload(target)

        if validation.get("status") == "ok":
            return {"locator": primary, "source": "target", "used_first_fallback": False, "details": "validated unique target"}

        unique_candidates = [c for c in candidates if c.get("strict_match_count") == 1 and isinstance(c.get("locator"), dict)]
        if unique_candidates:
            chosen = self._choose_best_candidate(unique_candidates)
            return {"locator": chosen["locator"], "source": "candidate", "used_first_fallback": False, "details": chosen.get("reason", "")}

        structured_candidates = [c for c in candidates if c.get("kind") == "nested" and isinstance(c.get("locator"), dict)]
        if structured_candidates:
            chosen = self._choose_best_candidate(structured_candidates)
            return {"locator": chosen["locator"], "source": "scoped", "used_first_fallback": False, "details": chosen.get("reason", "")}

        return {"locator": primary, "source": "first_fallback", "used_first_fallback": True, "details": "all recorded candidates ambiguous"}
```

- [ ] **Step 2: Add a deterministic candidate sorter**

```python
    def _choose_best_candidate(self, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        priority = {
            "nested": 0,
            "role": 1,
            "testid": 2,
            "label": 3,
            "placeholder": 4,
            "alt": 5,
            "text": 6,
            "title": 7,
            "css": 8,
        }
        return sorted(
            candidates,
            key=lambda c: (priority.get(c.get("kind", ""), 99), c.get("score", 999)),
        )[0]
```

- [ ] **Step 3: Update click-style emission to append `.first` only for explicit last-resort fallback**

```python
            locator_meta = self._resolve_replay_locator_payload(step)
            locator = self._build_locator_for_page(locator_meta["locator"], scope_var)
            if locator_meta["used_first_fallback"]:
                lines.append("    # Fallback: recorder candidates were ambiguous, using first matching locator")
                locator = f"{locator}.first"
```

- [ ] **Step 4: Add a helper for parsing raw target payloads safely**

```python
    def _parse_locator_payload(self, target: Any) -> Dict[str, Any]:
        try:
            payload = json.loads(target) if isinstance(target, str) else target
        except (json.JSONDecodeError, TypeError):
            payload = {"method": "css", "value": str(target or "body")}
        if not isinstance(payload, dict):
            return {"method": "css", "value": str(target or "body")}
        return payload
```

- [ ] **Step 5: Run the focused generator tests to verify GREEN**

Run: `python3 -m unittest RpaClaw/backend/tests/test_rpa_generator.py`
Expected: PASS

### Task 3: Guard Compatibility and Edge Cases

**Files:**
- Modify: `RpaClaw/backend/tests/test_rpa_generator.py`
- Modify: `RpaClaw/backend/rpa/generator.py`

- [ ] **Step 1: Add a regression test for legacy steps without candidates**

```python
    def test_preserves_legacy_target_behavior_without_candidate_metadata(self):
        generator = PlaywrightGenerator()
        steps = [{
            "action": "click",
            "target": {"method": "role", "role": "button", "name": "Save"},
            "url": "https://example.com",
        }]

        script = generator.generate_script(steps)

        self.assertIn('get_by_role("button", name="Save", exact=True)', script)
        self.assertNotIn('.first', script)
```

- [ ] **Step 2: Run the legacy compatibility test and verify RED if needed**

Run: `python3 -m unittest RpaClaw/backend/tests/test_rpa_generator.py`
Expected: PASS if compatibility was preserved, otherwise FAIL and fix generator without broadening fallback.

- [ ] **Step 3: Keep resolver defensive around malformed candidate payloads**

```python
        unique_candidates = [
            c for c in candidates
            if c.get("strict_match_count") == 1 and isinstance(c.get("locator"), dict)
        ]
```

- [ ] **Step 4: Run the complete focused test suite**

Run: `python3 -m unittest RpaClaw/backend/tests/test_rpa_generator.py`
Expected: PASS

### Task 4: Verify End-To-End Output Shape

**Files:**
- Modify: `RpaClaw/backend/tests/test_rpa_generator.py`
- Modify: `RpaClaw/backend/rpa/generator.py`

- [ ] **Step 1: Add an assertion that fallback comments appear only on `.first()` paths**

```python
    def test_does_not_emit_fallback_comment_for_unique_candidate(self):
        generator = PlaywrightGenerator()
        steps = [...]

        script = generator.generate_script(steps)

        self.assertNotIn("Fallback: recorder candidates were ambiguous", script)
```

- [ ] **Step 2: Run the full generator test file**

Run: `python3 -m unittest RpaClaw/backend/tests/test_rpa_generator.py`
Expected: PASS

- [ ] **Step 3: Review the generated diff for scope**

Run: `git diff -- RpaClaw/backend/rpa/generator.py RpaClaw/backend/tests/test_rpa_generator.py`
Expected: Only generator fallback logic and focused tests changed.
