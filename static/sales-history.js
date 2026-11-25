(function () {
    "use strict";

    const rows = document.getElementById("salesHistoryRows");
    const pager = document.getElementById("salesHistoryPager");
    const summary = document.getElementById("salesHistorySummary");
    const filters = document.getElementById("salesHistoryFilters");
    const overlay = document.getElementById("salesDetailOverlay");
    const detailContent = document.getElementById("salesDetailContent");
    const pageSize = 5;
    const localDateTime = value => {
        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime()) ? "—" : parsed.toLocaleString("en-LK", { timeZone: "Asia/Colombo" });
    };
    let currentPage = 1;
    if (!rows || !pager || !filters || !overlay || !detailContent) return;

    const historySearch = document.getElementById("salesHistorySearch");
    if (window.RiceAutocomplete && historySearch) {
        window.RiceAutocomplete.attach(historySearch, document.getElementById("salesHistorySuggestions"), {
            minChars: 2, debounceMs: 250,
            fetchItems: async term => {
                const params = new URLSearchParams({ search: term, page: "1", perPage: "10" });
                const response = await window.csrfFetch(`/api/sales/history?${params}`);
                const data = await response.json().catch (() => ({}));
                if (!response.ok || !data.success) throw new Error(data.message || "Invoice suggestions could not be loaded.");
                return data.sales || [];
            },
            key: sale => `${sale.recordType || "sale"}-${sale.id}`,
            label: sale => `${sale.reference || sale.invoiceNo} | ${window.RiceCustomerDisplay.format(sale)}`,
            searchText: sale => `${sale.reference || sale.invoiceNo} ${sale.invoiceNo || ""} ${sale.customerName || ""} ${sale.transactionType || ""}`,
            onSelect: sale => {
                historySearch.value = sale.reference || sale.invoiceNo;
                filters.requestSubmit();
            }
        });
    }

    function escapeHtml(value) {
        return String(value ?? "").replace(/[&<>"']/g, char => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
        })[char]);
    }
    function money(value) {
        return Number(value || 0).toLocaleString("en-LK", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    function showState(message, retry) {
        rows.innerHTML = `<tr><td colspan="8" class="sales-history-state" role="status">${escapeHtml(message)}</td></tr>`;
        pager.innerHTML = retry ? '<button type="button" data-sales-history-retry>Retry</button>' : "";
        if (summary) summary.textContent = "";
    }
    function invoiceUrl(id) {
        const base = document.querySelector(".sales-page")?.dataset.salesBillUrl || "/sales-bill";
        const url = new URL(base, window.location.href);
        url.searchParams.set("saleId", String(id));
        return url.pathname + url.search;
    }
    function renderHistoryRow(transaction) {
        const isReturn = transaction.recordType === "return";
        const isExchange = isReturn && transaction.transactionType === "Return Exchange";
        const reference = isReturn ? transaction.returnNo : transaction.invoiceNo;
        const action = isReturn
            ? '<button type="button" class="sales-history-action" data-return-details="' + Number(transaction.id) + '">View</button>' +
            (isExchange && transaction.documentNo ? '<a class="sales-history-action reprint" href="' + escapeHtml("/sales-bill?returnExchangeDocument=" + encodeURIComponent(transaction.documentNo)) + '" target="_blank" rel="noopener">Exchange Bill</a>' : '') +
            (transaction.receiptUrl ? '<a class="sales-history-action reprint" href="' + escapeHtml(transaction.receiptUrl) + '" target="_blank" rel="noopener">Receipt</a>' : '')
            : '<button type="button" class="sales-history-action" data-sale-details="' + Number(transaction.id) + '">View</button>' +
            '<a class="sales-history-action reprint" href="' + escapeHtml(invoiceUrl(transaction.id)) + '" target="_blank" rel="noopener">Reprint</a>';
        const exchangeDetails = isExchange
            ? '<small class="sales-history-reference-detail">Exchange document: ' + escapeHtml(transaction.documentNo || "Unavailable") + '</small>'
            : '';
        const exchangeAmount = isExchange
            ? '<small class="sales-history-amount-detail">Replacement Rs. ' + money(transaction.replacementSubtotal) +
            ' · ' + (Number(transaction.refundDue) > 0 ? 'Refund due: Rs. ' + money(transaction.refundDue) :
                Number(transaction.netAmountPayable) > 0 ? 'Net payable: Rs. ' + money(transaction.netAmountPayable) : 'No balance due') + '</small>'
            : '';
        return '<tr class="' + (isReturn ? 'sales-history-return-row' : '') + '">' +
            '<td>' + escapeHtml(localDateTime(transaction.date)) + '</td>' +
            '<td>' + escapeHtml(reference) + exchangeDetails + '</td><td>' + escapeHtml(transaction.invoiceNo || '—') + '</td>' +
            '<td>' + escapeHtml(window.RiceCustomerDisplay.format(transaction)) + '</td>' +
            '<td><span class="' + (isReturn ? 'sales-history-return-tag' : 'sales-history-sale-tag') + '">' + escapeHtml(transaction.transactionType || 'Sale') + '</span></td>' +
            '<td class="number">Rs. ' + money(transaction.amount) + exchangeAmount + '</td><td>' + escapeHtml(transaction.status || 'Completed') + '</td>' +
            '<td><div class="sales-history-actions">' + action + '</div></td></tr>';
    }
    async function loadSalesHistory(page = currentPage) {
        currentPage = page;
        showState("Loading sales history...");
        const params = new URLSearchParams({ page: String(page), perPage: String(pageSize) });
        const values = {
            search: document.getElementById("salesHistorySearch")?.value.trim(),
            dateFrom: document.getElementById("salesHistoryDateFrom")?.value,
            dateTo: document.getElementById("salesHistoryDateTo")?.value,
            paymentType: document.getElementById("salesHistoryPaymentType")?.value,
            transactionType: document.getElementById("salesHistoryTransactionType")?.value
        };
        Object.entries(values).forEach(([key, value]) => {
            if (value) params.set(key, value);
        });
        try {
            const response = await window.csrfFetch(`/api/sales/history?${params.toString()}`);
            const data = await response.json();
            if (!response.ok || !data.success || !Array.isArray(data.sales)) throw new Error(data.message || "Sales history could not be loaded.");
            const totals = data.summary || {};
            summary.innerHTML = `<span>${Number(totals.count || 0)} transactions</span><span>Sales: ${Number(totals.salesCount || 0)}</span><span>Sales amount: Rs. ${money(totals.amount)}</span><span>Return value (separate): Rs. ${money(totals.returnValue)}</span><span>Amount paid: Rs. ${money(totals.paid)}</span><span>Outstanding: Rs. ${money(totals.due)}</span>`;
            if (!data.sales.length) {
                showState("No sales or closed returns found for the selected filters.");
                summary.innerHTML = `<span>0 transactions</span><span>Sales amount: Rs. 0.00</span><span>Return value (separate): Rs. 0.00</span><span>Amount paid: Rs. 0.00</span><span>Outstanding: Rs. 0.00</span>`;
            } else {
                rows.innerHTML = data.sales.map(renderHistoryRow).join("");
            }
            const pagination = data.pagination || {};
            window.RicePagination.render(pager, pagination, "data-sales-history-page", "sales-history-action");
        } catch (error) {
            showState(error.message || "Sales history could not be loaded.", true);
        }
    }
    function dateText(value) {
        return localDateTime(value);
    }
    async function showSaleDetails(id) {
        overlay.hidden = false;
        detailContent.innerHTML = '<p class="sales-history-state" role="status">Loading invoice details...</p>';
        document.getElementById("salesDetailTitle").textContent = "Invoice Details";
        try {
            const response = await window.csrfFetch(`/api/sales/${encodeURIComponent(id)}`);
            const data = await response.json();
            if (!response.ok || !data.success || !data.sale) throw new Error(data.message || "Invoice details could not be loaded.");
            const sale = data.sale;
            document.getElementById("salesDetailTitle").textContent = `Invoice ${sale.invoiceNo}`;
            const items = Array.isArray(sale.items) ? sale.items : [];
            detailContent.innerHTML = `<div class="sales-detail-meta">
                <div><span>Customer</span><strong>${escapeHtml(window.RiceCustomerDisplay.format(sale))}</strong></div>
                ${sale.customerId ? `<div><span>Customer ID</span><strong>${Number(sale.customerId)}</strong></div>` : ""}
                <div><span>Date and Time</span><strong>${escapeHtml(dateText(sale.date))}</strong></div>
                <div><span>Payment Type</span><strong>${escapeHtml(sale.paymentType)}</strong></div>
                <div><span>Payment Method</span><strong>${escapeHtml(sale.paymentMethod || "—")}</strong></div>
                <div><span>Invoice Number</span><strong>${escapeHtml(sale.invoiceNo)}</strong></div>
                <div><span>Outstanding Balance</span><strong>Rs. ${money(sale.due)}</strong></div>
            </div>
            <div class="sales-history-table-wrap"><table class="sales-detail-table"><thead><tr><th>Product</th><th>Category</th><th>Quantity</th><th>Unit Price</th><th>Line Total</th></tr></thead><tbody>
            ${items.map(item => `<tr><td>${escapeHtml(item.product)}${item.variety ? ` (${escapeHtml(item.variety)})` : ""}</td><td>${escapeHtml(item.category)}</td><td>${escapeHtml(item.quantity)} ${escapeHtml(item.unit || "")}</td><td>Rs. ${money(item.price)}</td><td>Rs. ${money(item.lineTotal)}</td></tr>`).join("") || '<tr><td colspan="5">No invoice items found.</td></tr>'}
            </tbody></table></div><div class="sales-detail-totals"><div>Subtotal: Rs. ${money(sale.subtotal)}</div><div>Grand Total: Rs. ${money(sale.amount)}</div><div>Amount Paid: Rs. ${money(sale.paid)}</div><strong>Outstanding Balance: Rs. ${money(sale.due)}</strong></div>`;
        } catch (error) {
            detailContent.innerHTML = `<p class="sales-history-state" role="alert">${escapeHtml(error.message || "Invoice details could not be loaded.")}</p>`;
        }
    }

    async function showReturnDetails(id) {
        overlay.hidden = false;
        detailContent.innerHTML = '<p class="sales-history-state" role="status">Loading completed Return Bill...</p>';
        document.getElementById("salesDetailTitle").textContent = "Return Bill Details";
        try {
            const response = await window.csrfFetch("/api/returns/" + encodeURIComponent(id) + "/history");
            const data = await response.json();
            if (!response.ok || !data.success || !data.return) throw new Error(data.message || "Return Bill details could not be loaded.");
            const ret = data.return;
            if (!Array.isArray(ret.items) || !Array.isArray(ret.settlements)) throw new Error("Closed Return Bill details are incomplete: items or settlements are missing.");
            const isExchange = ret.settlementType === "exchange";
            document.getElementById("salesDetailTitle").textContent = (isExchange ? "Return Exchange " : "Return Bill ") + ret.returnNo;
            const replacementSubtotal = ret.settlements.reduce((total, row) => total + Number(row.replacementSubtotal ?? row.replacementValue ?? 0), 0);
            const approvedReturnValue = Number(ret.returnValue) || 0;
            const netAmountPayable = Math.max(0, replacementSubtotal - approvedReturnValue);
            const refundDue = Math.max(0, approvedReturnValue - replacementSubtotal);
            const returnedRows = ret.items.map(item => '<tr><td>' + escapeHtml(item.product) + (item.variety ? ' (' + escapeHtml(item.variety) + ')' : '') +
                '</td><td>' + escapeHtml(item.quantity) + ' ' + escapeHtml(item.unit) + (item.packageSize ? ' · ' + escapeHtml(item.packageSize) + ' kg/bag' : '') +
                '</td><td>Rs. ' + money(item.originalPrice) + '/kg</td><td>Rs. ' + money(item.returnValue) + '</td></tr>').join("");
            const settlementHtml = ret.settlements.map(settlement => {
                if (!Array.isArray(settlement.replacements)) throw new Error("Settlement replacement details are missing or invalid.");
                const replacementRows = settlement.replacements.map(item => '<tr><td>' + escapeHtml(item.product) +
                    (item.variety ? ' (' + escapeHtml(item.variety) + ')' : '') + '</td><td>' + escapeHtml(item.quantity) + ' ' + escapeHtml(item.unit) +
                    (item.packageSize ? ' · ' + escapeHtml(item.packageSize) + ' kg/bag' : '') + '</td><td>Rs. ' + money(item.pricePerKg) +
                    '/kg</td><td>Rs. ' + money(item.lineTotal) + '</td></tr>').join("");
                return '<section class="sales-return-settlement"><h4>' + (settlement.type === "exchange" ? "Item Exchange" : "Cash Refund") +
                    ' · Returned item #' + Number(settlement.returnItemId) + '</h4><div class="sales-detail-meta">' +
                    '<div><span>Return Value</span><strong>Rs. ' + money(settlement.returnValue) + '</strong></div>' +
                    '<div><span>Replacement Value</span><strong>Rs. ' + money(settlement.replacementValue) + '</strong></div>' +
                    '<div><span>Cash Refund</span><strong>Rs. ' + money(settlement.cashRefund) + '</strong></div>' +
                    '<div><span>Additional Collected</span><strong>Rs. ' + money(settlement.amountCollected) + '</strong></div>' +
                    '<div><span>Difference Refunded</span><strong>Rs. ' + money(settlement.differenceRefund) + '</strong></div>' +
                    (settlement.type === "exchange" ? '<div><span>Net Amount Payable</span><strong>Rs. ' + money(settlement.netAmountPayable) + '</strong></div>' +
                        '<div><span>Refund Due</span><strong>Rs. ' + money(settlement.refundDue) + '</strong></div>' : '') +
                    '<div><span>Settled At</span><strong>' + escapeHtml(dateText(settlement.settledAt)) + '</strong></div></div>' +
                    (replacementRows ? '<h4>Replacement Products</h4><div class="sales-history-table-wrap"><table class="sales-detail-table">' +
                        '<thead><tr><th>Product</th><th>Quantity</th><th>Price</th><th>Total</th></tr></thead><tbody>' + replacementRows +
                        '</tbody></table></div>' : '') + '</section>';
            }).join("");
            const receipt = ret.receiptUrl
                ? '<a class="sales-history-action reprint" href="' + escapeHtml(ret.receiptUrl) + '" target="_blank" rel="noopener">Open / Print Customer Receipt</a>'
                : '<p>Combined receipt is unavailable.</p>';
            detailContent.innerHTML = '<div class="sales-detail-meta"><div><span>Return Bill Reference</span><strong>' + escapeHtml(ret.returnNo) +
                '</strong></div><div><span>Original Invoice</span><strong>' + escapeHtml(ret.originalInvoice || ret.invoiceNo) +
                '</strong></div><div><span>Customer</span><strong>' + escapeHtml(window.RiceCustomerDisplay.format(ret)) +
                '</strong></div><div><span>Return Date</span><strong>' + escapeHtml(dateText(ret.date)) +
                '</strong></div><div><span>Settlement Type</span><strong>' + (isExchange ? "Return Exchange" : "Cash Refund") +
                '</strong></div>' + (isExchange ? '<div><span>Exchange Document</span><strong>' + escapeHtml(ret.documentNo || "Unavailable") +
                    '</strong></div><div><span>Replacement Subtotal</span><strong>Rs. ' + money(replacementSubtotal) +
                    '</strong></div><div><span>Approved Return Value Deducted</span><strong>Rs. ' + money(approvedReturnValue) +
                    '</strong></div><div><span>Net Amount Payable</span><strong>Rs. ' + money(netAmountPayable) +
                    '</strong></div><div><span>Refund Due</span><strong>Rs. ' + money(refundDue) + '</strong></div>' : '') +
                '<div><span>Inventory Status</span><strong>' + escapeHtml(ret.inventoryStatus) +
                '</strong></div><div><span>Final Settlement Status</span><strong>Fully Settled / Closed</strong></div>' +
                '<div><span>Total Return Value</span><strong>Rs. ' + money(ret.returnValue) + '</strong></div></div>' +
                '<h4>Returned Products</h4><div class="sales-history-table-wrap"><table class="sales-detail-table">' +
                '<thead><tr><th>Product</th><th>Quantity</th><th>Original Selling Price</th><th>Return Value</th></tr></thead><tbody>' +
                returnedRows + '</tbody></table></div><h4>Settlement Details</h4>' + settlementHtml +
                '<div class="sales-return-receipt">' + receipt + '</div>';
        } catch (error) {
            detailContent.innerHTML = '<p class="sales-history-state" role="alert">' + escapeHtml(error.message || "Return Bill details could not be loaded.") + '</p>';
        }
    }
    filters.addEventListener("submit", event => {
        event.preventDefault();
        loadSalesHistory(1);
    });
    document.getElementById("salesHistoryTransactionType")?.addEventListener("change", event => {
        if (event.target.value === "return") document.getElementById("salesHistoryPaymentType").value = "";
    });
    document.getElementById("salesHistory").addEventListener("click", event => {
        const details = event.target.closest("[data-sale-details]");
        const returnDetails = event.target.closest("[data-return-details]");
        const page = event.target.closest("[data-sales-history-page]");
        if (details) showSaleDetails(details.dataset.saleDetails);
        else if (returnDetails) showReturnDetails(returnDetails.dataset.returnDetails);
        else if (page && !page.disabled) loadSalesHistory(Number(page.dataset.salesHistoryPage));
        else if (event.target.closest("[data-sales-history-retry]")) loadSalesHistory(currentPage);
    });
    document.getElementById("salesDetailClose").addEventListener("click", () => {
        overlay.hidden = true;
    });
    overlay.addEventListener("click", event => {
        if (event.target === overlay) overlay.hidden = true;
    });
    window.addEventListener("pageshow", () => loadSalesHistory(1));
    window.loadSalesHistory = loadSalesHistory;
})();
