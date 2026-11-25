(function (global) {
    "use strict";
    function render(container, data, attribute, buttonClass = "history-page-button") {
        if (!container) return;
        const total = Math.max(0, Number(data?.total || 0));
        const perPage = Number(data?.perPage || 5);
        const pages = Math.max(1, Number(data?.pages || Math.ceil(total / perPage) || 1));
        const page = Math.min(pages, Math.max(1, Number(data?.page || 1)));
        const start = total ? (page - 1) * perPage + 1 : 0;
        const end = total ? Math.min(page * perPage, total) : 0;
        const low = Math.max(1, page - 2), high = Math.min(pages, page + 2);
        const button = (label, target, disabled, current = false) =>
            `<button type="button" class="rice-pagination-button ${buttonClass}" ${attribute}="${target}" ${disabled ? "disabled" : ""} ${current ? 'aria-current="page"' : ""}>${label}</button>`;
        container.classList.add("rice-pagination");
        container.innerHTML = `<span class="history-page-summary rice-pagination-summary">Showing ${start}–${end} of ${total}</span>` +
            `<div class="rice-pagination-controls">` +
            button("First", 1, page === 1) + button("Previous", page - 1, page === 1) +
            (low > 1 ? `<span class="rice-pagination-ellipsis" aria-hidden="true">…</span>` : "") +
            Array.from({ length: high - low + 1 }, (_, index) => low + index)
            .map(number => button(number, number, false, number === page)).join("") +
            (high < pages ? `<span class="rice-pagination-ellipsis" aria-hidden="true">…</span>` : "") +
            button("Next", page + 1, page >= pages) + button("Last", pages, page >= pages) +
            `</div>`;
    }
    global.RicePagination = { render };
})(window);
