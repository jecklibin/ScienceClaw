(() => {
    var docMarker = '__rpa_capture_installed__';
    if (document[docMarker]) return;
    document[docMarker] = true;
    if (typeof window.__rpa_paused === 'undefined') window.__rpa_paused = false;

    function norm(value) {
        return (value || '').replace(/\s+/g, ' ').trim();
    }

    var INTERACTIVE = ['BUTTON', 'A', 'SELECT', 'TEXTAREA'];
    var INTERACTIVE_ROLES = ['button', 'link', 'checkbox', 'radio', 'tab', 'menuitem',
        'option', 'switch', 'combobox'];

    function retarget(el) {
        if (!el || !el.tagName) return null;
        if (['INPUT', 'TEXTAREA', 'SELECT'].indexOf(el.tagName) >= 0) return el;
        if (el.isContentEditable) return el;
        var cur = el;
        while (cur && cur !== document.body) {
            if (INTERACTIVE.indexOf(cur.tagName) >= 0) return cur;
            var role = cur.getAttribute && cur.getAttribute('role');
            if (role && INTERACTIVE_ROLES.indexOf(role) >= 0) return cur;
            cur = cur.parentElement;
        }
        return el;
    }

    function cssEsc(value) {
        try {
            return CSS.escape(value);
        } catch (error) {
            return String(value).replace(/([\\"'\[\](){}|^$.*+?])/g, '\\$1');
        }
    }

    function isGuidLike(id) {
        if (!id || id.length < 8) return false;
        var transitions = 0;
        for (var i = 1; i < id.length; i++) {
            var prev = charType(id[i - 1]);
            var next = charType(id[i]);
            if (prev !== next) transitions++;
        }
        return transitions >= id.length / 4;
    }

    function charType(char) {
        if (char >= 'a' && char <= 'z') return 1;
        if (char >= 'A' && char <= 'Z') return 2;
        if (char >= '0' && char <= '9') return 3;
        return 4;
    }

    function cssFallback(el) {
        var parts = [];
        var cur = el;
        while (cur && cur !== document.body && cur !== document.documentElement) {
            var seg = cur.tagName.toLowerCase();
            if (cur.id && !isGuidLike(cur.id)) {
                parts.unshift('#' + cssEsc(cur.id));
                break;
            }
            if (cur.parentElement) {
                var siblings = cur.parentElement.children;
                var sameTagIndex = 0;
                var sameTagCount = 0;
                for (var i = 0; i < siblings.length; i++) {
                    if (siblings[i].tagName === cur.tagName) {
                        sameTagCount += 1;
                        if (siblings[i] === cur) sameTagIndex = sameTagCount;
                    }
                }
                if (sameTagCount > 1) seg += ':nth-of-type(' + sameTagIndex + ')';
            }
            parts.unshift(seg);
            cur = cur.parentElement;
            if (parts.length >= 4) break;
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
        } catch (error) {
            // Cross-origin parent access can fail; keep the path collected so far.
        }
        return path;
    }

    function buildLocatorBundle(el) {
        var target = retarget(el);
        if (!target) {
            return {
                primary: null,
                candidates: [],
                validation: { status: 'broken', details: 'No target element to generate selector for' }
            };
        }
        if (!window.__rpaPlaywrightRecorder || !window.__rpaPlaywrightRecorder.buildLocatorBundle) {
            return {
                primary: null,
                candidates: [],
                validation: { status: 'broken', details: 'Playwright recorder runtime is unavailable' }
            };
        }
        return window.__rpaPlaywrightRecorder.buildLocatorBundle(target);
    }

    function buildElementSnapshot(el) {
        el = retarget(el);
        if (!el) return {};
        var text = norm(el.textContent || '');
        var role = '';
        var name = '';
        if (window.__rpaPlaywrightRecorder) {
            try {
                role = window.__rpaPlaywrightRecorder.getRole(el) || '';
            } catch (error) {}
            try {
                name = window.__rpaPlaywrightRecorder.getAccessibleName(el) || '';
            } catch (error) {}
        }
        return {
            tag: el.tagName.toLowerCase(),
            role: role,
            name: name,
            text: text.substring(0, 120),
            id: el.id || '',
            classes: (typeof el.className === 'string' ? el.className.trim().split(/\s+/).filter(Boolean) : []).slice(0, 6),
            type: el.getAttribute('type') || '',
            placeholder: norm(el.getAttribute('placeholder') || ''),
            title: norm(el.getAttribute('title') || ''),
            name_attr: el.getAttribute('name') || ''
        };
    }

    function hasClassToken(el, pattern) {
        if (!el || typeof el.className !== 'string') return false;
        return pattern.test(el.className);
    }

    function isMenuLikeElement(el) {
        if (!el || !el.getAttribute) return false;
        var role = (el.getAttribute('role') || '').toLowerCase();
        if (role === 'menu' || role === 'listbox') return true;
        return hasClassToken(el, /(menu|dropdown|popover|popup|listbox)/i);
    }

    function isMenuItemElement(el) {
        if (!el || !el.getAttribute) return false;
        var role = (el.getAttribute('role') || '').toLowerCase();
        if (['menuitem', 'menuitemcheckbox', 'menuitemradio', 'option'].indexOf(role) >= 0) return true;
        if (closestElement(el, '[role="menu"], [role="listbox"]')) return true;
        var cur = el;
        while (cur && cur !== document.body) {
            if (isMenuLikeElement(cur)) return true;
            cur = cur.parentElement;
        }
        return false;
    }

    function isMenuTriggerCandidate(el) {
        if (!el || !el.getAttribute) return false;
        var role = (el.getAttribute('role') || '').toLowerCase();
        var hasPopup = (el.getAttribute('aria-haspopup') || '').toLowerCase();
        if (hasPopup === 'menu' || hasPopup === 'listbox' || hasPopup === 'true') return true;
        if (el.hasAttribute('aria-expanded')) return true;
        if (role === 'button' || role === 'link') return true;
        return el.tagName === 'BUTTON' || el.tagName === 'A';
    }

    function mergeSignals(existingSignals, patchSignals) {
        var next = Object.assign({}, existingSignals || {});
        for (var key in patchSignals) {
            if (!Object.prototype.hasOwnProperty.call(patchSignals, key)) continue;
            next[key] = Object.assign({}, next[key] || {}, patchSignals[key] || {});
        }
        return next;
    }

    function annotateActionPayload(action, el, payload) {
        var next = Object.assign({}, payload || {});
        var target = retarget(el);
        if (!target) return next;
        if (action === 'hover' && isMenuTriggerCandidate(target)) {
            next.signals = mergeSignals(next.signals, {
                hover: {
                    is_menu_trigger_candidate: true
                }
            });
        }
        if (action === 'click' && isMenuItemElement(target)) {
            next.signals = mergeSignals(next.signals, {
                menu_context: {
                    is_menu_item: true
                }
            });
        }
        return next;
    }

    var _eventSequence = 0;

    function emit(evt) {
        evt.timestamp = Date.now();
        _eventSequence += 1;
        evt.sequence = _eventSequence;
        evt.url = location.href;
        evt.frame_path = getFramePath();
        if (!evt.tab_id && window.__rpa_tab_id) evt.tab_id = window.__rpa_tab_id;
        window.__rpa_emit(JSON.stringify(evt));
    }

    function emitAction(action, el, extra) {
        var locatorBundle = buildLocatorBundle(el);
        var payload = extra && typeof extra === 'object' ? extra : {};
        payload = annotateActionPayload(action, el, payload);
        emit(Object.assign({
            action: action,
            locator: locatorBundle.primary,
            locator_candidates: locatorBundle.candidates,
            validation: locatorBundle.validation,
            element_snapshot: buildElementSnapshot(el),
            tag: el && el.tagName ? el.tagName : ''
        }, payload));
    }

    if (!window.__rpaPlaywrightActions || !window.__rpaPlaywrightActions.install) {
        console.warn('[RPA] Recorder action runtime is unavailable');
        return;
    }

    window.__rpaPlaywrightActions.install({
        document: document,
        isPaused: function() { return !!window.__rpa_paused; },
        retarget: retarget,
        emitAction: emitAction,
    });

    console.log('[RPA] Event capture injected');
})();
