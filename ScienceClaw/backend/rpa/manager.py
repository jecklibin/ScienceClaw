import json
import logging
import os
import uuid
import asyncio
import base64
from typing import Dict, List, Optional, Any
from datetime import datetime

import httpx
from pydantic import BaseModel, Field
from .vlm_analyzer import VLMAnalyzer

logger = logging.getLogger(__name__)


class RPAStep(BaseModel):
    id: str
    action: str
    target: Optional[str] = None
    value: Optional[str] = None
    screenshot_url: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)
    description: Optional[str] = None
    tag: Optional[str] = None
    label: Optional[str] = None
    url: Optional[str] = None
    source: str = "record"  # "record" or "ai"
    prompt: Optional[str] = None  # original user instruction for AI steps


class RPASession(BaseModel):
    id: str
    user_id: str
    start_time: datetime = Field(default_factory=datetime.now)
    status: str = "recording"  # recording, stopped, testing, saved
    steps: List[RPAStep] = []
    sandbox_session_id: str


# ── Browser script that runs INSIDE the sandbox ──────────────────────
# Launched via nohup, writes events to /tmp/rpa_events.jsonl
BROWSER_SCRIPT = r'''
import os, json, sys, time
os.environ["DISPLAY"] = ":99"

from playwright.sync_api import sync_playwright

EVENT_FILE = "/tmp/rpa_events.jsonl"
# Clear previous events
with open(EVENT_FILE, "w") as f:
    pass

CAPTURE_JS = """
(() => {
    if (window.__rpa_injected) return;
    window.__rpa_injected = true;
    window.__rpa_paused = false;

    // ── Score constants (lower = better, mirrors Playwright codegen) ──
    var S_TESTID=1, S_ROLE_NAME=100, S_PLACEHOLDER=120, S_LABEL=140,
        S_ALT=160, S_TEXT=180, S_TITLE=200, S_CSS_ID=500,
        S_ROLE_ONLY=510, S_CSS_ATTR=520, S_CSS_TAG=530,
        S_NTH=10000, S_FALLBACK=10000000;

    // ── Helpers ─────────────────────────────────────────────────────
    function norm(s) { return (s||'').replace(/\\s+/g,' ').trim(); }
    function cssEsc(s) {
        try { return CSS.escape(s); } catch(e) {
            return s.replace(/([\\\\\"'\\[\\](){}|^$.*+?])/g,'\\\\$1');
        }
    }
    function isUnique(sel) {
        try { return document.querySelectorAll(sel).length===1; }
        catch(e) { return false; }
    }
    // Reject GUID-like IDs (framework-generated)
    function isGuidLike(id) {
        var transitions=0;
        for(var i=1;i<id.length;i++){
            var a=charType(id[i-1]), b=charType(id[i]);
            if(a!==b) transitions++;
        }
        return transitions >= id.length/4;
    }
    function charType(c){
        if(c>='a'&&c<='z') return 1;
        if(c>='A'&&c<='Z') return 2;
        if(c>='0'&&c<='9') return 3;
        return 4;
    }

    // ── Element retargeting (walk up to interactive ancestor) ───────
    var INTERACTIVE = ['BUTTON','A','SELECT','TEXTAREA'];
    var INTERACTIVE_ROLES = ['button','link','checkbox','radio','tab','menuitem',
                             'option','switch','combobox'];
    function retarget(el) {
        if (['INPUT','TEXTAREA','SELECT'].indexOf(el.tagName)>=0) return el;
        if (el.isContentEditable) return el;
        var cur = el;
        while(cur && cur !== document.body) {
            if (INTERACTIVE.indexOf(cur.tagName)>=0) return cur;
            var r = cur.getAttribute('role');
            if (r && INTERACTIVE_ROLES.indexOf(r)>=0) return cur;
            cur = cur.parentElement;
        }
        return el;  // no interactive ancestor, keep original
    }

    // ── Accessible name computation ─────────────────────────────────
    function accessibleName(el) {
        var a = el.getAttribute('aria-label');
        if (a) return norm(a);
        var lblBy = el.getAttribute('aria-labelledby');
        if (lblBy) {
            var parts = lblBy.split(/\\s+/).map(function(id){
                var ref = document.getElementById(id);
                return ref ? norm(ref.textContent) : '';
            }).filter(Boolean);
            if (parts.length) return parts.join(' ').substring(0,80);
        }
        // For form elements, check associated label
        if (['INPUT','TEXTAREA','SELECT'].indexOf(el.tagName)>=0) {
            if (el.id) {
                var lbl = document.querySelector('label[for=\"'+cssEsc(el.id)+'\"]');
                if (lbl) return norm(lbl.textContent).substring(0,80);
            }
            if (el.closest && el.closest('label'))
                return norm(el.closest('label').textContent).substring(0,80);
        }
        // For buttons/links: inner text
        if (['BUTTON','A'].indexOf(el.tagName)>=0 || el.getAttribute('role')) {
            var t = norm(el.textContent);
            return t.length<=80 ? t : t.substring(0,80);
        }
        return '';
    }

    // ── Role mapping ────────────────────────────────────────────────
    var ROLE_MAP = {
        BUTTON:'button', A:'link', H1:'heading', H2:'heading',
        H3:'heading', H4:'heading', H5:'heading', H6:'heading',
        SELECT:'combobox', TEXTAREA:'textbox', IMG:'img',
        NAV:'navigation', MAIN:'main', FORM:'form', TABLE:'table',
        DIALOG:'dialog'
    };
    function getRole(el) {
        var explicit = el.getAttribute('role');
        if (explicit) return explicit;
        if (el.tagName==='INPUT') {
            var t=(el.getAttribute('type')||'text').toLowerCase();
            if(t==='checkbox') return 'checkbox';
            if(t==='radio') return 'radio';
            if(t==='submit'||t==='button'||t==='reset') return 'button';
            return 'textbox';
        }
        return ROLE_MAP[el.tagName]||null;
    }

    // ── Score-based locator generator ───────────────────────────────
    function generateLocator(el) {
        el = retarget(el);
        var candidates = [];
        var tag = el.tagName;
        var role = getRole(el);
        var name = accessibleName(el);

        // 1. data-testid / data-test-id / data-test / data-cy
        var tid = el.getAttribute('data-testid')||el.getAttribute('data-test-id')
                ||el.getAttribute('data-test')||el.getAttribute('data-cy');
        if (tid) {
            var tsel = '[data-testid=\"'+cssEsc(tid)+'\"]';
            if (!isUnique(tsel)) tsel = '[data-test-id=\"'+cssEsc(tid)+'\"]';
            candidates.push({s:S_TESTID, m:'testid', v:tid, sel:tsel});
        }

        // 2. Role + accessible name
        if (role && name) {
            candidates.push({s:S_ROLE_NAME, m:'role', role:role, name:name});
        }

        // 3. Placeholder (for inputs/textareas)
        var ph = el.getAttribute('placeholder');
        if (ph) candidates.push({s:S_PLACEHOLDER, m:'placeholder', v:norm(ph).substring(0,80)});

        // 4. Label (for form elements)
        if (['INPUT','TEXTAREA','SELECT'].indexOf(tag)>=0) {
            var labelText = '';
            if (el.id) {
                var lbl = document.querySelector('label[for=\"'+cssEsc(el.id)+'\"]');
                if (lbl) labelText = norm(lbl.textContent);
            }
            if (!labelText && el.closest && el.closest('label'))
                labelText = norm(el.closest('label').textContent);
            if (labelText)
                candidates.push({s:S_LABEL, m:'label', v:labelText.substring(0,80)});
        }

        // 5. Alt text (for images)
        var alt = el.getAttribute('alt');
        if (alt) candidates.push({s:S_ALT, m:'alt', v:norm(alt).substring(0,80)});

        // 6. Text content (only for short, non-generic elements)
        if (name && name.length<=50 && ['BUTTON','A','LABEL','OPTION'].indexOf(tag)>=0) {
            candidates.push({s:S_TEXT, m:'text', v:name});
        }

        // 7. Title attribute
        var title = el.getAttribute('title');
        if (title) candidates.push({s:S_TITLE, m:'title', v:norm(title).substring(0,80)});

        // 8. CSS #id (skip GUID-like)
        if (el.id && !isGuidLike(el.id)) {
            candidates.push({s:S_CSS_ID, m:'css', v:'#'+cssEsc(el.id),
                             sel:'#'+cssEsc(el.id)});
        }

        // 9. Role without name
        if (role && !name) {
            candidates.push({s:S_ROLE_ONLY, m:'role_only', role:role});
        }

        // 10. CSS [name=...] for form elements
        var nameAttr = el.getAttribute('name');
        if (nameAttr) {
            var nsel = tag.toLowerCase()+'[name=\"'+cssEsc(nameAttr)+'\"]';
            candidates.push({s:S_CSS_ATTR, m:'css', v:nsel, sel:nsel});
        }

        // 11. CSS input[type=...]
        if (tag==='INPUT') {
            var itype = el.getAttribute('type')||'text';
            var tsel2 = 'input[type=\"'+itype+'\"]';
            candidates.push({s:S_CSS_ATTR, m:'css', v:tsel2, sel:tsel2});
        }

        // 12. CSS tag.class combos
        if (el.className && typeof el.className==='string') {
            var classes = el.className.trim().split(/\\s+/).filter(function(c){
                return c && !isGuidLike(c);
            });
            for (var ci=1; ci<=Math.min(classes.length,3); ci++) {
                var csel = tag.toLowerCase()+'.'+classes.slice(0,ci).map(cssEsc).join('.');
                candidates.push({s:S_CSS_TAG, m:'css', v:csel, sel:csel});
            }
        }

        // Sort by score
        candidates.sort(function(a,b){ return a.s - b.s; });

        // Test uniqueness, pick first unique candidate
        for (var i=0; i<candidates.length; i++) {
            var c = candidates[i];
            if (testUnique(el, c)) return formatCandidate(c);
        }

        // No unique candidate found — try nesting with parent
        for (var i=0; i<candidates.length; i++) {
            var c = candidates[i];
            var nested = tryNested(el, c);
            if (nested) return nested;
        }

        // Absolute fallback: CSS path
        return {method:'css', value:cssFallback(el)};
    }

    function testUnique(el, c) {
        if (c.m==='role' || c.m==='role_only') {
            // Count elements with same role+name
            var all = document.querySelectorAll('*');
            var count=0;
            for(var i=0;i<all.length;i++){
                if(getRole(all[i])===c.role){
                    if(c.m==='role_only' || accessibleName(all[i])===c.name){
                        count++;
                        if(count>1) return false;
                    }
                }
            }
            return count===1;
        }
        if (c.m==='text') {
            // Check text uniqueness
            var all2 = document.querySelectorAll('*');
            var count2=0;
            for(var j=0;j<all2.length;j++){
                var t=norm(all2[j].textContent);
                if(t===c.v && all2[j].children.length===0){
                    count2++;
                    if(count2>1) return false;
                }
            }
            return count2===1;
        }
        if (c.m==='placeholder'||c.m==='label'||c.m==='alt'||c.m==='title'||c.m==='testid') {
            // These are generally unique enough, but verify
            var attr = c.m==='testid'?'data-testid':c.m;
            if (c.m==='label') return true; // label association is usually unique
            if (c.m==='placeholder') {
                var pAll = document.querySelectorAll('[placeholder]');
                var pc=0;
                for(var k=0;k<pAll.length;k++){
                    if(norm(pAll[k].getAttribute('placeholder'))===c.v){pc++;if(pc>1)return false;}
                }
                return pc===1;
            }
            return true;
        }
        // CSS-based: use querySelectorAll
        if (c.sel) return isUnique(c.sel);
        return false;
    }

    function formatCandidate(c) {
        if (c.m==='role') return {method:'role', role:c.role, name:c.name};
        if (c.m==='role_only') return {method:'role', role:c.role, name:''};
        if (c.m==='testid') return {method:'testid', value:c.v};
        if (c.m==='placeholder') return {method:'placeholder', value:c.v};
        if (c.m==='label') return {method:'label', value:c.v};
        if (c.m==='alt') return {method:'alt', value:c.v};
        if (c.m==='text') return {method:'text', value:c.v};
        if (c.m==='title') return {method:'title', value:c.v};
        if (c.m==='css') return {method:'css', value:c.v};
        return {method:'css', value:'body'};
    }

    // Try parent >> child nesting for non-unique candidates
    function tryNested(el, c) {
        var parent = el.parentElement;
        for (var depth=0; depth<3 && parent && parent!==document.body; depth++) {
            // Try parent with id
            if (parent.id && !isGuidLike(parent.id)) {
                var psel = '#'+cssEsc(parent.id);
                var childSel = c.sel || c.v || '';
                if (childSel && c.m==='css') {
                    var combo = psel+' '+childSel;
                    if (isUnique(combo)) return {method:'css', value:combo};
                }
            }
            // Try parent role
            var pRole = getRole(parent);
            var pName = accessibleName(parent);
            if (pRole && pName) {
                // Return as nested locator
                return {method:'nested', parent:{method:'role',role:pRole,name:pName},
                        child:formatCandidate(c)};
            }
            parent = parent.parentElement;
        }
        return null;
    }

    // CSS path fallback: walk up using id > nth-child
    function cssFallback(el) {
        var parts = [];
        var cur = el;
        while (cur && cur!==document.body && cur!==document.documentElement) {
            var seg = cur.tagName.toLowerCase();
            if (cur.id && !isGuidLike(cur.id)) {
                parts.unshift('#'+cssEsc(cur.id));
                break;
            }
            // nth-child
            if (cur.parentElement) {
                var sibs = cur.parentElement.children;
                var idx=0;
                for(var i=0;i<sibs.length;i++){
                    if(sibs[i].tagName===cur.tagName) idx++;
                    if(sibs[i]===cur) break;
                }
                var sameTag=0;
                for(var j=0;j<sibs.length;j++){
                    if(sibs[j].tagName===cur.tagName) sameTag++;
                }
                if(sameTag>1) seg += ':nth-of-type('+idx+')';
            }
            parts.unshift(seg);
            cur = cur.parentElement;
            if (parts.length>=4) break;  // limit depth
        }
        return parts.join(' > ');
    }

    // ── Navigation deduplication ────────────────────────────────────
    var _lastAction = null;  // {action, time}
    var _lastClick = null;   // {locatorJson, time} for click dedup

    function emit(evt) {
        evt.timestamp = Date.now();
        evt.url = location.href;
        _lastAction = {action:evt.action, time:evt.timestamp};
        window.__rpa_emit(JSON.stringify(evt));
    }

    // ── Event listeners ─────────────────────────────────────────────
    document.addEventListener('click', function(e) {
        if (!e.isTrusted) return;
        if (window.__rpa_paused) return;
        var el = e.target;
        // Skip clicks on SELECT/OPTION (handled by change event)
        if (el.tagName==='SELECT'||el.tagName==='OPTION') return;
        var loc = generateLocator(el);
        var locJson = JSON.stringify(loc);
        var now = Date.now();
        // Deduplicate rapid clicks on the same element (within 1s)
        if (_lastClick && _lastClick.locatorJson===locJson && now-_lastClick.time<1000) {
            return;
        }
        _lastClick = {locatorJson:locJson, time:now};
        emit({action:'click', locator:loc, tag:retarget(el).tagName});
    }, true);

    document.addEventListener('input', function(e) {
        if (!e.isTrusted) return;
        if (window.__rpa_paused) return;
        var el = e.target;
        clearTimeout(el.__rpa_timer);
        el.__rpa_timer = setTimeout(function() {
            emit({action:'fill', locator:generateLocator(el),
                  value:el.value||'', tag:el.tagName});
        }, 800);
    }, true);

    document.addEventListener('change', function(e) {
        if (!e.isTrusted) return;
        if (window.__rpa_paused) return;
        var el = e.target;
        if (el.tagName === 'SELECT') {
            emit({action:'select', locator:generateLocator(el),
                  value:el.value||'', tag:el.tagName});
        }
    }, true);

    document.addEventListener('keydown', function(e) {
        if (!e.isTrusted) return;
        if (window.__rpa_paused) return;
        if (e.key === 'Enter') {
            var el = e.target;
            emit({action:'press', locator:generateLocator(el),
                  value:'Enter', tag:el.tagName});
        }
    }, true);

    console.log('[RPA] Event capture injected');
})();
"""

p = sync_playwright().start()
browser = p.chromium.launch(
    headless=False,
    executable_path="/usr/bin/chromium-browser",
    args=["--no-sandbox", "--disable-gpu", "--start-maximized",
          "--window-size=1280,720", "--disable-dev-shm-usage"]
)
# no_viewport=True lets --start-maximized control the actual window size
context = browser.new_context(no_viewport=True)
page = context.new_page()

# Track last known URL to detect address-bar navigation
_last_url = {"value": ""}

# Bridge JS events to Python file via expose_function (thread-safe)
def rpa_emit(event_json):
    try:
        with open(EVENT_FILE, "a") as f:
            f.write(event_json + "\n")
    except Exception as ex:
        print(f"[RPA] emit error: {ex}", file=sys.stderr)

page.expose_function("__rpa_emit", rpa_emit)

# Capture address-bar navigation (typing URL + Enter, bookmarks, etc.)
# This fires for ALL navigations including link clicks, but we only emit
# a "navigate" event when the URL actually changes to a new origin/path.
def on_navigated(frame):
    if frame != page.main_frame:
        return
    new_url = frame.url
    if new_url and new_url != _last_url["value"] and new_url != "about:blank":
        _last_url["value"] = new_url
        evt = json.dumps({
            "action": "navigate",
            "url": new_url,
            "timestamp": int(time.time() * 1000),
        })
        try:
            with open(EVENT_FILE, "a") as f:
                f.write(evt + "\n")
        except Exception:
            pass

page.on("framenavigated", on_navigated)

# Re-inject JS capture on every page load
def on_load(loaded_page):
    try:
        loaded_page.evaluate(CAPTURE_JS)
    except Exception:
        pass

page.on("load", on_load)

# Start on about:blank — let user decide where to go
page.goto("about:blank")

# ── Command file execution (for AI assistant) ───────────────────────
import traceback as _tb

def _execute_command(page, cmd_path, result_path):
    """Execute a command file and write result."""
    try:
        code = open(cmd_path, 'r', encoding='utf-8').read()
        os.remove(cmd_path)
    except Exception:
        return

    # Pause event capture during AI script execution
    try:
        page.evaluate("window.__rpa_paused = true")
    except Exception:
        pass

    result = {"success": False, "output": "", "error": None}
    try:
        ns = {"page": page, "os": os, "json": json}
        exec(code, ns)
        if "run" in ns and callable(ns["run"]):
            ret = ns["run"](page)
            result = {"success": True, "output": str(ret) if ret else "ok", "error": None}
        else:
            result = {"success": False, "output": "", "error": "No run(page) function defined"}
    except Exception as e:
        result = {"success": False, "output": "", "error": _tb.format_exc()}

    # Resume event capture
    try:
        page.evaluate("window.__rpa_paused = false")
    except Exception:
        pass

    with open(result_path, 'w', encoding='utf-8') as f:
        json.dump(result, f)

CMD_PATH = "/tmp/rpa_command.py"
CMD_RESULT_PATH = "/tmp/rpa_command_result.json"

print("READY", flush=True)

# Main loop — process commands and keep Playwright event loop alive
try:
    while True:
        if os.path.exists(CMD_PATH):
            _execute_command(page, CMD_PATH, CMD_RESULT_PATH)
        page.wait_for_timeout(500)
except KeyboardInterrupt:
    browser.close()
    p.stop()
'''


