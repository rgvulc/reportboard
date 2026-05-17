// Drag-and-drop wiring (SortableJS) for kanban cards, workspace list, and checklist.
// Each handler updates the DOM optimistically; the POST persists the new order.

(function () {
    function postForm(url, formData) {
        return fetch(url, { method: 'POST', body: formData })
            .then(function (r) {
                if (!r.ok) throw new Error('Request failed: ' + r.status);
                return r;
            })
            .catch(function (err) { console.error(err); });
    }

    function initBoardSortables() {
        document.querySelectorAll('.board-cards').forEach(function (list) {
            if (list._sortableInitialized) return;
            list._sortableInitialized = true;
            new Sortable(list, {
                group: 'board-cards',
                animation: 150,
                draggable: '.board-card',
                onEnd: function (evt) {
                    var reportId = evt.item.dataset.reportId;
                    var destColumn = evt.to.closest('.board-column');
                    if (!destColumn) return;
                    var destBoardId = destColumn.dataset.boardId;
                    var fd = new FormData();
                    fd.append('board_id', destBoardId);
                    fd.append('position', evt.newIndex);
                    postForm('/reports/' + reportId + '/move', fd);
                },
            });
        });
    }

    function initWorkspaceSortable() {
        var list = document.querySelector('.workspace-list');
        if (!list || list._sortableInitialized) return;
        list._sortableInitialized = true;
        new Sortable(list, {
            animation: 150,
            handle: '.workspace-drag-handle',
            draggable: '.workspace-item',
            onEnd: function () {
                var ids = Array.from(list.querySelectorAll('.workspace-item'))
                    .map(function (li) { return li.dataset.id; });
                var fd = new FormData();
                ids.forEach(function (id) { fd.append('workspace_ids', id); });
                postForm('/workspaces/reorder', fd);
            },
        });
    }

    function initSettingsSortables() {
        document.querySelectorAll('.settings-list').forEach(function (list) {
            if (list._sortableInitialized) return;
            list._sortableInitialized = true;
            var fieldName = list.dataset.idField;
            var url = list.dataset.reorderUrl;
            new Sortable(list, {
                animation: 150,
                handle: '.settings-drag-handle',
                draggable: '.settings-item',
                onEnd: function () {
                    var ids = Array.from(list.querySelectorAll('.settings-item'))
                        .map(function (li) { return li.dataset.id; });
                    var fd = new FormData();
                    ids.forEach(function (id) { fd.append(fieldName, id); });
                    postForm(url, fd);
                },
            });
        });
    }

    function initFilter() {
        var panel = document.getElementById('filter-panel');
        if (!panel || panel._filterInitialized) return;
        panel._filterInitialized = true;

        var toggle = document.getElementById('filter-toggle');
        var summary = document.getElementById('filter-summary');
        var wsId = panel.dataset.workspaceId;
        var storageKey = 'filter:ws:' + wsId;

        var checkboxes = Array.from(panel.querySelectorAll('input[data-filter]'));
        var cards = Array.from(document.querySelectorAll('.board-card'));
        var tagSearch = panel.querySelector('.filter-tag-search');

        // Restore saved state. Only keys present in the current UI are honored;
        // anything missing defaults to checked (so new tags appear, not hidden).
        try {
            var raw = localStorage.getItem(storageKey);
            if (raw) {
                var saved = JSON.parse(raw);
                checkboxes.forEach(function (cb) {
                    var section = cb.dataset.filter;
                    if (saved[section] && cb.value in saved[section]) {
                        cb.checked = !!saved[section][cb.value];
                    }
                });
            }
        } catch (e) { /* corrupt or unavailable */ }

        function saveState() {
            var state = { importance: {}, tag: {} };
            checkboxes.forEach(function (cb) {
                state[cb.dataset.filter][cb.value] = cb.checked;
            });
            try { localStorage.setItem(storageKey, JSON.stringify(state)); }
            catch (e) {}
        }

        function activeValues(section) {
            var out = {};
            checkboxes.forEach(function (cb) {
                if (cb.dataset.filter === section && cb.checked) {
                    out[cb.value] = true;
                }
            });
            return out;
        }

        function sectionActiveFiltering(section) {
            return checkboxes.some(function (cb) {
                return cb.dataset.filter === section && !cb.checked;
            });
        }

        function apply() {
            var imp = activeValues('importance');
            var tags = activeValues('tag');

            var shown = 0;
            cards.forEach(function (card) {
                var cardImp = card.dataset.importance || '';
                var cardTagsRaw = card.dataset.tags || '';
                var cardTags = cardTagsRaw ? cardTagsRaw.split(',') : [];

                var impOk = !!imp[cardImp];
                var tagOk = cardTags.length === 0
                    ? !!tags['']
                    : cardTags.some(function (t) { return !!tags[t]; });

                var visible = impOk && tagOk;
                card.classList.toggle('filter-hidden', !visible);
                if (visible) shown += 1;
            });

            if (summary) {
                if (cards.length === shown) {
                    summary.textContent = 'Showing all ' + cards.length + ' reports';
                } else {
                    summary.textContent = 'Showing ' + shown + ' of '
                        + cards.length + ' reports';
                }
            }

            var anyActive = sectionActiveFiltering('importance')
                || sectionActiveFiltering('tag');
            if (toggle) toggle.classList.toggle('has-active-filter', anyActive);
        }

        checkboxes.forEach(function (cb) {
            cb.addEventListener('change', function () {
                saveState();
                apply();
            });
        });

        panel.querySelectorAll('.filter-section').forEach(function (section) {
            var name = section.dataset.filterSection;
            var allBtn = section.querySelector('.filter-all');
            var noneBtn = section.querySelector('.filter-none');
            function setAll(checked) {
                checkboxes.forEach(function (cb) {
                    if (cb.dataset.filter === name) cb.checked = checked;
                });
                saveState();
                apply();
            }
            if (allBtn) allBtn.addEventListener('click', function () { setAll(true); });
            if (noneBtn) noneBtn.addEventListener('click', function () { setAll(false); });
        });

        if (tagSearch) {
            tagSearch.addEventListener('input', function () {
                var q = tagSearch.value.trim().toLowerCase();
                panel.querySelectorAll('[data-filter-section="tag"] .filter-checkbox')
                    .forEach(function (label) {
                        var cb = label.querySelector('input');
                        if (!cb || cb.value === '') return; // keep "No tags" visible
                        var match = !q || cb.value.toLowerCase().indexOf(q) !== -1;
                        label.classList.toggle('filter-hidden', !match);
                    });
            });
        }

        if (toggle) {
            toggle.addEventListener('click', function () {
                var open = !panel.hasAttribute('hidden');
                if (open) {
                    panel.setAttribute('hidden', '');
                    toggle.setAttribute('aria-expanded', 'false');
                } else {
                    panel.removeAttribute('hidden');
                    toggle.setAttribute('aria-expanded', 'true');
                }
            });
        }

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && !panel.hasAttribute('hidden')) {
                panel.setAttribute('hidden', '');
                if (toggle) toggle.setAttribute('aria-expanded', 'false');
            }
        });

        apply();
    }

    function initThemeSwitcher() {
        var select = document.getElementById('theme-select');
        if (!select || select._themeInitialized) return;
        select._themeInitialized = true;

        var current = 'paper';
        try { current = localStorage.getItem('theme') || 'paper'; } catch (e) {}
        select.value = current;

        select.addEventListener('change', function () {
            var v = select.value;
            try {
                if (v === 'default') {
                    document.documentElement.removeAttribute('data-theme');
                } else {
                    document.documentElement.setAttribute('data-theme', v);
                }
                // Persist even 'default' explicitly: an absent key now means
                // "no preference" and falls back to Paper, so choosing the
                // plain theme must be recorded so it sticks.
                localStorage.setItem('theme', v);
            } catch (e) { console.error(e); }
        });
    }

    function initChecklistSortable() {
        var list = document.getElementById('checklist');
        if (!list || list._sortableInitialized) return;
        list._sortableInitialized = true;
        new Sortable(list, {
            animation: 150,
            draggable: '.checklist-item',
            filter: 'input, button',
            preventOnFilter: false,
            onEnd: function () {
                var ids = Array.from(list.querySelectorAll('.checklist-item'))
                    .map(function (li) { return li.dataset.id; });
                var fd = new FormData();
                ids.forEach(function (id) { fd.append('item_ids', id); });
                postForm(list.dataset.reorderUrl, fd);
            },
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        initThemeSwitcher();
        initBoardSortables();
        initWorkspaceSortable();
        initSettingsSortables();
        initChecklistSortable();
        initFilter();
    });

    // After HTMX swaps the checklist fragment, the old <ul id="checklist"> is
    // replaced by a fresh node; re-init Sortable on the new node.
    //
    // When the swap was triggered by the "Add checklist item" form, also focus
    // the new empty add input so the user can keep typing items without
    // reaching for the mouse.
    let _refocusChecklistAdd = false;
    document.body.addEventListener('htmx:configRequest', function (evt) {
        var elt = evt.detail && evt.detail.elt;
        if (elt && elt.classList && elt.classList.contains('checklist-add-form')) {
            _refocusChecklistAdd = true;
        }
    });
    document.body.addEventListener('htmx:afterSwap', function () {
        initChecklistSortable();
        if (_refocusChecklistAdd) {
            _refocusChecklistAdd = false;
            var input = document.querySelector('.checklist-add input[name="text"]');
            if (input) input.focus();
        }
    });
})();
