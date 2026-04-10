import json
import logging
import uuid
import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, Field
from playwright.async_api import Page, BrowserContext

from .cdp_connector import get_cdp_connector

logger = logging.getLogger(__name__)

RPA_PAGE_TIMEOUT_MS = 60000


class RPAStep(BaseModel):
    id: str
    action: str
    target: Optional[str] = None
    frame_path: List[str] = Field(default_factory=list)
    locator_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    validation: Dict[str, Any] = Field(default_factory=dict)
    signals: Dict[str, Any] = Field(default_factory=dict)
    element_snapshot: Dict[str, Any] = Field(default_factory=dict)
    value: Optional[str] = None
    screenshot_url: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)
    description: Optional[str] = None
    tag: Optional[str] = None
    label: Optional[str] = None
    url: Optional[str] = None
    source: str = "record"  # "record" or "ai"
    prompt: Optional[str] = None  # original user instruction for AI steps
    sensitive: bool = False
    tab_id: Optional[str] = None
    source_tab_id: Optional[str] = None
    target_tab_id: Optional[str] = None
    result_key: Optional[str] = None
    collection_hint: Dict[str, Any] = Field(default_factory=dict)
    item_hint: Dict[str, Any] = Field(default_factory=dict)
    ordinal: Optional[str] = None
    assistant_diagnostics: Dict[str, Any] = Field(default_factory=dict)


class RPATab(BaseModel):
    tab_id: str
    title: str = ""
    url: str = ""
    opener_tab_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    last_seen_at: datetime = Field(default_factory=datetime.now)
    status: str = "open"


