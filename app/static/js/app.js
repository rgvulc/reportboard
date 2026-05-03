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
        initBoardSortables();
        initWorkspaceSortable();
        initSettingsSortables();
        initChecklistSortable();
    });

    // After HTMX swaps the checklist fragment, the old <ul id="checklist"> is
    // replaced by a fresh node; re-init Sortable on the new node.
    document.body.addEventListener('htmx:afterSwap', function () {
        initChecklistSortable();
    });
})();
