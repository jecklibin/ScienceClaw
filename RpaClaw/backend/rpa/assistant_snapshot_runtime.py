from __future__ import annotations


SNAPSHOT_V2_JS = r"""() => {
    const ACTIONABLE = 'a,button,input,textarea,select,[role=button],[role=link],[role=menuitem],[role=menuitemradio],[role=tab],[role=checkbox],[role=radio],[contenteditable=true]';
    const CONTENT = 'h1,h2,h3,h4,h5,h6,th,td,dt,dd,li,p,span,label,[data-field],[data-label],[data-value],[role=heading],[role=cell],[role=rowheader],[role=columnheader]';
    const recorder = globalThis.__rpaPlaywrightRecorder || null;
    const result = { actionable_nodes: [], content_nodes: [], containers: [] };
    const containerMap = new Map();
    let actionableIndex = 1;
    let contentIndex = 1;
    let containerIndex = 1;

    function normalizeText(value, limit) {
        return String(value || '').replace(/\s+/g, ' ').trim().slice(0, limit || 160);
    }

    function isVisible(el, rect) {
        if (!rect || rect.width <= 0 || rect.height <= 0)
            return false;
        const style = getComputedStyle(el);
        if (!style)
            return false;
        return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
    }

    function centerPoint(rect) {
        return {
            x: Math.round(rect.left + rect.width / 2),
            y: Math.round(rect.top + rect.height / 2),
        };
    }

    function hitTestOk(el, rect) {
        try {
            const point = centerPoint(rect);
            const hit = document.elementFromPoint(point.x, point.y);
            if (!hit)
                return false;
            return hit === el || el.contains(hit) || hit.contains(el);
        } catch (e) {
            return false;
        }
    }

    function bbox(rect) {
        return {
            x: Math.round(rect.left),
            y: Math.round(rect.top),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
        };
    }

    function escapeCssAttributeValue(value) {
        return String(value || '')
            .replace(/\\/g, '\\\\')
            .replace(/"/g, '\\"')
            .replace(/\s+/g, ' ')
            .trim();
    }

    function fallbackRole(el) {
        const explicitRole = el.getAttribute('role');
        if (explicitRole)
            return explicitRole;
        const tag = el.tagName.toLowerCase();
        if (tag === 'button')
            return 'button';
        if (tag === 'select')
            return 'combobox';
        if (tag === 'textarea')
            return 'textbox';
        if (tag === 'input') {
            const type = (el.getAttribute('type') || '').toLowerCase();
            if (type === 'checkbox')
                return 'checkbox';
            if (type === 'radio')
                return 'radio';
            if (type === 'button' || type === 'submit')
                return 'button';
            return 'textbox';
        }
        if (tag === 'a' && el.hasAttribute('href'))
            return 'link';
        return '';
    }

    function getRole(el) {
        try {
            return recorder && recorder.getRole ? recorder.getRole(el) || '' : fallbackRole(el);
        } catch (e) {
            return fallbackRole(el);
        }
    }

    function getAccessibleName(el) {
        try {
            if (recorder && recorder.getAccessibleName)
                return normalizeText(recorder.getAccessibleName(el) || '', 160);
        } catch (e) {}
        return normalizeText(el.getAttribute('aria-label') || el.innerText || el.value || '', 160);
    }

    function detectContainerKind(el) {
        if (!el)
            return '';
        const tag = el.tagName.toLowerCase();
        const role = el.getAttribute('role') || '';
        const className = normalizeText(el.className || '', 120).toLowerCase();
        if (tag === 'table' || role === 'table' || role === 'grid')
            return 'table';
        if (tag === 'ul' || tag === 'ol' || role === 'list')
            return 'list';
        if (role === 'toolbar')
            return 'toolbar';
        if (tag === 'form')
            return 'form_section';
        if (tag === 'section')
            return 'form_section';
        if (tag === 'article')
            return 'card_group';
        if (/\bcard\b/.test(className))
            return 'card';
        if (/\bpanel\b/.test(className))
            return 'panel';
        if (/\bform\b/.test(className))
            return 'form_section';
        if (/\bdetail\b|\binfo\b|\bsummary\b|\bprofile\b/.test(className))
            return 'detail_section';
        if (el.hasAttribute('data-section') || el.hasAttribute('data-region'))
            return 'section';
        if (role === 'region')
            return 'section';
        if (el.querySelector('table,[role=table],[role=grid]'))
            return 'detail_section';
        if (el.querySelector('label,[data-field],[data-value],[data-label],dt,dd'))
            return 'detail_section';
        return '';
    }

    function detectContainerName(el) {
        if (!el)
            return '';
        const direct = normalizeText(
            el.getAttribute('aria-label') ||
            el.getAttribute('title') ||
            (el.querySelector('caption,h1,h2,h3,h4,legend,[role=heading]') || {}).innerText ||
            '',
            80
        );
        return direct;
    }

    function isMeaningfulBusinessContainer(el) {
        if (!el)
            return false;
        const tag = el.tagName.toLowerCase();
        const role = el.getAttribute('role') || '';
        if (!['div', 'main', 'aside', 'nav', 'header', 'footer'].includes(tag) && !['region', 'group'].includes(role))
            return false;

        const className = normalizeText(el.className || '', 120).toLowerCase();
        const directLabel = normalizeText(
            [
                el.getAttribute('aria-label'),
                el.getAttribute('title'),
                el.getAttribute('data-section'),
                el.getAttribute('data-region'),
                el.getAttribute('data-card'),
                el.getAttribute('data-panel'),
            ].filter(Boolean).join(' '),
            120
        ).toLowerCase();
        const hasExplicitCue = Boolean(
            directLabel ||
            el.hasAttribute('aria-label') ||
            el.hasAttribute('title') ||
            el.hasAttribute('data-section') ||
            el.hasAttribute('data-region') ||
            el.hasAttribute('data-card') ||
            el.hasAttribute('data-panel')
        );
        const keywordHit = /\b(card|panel|section|form|detail|info|summary|profile|content|record)\b/.test(className + ' ' + directLabel);
        if (!hasExplicitCue && !keywordHit)
            return false;

        const structuralHit = Boolean(el.querySelector('h1,h2,h3,h4,h5,h6,table,[role=table],[role=grid],label,[data-field],[data-value],[data-label],dt,dd'));
        const meaningfulChildren = el.querySelectorAll('h1,h2,h3,h4,h5,h6,table,[role=table],[role=grid],label,[data-field],[data-value],[data-label],dt,dd,button,a,input,textarea,select').length;
        return structuralHit || meaningfulChildren >= 3;
    }

    function findContainerElement(el) {
        const explicitContainer = el.closest('table,[role=table],[role=grid],ul,ol,[role=list],form,[role=toolbar],section,article');
        if (explicitContainer)
            return explicitContainer;

        let current = el.parentElement;
        while (current) {
            if (isMeaningfulBusinessContainer(current))
                return current;
            current = current.parentElement;
        }
        return null;
    }

    function ensureContainer(el) {
        const containerEl = findContainerElement(el);
        if (!containerEl)
            return '';
        if (containerMap.has(containerEl))
            return containerMap.get(containerEl).container_id;
        const rect = containerEl.getBoundingClientRect();
        const container = {
            container_id: 'container-' + containerIndex++,
            frame_path: [],
            container_kind: detectContainerKind(containerEl) || 'container',
            name: detectContainerName(containerEl),
            bbox: bbox(rect),
            summary: normalizeText(containerEl.innerText || '', 120),
            child_actionable_ids: [],
            child_content_ids: [],
        };
        containerMap.set(containerEl, container);
        result.containers.push(container);
        return container.container_id;
    }

    function buildContentLocator(el, role, name, text, placeholder, title) {
        const dataField = normalizeText(el.getAttribute('data-field') || '', 80);
        const dataLabel = normalizeText(el.getAttribute('data-label') || '', 80);
        const dataValue = normalizeText(el.getAttribute('data-value') || '', 80);
        const stableAttributes = [
            { attr: 'data-field', value: dataField, reason: 'stable data-field locator' },
            { attr: 'data-label', value: dataLabel, reason: 'stable data-label locator' },
            { attr: 'data-value', value: dataValue, reason: 'stable data-value locator' },
        ];
        for (const item of stableAttributes) {
            if (!item.value)
                continue;
            const cssValue = escapeCssAttributeValue(item.value);
            const cssSelector = '[' + item.attr + '="' + cssValue + '"]';
            return {
                primary: { method: 'css', value: cssSelector },
                candidates: [{
                    kind: 'css',
                    selected: true,
                    locator: { method: 'css', value: cssSelector },
                    strict_match_count: 1,
                    visible_match_count: 1,
                    reason: item.reason,
                }],
                validation: { status: 'stable', details: item.reason, selected_candidate_index: 0, selected_candidate_kind: 'css' },
            };
        }
        return buildFallbackLocator(el, role, name, text, placeholder, title);
    }

    function buildFallbackLocator(el, role, name, text, placeholder, title) {
        if (role && name) {
            return {
                primary: { method: 'role', role, name },
                candidates: [{ kind: 'role', selected: true, locator: { method: 'role', role, name }, strict_match_count: 1, visible_match_count: 1, reason: 'fallback role candidate' }],
                validation: { status: 'fallback', details: 'fallback role candidate', selected_candidate_index: 0, selected_candidate_kind: 'role' },
            };
        }
        if (placeholder) {
            return {
                primary: { method: 'placeholder', value: placeholder },
                candidates: [{ kind: 'placeholder', selected: true, locator: { method: 'placeholder', value: placeholder }, strict_match_count: 1, visible_match_count: 1, reason: 'fallback placeholder candidate' }],
                validation: { status: 'fallback', details: 'fallback placeholder candidate', selected_candidate_index: 0, selected_candidate_kind: 'placeholder' },
            };
        }
        if (text || name) {
            const value = text || name;
            return {
                primary: { method: 'text', value },
                candidates: [{ kind: 'text', selected: true, locator: { method: 'text', value }, strict_match_count: 1, visible_match_count: 1, reason: 'fallback text candidate' }],
                validation: { status: 'fallback', details: 'fallback text candidate', selected_candidate_index: 0, selected_candidate_kind: 'text' },
            };
        }
        if (title) {
            return {
                primary: { method: 'title', value: title },
                candidates: [{ kind: 'title', selected: true, locator: { method: 'title', value: title }, strict_match_count: 1, visible_match_count: 1, reason: 'fallback title candidate' }],
                validation: { status: 'fallback', details: 'fallback title candidate', selected_candidate_index: 0, selected_candidate_kind: 'title' },
            };
        }
        const tag = el.tagName.toLowerCase();
        return {
            primary: { method: 'css', value: tag },
            candidates: [{ kind: 'css', selected: true, locator: { method: 'css', value: tag }, strict_match_count: 1, visible_match_count: 1, reason: 'fallback css candidate' }],
            validation: { status: 'fallback', details: 'fallback css candidate', selected_candidate_index: 0, selected_candidate_kind: 'css' },
        };
    }

    function buildLocatorBundle(el, role, name, text, placeholder, title) {
        try {
            if (recorder && recorder.buildLocatorBundle) {
                const bundle = recorder.buildLocatorBundle(el);
                if (bundle && bundle.primary)
                    return bundle;
            }
        } catch (e) {}
        return buildFallbackLocator(el, role, name, text, placeholder, title);
    }

    function actionKinds(el, role) {
        const tag = el.tagName.toLowerCase();
        const type = (el.getAttribute('type') || '').toLowerCase();
        const actions = new Set();
        if (tag === 'input' || tag === 'textarea' || el.isContentEditable)
            actions.add('fill');
        if (tag === 'select')
            actions.add('select');
        if (!actions.size || role === 'button' || role === 'link' || role === 'checkbox' || role === 'radio')
            actions.add('click');
        if (role === 'textbox')
            actions.add('press');
        if (type === 'checkbox' || type === 'radio') {
            actions.delete('fill');
            actions.add('click');
        }
        return Array.from(actions);
    }

    function semanticKind(el, role) {
        const tag = el.tagName.toLowerCase();
        const className = normalizeText(el.className || '', 80);
        const dataField = normalizeText(el.getAttribute('data-field') || '', 80);
        const dataLabel = normalizeText(el.getAttribute('data-label') || '', 80);
        const dataValue = normalizeText(el.getAttribute('data-value') || '', 80);
        if (role === 'heading' || /^h[1-6]$/.test(tag))
            return 'heading';
        if (tag === 'td' || role === 'cell')
            return 'cell';
        if (tag === 'th' || role === 'rowheader' || role === 'columnheader')
            return 'header_cell';
        if (tag === 'li')
            return 'item';
        if (tag === 'label' || /label/i.test(className) || dataLabel)
            return 'label';
        if (dataField || dataValue || /value/i.test(className))
            return 'field_value';
        return 'text';
    }

    const actionableSeen = new Set();
    for (const el of Array.from(document.querySelectorAll(ACTIONABLE))) {
        const rect = el.getBoundingClientRect();
        if (!isVisible(el, rect))
            continue;
        if (el.disabled)
            continue;
        const role = getRole(el);
        const name = getAccessibleName(el);
        const text = normalizeText(el.innerText || '', 160);
        const placeholder = normalizeText(el.getAttribute('placeholder') || '', 80);
        const title = normalizeText(el.getAttribute('title') || '', 80);
        const key = [role, name, placeholder, title, bbox(rect).x, bbox(rect).y].join('|');
        if (actionableSeen.has(key))
            continue;
        actionableSeen.add(key);
        const containerId = ensureContainer(el);
        const locatorBundle = buildLocatorBundle(el, role, name, text, placeholder, title);
        const node = {
            node_id: 'actionable-' + actionableIndex++,
            frame_path: [],
            container_id: containerId,
            tag: el.tagName.toLowerCase(),
            role,
            name,
            text,
            type: normalizeText(el.getAttribute('type') || '', 40),
            placeholder,
            title,
            bbox: bbox(rect),
            center_point: centerPoint(rect),
            is_visible: true,
            is_enabled: !el.disabled,
            hit_test_ok: hitTestOk(el, rect),
            action_kinds: actionKinds(el, role),
            locator: locatorBundle.primary,
            locator_candidates: locatorBundle.candidates || [],
            validation: locatorBundle.validation || { status: 'fallback', details: 'locator bundle unavailable' },
            element_snapshot: {
                tag: el.tagName.toLowerCase(),
                text,
                title,
                href: normalizeText(el.getAttribute('href') || '', 120),
            },
        };
        result.actionable_nodes.push(node);
        if (containerId) {
            const container = Array.from(containerMap.values()).find(item => item.container_id === containerId);
            if (container)
                container.child_actionable_ids.push(node.node_id);
        }
        if (result.actionable_nodes.length >= 120)
            break;
    }

    const contentSeen = new Set();
    for (const el of Array.from(document.querySelectorAll(CONTENT))) {
        const rect = el.getBoundingClientRect();
        if (!isVisible(el, rect))
            continue;
        const text = normalizeText(el.innerText || '', 200);
        if (!text)
            continue;
        const key = [text, bbox(rect).x, bbox(rect).y].join('|');
        if (contentSeen.has(key))
            continue;
        contentSeen.add(key);
        const role = getRole(el);
        const containerId = ensureContainer(el);
        const node = {
            node_id: 'content-' + contentIndex++,
            frame_path: [],
            container_id: containerId,
            semantic_kind: semanticKind(el, role),
            role,
            text,
            bbox: bbox(rect),
            locator: buildContentLocator(el, role, '', text, '', '').primary,
            element_snapshot: {
                tag: el.tagName.toLowerCase(),
                text,
                class: normalizeText(el.className || '', 80),
                data_field: normalizeText(el.getAttribute('data-field') || '', 80),
                data_label: normalizeText(el.getAttribute('data-label') || '', 80),
                data_value: normalizeText(el.getAttribute('data-value') || '', 80),
            },
        };
        result.content_nodes.push(node);
        if (containerId) {
            const container = Array.from(containerMap.values()).find(item => item.container_id === containerId);
            if (container)
                container.child_content_ids.push(node.node_id);
        }
        if (result.content_nodes.length >= 160)
            break;
    }

    return JSON.stringify(result);
}"""
