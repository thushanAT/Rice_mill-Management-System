(function (global) {
    "use strict";

    function attach(input, list, options) {
        if (!input || !list || input.dataset.autocompleteAttached === "true") return { close() {} };
        const settings = Object.assign({ minChars: 2, debounceMs: 200, fetchItems: null,
            getItems: null, onSelect: null, emptyText: "No matching results", errorText: "Suggestions unavailable" }, options || {});
        const computed = global.getComputedStyle(input);
        const wrapper = document.createElement("span");
        wrapper.className = "autocomplete-wrapper";
        wrapper.style.flex = computed.flex;
        wrapper.style.width = "100%";
        wrapper.style.minWidth = computed.minWidth;
        wrapper.style.boxSizing = "border-box";
        input.parentNode.insertBefore(wrapper, input);
        wrapper.append(input, list);
        input.dataset.autocompleteAttached = "true";
        input.setAttribute("aria-controls", list.id || `${input.id}-suggestions`);
        if (!list.id) list.id = `${input.id}-suggestions`;
        list.style.position = "absolute";
        list.style.left = "0";
        list.style.right = "0";
        list.style.top = "calc(100% + 3px)";
        list.style.width = "100%";
        let timer = null;
        let sequence = 0;
        let items = [];
        let active = -1;

        function close() {
            list.hidden = true;
            input.setAttribute("aria-expanded", "false");
            active = -1;
            wrapper.classList.remove("autocomplete-open");
        }
        function render(rows, message) {
            list.replaceChildren();
            items = rows || [];
            active = -1;
            if (!items.length) {
                const status = document.createElement("div");
                status.className = "autocomplete-status";
                status.textContent = message || settings.emptyText;
                list.appendChild(status);
            } else items.forEach((item, index) => {
                const option = document.createElement("button");
                option.type = "button";
                option.role = "option";
                option.className = "autocomplete-option";
                option.id = `${input.id || "autocomplete"}-option-${index}`;
                option.textContent = settings.label ? settings.label(item) : (item.label || item.name || "");
                option.addEventListener("mousedown", event => event.preventDefault());
                option.addEventListener("click", () => select(index));
                list.appendChild(option);
            });
            list.hidden = false;
            input.setAttribute("aria-expanded", "true");
            wrapper.classList.add("autocomplete-open");
        }
        function select(index) {
            const item = items[index];
            if (!item) return;
            if (settings.onSelect) settings.onSelect(item);
            else input.value = settings.label ? settings.label(item) : (item.label || item.name || "");
            close();
        }
        async function search(value) {
            const request = ++sequence;
            try {
                let rows;
                if (settings.fetchItems) rows = await settings.fetchItems(value);
                else rows = (settings.getItems ? settings.getItems() : []).filter(item =>
                    String(settings.searchText ? settings.searchText(item) : (item.label || item.name || ""))
                    .toLocaleLowerCase().includes(value.toLocaleLowerCase()));
                if (request !== sequence || input.value.trim() !== value) return;
                const unique = new Map();
                (rows || []).forEach(item => unique.set(String(settings.key ? settings.key(item) : (item.id ?? settings.label?.(item) ?? item.name)), item));
                render([...unique.values()], settings.emptyText);
            } catch (_error) {
                if (request === sequence) render([], settings.errorText);
            }
        }
        input.addEventListener("input", () => {
            const value = input.value.trim();
            sequence++;
            clearTimeout(timer);
            if (value.length < settings.minChars) {
                close();
                return;
            }
            if (settings.fetchItems) {
                list.replaceChildren();
                const status = document.createElement("div");
                status.className = "autocomplete-status";
                status.textContent = "Loading…";
                list.replaceChildren(status);
                list.hidden = false;
                input.setAttribute("aria-expanded", "true");
                wrapper.classList.add("autocomplete-open");
            }
            timer = setTimeout(() => search(value), settings.debounceMs);
        });
        input.addEventListener("keydown", event => {
            if (list.hidden) return;
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                const options = [...list.querySelectorAll('[role="option"]')];
                if (!options.length) return;
                active = (active + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
                options.forEach((option, index) => {
                    option.setAttribute("aria-selected", String(index === active));
                });
            } else if (event.key === "Enter" && active >= 0) {
                event.preventDefault();
                select(active);
            }
            else if (event.key === "Escape") close();
        });
        const outsideClick = event => {
            if (!wrapper.contains(event.target)) close();
        };
        document.addEventListener("click", outsideClick);
        return { close, destroy() {
            clearTimeout(timer);
            sequence++;
            document.removeEventListener("click", outsideClick);
            input.dataset.autocompleteAttached = "false";
        }
        };
    }
    global.RiceAutocomplete = { attach };
})(window);
