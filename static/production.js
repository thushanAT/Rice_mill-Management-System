"use strict";

document.addEventListener("DOMContentLoaded", () => {
    const $ = id => document.getElementById(id);
    const stockRows = [];
    const keyStoragePrefix = "rice-mill-production-key:";
    const memoryKeys = new Map();
    let records = [];
    let productionHistoryPage = 1;

    function setMessage(id, message, state = "") {
        const element = $(id);
        element.textContent = message || "";
        element.dataset.state = state;
    }

    function number(id) {
        const value = Number($(id).value);
        return Number.isFinite(value) ? value : 0;
    }

    function formatKg(value) {
        return Number(value || 0).toLocaleString(undefined, {
            minimumFractionDigits: 2,
            maximumFractionDigits: 3
        });
    }

    function today() {
        const now = new Date();
        return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
    }

    async function fetchJson(url, options = {}) {
        const response = await window.csrfFetch(url, options);
        let body;
        try {
            body = await response.json();
        }
        catch (_) {
            throw new Error("The server returned an unreadable response.");
        }
        if (!response.ok) throw new Error(body.message || "The production request could not be completed.");
        return body;
    }

    function stockFor(type, variety) {
        return stockRows.find(row => row.type === type && row.variety === variety);
    }

    function fillStockSelect(selectId, type, hintId, riceLabel = false) {
        const select = $(selectId);
        const previous = select.value;
        const available = stockRows.filter(row => row.type === type && Number(row.quantityKg) > 0);
        select.replaceChildren(new Option(selectId === "packagingRiceType" ? "Select Rice Type" : "Select Variety", ""));
        available.forEach(row => {
            const label = riceLabel ? `${row.variety} Rice — ${formatKg(row.quantityKg)} kg available` :
                `${row.variety} — ${formatKg(row.quantityKg)} kg available`;
            select.add(new Option(label, row.variety));
        });
        if (available.some(row => row.variety === previous)) select.value = previous;
        $(hintId).textContent = available.length ?
            `${available.length} item${available.length === 1 ? "" : "s"} with available stock.` :
            "No stock is currently available for this process.";
    }

    function renderStockOptions(data) {
        stockRows.splice(0, stockRows.length, ...(data.stocks || []));
        fillStockSelect("dryingVariety", "wet_paddy", "dryingStockHint");
        fillStockSelect("millingVariety", "dry_paddy", "millingStockHint");
        fillStockSelect("packagingRiceType", "rice", "packagingStockHint", true);
        updateInputLimits();
        updateCalculations();
    }

    function updateInputLimits() {
        [["dryingVariety", "wet_paddy", "wetInput"], ["millingVariety", "dry_paddy", "millingInput"],
            ["packagingRiceType", "rice", "bulkRiceUsed"]].forEach(([selectId, type, inputId]) => {
                const variety = $(selectId).value;
                const stock = stockFor(type, variety);
                $(inputId).max = stock ? stock.quantityKg : "";
                if (stock) $(inputId).title = `${formatKg(stock.quantityKg)} kg available`;
                else $(inputId).removeAttribute("title");
            });
        updateAvailabilityHints();
    }

    function updateAvailabilityHints() {
        [["dryingVariety", "wet_paddy", "dryingStockHint"], ["millingVariety", "dry_paddy", "millingStockHint"],
            ["packagingRiceType", "rice", "packagingStockHint"]].forEach(([selectId, type, hintId]) => {
                const stock = stockFor(type, $(selectId).value);
                $(hintId).textContent = stock ? `Available: ${formatKg(stock.quantityKg)} kg` :
                    "Choose a variety with available stock.";
            });
    }

    function renderSummary(summary = {}) {
        $("totalWetProcessed").textContent = formatKg(summary.wetProcessedKg);
        $("totalDryProduced").textContent = formatKg(summary.dryProducedKg);
        $("totalRiceProduced").textContent = formatKg(summary.riceProducedKg);
        $("totalPackedProduced").textContent = formatKg(summary.packedKg);
        $("summaryRecords").textContent = Number(summary.records || 0).toLocaleString();
        $("summaryDryingLoss").textContent = formatKg(summary.dryingLossKg);
        $("summaryMillingWaste").textContent = formatKg(summary.millingWasteKg);
        $("summaryPackets").textContent = Number(summary.packets || 0).toLocaleString();
    }

    function historyUrl(page = productionHistoryPage) {
        const params = new URLSearchParams();
        params.set("page", String(page));
        params.set("perPage", "5");
        if ($("historyTypeFilter").value) params.set("type", $("historyTypeFilter").value);
        if ($("historyDateFrom").value) params.set("dateFrom", $("historyDateFrom").value);
        if ($("historyDateTo").value) params.set("dateTo", $("historyDateTo").value);
        const query = params.toString();
        return "/api/production" + (query ? `?${query}` : "");
    }

    function addCell(row, value) {
        const cell = document.createElement("td");
        cell.textContent = value == null || value === "" ? "—" : String(value);
        row.appendChild(cell);
    }

    function renderHistory(rows) {
        const body = $("productionHistoryBody");
        body.replaceChildren();
        if (!rows.length) {
            const row = document.createElement("tr");
            const cell = document.createElement("td");
            cell.colSpan = 9;
            cell.className = "production-empty";
            cell.textContent = "No production records match these filters.";
            row.appendChild(cell);
            body.appendChild(row);
            return;
        }
        rows.forEach(record => {
            const row = document.createElement("tr");
            const type = { drying: "Drying", milling: "Milling", packaging: "Packaging" }[record.type] || record.type;
            const product = record.type === "packaging" ? `${record.variety} Rice (${record.packageSize} kg)` :
                record.type === "milling" ? `${record.variety} Rice` : `${record.variety} Paddy`;
            const balance = record.type === "packaging" ? `${formatKg(record.remainingKg)} remaining` : formatKg(record.wasteKg);
            addCell(row, record.date);
            addCell(row, type);
            addCell(row, product);
            addCell(row, `${formatKg(record.inputKg)} kg`);
            addCell(row, `${formatKg(record.outputKg)} kg`);
            addCell(row, record.type === "milling" ? `${formatKg(record.secondaryOutputKg)} kg bran` : "—");
            addCell(row, `${balance} kg`);
            addCell(row, record.operator);
            addCell(row, record.batchNo);
            body.appendChild(row);
        });
    }

    async function refreshHistory(page = productionHistoryPage) {
        productionHistoryPage = page;
        setMessage("productionHistoryStatus", "Loading production history…");
        try {
            const data = await fetchJson(historyUrl(page));
            records = data.records || [];
            renderHistory(records);
            window.RicePagination.render($("productionHistoryPagination"), data.pagination || { page: 1, pages: 1, total: records.length, perPage: 5 }, "data-production-page");
            renderSummary(data.summary);
            setMessage("productionHistoryStatus", `${records.length} record${records.length === 1 ? "" : "s"} shown.`, "success");
        } catch (error) {
            setMessage("productionHistoryStatus", error.message, "error");
            throw error;
        }
    }

    async function refreshOptions() {
        const data = await fetchJson("/api/production/options");
        renderStockOptions(data);
    }

    async function refreshPage() {
        setMessage("productionPageStatus", "Loading stock and production records…");
        const results = await Promise.allSettled([refreshOptions(), refreshHistory()]);
        const failed = results.find(result => result.status === "rejected");
        if (failed) {
            setMessage("productionPageStatus", failed.reason.message, "error");
        } else {
            setMessage("productionPageStatus", "Stock balances and production history are up to date.", "success");
        }
    }

    function setDates() {
        ["dryingDate", "millingDate", "packagingDate"].forEach(id => {
            $(id).value = today();
        });
    }

    function updateCalculations() {
        const wet = number("wetInput");
        const dry = number("dryOutput");
        const dryingLoss = wet - dry;
        $("dryingInputPreview").textContent = `${formatKg(wet)} kg`;
        $("dryingLossPreview").textContent = `${formatKg(dryingLoss)} kg`;
        $("dryingLossPercent").textContent = `${formatKg(wet ? dryingLoss / wet * 100 : 0)}%`;

        const millingInput = number("millingInput");
        const rice = number("riceOutput");
        const bran = number("branOutput");
        const waste = millingInput - rice - bran;
        $("millingWaste").value = formatKg(waste);
        $("millingInputPreview").textContent = `${formatKg(millingInput)} kg`;
        $("millingOutputPreview").textContent = `${formatKg(rice + bran)} kg`;
        $("millingBalancePreview").textContent = `${formatKg(waste)} kg`;

        const size = number("packetSize");
        const bulk = number("bulkRiceUsed");
        const count = size > 0 ? Math.floor((bulk + 1e-9) / size) : 0;
        const packed = size * count;
        $("packetCount").value = count;
        $("packetSizePreview").textContent = `${size || 0} kg`;
        $("packagedWeightPreview").textContent = `${formatKg(packed)} kg`;
        $("packagingBalancePreview").textContent = `${formatKg(bulk - packed)} kg`;
    }

    function stableKey(formId, signature) {
        const storageKey = keyStoragePrefix + formId;
        const memoryKey = `${storageKey}:${signature}`;
        let entries = {};
        try {
            entries = JSON.parse(sessionStorage.getItem(storageKey) || "{}");
        } catch (_) {
            entries = {};
        }
        if (!entries[signature] && !memoryKeys.has(memoryKey)) {
            entries[signature] = window.crypto?.randomUUID ? crypto.randomUUID() :
                `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
            memoryKeys.set(memoryKey, entries[signature]);
            try {
                sessionStorage.setItem(storageKey, JSON.stringify(entries));
            } catch (_) { /* in-memory retry still works */}
        }
        const value = entries[signature] || memoryKeys.get(memoryKey);
        memoryKeys.set(memoryKey, value);
        return { key: value, clear() {
            delete entries[signature];
            memoryKeys.delete (memoryKey);
            try {
                sessionStorage.setItem(storageKey, JSON.stringify(entries));
            } catch (_) { /* ignore unavailable storage */}
        }
        };
    }

    function bindSubmit(formId, kind, buildPayload, amountInput, varietySelect, stockType) {
        const form = $(formId);
        const button = form.querySelector('button[type="submit"]');
        form.addEventListener("submit", async event => {
            event.preventDefault();
            if (form.dataset.submitting === "true") return;
            const variety = $(varietySelect).value;
            const available = stockFor(stockType, variety);
            if (!available) {
                setMessage("productionPageStatus", "Choose a variety with available stock.", "error");
                $(varietySelect).focus();
                return;
            }
            const inputKg = Number($(amountInput).value);
            if (!Number.isFinite(inputKg) || inputKg <= 0 || inputKg > Number(available.quantityKg)) {
                setMessage("productionPageStatus", `Input must be greater than zero and no more than ${formatKg(available.quantityKg)} kg available.`, "error");
                $(amountInput).focus();
                return;
            }
            if (kind === "drying" && number("dryOutput") > inputKg) {
                setMessage("productionPageStatus", "Dry paddy output cannot exceed wet paddy input.", "error");
                $("dryOutput").focus();
                return;
            }
            if (kind === "milling" && number("riceOutput") + number("branOutput") > inputKg) {
                setMessage("productionPageStatus", "Rice and bran output cannot exceed dry paddy input.", "error");
                $("branOutput").focus();
                return;
            }
            if (kind === "packaging" && number("packetCount") < 1) {
                setMessage("productionPageStatus", "The selected bulk amount is not enough for one complete package.", "error");
                $("bulkRiceUsed").focus();
                return;
            }
            const payload = buildPayload();
            const signature = JSON.stringify(payload);
            const key = stableKey(formId, signature);
            payload.idempotencyKey = key.key;
            form.dataset.submitting = "true";
            button.disabled = true;
            const label = button.textContent;
            button.textContent = "Saving…";
            setMessage("productionPageStatus", `Saving ${kind} record…`);
            try {
                await fetchJson(`/api/production/${kind}`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                key.clear();
                setMessage("productionPageStatus", `${kind[0].toUpperCase() + kind.slice(1)} record saved. Refreshing stock and history…`, "success");
                await refreshPage();
                form.reset();
                setDates();
                updateInputLimits();
                updateCalculations();
            } catch (error) {
                setMessage("productionPageStatus", error.message, "error");
            } finally {
                form.dataset.submitting = "false";
                button.disabled = false;
                button.textContent = label;
            }
        });
    }

    function setupKeyboardNavigation(formId) {
        const form = $(formId);
        const fields = [...form.querySelectorAll("input:not([type='hidden']), select, textarea")]
            .filter(field => !field.disabled && !field.readOnly);
        fields.forEach((field, index) => field.addEventListener("keydown", event => {
            if (event.key === "Enter" && field.tagName !== "TEXTAREA") {
                event.preventDefault();
                if (index + 1 < fields.length) fields[index + 1].focus();
                else form.requestSubmit();
            }
        }));
    }

    setDates();
    setupKeyboardNavigation("dryingForm");
    setupKeyboardNavigation("millingForm");
    setupKeyboardNavigation("packagingForm");

    ["wetInput", "dryOutput", "millingInput", "riceOutput", "branOutput", "packetSize", "bulkRiceUsed"]
        .forEach(id => $(id).addEventListener("input", updateCalculations));
    ["dryingVariety", "millingVariety", "packagingRiceType"].forEach(id =>
        $(id).addEventListener("change", updateInputLimits));

    ["dryingForm", "millingForm", "packagingForm"].forEach(id => $(id).addEventListener("reset", () =>
        setTimeout(() => {
            updateInputLimits();
            updateCalculations();
        }, 0)));

    bindSubmit("dryingForm", "drying", () => ({
        variety: $("dryingVariety").value, inputKg: $("wetInput").value,
        outputKg: $("dryOutput").value, date: $("dryingDate").value,
        batchNo: $("dryingBatch").value, operator: $("dryingPerson").value, notes: $("dryingNotes").value
    }), "wetInput", "dryingVariety", "wet_paddy");

    bindSubmit("millingForm", "milling", () => ({
        variety: $("millingVariety").value, inputKg: $("millingInput").value,
        riceOutput: $("riceOutput").value, branOutput: $("branOutput").value,
        date: $("millingDate").value, batchNo: $("millingBatch").value,
        operator: $("millingPerson").value, notes: $("millingNotes").value
    }), "millingInput", "millingVariety", "dry_paddy");

    bindSubmit("packagingForm", "packaging", () => ({
        variety: $("packagingRiceType").value, inputKg: $("bulkRiceUsed").value,
        packageSize: Number($("packetSize").value), packetCount: Number($("packetCount").value),
        date: $("packagingDate").value, batchNo: $("packagingBatch").value,
        operator: $("packagingPerson").value, notes: $("packagingNotes").value
    }), "bulkRiceUsed", "packagingRiceType", "rice");

    ["historyTypeFilter", "historyDateFrom", "historyDateTo"].forEach(id =>
        $(id).addEventListener("change", () => refreshHistory(1).catch (() => {})));
    $("clearHistoryFilters").addEventListener("click", () => {
        $("historyTypeFilter").value = "";
        $("historyDateFrom").value = "";
        $("historyDateTo").value = "";
        refreshHistory(1).catch (() => {});
    });
    $("productionHistoryPagination").addEventListener("click", event => {
        const button = event.target.closest("[data-production-page]");
        if (button && !button.disabled) refreshHistory(Number(button.dataset.productionPage)).catch (() => {});
    });

    updateCalculations();
    refreshPage();
});
