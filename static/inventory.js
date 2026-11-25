(() => {
    "use strict";

    const categoryMeta = {
        wet_paddy: { label: "Wet Paddy", filter: "wet" },
        dry_paddy: { label: "Dry Paddy", filter: "dry" },
        rice: { label: "Bulk Rice", filter: "bulk" },
        packed: { label: "Packed Rice", filter: "packed" },
        other: { label: "Other", filter: "other" }
    };
    const movementLimit = 5;
    const idempotencyKeys = new WeakMap();
    let inventoryLoaded = false;

    function byId(id) {
        return document.getElementById(id);
    }

    function formatQuantity(value, decimals = 3) {
        const raw = String(value ?? "0");
        const[wholeRaw, fractionRaw = ""] = raw.split(".");
        const negative = wholeRaw.startsWith("-");
        const digits = (negative ? wholeRaw.slice(1) : wholeRaw).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
        const fraction = fractionRaw.slice(0, decimals).padEnd(decimals, "0");
        return `${negative ? "-" : ""}${digits}${decimals ? `.${fraction}` : ""}`;
    }

    function setFeedback(element, message, state = "") {
        if (!element) return;
        element.textContent = message;
        element.dataset.state = state;
    }

    function setCell(row, text, className = "") {
        const cell = document.createElement("td");
        if (className) cell.className = className;
        cell.textContent = text;
        row.appendChild(cell);
        return cell;
    }

    async function fetchJson(url, options = {}) {
        const response = await window.csrfFetch(url, options);
        let data;
        try {
            data = await response.json();
        }
        catch (_) {
            throw new Error("The server returned an unreadable response.");
        }
        if (!response.ok) throw new Error(data.message || "The inventory request could not be completed.");
        return data;
    }

    function applyInventoryFilters() {
        const search = (byId("inventorySearch")?.value || "").trim().toLocaleLowerCase();
        const active = document.querySelector(".inventory-filter.active")?.dataset.filter || "all";
        const rows = [...document.querySelectorAll("#inventoryTableBody tr[data-category]")];
        let shown = 0;
        rows.forEach(row => {
            const match = (active === "all" || row.dataset.category === active) &&
                row.textContent.toLocaleLowerCase().includes(search);
            row.hidden = !match;
            if (match) shown += 1;
        });
        const noResults = byId("noInventoryResults");
        if (noResults) noResults.style.display = rows.length && shown === 0 ? "block" : "none";
        const empty = byId("inventoryEmptyState");
        if (empty) empty.hidden = !inventoryLoaded || rows.length !== 0;
    }

    function renderStocks(stocks) {
        const body = byId("inventoryTableBody");
        if (!body) return;
        body.replaceChildren();
        (stocks || []).forEach(stock => {
            const meta = categoryMeta[stock.section || stock.type];
            if (!meta) return;
            const row = document.createElement("tr");
            row.dataset.category = meta.filter;

            const productCell = document.createElement("td");
            const product = document.createElement("span");
            product.className = "product-name";
            product.textContent = stock.item || "";
            productCell.appendChild(product);
            const subtitle = document.createElement("span");
            subtitle.className = "product-subtitle";
            subtitle.textContent = stock.variety ? `${stock.variety} · ${meta.label}` : meta.label;
            productCell.appendChild(subtitle);
            row.appendChild(productCell);

            setCell(row, meta.label);
            setCell(row, stock.type === "packed" ? `${stock.packageSize} kg` : "—");

            const quantityCell = setCell(row, "", "stock-quantity");
            if (stock.type === "packed") {
                quantityCell.append(document.createTextNode(formatQuantity(stock.quantityBags)));
                const kgNote = document.createElement("small");
                kgNote.textContent = `${formatQuantity(stock.quantityKg)} kg total`;
                quantityCell.appendChild(kgNote);
                setCell(row, "bags");
            } else {
                quantityCell.textContent = formatQuantity(stock.quantityKg);
                setCell(row, "kg");
            }

            const available = Number(stock.quantityKg || 0) > 0 || Number(stock.quantityBags || 0) > 0;
            const statusCell = document.createElement("td");
            const badge = document.createElement("span");
            badge.className = `inventory-status ${available ? "status-available" : "status-empty"}`;
            badge.textContent = available ? "Available" : "Out of stock";
            statusCell.appendChild(badge);
            row.appendChild(statusCell);
            body.appendChild(row);
        });
        const searchInput = byId("inventorySearch");
        if (searchInput && window.RiceAutocomplete && !searchInput.dataset.autocompleteReady) {
            searchInput.dataset.autocompleteReady = "true";
            window.RiceAutocomplete.attach(searchInput, byId("inventorySearchSuggestions"), {
                minChars: 1,
                getItems: () => [...body.querySelectorAll("tr[data-category]")].map(row => ({
                    key: `${row.dataset.category}:${row.textContent.trim()}`,
                    label: row.textContent.replace(/\\s+/g, " ").trim(),
                    text: row.textContent
                })),
                searchText: item => item.text,
                label: item => item.label,
                key: item => item.key,
                onSelect: item => {
                    searchInput.value = item.label;
                    applyInventoryFilters();
                }
            });
        }
        applyInventoryFilters();
    }

    function renderSummary(totals) {
        const sectionTotals = { ...(totals || {}) };
        const bran = sectionTotals.bran || { quantityKg: 0, quantityBags: 0 };
        const other = sectionTotals.other || { quantityKg: 0, quantityBags: 0 };
        sectionTotals.other = {
            quantityKg: Number(bran.quantityKg || 0) + Number(other.quantityKg || 0),
            quantityBags: Number(bran.quantityBags || 0) + Number(other.quantityBags || 0)
        };
        Object.entries(sectionTotals).forEach(([kind, amounts]) => {
            const total = document.querySelector(`[data-stock-total="${kind}"]`);
            if (total) total.textContent = formatQuantity(amounts.quantityKg);
            const bags = document.querySelector(`[data-stock-bags="${kind}"]`);
            if (bags) bags.textContent = formatQuantity(amounts.quantityBags, 0);
        });
    }

    function setSummaryUnavailable() {
        document.querySelectorAll("[data-stock-total], [data-stock-bags]")
            .forEach(element => {
                element.textContent = "—";
            });
    }

    async function loadInventory() {
        const feedback = byId("inventoryLoadFeedback");
        setFeedback(feedback, "Loading inventory...");
        try {
            const data = await fetchJson("/api/inventory/summary");
            if (!data.success || !data.totals || !Array.isArray(data.stocks)) {
                throw new Error("The inventory summary response was incomplete.");
            }
            renderSummary(data.totals);
            inventoryLoaded = true;
            renderStocks(data.stocks);
            populateAdjustmentReasons(data.adjustmentReasons || []);
            setFeedback(feedback, "");
        } catch (error) {
            inventoryLoaded = false;
            setSummaryUnavailable();
            byId("inventoryEmptyState").hidden = true;
            setFeedback(feedback, error.message, "error");
        }
    }

    function populateAdjustmentReasons(reasons) {
        const select = byId("adjustmentReason");
        if (!select) return;
        const current = select.value;
        select.replaceChildren(new Option("Select a reason", ""));
        reasons.forEach(reason => select.add(new Option(reason, reason)));
        if (reasons.includes(current)) select.value = current;
        updateOtherReason();
    }

    function updateFormCategory(form) {
        const type = form.querySelector('[name="stockType"]');
        const unit = form.querySelector('[name="unit"]');
        const packageField = form.querySelector('[data-package-field]');
        const packageSize = form.querySelector('[name="packageSize"]');
        const packed = type.value === "packed";
        unit.value = packed ? "bags" : "kg";
        unit.disabled = true;
        packageField.hidden = !packed;
        packageSize.required = packed;
        if (!packed) packageSize.value = "";
        const quantity = form.querySelector('[name="quantity"]');
        quantity.step = packed ? "1" : "0.001";
        quantity.min = packed ? "1" : "0.001";
    }

    function updateOtherReason() {
        const select = byId("adjustmentReason");
        const field = byId("otherReasonField");
        const input = byId("otherReason");
        if (!select || !field || !input) return;
        const isOther = select.value === "Other";
        field.hidden = !isOther;
        input.required = isOther;
        if (!isOther) input.value = "";
    }

    function makeIdempotencyKey() {
        if (window.crypto?.randomUUID) return window.crypto.randomUUID();
        const bytes = new Uint8Array(16);
        window.crypto.getRandomValues(bytes);
        return [...bytes].map(value => value.toString(16).padStart(2, "0")).join("");
    }

    function connectStockForm(formId, endpoint, opening) {
        const form = byId(formId);
        if (!form) return;
        const button = form.querySelector('button[type="submit"]');
        const feedback = byId(opening ? "openingStockFeedback" : "stockAdjustmentFeedback");
        updateFormCategory(form);
        form.querySelector('[name="stockType"]').addEventListener("change", () => updateFormCategory(form));
        form.addEventListener("input", () => {
            if (!button.disabled) idempotencyKeys.delete (form);
        });
        form.addEventListener("change", () => {
            if (!button.disabled) idempotencyKeys.delete (form);
        });
        form.addEventListener("submit", async event => {
            event.preventDefault();
            updateFormCategory(form);
            if (opening) {
                const keyFields = ["item", "variety", "stockType", "quantity", "unit"];
                const missing = keyFields.some(name => !String(form.elements[name].value || "").trim());
                if (missing || !form.reportValidity()) {
                    setFeedback(feedback, "Complete the required stock details before saving.", "error");
                    return;
                }
            } else if (!form.reportValidity()) {
                setFeedback(feedback, "Check the required stock details and try again.", "error");
                return;
            }

            const payload = {
                idempotencyKey: idempotencyKeys.get(form) || makeIdempotencyKey(),
                item: form.elements.item.value.trim(),
                variety: form.elements.variety.value.trim(),
                stockType: form.elements.stockType.value,
                quantity: form.elements.quantity.value,
                unit: form.elements.unit.value
            };
            if (payload.stockType === "packed") payload.packageSize = Number(form.elements.packageSize.value);
            if (!opening) {
                payload.direction = form.elements.direction.value;
                payload.reason = form.elements.reason.value;
                if (payload.reason === "Other") payload.otherReason = form.elements.otherReason.value.trim();
            }
            idempotencyKeys.set(form, payload.idempotencyKey);
            button.disabled = true;
            setFeedback(feedback, opening ? "Saving opening stock..." : "Saving stock adjustment...");
            try {
                await fetchJson(endpoint, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                form.reset();
                idempotencyKeys.delete (form);
                updateFormCategory(form);
                if (!opening) updateOtherReason();
                setFeedback(feedback, opening ? "Opening stock saved." : "Stock adjustment saved.", "success");
                await Promise.all([loadInventory(), loadMovements(true)]);
            } catch (error) {
                setFeedback(feedback, error.message, "error");
            } finally {
                button.disabled = false;
            }
        });
    }

    let movementOffset = 0;
    let movementTotal = 0;

    function signedMovement(value) {
        const amount = String(value ?? "0");
        return amount.startsWith("-") ? amount : `+${amount}`;
    }

    function formatMovementDate(value) {
        if (!value) return "—";
        const timestamp = /(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`;
        const parsed = new Date(timestamp);
        if (Number.isNaN(parsed.getTime())) return String(value);
        return new Intl.DateTimeFormat(undefined, {
            dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Colombo"
        }).format(parsed);
    }

    function renderMovements(movements, total) {
        const body = byId("stockMovementRows");
        if (!body) return;
        body.replaceChildren();
        (movements || []).forEach(movement => {
            const row = document.createElement("tr");
            const dateCell = setCell(row, formatMovementDate(movement.date));
            dateCell.title = movement.date || "";
            const reference = movement.referenceType
                ? `${movement.referenceType}${movement.referenceId ? ` #${movement.referenceId}` : ""}` : "—";
            setCell(row, reference);
            const product = movement.variety ? `${movement.item} · ${movement.variety}` : movement.item;
            setCell(row, product);
            const movementCell = setCell(row, movement.movementType || "—");
            const outbound = String(movement.quantityKg).startsWith("-") || String(movement.quantityBags).startsWith("-");
            movementCell.className = outbound ? "movement-out" : "movement-in";
            const quantityCell = document.createElement("td");
            if (Number(movement.quantityBags || 0) !== 0) {
                quantityCell.textContent = `${signedMovement(formatQuantity(movement.quantityBags))} bags`;
                const kg = document.createElement("small");
                kg.textContent = `${signedMovement(formatQuantity(movement.quantityKg))} kg`;
                quantityCell.appendChild(kg);
            } else {
                quantityCell.textContent = `${signedMovement(formatQuantity(movement.quantityKg))} kg`;
            }
            row.appendChild(quantityCell);
            setCell(row, movement.reason || "—");
            setCell(row, movement.user || "—");
            body.appendChild(row);
        });
        movementTotal = total || 0;
        const page = Math.floor(movementOffset / movementLimit) + 1;
        const pages = Math.max(1, Math.ceil(movementTotal / movementLimit));
        window.RicePagination.render(byId("movementPagination"), { page, pages, total: movementTotal, perPage: movementLimit }, "data-movement-page", "inventory-submit");
        byId("movementEmptyState").hidden = movementTotal !== 0;
    }

    async function loadMovements(reset = false) {
        if (reset) movementOffset = 0;
        const feedback = byId("movementLoadFeedback");
        setFeedback(feedback, "Loading stock movements...");
        const params = new URLSearchParams({ limit: String(movementLimit), offset: String(movementOffset) });
        if (byId("movementDateFrom").value) params.set("dateFrom", byId("movementDateFrom").value);
        if (byId("movementDateTo").value) params.set("dateTo", byId("movementDateTo").value);
        try {
            const data = await fetchJson(`/api/inventory/movements?${params.toString()}`);
            renderMovements(data.movements, data.total);
            setFeedback(feedback, "");
        } catch (error) {
            setFeedback(feedback, error.message, "error");
        }
    }

    document.addEventListener("DOMContentLoaded", () => {
        document.querySelectorAll(".inventory-filter").forEach(button => {
            button.addEventListener("click", () => {
                document.querySelectorAll(".inventory-filter").forEach(item => item.classList.remove("active"));
                button.classList.add("active");
                applyInventoryFilters();
            });
        });
        byId("inventorySearch")?.addEventListener("input", applyInventoryFilters);
        connectStockForm("openingStockForm", "/api/inventory/opening-stock", true);
        connectStockForm("stockAdjustmentForm", "/api/inventory/adjustments", false);
        byId("adjustmentReason")?.addEventListener("change", updateOtherReason);
        byId("filterMovements")?.addEventListener("click", () => loadMovements(true));
        byId("movementDateFrom")?.addEventListener("change", () => loadMovements(true));
        byId("movementDateTo")?.addEventListener("change", () => loadMovements(true));
        byId("clearMovementFilters")?.addEventListener("click", () => {
            byId("movementDateFrom").value = "";
            byId("movementDateTo").value = "";
            loadMovements(true);
        });
        byId("movementPagination")?.addEventListener("click", event => {
            const button = event.target.closest("[data-movement-page]");
            if (!button || button.disabled) return;
            movementOffset = (Number(button.dataset.movementPage) - 1) * movementLimit;
            loadMovements();
        });
        loadInventory();
        loadMovements(true);
    });
})();
