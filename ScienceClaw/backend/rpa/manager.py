import json
import logging
import uuid
import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime

from pydantic import BaseModel, Field
from playwright.async_api import Page, BrowserContext

from .cdp_connector import get_cdp_connector

logger = logging.getLogger(__name__)

RPA_PAGE_TIMEOUT_MS = 60000


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
    paused: bool = False  # pause event recording during AI execution


# ── CAPTURE_JS: injected into pages to capture user events ──────────
# Calls window.__rpa_emit(JSON.stringify(evt)) which is bridged to
# Python via page.expose_function().
CAPTURE_JS = r"""
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
    function norm(s) { return (s||'').replace(/\s+/g,' ').trim(); }
    function cssEsc(s) {
        try { return CSS.escape(s); } catch(e) {
            return s.replace(/([\\"'\[\](){}|^$.*+?])/g,'\\$1');
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
            var parts = lblBy.split(/\s+/).map(function(id){
                var ref = document.getElementById(id);
                return ref ? norm(ref.textContent) : '';
            }).filter(Boolean);
            if (parts.length) return parts.join(' ').substring(0,80);
        }
        // For form elements, check associated label
        if (['INPUT','TEXTAREA','SELECT'].indexOf(el.tagName)>=0) {
            if (el.id) {
                var lbl = document.querySelector('label[for="'+cssEsc(el.id)+'"]');
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
            var tsel = '[data-testid="'+cssEsc(tid)+'"]';
            if (!isUnique(tsel)) tsel = '[data-test-id="'+cssEsc(tid)+'"]';
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
                var lbl = document.querySelector('label[for="'+cssEsc(el.id)+'"]');
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
            var nsel = tag.toLowerCase()+'[name="'+cssEsc(nameAttr)+'"]';
            candidates.push({s:S_CSS_ATTR, m:'css', v:nsel, sel:nsel});
        }

        // 11. CSS input[type=...]
        if (tag==='INPUT') {
            var itype = el.getAttribute('type')||'text';
            var tsel2 = 'input[type="'+itype+'"]';
            candidates.push({s:S_CSS_ATTR, m:'css', v:tsel2, sel:tsel2});
        }

        // 12. CSS tag.class combos
        if (el.className && typeof el.className==='string') {
            var classes = el.className.trim().split(/\s+/).filter(function(c){
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

    function matchesCandidate(el, c) {
        if (!el || !c) return false;
        if (c.m === 'role' || c.m === 'role_only') {
            if (getRole(el) !== c.role) return false;
            return c.m === 'role_only' || accessibleName(el) === c.name;
        }
        if (c.m === 'testid') {
            var tid = el.getAttribute('data-testid') || el.getAttribute('data-test-id')
                || el.getAttribute('data-test') || el.getAttribute('data-cy') || '';
            return norm(tid) === c.v;
        }
        if (c.m === 'placeholder') return norm(el.getAttribute('placeholder')) === c.v;
        if (c.m === 'label') return accessibleName(el) === c.v;
        if (c.m === 'alt') return norm(el.getAttribute('alt')) === c.v;
        if (c.m === 'title') return norm(el.getAttribute('title')) === c.v;
        if (c.m === 'text') return norm(el.textContent) === c.v && el.children.length === 0;
        if (c.m === 'css' && c.sel) {
            try { return el.matches(c.sel); } catch(e) { return false; }
        }
        return false;
    }

    function countNestedMatches(parentMatcher, childCandidate, targetEl) {
        var all = document.querySelectorAll('*');
        var count = 0;
        var targetMatched = false;
        for (var i = 0; i < all.length; i++) {
            var parentEl = all[i];
            if (!parentMatcher(parentEl)) continue;
            var descendants = parentEl.querySelectorAll('*');
            for (var j = 0; j < descendants.length; j++) {
                var childEl = descendants[j];
                if (!matchesCandidate(childEl, childCandidate)) continue;
                count++;
                if (childEl === targetEl) targetMatched = true;
                if (count > 1 && targetMatched) return {count: count, targetMatched: true};
            }
        }
        return {count: count, targetMatched: targetMatched};
    }

    // Try parent >> child nesting for non-unique candidates
    function tryNested(el, c) {
        var parent = el.parentElement;
        for (var depth=0; depth<3 && parent && parent!==document.body; depth++) {
            // Try parent with id
            if (parent.id && !isGuidLike(parent.id)) {
                var psel = '#'+cssEsc(parent.id);
                if (c.m === 'css' && c.sel) {
                    var combo = psel+' '+c.sel;
                    if (isUnique(combo)) return {method:'css', value:combo};
                }
                var byIdNested = countNestedMatches(function(parentEl) {
                    return parentEl.id === parent.id;
                }, c, el);
                if (byIdNested.count === 1 && byIdNested.targetMatched) {
                    return {method:'nested', parent:{method:'css', value:psel}, child:formatCandidate(c)};
                }
            }
            // Try parent role
            var pRole = getRole(parent);
            var pName = accessibleName(parent);
            if (pRole && pName) {
                var byRoleNested = countNestedMatches(function(parentEl) {
                    return getRole(parentEl) === pRole && accessibleName(parentEl) === pName;
                }, c, el);
                if (byRoleNested.count === 1 && byRoleNested.targetMatched) {
                    return {method:'nested', parent:{method:'role',role:pRole,name:pName},
                            child:formatCandidate(c)};
                }
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


class RPASessionManager:
    def __init__(self):
        self.sessions: Dict[str, RPASession] = {}
        self.ws_connections: Dict[str, List] = {}
        self._contexts: Dict[str, BrowserContext] = {}
        self._pages: Dict[str, Page] = {}

    async def create_session(self, user_id: str, sandbox_session_id: str) -> RPASession:
        session_id = str(uuid.uuid4())
        session = RPASession(
            id=session_id,
            user_id=user_id,
            sandbox_session_id=sandbox_session_id,
        )
        self.sessions[session_id] = session

        browser = await get_cdp_connector().get_browser()
        context = await browser.new_context(no_viewport=True)
        page = await context.new_page()
        page.set_default_timeout(RPA_PAGE_TIMEOUT_MS)
        page.set_default_navigation_timeout(RPA_PAGE_TIMEOUT_MS)

        self._contexts[session_id] = context
        self._pages[session_id] = page

        async def rpa_emit(event_json: str):
            try:
                evt = json.loads(event_json)
                await self._handle_event(session_id, evt)
            except Exception as e:
                logger.error(f"[RPA] emit error: {e}")

        await page.expose_function("__rpa_emit", rpa_emit)
        await page.evaluate(CAPTURE_JS)

        last_url = {"value": ""}

        def on_navigated(frame):
            if frame != page.main_frame:
                return
            new_url = frame.url
            if new_url and new_url != last_url["value"] and new_url != "about:blank":
                last_url["value"] = new_url
                evt = {
                    "action": "navigate",
                    "url": new_url,
                    "timestamp": int(datetime.now().timestamp() * 1000),
                }
                asyncio.create_task(self._handle_event(session_id, evt))

        page.on("framenavigated", on_navigated)

        async def on_load(loaded_page):
            try:
                await loaded_page.evaluate(CAPTURE_JS)
            except Exception:
                pass

        page.on("load", on_load)

        await page.goto("about:blank")
        await page.bring_to_front()

        logger.info(f"[RPA] Session {session_id} started via CDP")
        return session

    async def stop_session(self, session_id: str):
        if session_id in self.sessions:
            self.sessions[session_id].status = "stopped"

        context = self._contexts.pop(session_id, None)
        self._pages.pop(session_id, None)
        if context:
            try:
                await context.close()
            except Exception as e:
                logger.warning(f"[RPA] Error closing context: {e}")

        logger.info(f"[RPA] Session {session_id} stopped")

    async def get_session(self, session_id: str) -> Optional[RPASession]:
        return self.sessions.get(session_id)

    async def delete_step(self, session_id: str, step_index: int) -> bool:
        """Delete a step by index from the session."""
        session = self.sessions.get(session_id)
        if not session or step_index < 0 or step_index >= len(session.steps):
            return False
        session.steps.pop(step_index)
        return True

    def pause_recording(self, session_id: str):
        """Pause event recording (used during AI execution)."""
        if session_id in self.sessions:
            self.sessions[session_id].paused = True

    def resume_recording(self, session_id: str):
        """Resume event recording."""
        if session_id in self.sessions:
            self.sessions[session_id].paused = False

    def get_page(self, session_id: str) -> Optional[Page]:
        return self._pages.get(session_id)

    async def _handle_event(self, session_id: str, evt: dict):
        if session_id not in self.sessions:
            return
        session = self.sessions[session_id]
        if session.status != "recording" or session.paused:
            return

        if evt.get("action") == "navigate":
            nav_ts = evt.get("timestamp", 0)
            steps = self.sessions[session_id].steps
            if steps:
                last_step = steps[-1]
                if last_step.action in ("click", "press", "fill"):
                    last_ts = last_step.timestamp.timestamp() * 1000
                    if nav_ts - last_ts < 5000:
                        logger.debug(f"[RPA] Skipping nav (side-effect): {evt.get('url', '')[:60]}")
                        return

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
        logger.debug(f"[RPA] Step: {step_data['description'][:60]}")

    @staticmethod
    def _make_description(evt: dict) -> str:
        action = evt.get("action", "")
        value = evt.get("value", "")
        locator = evt.get("locator", {})

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

    async def add_step(self, session_id: str, step_data: Dict[str, Any]) -> RPAStep:
        if session_id not in self.sessions:
            raise ValueError(f"Session {session_id} not found")

        session = self.sessions[session_id]
        step = RPAStep(id=str(uuid.uuid4()), **step_data)
        session.steps.append(step)

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


# ── Global instance ──────────────────────────────────────────────────
rpa_manager = RPASessionManager()
