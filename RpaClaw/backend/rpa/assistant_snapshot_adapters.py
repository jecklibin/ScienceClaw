from __future__ import annotations


TABLE_VIEW_ADAPTERS_JS = r"""    function collectJalorGridTableView(root) {
        const headerCells = Array.from(root.querySelectorAll('.jalor-igrid-head tbody.igrid-head td'));
        const bodyRows = Array.from(root.querySelectorAll('.jalor-igrid-body tbody.igrid-data tr.grid-row'))
            .filter(row => !row.matches('tr.grid-row-group') && row.querySelector('td'));
        if (!headerCells.length || !bodyRows.length)
            return null;

        const headerByField = new Map();
        const headerByCol = new Map();
        const headers = [];
        headerCells.forEach((cell, index) => {
            const fieldName = attr(cell, 'field', 80);
            const colNumber = attr(cell, '_col', 40);
            const header = textOf(cell, 120);
            const columnId = fieldName || colNumber || `index:${index}`;
            const record = {
                index,
                column_id: columnId,
                field: fieldName,
                col: colNumber,
                header,
                role: '',
            };
            headers.push(record);
            if (fieldName)
                headerByField.set(fieldName, record);
            if (colNumber)
                headerByCol.set(colNumber, record);
        });

        const bodyTable = root.querySelector('.jalor-igrid-body table');
        const bodyTableId = attr(bodyTable, 'id', 120);
        const rowSelector = bodyTableId ? `#${escapeCssIdentifier(bodyTableId)} tbody.igrid-data tr.grid-row` : '.jalor-igrid-body tbody.igrid-data tr.grid-row';
        const rows = [];
        const columnSamples = new Map();
        for (const row of bodyRows.slice(0, 10)) {
            const rowIndex = rows.length;
            const cells = [];
            const cellEls = Array.from(row.querySelectorAll('td')).filter(cell => !cell.closest('tr.grid-row-group'));
            cellEls.forEach((cell, cellIndex) => {
                const fieldName = attr(cell, 'field', 80);
                const colNumber = attr(cell, '_col', 40);
                const columnKey = fieldName || colNumber || `index:${cellIndex}`;
                const headerRecord = (fieldName ? headerByField.get(fieldName) : null) || (colNumber ? headerByCol.get(colNumber) : null) || headers[cellIndex];
                const text = textOf(cell, 200);
                const actions = Array.from(cell.querySelectorAll('a,button,input[type=checkbox],[role=button],[role=link]')).slice(0, 4).map(action => {
                    const tag = action.tagName.toLowerCase();
                    const role = getRole(action) || tag;
                    const label = getAccessibleName(action) || textOf(action, 120) || role;
                    const selector = fieldName
                        ? `td[field="${escapeCssAttributeValue(fieldName)}"] ${tag}`
                        : (colNumber ? `td[_col="${escapeCssAttributeValue(colNumber)}"] ${tag}` : `td:nth-child(${cellIndex + 1}) ${tag}`);
                    return {
                        kind: role,
                        label,
                        locator: { method: 'relative_css', scope: 'row', value: selector },
                    };
                });
                cells.push({
                    column_id: headerRecord ? headerRecord.column_id : columnKey,
                    field: fieldName,
                    col: colNumber,
                    column_index: cellIndex,
                    column_header: headerRecord ? headerRecord.header : '',
                    text,
                    value_kind: valueKind(text),
                    row_local_actions: actions,
                    actions,
                });
                if (!columnSamples.has(columnKey))
                    columnSamples.set(columnKey, { texts: [], hasCheckbox: false, hasLink: false });
                const sample = columnSamples.get(columnKey);
                if (text)
                    sample.texts.push(text);
                sample.hasCheckbox = sample.hasCheckbox || Boolean(cell.querySelector('input[type=checkbox]'));
                sample.hasLink = sample.hasLink || Boolean(cell.querySelector('a,[role=link]'));
            });
            rows.push({
                index: rowIndex,
                source_row_index: attr(row, '_row', 40),
                cells,
                locator_hints: [
                    {
                        kind: 'playwright',
                        expression: "page.locator('" + rowSelector + "').nth(" + rowIndex + ")",
                    },
                ],
            });
        }

        const columns = headers.map((header, index) => {
            const sample = columnSamples.get(header.field || header.col || `index:${index}`) || { texts: [], hasCheckbox: false, hasLink: false };
            return {
                index,
                column_id: header.column_id,
                field: header.field,
                col: header.col,
                header: header.header,
                role: columnRole(header.header, header.column_id, sample.texts.slice(0, 5), sample.hasCheckbox, sample.hasLink),
                sample_values: sample.texts.slice(0, 3),
            };
        });

        const explicitTitle = attr(root, 'aria-label', 120) || attr(root, 'title', 120);
        const nearbyTitle = nearestTableTitle(root);
        const title = explicitTitle || nearbyTitle.title;
        return {
            kind: 'table_view',
            framework_hint: 'jalor-igrid',
            title,
            title_source: explicitTitle ? 'root_attribute' : nearbyTitle.source,
            nearby_headings: nearbyTitle.title ? [nearbyTitle.title] : [],
            row_count_observed: bodyRows.length,
            columns,
            rows,
            auxiliary_text: [],
        };
    }

    function collectElementTableView(root) {
        const headerCells = Array.from(root.querySelectorAll('.el-table__header-wrapper thead th'))
            .filter(cell => textOf(cell, 120));
        const bodyRows = Array.from(root.querySelectorAll('.el-table__body-wrapper tbody tr'))
            .filter(row => row.querySelector('td'));
        if (!headerCells.length || !bodyRows.length)
            return null;

        const headers = headerCells.map((cell, index) => {
            const header = textOf(cell, 120);
            const colId = attr(cell, 'data-colid', 80) || attr(cell, 'data-column-id', 80) || `index:${index}`;
            return {
                index,
                column_id: colId,
                header,
                role: '',
            };
        });

        const rows = [];
        const columnSamples = new Map();
        for (const row of bodyRows.slice(0, 10)) {
            const rowIndex = rows.length;
            const cells = [];
            const cellEls = Array.from(row.querySelectorAll('td'));
            cellEls.forEach((cell, cellIndex) => {
                const headerRecord = headers[cellIndex] || { column_id: `index:${cellIndex}`, header: '' };
                const text = textOf(cell, 200);
                const actions = Array.from(cell.querySelectorAll('a,button,input[type=checkbox],[role=button],[role=link]')).slice(0, 4).map(action => {
                    const tag = action.tagName.toLowerCase();
                    const role = getRole(action) || tag;
                    const label = getAccessibleName(action) || textOf(action, 120) || role;
                    return {
                        kind: role,
                        label,
                        locator: { method: 'relative_css', scope: 'row', value: `td:nth-child(${cellIndex + 1}) ${tag}` },
                    };
                });
                const controls = Array.from(cell.querySelectorAll('input,textarea,select,[contenteditable=true],[role=textbox],[role=spinbutton],[role=combobox]')).slice(0, 6).map(control => {
                    const tag = control.tagName.toLowerCase();
                    const role = getRole(control) || (tag === 'input' && attr(control, 'type', 40) === 'number' ? 'spinbutton' : tag);
                    const label = getAccessibleName(control) || attr(control, 'aria-label', 120) || attr(control, 'placeholder', 120) || textOf(control, 120) || role;
                    const nth = Array.from(cell.querySelectorAll(tag)).indexOf(control);
                    return {
                        kind: role,
                        label,
                        placeholder: attr(control, 'placeholder', 120),
                        test_id: attr(control, 'data-testid', 120) || attr(control, 'data-test', 120),
                        input_type: tag === 'input' ? attr(control, 'type', 40) : '',
                        value: 'value' in control ? normalizeText(control.value || '', 120) : textOf(control, 120),
                        locator: { method: 'relative_css', scope: 'row', value: `td:nth-child(${cellIndex + 1}) ${tag}${nth > 0 ? `:nth-of-type(${nth + 1})` : ''}` },
                    };
                });
                cells.push({
                    column_id: headerRecord.column_id,
                    column_index: cellIndex,
                    column_header: headerRecord.header,
                    text,
                    value_kind: valueKind(text),
                    controls,
                    row_local_actions: actions,
                    actions,
                });
                const key = headerRecord.column_id || `index:${cellIndex}`;
                if (!columnSamples.has(key))
                    columnSamples.set(key, { texts: [], hasCheckbox: false, hasLink: false });
                const sample = columnSamples.get(key);
                if (text)
                    sample.texts.push(text);
                sample.hasCheckbox = sample.hasCheckbox || Boolean(cell.querySelector('input[type=checkbox]'));
                sample.hasLink = sample.hasLink || Boolean(cell.querySelector('a,[role=link]'));
            });
            rows.push({
                index: rowIndex,
                cells,
                locator_hints: [
                    {
                        kind: 'playwright',
                        expression: "page.locator('.el-table__body-wrapper tbody tr').nth(" + rowIndex + ")",
                    },
                ],
            });
        }

        const columns = headers.map((header, index) => {
            const sample = columnSamples.get(header.column_id || `index:${index}`) || { texts: [], hasCheckbox: false, hasLink: false };
            return {
                index,
                column_id: header.column_id,
                header: header.header,
                role: columnRole(header.header, header.column_id, sample.texts.slice(0, 5), sample.hasCheckbox, sample.hasLink),
                sample_values: sample.texts.slice(0, 3),
            };
        });

        const explicitTitle = attr(root, 'aria-label', 120) || attr(root, 'title', 120);
        const nearbyTitle = nearestTableTitle(root);
        const title = explicitTitle || nearbyTitle.title;
        return {
            kind: 'table_view',
            framework_hint: 'element-table',
            title,
            title_source: explicitTitle ? 'root_attribute' : nearbyTitle.source,
            nearby_headings: nearbyTitle.title ? [nearbyTitle.title] : [],
            row_count_observed: bodyRows.length,
            columns,
            rows,
            auxiliary_text: [],
        };
    }

    const tableViewAdapters = [
        {
            name: 'jalor-igrid',
            rootSelector: '.jalor-igrid',
            collect: collectJalorGridTableView,
        },
        {
            name: 'element-table',
            rootSelector: '.el-table',
            collect: collectElementTableView,
        },
    ];
"""


MODAL_VIEW_ADAPTERS_JS = r"""    const modalViewAdapters = [
        {
            name: 'semantic',
            rootSelector: '[role="dialog"],[aria-modal="true"]',
            titleSelector: 'header,h1,h2,h3,[role=heading]',
            frameworkHint: '',
        },
        {
            name: 'element',
            rootSelector: '.el-overlay-dialog',
            titleSelector: '.el-dialog__title,header,h1,h2,h3,[role=heading]',
            frameworkHint: 'element',
        },
        {
            name: 'ant',
            rootSelector: '.ant-modal',
            titleSelector: '.ant-modal-title,header,h1,h2,h3,[role=heading]',
            frameworkHint: 'ant',
        },
        {
            name: 'vant',
            rootSelector: '.v-modal',
            titleSelector: 'header,h1,h2,h3,[role=heading]',
            frameworkHint: 'vant',
        },
        {
            name: 'class-modal',
            rootSelector: '.modal',
            titleSelector: 'header,h1,h2,h3,[role=heading]',
            frameworkHint: 'class-modal',
        },
    ];
"""