class RPASessionManager:
    def __init__(self, sandbox_url: str, vlm_api_key: str = "", vlm_base_url: str = ""):
        self.sandbox_url = sandbox_url.rstrip("/")
        self.sessions: Dict[str, RPASession] = {}
        self.vlm_analyzer = VLMAnalyzer(vlm_api_key, vlm_base_url) if vlm_api_key else None
        self.ws_connections: Dict[str, List] = {}

    # ── Session lifecycle ────────────────────────────────────────────

    async def create_session(self, user_id: str, sandbox_session_id: str) -> RPASession:
        session_id = str(uuid.uuid4())
        session = RPASession(
            id=session_id,
            user_id=user_id,
            sandbox_session_id=sandbox_session_id,
        )
        self.sessions[session_id] = session

        # Kill any leftover RPA browser processes
        # Then stop sandbox's built-in browser via supervisorctl (pkill won't work
        # because supervisord has autorestart=true on the browser service)
        await self._exec_sandbox_cmd(
            sandbox_session_id,
            "pkill -f rpa_browser.py 2>/dev/null; "
            "pkill -f 'playwright_chromiumdev' 2>/dev/null; "
            "supervisorctl stop browser 2>/dev/null; "
            "supervisorctl stop mcp-server-browser 2>/dev/null; "
            "sleep 1; echo ok"
        )

        # Write the browser script into the sandbox
        await self._write_browser_script(sandbox_session_id)

        # Launch browser in background
        await self._exec_sandbox_cmd(
            sandbox_session_id,
            "nohup python3 /tmp/rpa_browser.py > /tmp/browser.log 2>&1 &"
        )

        # Wait for browser to be ready
        ready = False
        for _ in range(10):
            await asyncio.sleep(2)
            log = await self._exec_sandbox_cmd(
                sandbox_session_id, "cat /tmp/browser.log 2>/dev/null"
            )
            if "READY" in log:
                ready = True
                break
            print(f"[RPA] Waiting for browser... log: {log[:200]}")

        if ready:
            print("[RPA] Browser started successfully")
        else:
            log = await self._exec_sandbox_cmd(
                sandbox_session_id, "cat /tmp/browser.log 2>/dev/null | tail -20"
            )
            print(f"[RPA] WARNING: Browser may not be ready. Log:\n{log[:500]}")

        # Start polling for events
        asyncio.create_task(self._poll_events(session_id, sandbox_session_id))
        return session

    async def stop_session(self, session_id: str):
        if session_id in self.sessions:
            self.sessions[session_id].status = "stopped"
            sandbox_sid = self.sessions[session_id].sandbox_session_id
            # Kill Playwright browser and restart sandbox's built-in browser
            await self._exec_sandbox_cmd(
                sandbox_sid,
                "pkill -f rpa_browser.py 2>/dev/null; "
                "pkill -f 'playwright_chromiumdev' 2>/dev/null; "
                "supervisorctl start browser 2>/dev/null; "
                "supervisorctl start mcp-server-browser 2>/dev/null; "
                "echo stopped"
            )

    async def get_session(self, session_id: str) -> Optional[RPASession]:
        return self.sessions.get(session_id)

    # ── Write browser script to sandbox ──────────────────────────────

    async def _write_browser_script(self, sandbox_session_id: str):
        """Write the Playwright browser script into the sandbox via sandbox_execute_code."""
        encoded = base64.b64encode(BROWSER_SCRIPT.encode()).decode()
        write_code = (
            "import base64\n"
            f"data = base64.b64decode('{encoded}')\n"
            "with open('/tmp/rpa_browser.py', 'wb') as f:\n"
            "    f.write(data)\n"
            "print('Script written OK')"
        )
        result = await self._exec_sandbox_code(sandbox_session_id, write_code)
        print(f"[RPA] Write script result: {result[:200]}")

    # ── Event polling ────────────────────────────────────────────────

    async def _poll_events(self, session_id: str, sandbox_session_id: str):
        """Poll /tmp/rpa_events.jsonl for new events."""
        seen_count = 0
        while session_id in self.sessions and self.sessions[session_id].status == "recording":
            try:
                raw = await self._exec_sandbox_cmd(
                    sandbox_session_id,
                    "cat /tmp/rpa_events.jsonl 2>/dev/null | wc -l"
                )
                total = int(raw.strip()) if raw.strip().isdigit() else 0

                if total > seen_count:
                    skip = seen_count + 1
                    new_lines = await self._exec_sandbox_cmd(
                        sandbox_session_id,
                        f"tail -n +{skip} /tmp/rpa_events.jsonl"
                    )
                    # Parse all new events
                    new_events = []
                    for line in new_lines.strip().split("\n"):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            new_events.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass

                    # Deduplicate: drop navigate events that follow a
                    # click/press/fill within 5 seconds (navigation is a
                    # side-effect of the user action, not a separate step)
                    filtered = []
                    for evt in new_events:
                        if evt.get("action") == "navigate":
                            nav_ts = evt.get("timestamp", 0)
                            # Check preceding events in this batch + session steps
                            is_side_effect = False
                            for prev in reversed(filtered):
                                if prev.get("action") in ("click", "press", "fill"):
                                    if nav_ts - prev.get("timestamp", 0) < 5000:
                                        is_side_effect = True
                                    break
                            if not is_side_effect and self.sessions[session_id].steps:
                                last_step = self.sessions[session_id].steps[-1]
                                if last_step.action in ("click", "press", "fill"):
                                    last_ts = last_step.timestamp.timestamp() * 1000
                                    if nav_ts - last_ts < 5000:
                                        is_side_effect = True
                            if is_side_effect:
                                print(f"[RPA] Skipping nav (side-effect): {evt.get('url', '')[:60]}")
                                continue
                        filtered.append(evt)

                    for evt in filtered:
                        locator_info = evt.get("locator", {})
                        step_data = {
                            "action": evt.get("action", "unknown"),
                            "target": json.dumps(locator_info) if locator_info else "",
                            "value": evt.get("value", ""),
                            "label": "",
                            "tag": evt.get("tag", ""),
                            "url": evt.get("url", ""),
                            "description": self._make_description(evt),
                        }
                        await self.add_step(session_id, step_data)
                        print(f"[RPA] Step: {step_data['description'][:60]}")
                    seen_count = total
            except Exception as e:
                print(f"[RPA] Poll error: {e}")

            await asyncio.sleep(2)

    @staticmethod
    def _make_description(evt: dict) -> str:
        action = evt.get("action", "")
        value = evt.get("value", "")
        locator = evt.get("locator", {})

        # Build a human-readable target from the locator info
        method = locator.get("method", "") if isinstance(locator, dict) else ""
        if method == "role":
            name = locator.get("name", "")
            target = f'{locator.get("role", "")}("{name}")' if name else locator.get("role", "")
        elif method in ("testid", "label", "placeholder", "alt", "title", "text"):
            target = f'{method}("{locator.get("value", "")}")'
        elif method == "nested":
            parent = locator.get("parent", {})
            child = locator.get("child", {})
            p_name = parent.get("name", parent.get("value", ""))
            c_name = child.get("name", child.get("value", ""))
            target = f'{p_name} >> {c_name}'
        elif method == "css":
            target = locator.get("value", "")
        else:
            target = str(locator)

        if action == "fill":
            return f'输入 "{value}" 到 {target}'
        if action == "click":
            return f"点击 {target}"
        if action == "press":
            return f"按下 {value} 在 {target}"
        if action == "select":
            return f"选择 {value} 在 {target}"
        if action == "navigate":
            return f"导航到 {evt.get('url', '')}"
        return f"{action} on {target}"

    # ── Step management ──────────────────────────────────────────────

    async def add_step(self, session_id: str, step_data: Dict[str, Any]) -> RPAStep:
        if session_id not in self.sessions:
            raise ValueError(f"Session {session_id} not found")

        session = self.sessions[session_id]
        step = RPAStep(id=str(uuid.uuid4()), **step_data)
        session.steps.append(step)

        # Broadcast to WebSocket clients
        await self._broadcast_step(session_id, step)
        return step

    async def _broadcast_step(self, session_id: str, step: RPAStep):
        if session_id in self.ws_connections:
            message = {"type": "step", "data": step.model_dump()}
            for ws in self.ws_connections[session_id]:
                try:
                    await ws.send_json(message)
                except Exception:
                    pass

    def register_ws(self, session_id: str, websocket):
        if session_id not in self.ws_connections:
            self.ws_connections[session_id] = []
        self.ws_connections[session_id].append(websocket)

    def unregister_ws(self, session_id: str, websocket):
        if session_id in self.ws_connections:
            try:
                self.ws_connections[session_id].remove(websocket)
            except ValueError:
                pass

    # ── Sandbox MCP helpers ──────────────────────────────────────────

    async def _exec_sandbox_cmd(self, session_id: str, cmd: str) -> str:
        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "tools/call",
            "params": {"name": "sandbox_execute_bash", "arguments": {"cmd": cmd}},
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.sandbox_url}/mcp",
                json=payload,
                headers={
                    "X-Session-ID": session_id,
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
            resp.raise_for_status()
            result = resp.json()
            return result.get("result", {}).get("structuredContent", {}).get("output", "")

    async def _exec_sandbox_code(self, session_id: str, code: str) -> str:
        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "tools/call",
            "params": {"name": "sandbox_execute_code", "arguments": {"code": code, "language": "python"}},
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.sandbox_url}/mcp",
                json=payload,
                headers={
                    "X-Session-ID": session_id,
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
            resp.raise_for_status()
            result = resp.json()
            sc = result.get("result", {}).get("structuredContent", {})
            stdout = sc.get("stdout") or sc.get("output") or ""
            stderr = sc.get("stderr") or ""
            if stderr:
                print(f"[RPA] Code stderr: {stderr[:200]}")
            return stdout


# ── Global instance ──────────────────────────────────────────────────
from backend.config import settings

rpa_manager = RPASessionManager(
    sandbox_url=settings.sandbox_mcp_url.replace("/mcp", ""),
    vlm_api_key=settings.model_ds_api_key,
    vlm_base_url=settings.model_ds_base_url,
)