class RPASession(BaseModel):
    id: str
    user_id: str
    start_time: datetime = Field(default_factory=datetime.now)
    status: str = "recording"  # recording, stopped, testing, saved
    steps: List[RPAStep] = []
    sandbox_session_id: str
    paused: bool = False  # pause event recording during AI execution
    active_tab_id: Optional[str] = None


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

    function collectLocatorCandidates(el) {
        el = retarget(el);
        var candidates = [];
        var tag = el.tagName;
        var role = getRole(el);
        var name = accessibleName(el);

        var tid = el.getAttribute('data-testid')||el.getAttribute('data-test-id')
                ||el.getAttribute('data-test')||el.getAttribute('data-cy');
        if (tid) {
            var tsel = '[data-testid="'+cssEsc(tid)+'"]';
            if (!isUnique(tsel)) tsel = '[data-test-id="'+cssEsc(tid)+'"]';
            candidates.push({s:S_TESTID, m:'testid', v:tid, sel:tsel});
        }
        if (role && name) candidates.push({s:S_ROLE_NAME, m:'role', role:role, name:name});

        var ph = el.getAttribute('placeholder');
        if (ph) candidates.push({s:S_PLACEHOLDER, m:'placeholder', v:norm(ph).substring(0,80)});

        if (['INPUT','TEXTAREA','SELECT'].indexOf(tag)>=0) {
            var labelText = '';
            if (el.id) {
                var lbl = document.querySelector('label[for="'+cssEsc(el.id)+'"]');
                if (lbl) labelText = norm(lbl.textContent);
            }
            if (!labelText && el.closest && el.closest('label'))
                labelText = norm(el.closest('label').textContent);
            if (labelText) candidates.push({s:S_LABEL, m:'label', v:labelText.substring(0,80)});
        }

        var alt = el.getAttribute('alt');
        if (alt) candidates.push({s:S_ALT, m:'alt', v:norm(alt).substring(0,80)});

        if (name && name.length<=50 && ['BUTTON','A','LABEL','OPTION'].indexOf(tag)>=0) {
            candidates.push({s:S_TEXT, m:'text', v:name});
        }

        var title = el.getAttribute('title');
        if (title) candidates.push({s:S_TITLE, m:'title', v:norm(title).substring(0,80)});

        if (el.id && !isGuidLike(el.id)) {
            candidates.push({s:S_CSS_ID, m:'css', v:'#'+cssEsc(el.id), sel:'#'+cssEsc(el.id)});
        }
        if (role && !name) candidates.push({s:S_ROLE_ONLY, m:'role_only', role:role});

        var nameAttr = el.getAttribute('name');
        if (nameAttr) {
            var nsel = tag.toLowerCase()+'[name="'+cssEsc(nameAttr)+'"]';
            candidates.push({s:S_CSS_ATTR, m:'css', v:nsel, sel:nsel});
        }
        if (tag==='INPUT') {
            var itype = el.getAttribute('type')||'text';
            var tsel2 = 'input[type="'+itype+'"]';
            candidates.push({s:S_CSS_ATTR, m:'css', v:tsel2, sel:tsel2});
        }
        if (el.className && typeof el.className==='string') {
            var classes = el.className.trim().split(/\s+/).filter(function(c){
                return c && !isGuidLike(c);
            });
            for (var ci=1; ci<=Math.min(classes.length,3); ci++) {
                var csel = tag.toLowerCase()+'.'+classes.slice(0,ci).map(cssEsc).join('.');
                candidates.push({s:S_CSS_TAG, m:'css', v:csel, sel:csel});
            }
        }

        candidates.sort(function(a,b){ return a.s - b.s; });
        return candidates.map(function(c) { return buildCandidateMeta(c); });
    }

    function buildCandidateMeta(c) {
        var matchCount = countCandidateMatches(c);
        return {
            kind: c.m,
            score: c.s,
            strict_match_count: matchCount,
            visible_match_count: matchCount,
            selected: false,
            locator: formatCandidate(c),
            reason: matchCount === 1 ? 'strict unique match' : ('strict matches = ' + matchCount)
        };
    }

    function countCandidateMatches(c) {
        var all = document.querySelectorAll('*');
        var count = 0;
        for (var i = 0; i < all.length; i++) {
            if (matchesCandidate(all[i], c)) count++;
        }
        return count;
    }

    function buildLocatorBundle(el) {
        var primary = generateLocator(el);
        var candidatePayloads = collectLocatorCandidates(el);
        var primaryJson = JSON.stringify(primary);
        var selectedPayload = null;

        for (var i = 0; i < candidatePayloads.length; i++) {
            if (JSON.stringify(candidatePayloads[i].locator) === primaryJson) {
                candidatePayloads[i].selected = true;
                selectedPayload = candidatePayloads[i];
                break;
            }
        }

        if (!selectedPayload) {
            selectedPayload = {
                kind: primary.method || 'css',
                score: S_FALLBACK,
                strict_match_count: 1,
                visible_match_count: 1,
                selected: true,
                locator: primary,
                reason: primary.method === 'nested' ? 'scoped parent-child fallback' : 'selected generated locator'
            };
            candidatePayloads.push(selectedPayload);
        }

        return {
            primary: primary,
            candidates: candidatePayloads,
            validation: {
                status: selectedPayload.strict_match_count === 1 ? 'ok' : 'fallback',
                details: selectedPayload.reason
            }
        };
    }

    function buildElementSnapshot(el) {
        el = retarget(el);
        var text = norm(el.textContent || '');
        return {
            tag: el.tagName.toLowerCase(),
            role: getRole(el) || '',
            name: accessibleName(el) || '',
            text: text.substring(0, 120),
            href: el.getAttribute('href') || '',
            target_attr: el.getAttribute('target') || '',
            id: el.id || '',
            classes: (typeof el.className === 'string' ? el.className.trim().split(/\s+/).filter(Boolean) : []).slice(0, 6),
            type: el.getAttribute('type') || '',
            placeholder: norm(el.getAttribute('placeholder') || ''),
            title: norm(el.getAttribute('title') || ''),
            name_attr: el.getAttribute('name') || ''
        };
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

    function describeFrameElement(frameEl) {
        if (!frameEl) return 'iframe';
        var tag = (frameEl.tagName || 'iframe').toLowerCase();
        var name = frameEl.getAttribute('name');
        if (name) return tag + '[name="' + cssEsc(name) + '"]';
        var title = frameEl.getAttribute('title');
        if (title) return tag + '[title="' + cssEsc(title) + '"]';
        if (frameEl.id && !isGuidLike(frameEl.id)) return tag + '#' + cssEsc(frameEl.id);
        var src = frameEl.getAttribute('src');
        if (src) return tag + '[src="' + cssEsc(src) + '"]';
        return cssFallback(frameEl);
    }

    function getFramePath() {
        var path = [];
        var currentWindow = window;
        try {
            while (currentWindow && currentWindow !== currentWindow.parent) {
                var frameEl = currentWindow === window ? window.frameElement : currentWindow.frameElement;
                if (!frameEl) break;
                path.unshift(describeFrameElement(frameEl));
                currentWindow = currentWindow.parent;
            }
        } catch (e) {
            // Cross-origin access can break parent traversal; keep collected path.
        }
        return path;
    }

    // ── Navigation deduplication ────────────────────────────────────
    var _lastAction = null;  // {action, time}
    var _lastClick = null;   // {locatorJson, time} for click dedup

    function emit(evt) {
        evt.timestamp = Date.now();
        evt.url = location.href;
        evt.frame_path = getFramePath();
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
        var locatorBundle = buildLocatorBundle(el);
        var locJson = JSON.stringify(locatorBundle.primary);
        var now = Date.now();
        // Deduplicate rapid clicks on the same element (within 1s)
        if (_lastClick && _lastClick.locatorJson===locJson && now-_lastClick.time<1000) {
            return;
        }
        _lastClick = {locatorJson:locJson, time:now};
        emit({
            action:'click',
            locator:locatorBundle.primary,
            locator_candidates:locatorBundle.candidates,
            validation:locatorBundle.validation,
            element_snapshot:buildElementSnapshot(el),
            tag:retarget(el).tagName
        });
    }, true);

    document.addEventListener('input', function(e) {
        if (!e.isTrusted) return;
        if (window.__rpa_paused) return;
        var el = e.target;
        clearTimeout(el.__rpa_timer);
        el.__rpa_timer = setTimeout(function() {
            var isPassword = (el.type === 'password');
            var locatorBundle = buildLocatorBundle(el);
            emit({action:'fill', locator:locatorBundle.primary,
                  locator_candidates:locatorBundle.candidates,
                  validation:locatorBundle.validation,
                  element_snapshot:buildElementSnapshot(el),
                  value: isPassword ? '{{credential}}' : (el.value||''),
                  tag:el.tagName,
                  sensitive: isPassword});
        }, 1500);
    }, true);

    document.addEventListener('change', function(e) {
        if (!e.isTrusted) return;
        if (window.__rpa_paused) return;
        var el = e.target;
        if (el.tagName === 'SELECT') {
            var locatorBundle = buildLocatorBundle(el);
            emit({action:'select', locator:locatorBundle.primary,
                  locator_candidates:locatorBundle.candidates,
                  validation:locatorBundle.validation,
                  element_snapshot:buildElementSnapshot(el),
                  value:el.value||'', tag:el.tagName});
        }
    }, true);

    document.addEventListener('keydown', function(e) {
        if (!e.isTrusted) return;
        if (window.__rpa_paused) return;
        if (e.key === 'Enter') {
            var el = e.target;
            var locatorBundle = buildLocatorBundle(el);
            emit({action:'press', locator:locatorBundle.primary,
                  locator_candidates:locatorBundle.candidates,
                  validation:locatorBundle.validation,
                  element_snapshot:buildElementSnapshot(el),
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
        self._tabs: Dict[str, Dict[str, Page]] = {}
        self._tab_meta: Dict[str, Dict[str, RPATab]] = {}
        self._page_tab_ids: Dict[str, Dict[int, str]] = {}
        self._bridged_context_ids: Dict[str, set[int]] = {}

    def attach_context(self, session_id: str, context: BrowserContext):
        self._contexts[session_id] = context
        self._pages.pop(session_id, None)
        self._tabs[session_id] = {}
        self._tab_meta[session_id] = {}
        self._page_tab_ids[session_id] = {}
        self._bridged_context_ids[session_id] = set()

        session = self.sessions.get(session_id)
        if session:
            session.active_tab_id = None

    def detach_context(self, session_id: str, context: Optional[BrowserContext] = None):
        current_context = self._contexts.get(session_id)
        if context is not None and current_context is not context:
            return

        self._contexts.pop(session_id, None)
        self._pages.pop(session_id, None)
        self._tabs.pop(session_id, None)
        self._tab_meta.pop(session_id, None)
        self._page_tab_ids.pop(session_id, None)
        self._bridged_context_ids.pop(session_id, None)

        session = self.sessions.get(session_id)
        if session:
            session.active_tab_id = None

    async def create_session(self, user_id: str, sandbox_session_id: str) -> RPASession:
        session_id = str(uuid.uuid4())
        session = RPASession(
            id=session_id,
            user_id=user_id,
            sandbox_session_id=sandbox_session_id,
        )
        self.sessions[session_id] = session

        browser = await get_cdp_connector().get_browser(
            session_id=sandbox_session_id,
            user_id=user_id,
        )
        context = await browser.new_context(no_viewport=True, accept_downloads=True)
        page = await context.new_page()
        page.set_default_timeout(RPA_PAGE_TIMEOUT_MS)
        page.set_default_navigation_timeout(RPA_PAGE_TIMEOUT_MS)

        self.attach_context(session_id, context)
        await self.register_page(session_id, page, make_active=True)

        def on_context_page(new_page):
            asyncio.create_task(self.register_context_page(session_id, new_page, make_active=True))

        context.on("page", on_context_page)
        await page.goto("about:blank")

        logger.info(f"[RPA] Session {session_id} started via CDP")
        return session

    async def register_page(
        self,
        session_id: str,
        page: Page,
        opener_tab_id: Optional[str] = None,
        make_active: bool = False,
    ) -> str:
        if session_id not in self.sessions:
            raise ValueError(f"Session {session_id} not found")

        tab_id = str(uuid.uuid4())
        self._tabs.setdefault(session_id, {})[tab_id] = page
        self._page_tab_ids.setdefault(session_id, {})[id(page)] = tab_id
        self._tab_meta.setdefault(session_id, {})[tab_id] = RPATab(
            tab_id=tab_id,
            title=await self._safe_page_title(page),
            url=getattr(page, "url", "") or "",
            opener_tab_id=opener_tab_id,
        )

        await self._ensure_context_recorder(session_id, page.context)
        await self._bind_page(session_id, tab_id, page)

        if make_active or not self.sessions[session_id].active_tab_id:
            await self.activate_tab(session_id, tab_id, source="auto")

        return tab_id

    async def register_context_page(self, session_id: str, page: Page, make_active: bool = True) -> str:
        session = self.sessions.get(session_id)
        opener_tab_id = session.active_tab_id if session else None
        tab_id = await self.register_page(
            session_id,
            page,
            opener_tab_id=opener_tab_id,
            make_active=make_active,
        )
        if opener_tab_id:
            await self._upgrade_recent_click_to_open_tab(session_id, opener_tab_id, tab_id)
        return tab_id

    async def _upgrade_recent_click_to_open_tab(self, session_id: str, source_tab_id: str, target_tab_id: str):
        session = self.sessions.get(session_id)
        if not session or not session.steps:
            return

        now = datetime.now()
        for step in reversed(session.steps):
            age_s = (now - step.timestamp).total_seconds()
            if age_s > 5:
                return
            if step.tab_id != source_tab_id:
                continue
            if step.action != "click":
                return

            step.action = "open_tab_click"
            step.source_tab_id = source_tab_id
            step.target_tab_id = target_tab_id
            step.description = f"{step.description} 并在新标签页打开"
            await self._broadcast_step(session_id, step)
            logger.debug(f"[RPA] Upgraded click to open_tab_click: source={source_tab_id} target={target_tab_id}")
            return

    @staticmethod
    def _describe_switch_tab(tab_id: str, title: str = "") -> str:
        return f'切换到标签页 {title or tab_id}'

    @staticmethod
    def _describe_close_tab(title: str = "", has_fallback: bool = False) -> str:
        label = title or "当前标签页"
        suffix = " 并切换到其他标签页" if has_fallback else ""
        return f"关闭标签页 {label}{suffix}"

    @staticmethod
    def _normalize_url(url: str) -> str:
        normalized = (url or "").strip()
        if not normalized:
            raise ValueError("URL is required")
        parsed = urlparse(normalized)
        if not parsed.scheme:
            normalized = f"https://{normalized}"
            parsed = urlparse(normalized)
        if parsed.scheme in {"http", "https"} and not parsed.netloc:
            raise ValueError("Invalid URL")
        return normalized

    async def navigate_active_tab(self, session_id: str, url: str) -> Dict[str, str]:
        session = self.sessions.get(session_id)
        if not session or not session.active_tab_id:
            raise ValueError(f"No active tab for session {session_id}")

        page = self.get_active_page(session_id)
        if page is None:
            raise ValueError(f"No active page for session {session_id}")

        normalized_url = self._normalize_url(url)
        await page.goto(normalized_url)
        await page.wait_for_load_state("domcontentloaded")

        tab = self._tab_meta.get(session_id, {}).get(session.active_tab_id)
        if tab:
            tab.url = getattr(page, "url", normalized_url) or normalized_url
            tab.last_seen_at = datetime.now()
            title = await self._safe_page_title(page)
            if title:
                tab.title = title

        return {
            "tab_id": session.active_tab_id,
            "url": getattr(page, "url", normalized_url) or normalized_url,
        }

    async def activate_tab(self, session_id: str, tab_id: str, source: str = "auto"):
        page = self._tabs.get(session_id, {}).get(tab_id)
        if page is None:
            raise ValueError(f"Tab {tab_id} not found for session {session_id}")

        session = self.sessions[session_id]
        previous_tab_id = session.active_tab_id
        session.active_tab_id = tab_id
        self._pages[session_id] = page

        tab = self._tab_meta.get(session_id, {}).get(tab_id)
        if tab:
            tab.last_seen_at = datetime.now()
            tab.url = getattr(page, "url", tab.url) or tab.url
            title = await self._safe_page_title(page)
            if title:
                tab.title = title

        try:
            await page.bring_to_front()
        except Exception:
            pass

        if (
            previous_tab_id
            and previous_tab_id != tab_id
            and source in {"user", "fallback"}
            and session.status == "recording"
            and not session.paused
        ):
            await self.add_step(
                session_id,
                {
                    "action": "switch_tab",
                    "target": "",
                    "value": "",
                    "label": "",
                    "tag": "",
                    "url": tab.url if tab else getattr(page, "url", ""),
                    "description": self._describe_switch_tab(tab_id, tab.title if tab else ""),
                    "sensitive": False,
                    "tab_id": previous_tab_id,
                    "source_tab_id": previous_tab_id,
                    "target_tab_id": tab_id,
                },
            )

        return {"tab_id": tab_id, "source": source}

    async def close_tab(self, session_id: str, tab_id: str, close_page: bool = True):
        page = self._tabs.get(session_id, {}).get(tab_id)
        tab = self._tab_meta.get(session_id, {}).get(tab_id)
        if page is None or tab is None:
            raise ValueError(f"Tab {tab_id} not found for session {session_id}")

        session = self.sessions[session_id]
        fallback_tab_id = None
        if session.active_tab_id == tab_id:
            if tab.opener_tab_id and tab.opener_tab_id in self._tabs.get(session_id, {}):
                fallback_tab_id = tab.opener_tab_id
            elif self._tabs.get(session_id):
                remaining_tab_ids = [existing_tab_id for existing_tab_id in self._tabs[session_id] if existing_tab_id != tab_id]
                if remaining_tab_ids:
                    fallback_tab_id = remaining_tab_ids[-1]

        tab.status = "closed"
        tab.last_seen_at = datetime.now()

        if session.status == "recording" and not session.paused:
            await self.add_step(
                session_id,
                {
                    "action": "close_tab",
                    "target": "",
                    "value": "",
                    "label": "",
                    "tag": "",
                    "url": tab.url,
                    "description": self._describe_close_tab(tab.title, fallback_tab_id is not None),
                    "sensitive": False,
                    "tab_id": tab_id,
                    "source_tab_id": tab_id,
                    "target_tab_id": fallback_tab_id,
                },
            )

        if close_page:
            try:
                await page.close()
            except Exception:
                pass

        self._tabs.get(session_id, {}).pop(tab_id, None)
        self._page_tab_ids.get(session_id, {}).pop(id(page), None)

        if session.active_tab_id == tab_id:
            if fallback_tab_id:
                await self.activate_tab(session_id, fallback_tab_id, source="fallback")
            else:
                session.active_tab_id = None
                self._pages.pop(session_id, None)

    def list_tabs(self, session_id: str) -> List[Dict[str, Any]]:
        active_tab_id = None
        if session_id in self.sessions:
            active_tab_id = self.sessions[session_id].active_tab_id

        return [
            {
                "tab_id": tab.tab_id,
                "title": tab.title,
                "url": tab.url,
                "opener_tab_id": tab.opener_tab_id,
                "status": tab.status,
                "active": tab.tab_id == active_tab_id,
            }
            for tab in self._tab_meta.get(session_id, {}).values()
        ]

    def get_active_page(self, session_id: str) -> Optional[Page]:
        active_tab_id = self.sessions.get(session_id).active_tab_id if session_id in self.sessions else None
        if not active_tab_id:
            return None
        return self._tabs.get(session_id, {}).get(active_tab_id)

    async def _ensure_context_recorder(self, session_id: str, context: BrowserContext):
        bridged_context_ids = self._bridged_context_ids.setdefault(session_id, set())
        context_key = id(context)
        if context_key in bridged_context_ids:
            return

        async def rpa_emit(source, event_json: str):
            try:
                evt = json.loads(event_json)
                source_page = getattr(source, "page", None)
                source_frame = getattr(source, "frame", None)
                resolved_tab_id = self._page_tab_ids.get(session_id, {}).get(id(source_page))
                if not resolved_tab_id:
                    session = self.sessions.get(session_id)
                    resolved_tab_id = session.active_tab_id if session else None
                if resolved_tab_id:
                    evt.setdefault("tab_id", resolved_tab_id)
                if source_frame and not evt.get("frame_path"):
                    evt["frame_path"] = await self._build_frame_path(source_frame)
                await self._handle_event(session_id, evt)
            except Exception as e:
                logger.error(f"[RPA] binding emit error: {e}")

        await context.expose_binding("__rpa_emit", rpa_emit, handle=False)
        await context.add_init_script(script=CAPTURE_JS)
        bridged_context_ids.add(context_key)

    async def _build_frame_path(self, frame) -> List[str]:
        path: List[str] = []
        current_frame = frame
        while current_frame:
            try:
                frame_selector = await self._describe_frame_selector(current_frame)
            except Exception:
                break
            path.append(frame_selector)
            current_frame = getattr(current_frame, "parent_frame", None)
        path.reverse()
        return path

    async def build_frame_path(self, frame) -> List[str]:
        return await self._build_frame_path(frame)

    async def _describe_frame_selector(self, frame) -> str:
        frame_element = await frame.frame_element()
        tag_name = str(await frame_element.evaluate("el => el.tagName.toLowerCase()")).lower()
        name_attr = await frame_element.get_attribute("name")
        if name_attr:
            return f"{tag_name}[name='{self._escape_css_attr_value(name_attr)}']"
        title_attr = await frame_element.get_attribute("title")
        if title_attr:
            return f"{tag_name}[title='{self._escape_css_attr_value(title_attr)}']"
        element_id = await frame_element.get_attribute("id")
        if element_id and not self._is_guid_like(element_id):
            return f"{tag_name}#{self._escape_css_identifier(element_id)}"
        return await frame_element.evaluate(
            """
            el => {
                const tag = el.tagName.toLowerCase();
                if (!el.parentElement) return tag;
                const siblings = Array.from(el.parentElement.children)
                    .filter(child => child.tagName === el.tagName);
                if (siblings.length <= 1) return tag;
                const index = siblings.indexOf(el) + 1;
                return `${tag}:nth-of-type(${index})`;
            }
            """
        )

    @staticmethod
    def _escape_css_attr_value(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    @staticmethod
    def _escape_css_identifier(value: str) -> str:
        escaped = []
        for char in value:
            if char.isalnum() or char in {"-", "_"}:
                escaped.append(char)
            else:
                escaped.append(f"\\{char}")
        return "".join(escaped)

    @staticmethod
    def _is_guid_like(value: str) -> bool:
        transitions = 0
        previous_type: Optional[str] = None
        for char in value:
            if char.islower():
                current_type = "lower"
            elif char.isupper():
                current_type = "upper"
            elif char.isdigit():
                current_type = "digit"
            else:
                current_type = "other"
            if previous_type and current_type != previous_type:
                transitions += 1
            previous_type = current_type
        return bool(value) and transitions >= len(value) / 4

    async def _bind_page(self, session_id: str, tab_id: str, page: Page):
        last_url = {"value": ""}

        def on_navigated(frame):
            if frame != page.main_frame:
                return
            new_url = frame.url
            if new_url and new_url != last_url["value"] and new_url != "about:blank":
                last_url["value"] = new_url
                tab = self._tab_meta.get(session_id, {}).get(tab_id)
                if tab:
                    tab.url = new_url
                    tab.last_seen_at = datetime.now()
                evt = {
                    "action": "navigate",
                    "url": new_url,
                    "timestamp": int(datetime.now().timestamp() * 1000),
                    "tab_id": tab_id,
                }
                asyncio.create_task(self._handle_event(session_id, evt))

        page.on("framenavigated", on_navigated)

        async def on_load(loaded_page):
            tab = self._tab_meta.get(session_id, {}).get(tab_id)
            if tab:
                tab.url = getattr(page, "url", tab.url) or tab.url
                tab.title = await self._safe_page_title(page)
                tab.last_seen_at = datetime.now()

        page.on("load", on_load)

        async def on_download(download):
            suggested = download.suggested_filename
            # Wait briefly for the click step to be recorded before upgrading it
            await asyncio.sleep(0.3)
            session = self.sessions.get(session_id)
            if session and session.steps:
                # Upgrade the most recent click step to a download_click
                for step in reversed(session.steps):
                    if step.action == "click" and step.tab_id == tab_id:
                        step.action = "download_click"
                        step.value = suggested
                        step.description = f"下载文件 {suggested}"
                        await self._broadcast_step(session_id, step)
                        return
            # Fallback: no preceding click found, record standalone
            evt = {
                "action": "download",
                "value": suggested,
                "url": getattr(page, "url", ""),
                "timestamp": int(datetime.now().timestamp() * 1000),
                "tab_id": tab_id,
            }
            await self._handle_event(session_id, evt)

        page.on("download", on_download)

        def on_close():
            if session_id in self.sessions and tab_id in self._tab_meta.get(session_id, {}):
                asyncio.create_task(self.close_tab(session_id, tab_id, close_page=False))

        page.on("close", on_close)

    async def _safe_page_title(self, page: Page) -> str:
        try:
            return await page.title()
        except Exception:
            return ""

    async def stop_session(self, session_id: str):
        if session_id in self.sessions:
            self.sessions[session_id].status = "stopped"

        context = self._contexts.pop(session_id, None)
        self.detach_context(session_id)
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

    async def select_step_locator_candidate(self, session_id: str, step_index: int, candidate_index: int) -> RPAStep:
        session = self.sessions.get(session_id)
        if not session or step_index < 0 or step_index >= len(session.steps):
            raise ValueError("Invalid step index")

        step = session.steps[step_index]
        if candidate_index < 0 or candidate_index >= len(step.locator_candidates):
            raise ValueError("Invalid locator candidate index")

        for index, candidate in enumerate(step.locator_candidates):
            candidate["selected"] = index == candidate_index

        selected_candidate = step.locator_candidates[candidate_index]
        locator = selected_candidate.get("locator")
        if not locator:
            raise ValueError("Locator candidate is missing locator payload")

        step.target = json.dumps(locator)
        if step.validation:
            step.validation["selected_candidate_index"] = candidate_index
            step.validation["selected_candidate_kind"] = selected_candidate.get("kind", "")
        await self._broadcast_step(session_id, step)
        return step

    def pause_recording(self, session_id: str):
        """Pause event recording (used during AI execution)."""
        if session_id in self.sessions:
            self.sessions[session_id].paused = True

    def resume_recording(self, session_id: str):
        """Resume event recording."""
        if session_id in self.sessions:
            self.sessions[session_id].paused = False

    def get_page(self, session_id: str) -> Optional[Page]:
        active_page = self.get_active_page(session_id)
        if active_page is not None:
            return active_page
        return self._pages.get(session_id)

    def owns_sandbox_session(self, user_id: str, sandbox_session_id: str) -> bool:
        return any(
            session.user_id == user_id and session.sandbox_session_id == sandbox_session_id
            for session in self.sessions.values()
        )

    async def _handle_event(self, session_id: str, evt: dict):
        if session_id not in self.sessions:
            return
        session = self.sessions[session_id]
        if session.status != "recording" or session.paused:
            return

        event_tab_id = evt.get("tab_id")
        if event_tab_id and event_tab_id != session.active_tab_id:
            if event_tab_id in self._tabs.get(session_id, {}):
                await self.activate_tab(session_id, event_tab_id, source="event")

        if evt.get("action") == "navigate":
            nav_ts = evt.get("timestamp", 0)
            steps = self.sessions[session_id].steps
            if steps:
                last_step = steps[-1]
                if (
                    last_step.action == "open_tab_click"
                    and last_step.target_tab_id == evt.get("tab_id")
                ):
                    logger.debug(f"[RPA] Skipping nav after popup open: {evt.get('url', '')[:60]}")
                    return
                if last_step.action in ("click", "press", "fill"):
                    last_ts = last_step.timestamp.timestamp() * 1000
                    same_tab = last_step.tab_id == evt.get("tab_id")
                    if nav_ts - last_ts < 5000 and same_tab:
                        if last_step.action == "click":
                            last_step.action = "navigate_click"
                            last_step.url = evt.get("url", last_step.url)
                            last_step.description = f"{last_step.description} 并跳转页面"
                            await self._broadcast_step(session_id, last_step)
                            logger.debug(f"[RPA] Upgraded click to navigate_click: {evt.get('url', '')[:60]}")
                            return
                        logger.debug(f"[RPA] Preserving nav after {last_step.action}: {evt.get('url', '')[:60]}")

        locator_info = evt.get("locator", {})
        is_sensitive = evt.get("sensitive", False)
        step_data = {
            "action": evt.get("action", "unknown"),
            "target": json.dumps(locator_info) if locator_info else "",
            "frame_path": evt.get("frame_path", []) or [],
            "locator_candidates": evt.get("locator_candidates", []) or [],
            "validation": evt.get("validation", {}) or {},
            "signals": evt.get("signals", {}) or {},
            "element_snapshot": evt.get("element_snapshot", {}) or {},
            "value": "{{credential}}" if is_sensitive else evt.get("value", ""),
            "label": "",
            "tag": evt.get("tag", ""),
            "url": evt.get("url", ""),
            "description": self._make_description(evt),
            "sensitive": is_sensitive,
            "tab_id": evt.get("tab_id"),
            "source_tab_id": evt.get("source_tab_id"),
            "target_tab_id": evt.get("target_tab_id"),
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
            display_value = '*****' if evt.get("sensitive") else f'"{value}"'
            return f'输入 {display_value} 到 {target}'
        if action == "click":
            return f"点击 {target}"
        if action == "press":
            return f"按下 {value} 在 {target}"
        if action == "select":
            return f"选择 {value} 在 {target}"
        if action == "navigate":
            return f"导航到 {evt.get('url', '')}"
        if action == "download":
            return f"下载文件 {value}"
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
