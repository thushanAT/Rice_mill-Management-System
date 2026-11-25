"use strict";

let paymentCustomers = [];
let paymentCustomerMatches = [];
let paymentInvoices = [];
let outstandingInvoices = [];
let completedOutstandingInvoices = [];
let selectedPaymentCustomer = null;
let selectedPaymentInvoices = [];
let latestReceipt = null;
let outstandingPage = 1;
let outstandingPages = 1;
let outstandingTotal = 0;
let completedOutstandingPage = 1;
let completedOutstandingPages = 1;
let completedOutstandingTotal = 0;
let outstandingQuery = "";
let outstandingRequestVersion = 0;
let completedOutstandingRequestVersion = 0;
let customerSearchTimer = null;
let customerLookupVersion = 0;
let pendingPaymentOperation = null;
let paymentSubmitting = false;

function formatMoney(value) {
    const amount = Number(value || 0);
    return "Rs. " + amount.toLocaleString("en-LK", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, character => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
    })[character]);
}

function todayDate() {
    const now = new Date();
    return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

function showPaymentMessage(message, type) {
    const box = document.getElementById("paymentMessage");
    box.textContent = message;
    box.className = "payment-message " + type;
    box.setAttribute("role", type === "error" ? "alert" : "status");
}

function hidePaymentMessage() {
    const box = document.getElementById("paymentMessage");
    box.textContent = "";
    box.className = "payment-message";
}

async function paymentApi(url, options = {}) {
    const response = await window.csrfFetch(url, {
        credentials: "same-origin",
        ...options,
        headers: { "Content-Type": "application/json", ...(options.headers || {}) }
    });
    const data = await response.json().catch (() => ({}));
    if (!response.ok || data.success === false) {
        throw new Error(data.message || "Unable to load payment data.");
    }
    return data;
}

function cents(value) {
    const text = String(value ?? "").trim();
    if (!/^\d+(?:\.\d{1,2})?$/.test(text)) return null;
    const[whole, fraction = ""] = text.split(".");
    const result = Number(whole) * 100 + Number((fraction + "00").slice(0, 2));
    return Number.isSafeInteger(result) ? result : null;
}

function fromCents(amount) {
    return (amount / 100).toFixed(2);
}

function getCustomerName(customer) {
    return customer?.name || [customer?.firstName, customer?.lastName].filter(Boolean).join(" ");
}

function getCustomerId(customer) {
    return customer?.id;
}

function getInvoiceOutstanding(invoice) {
    if (invoice?.due !== undefined) return Math.max(0, Number(invoice.due || 0));
    return Math.max(0, Number(invoice?.amount || 0) - Number(invoice?.paid || 0));
}

function getCustomerOutstanding(customerId) {
    return paymentInvoices.filter(invoice => String(invoice.customerId) === String(customerId))
        .reduce((sum, invoice) => sum + getInvoiceOutstanding(invoice), 0);
}

function getSelectedInvoiceOutstanding() {
    if (document.getElementById("allocationMethod").value !== "manual") {
        return getCustomerOutstanding(getCustomerId(selectedPaymentCustomer));
    }
    return selectedPaymentInvoices.reduce((sum, invoiceId) => {
        const invoice = paymentInvoices.find(item => String(item.id) === String(invoiceId));
        return sum + (invoice ? getInvoiceOutstanding(invoice) : 0);
    }, 0);
}

function availablePaymentCents() {
    const invoices = document.getElementById("allocationMethod").value === "manual"
        ? selectedPaymentInvoices.map(id => paymentInvoices.find(invoice => String(invoice.id) === String(id))).filter(Boolean)
        : paymentInvoices;
    return invoices.reduce((total, invoice) => total + (cents(String(invoice.due)) || 0), 0);
}

function paymentSearchText(customer) {
    return [customer.customerCode, customer.id, customer.name, customer.phone].join(" ").toLowerCase();
}

function renderCustomerSuggestions(term) {
    const box = document.getElementById("paymentCustomerMatches");
    const normalized = term.trim().toLowerCase();
    if (!normalized) {
        box.innerHTML = "";
        box.hidden = true;
        return;
    }
    const rows = paymentCustomerMatches.filter(customer => paymentSearchText(customer).includes(normalized)).slice(0, 12);
    if (!rows.length) {
        box.innerHTML = '<div class="customer-match-empty">No active Registered Customers found.</div>';
        box.hidden = false;
        return;
    }
    box.innerHTML = rows.map(customer => `<button type="button" class="customer-match-option" role="option"
        data-customer-id="${escapeHTML(customer.id)}"><strong>${escapeHTML(customer.customerCode)}</strong>
        <span>${escapeHTML(getCustomerName(customer))}</span><small>${escapeHTML(window.RiceCustomerDisplay.typeLabel(customer))}${customer.phone ? ` · ${escapeHTML(customer.phone)}` : ""}</small></button>`).join("");
    box.hidden = false;
}

async function loadPaymentCustomers(query = "") {
    const version = ++customerLookupVersion;
    const suffix = query ? `?q=${encodeURIComponent(query)}` : "";
    const data = await paymentApi(`/api/payments/customers${suffix}`);
    if (query) {
        if (version !== customerLookupVersion) return paymentCustomerMatches;
        paymentCustomerMatches = data.customers || [];
        renderCustomerSuggestions(query);
        return paymentCustomerMatches;
    }
    paymentCustomers = data.customers || [];
    if (version === customerLookupVersion) paymentCustomerMatches = paymentCustomers;
    renderCustomerOutstanding(document.getElementById("customerOutstandingSearch").value);
    return paymentCustomers;
}

async function choosePaymentCustomer(customerId, preserveQuery = false) {
    const customer = paymentCustomers.find(item => String(item.id) === String(customerId)) ||
        paymentCustomerMatches.find(item => String(item.id) === String(customerId));
    if (!customer) return;
    if (!paymentCustomers.some(item => String(item.id) === String(customer.id))) paymentCustomers.push(customer);
    if (String(selectedPaymentCustomer?.id) !== String(customer.id)) {
        selectedPaymentInvoices = [];
        pendingPaymentOperation = null;
    }
    selectedPaymentCustomer = customer;
    document.getElementById("paymentCustomerSearch").value = `${customer.customerCode} — ${getCustomerName(customer)}`;
    document.getElementById("paymentCustomerId").value = customer.customerCode;
    document.getElementById("paymentCustomerName").value = getCustomerName(customer);
    document.getElementById("paymentCustomerMatches").hidden = true;
    document.getElementById("paymentCustomerInfo").style.display = "block";
    document.getElementById("customerCreditLimit").textContent = formatMoney(customer.creditLimit);
    document.getElementById("customerOutstanding").textContent = formatMoney(customer.outstanding);
    document.getElementById("customerAvailableCredit").textContent = formatMoney(
        Math.max(0, Number(customer.creditLimit || 0) - Number(customer.outstanding || 0))
    );
    if (!preserveQuery) document.getElementById("paymentCustomerSearch").blur();
    await loadCustomerInvoices(customer.id);
}

async function loadCustomerInvoices(customerId) {
    const tbody = document.getElementById("paymentInvoiceList");
    tbody.innerHTML = '<tr><td colspan="6" class="empty-row" role="status">Loading outstanding invoices…</td></tr>';
    try {
        const data = await paymentApi(`/api/payments/customers/${encodeURIComponent(customerId)}/invoices`);
        if (!selectedPaymentCustomer || String(selectedPaymentCustomer.id) !== String(customerId)) return;
        paymentInvoices = (data.invoices || []).map(invoice => ({ ...invoice, id: String(invoice.id), customerId: String(invoice.customerId || customerId) }));
        selectedPaymentInvoices = selectedPaymentInvoices.filter(id => paymentInvoices.some(row => String(row.id) === String(id)));
        const outstanding = Number(data.customerAccountBalance ?? data.invoiceOutstanding ??
            paymentInvoices.reduce((sum, invoice) => sum + getInvoiceOutstanding(invoice), 0));
        selectedPaymentCustomer.invoiceOutstanding = String(data.invoiceOutstanding ?? outstanding);
        selectedPaymentCustomer.outstanding = String(outstanding);
        document.getElementById("customerOutstanding").textContent = formatMoney(outstanding);
        document.getElementById("customerAvailableCredit").textContent = formatMoney(
            Math.max(0, Number(selectedPaymentCustomer.creditLimit || 0) - Number(selectedPaymentCustomer.invoiceOutstanding))
        );
        renderPaymentInvoices();
        await loadPaymentOverview();
    } catch (error) {
        paymentInvoices = [];
        renderPaymentInvoices(error.message || "Unable to load outstanding invoices.");
    }
}

function renderPaymentInvoices(errorMessage = "") {
    const tbody = document.getElementById("paymentInvoiceList");
    const manual = document.getElementById("allocationMethod").value === "manual";
    if (errorMessage) {
        tbody.innerHTML = `<tr><td colspan="6" class="empty-row payment-error" role="alert">${escapeHTML(errorMessage)}</td></tr>`;
        updateSelectedOutstanding();
        return;
    }
    if (!selectedPaymentCustomer) {
        tbody.innerHTML = '<tr><td colspan="6" class="empty-row">Search and select a Registered Customer.</td></tr>';
        updateSelectedOutstanding();
        return;
    }
    if (!paymentInvoices.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="empty-row">This customer has no outstanding invoices.</td></tr>';
        updateSelectedOutstanding();
        return;
    }
    tbody.innerHTML = paymentInvoices.map(invoice => {
        const checked = selectedPaymentInvoices.includes(String(invoice.id)) ? "checked" : "";
        return `<tr><td><input type="checkbox" value="${escapeHTML(invoice.id)}" ${checked} ${manual ? "" : "disabled"}
            aria-label="Select invoice ${escapeHTML(invoice.invoiceNo)} for manual allocation"></td>
            <td>${escapeHTML(invoice.invoiceNo)}</td><td>${escapeHTML(invoice.date)}</td>
            <td>${formatMoney(invoice.amount)}</td><td>${formatMoney(invoice.paid)}</td>
            <td><strong>${formatMoney(invoice.due)}</strong></td></tr>`;
    }).join("");
    updateSelectedOutstanding();
}

function togglePaymentInvoice(invoiceId, checked) {
    const id = String(invoiceId);
    if (checked && !selectedPaymentInvoices.includes(id)) selectedPaymentInvoices.push(id);
    if (!checked) selectedPaymentInvoices = selectedPaymentInvoices.filter(item => item !== id);
    updateSelectedOutstanding();
}

function updateSelectedOutstanding() {
    const manual = document.getElementById("allocationMethod").value === "manual";
    document.getElementById("selectedOutstanding").textContent = formatMoney(getSelectedInvoiceOutstanding());
    document.querySelector("#panel-receive .outstanding-total span:first-child").textContent = manual
        ? "Selected Invoice Outstanding" : "Customer Total Outstanding (FIFO uses oldest invoices first)";
    renderManualAllocations();
}

function renderManualAllocations() {
    const area = document.getElementById("manualAllocationList");
    if (!area) return;
    const invoices = selectedPaymentInvoices.map(id => paymentInvoices.find(invoice => String(invoice.id) === String(id))).filter(Boolean);
    if (!invoices.length) {
        area.innerHTML = '<p class="payment-help">Select invoices above to allocate the payment manually.</p>';
        updateManualAllocationTotal();
        return;
    }
    area.innerHTML = '<div class="allocation-row allocation-heading"><div>Invoice Number</div><div>Outstanding</div><div>Allocate Amount</div><div>Remaining</div></div>';
    invoices.forEach(invoice => {
        const row = document.createElement("div");
        row.className = "allocation-row";
        row.innerHTML = `<div><strong>${escapeHTML(invoice.invoiceNo)}</strong></div>
            <div>${formatMoney(invoice.due)}</div><div><input type="number" class="manual-allocation-input"
                data-invoice-id="${escapeHTML(invoice.id)}" min="0.01" max="${escapeHTML(invoice.due)}"
                step="0.01" inputmode="decimal" aria-label="Amount allocated to ${escapeHTML(invoice.invoiceNo)}"></div>
            <div data-remaining-for="${escapeHTML(invoice.id)}">${formatMoney(invoice.due)}</div>`;
        area.appendChild(row);
    });
    updateManualAllocationTotal();
}

function updateManualAllocationTotal() {
    let total = 0;
    document.querySelectorAll(".manual-allocation-input").forEach(input => {
        const amount = cents(input.value || "0");
        const invoice = paymentInvoices.find(row => String(row.id) === String(input.dataset.invoiceId));
        if (amount !== null) total += amount;
        const due = invoice ? cents(invoice.due) : 0;
        const remaining = Math.max(0, (due || 0) - (amount || 0));
        const output = document.querySelector(`[data-remaining-for="${CSS.escape(input.dataset.invoiceId)}"]`);
        if (output) output.textContent = formatMoney(fromCents(remaining));
    });
    document.getElementById("manualAllocationTotal").textContent = formatMoney(fromCents(total));
    document.getElementById("paymentPreview").textContent = formatMoney(document.getElementById("paymentAmount").value || 0);
}

function renderCustomerOutstanding(search = "") {
    const tbody = document.getElementById("customerOutstandingBody");
    const query = search.trim().toLowerCase();
    const rows = paymentCustomers.filter(customer => Number(customer.outstanding || 0) > 0 &&
        paymentSearchText(customer).includes(query));
    if (!rows.length) {
        tbody.innerHTML = `<tr><td colspan="5" class="empty-row">${paymentCustomers.length ? "No matching customers with outstanding balances." : "No Registered Customer accounts available."}</td></tr>`;
        return;
    }
    tbody.innerHTML = rows.map(customer => {
        const due = Number(customer.outstanding || 0);
        return `<tr><td>${escapeHTML(customer.customerCode)}</td><td>${escapeHTML(window.RiceCustomerDisplay.format(customer))}</td>
            <td>${formatMoney(customer.creditLimit)}</td><td><strong>${formatMoney(due)}</strong></td>
            <td>${formatMoney(Math.max(0, Number(customer.creditLimit || 0) - due))}</td></tr>`;
    }).join("");
}

function renderOutstandingTable() {
    const tbody = document.getElementById("outstandingTableBody");
    if (!outstandingInvoices.length) {
        tbody.innerHTML = '<tr><td colspan="8" class="empty-row">No outstanding invoices found.</td></tr>';
    } else {
        tbody.innerHTML = outstandingInvoices.map(invoice => `<tr><td>${escapeHTML(invoice.invoiceNo)}</td>
            <td>${escapeHTML(window.RiceCustomerDisplay.format(invoice))}</td><td>${escapeHTML(invoice.date)}</td>
            <td>${formatMoney(invoice.amount)}</td><td>${formatMoney(invoice.paid)}</td><td><strong>${formatMoney(invoice.due)}</strong></td>
            <td>${Number(invoice.paid) > 0 ? '<span class="status-badge status-partial">Partial</span>' : '<span class="status-badge status-credit">Unpaid</span>'}</td>
            <td><button type="button" class="payment-btn-small" data-action="receive" data-customer-id="${escapeHTML(invoice.customerId)}" data-invoice-id="${escapeHTML(invoice.id)}">Receive</button></td></tr>`).join("");
    }
    const pagination = document.getElementById("outstandingPagination");
    window.RicePagination.render(pagination, { page: outstandingPage, pages: outstandingPages, total: outstandingTotal, perPage: 5 }, "data-page", "payment-btn-small");
}

async function loadOutstanding(page = 1) {
    const version = ++outstandingRequestVersion;
    outstandingPage = page;
    const tbody = document.getElementById("outstandingTableBody");
    tbody.innerHTML = '<tr><td colspan="8" class="empty-row" role="status">Loading outstanding invoices…</td></tr>';
    try {
        const query = new URLSearchParams({ page: String(page), perPage: "5" });
        if (outstandingQuery) query.set("q", outstandingQuery);
        const data = await paymentApi(`/api/payments/outstanding?${query}`);
        if (version !== outstandingRequestVersion) return;
        outstandingInvoices = data.invoices || [];
        outstandingPages = data.pagination?.pages || 1;
        outstandingTotal = data.pagination?.total || 0;
        renderOutstandingTable();
    } catch (error) {
        if (version !== outstandingRequestVersion) return;
        outstandingInvoices = [];
        tbody.innerHTML = `<tr><td colspan="8" class="empty-row payment-error" role="alert">${escapeHTML(error.message)}</td></tr>`;
        document.getElementById("outstandingPagination").textContent = "";
    }
}

function completedDateTime(value) {
    if (!value) return "—";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? "—" : parsed.toLocaleString("en-LK", { timeZone: "Asia/Colombo" });
}

function renderCompletedOutstandingTable() {
    const tbody = document.getElementById("completedOutstandingBody");
    if (!completedOutstandingInvoices.length) {
        tbody.innerHTML = '<tr><td colspan="8" class="empty-row">No completed outstanding invoices found.</td></tr>';
    } else {
        tbody.innerHTML = completedOutstandingInvoices.map(invoice => `<tr>
            <td>${escapeHTML(invoice.invoiceNo)}</td><td>${escapeHTML(window.RiceCustomerDisplay.format(invoice))}</td>
            <td>${escapeHTML(invoice.date)}</td><td>${formatMoney(invoice.amount)}</td><td>${formatMoney(invoice.paid)}</td>
            <td>${escapeHTML(completedDateTime(invoice.completedAt))}</td><td><span class="status-badge status-credit">Fully Paid</span></td>
            <td><button type="button" class="payment-btn-small" data-action="completed-details" data-invoice-id="${escapeHTML(invoice.id)}">View Details</button></td></tr>`).join("");
    }
    const pagination = document.getElementById("completedOutstandingPagination");
    window.RicePagination.render(pagination, { page: completedOutstandingPage, pages: completedOutstandingPages, total: completedOutstandingTotal, perPage: 5 }, "data-completed-page", "payment-btn-small");
}

async function loadCompletedOutstanding(page = 1) {
    const version = ++completedOutstandingRequestVersion;
    completedOutstandingPage = page;
    const tbody = document.getElementById("completedOutstandingBody");
    tbody.innerHTML = '<tr><td colspan="8" class="empty-row" role="status">Loading completed outstanding invoices…</td></tr>';
    try {
        const query = new URLSearchParams({ page: String(page), perPage: "5" });
        if (outstandingQuery) query.set("q", outstandingQuery);
        const data = await paymentApi(`/api/payments/completed-outstanding?${query}`);
        if (version !== completedOutstandingRequestVersion) return;
        completedOutstandingInvoices = data.invoices || [];
        completedOutstandingPages = data.pagination?.pages || 1;
        completedOutstandingTotal = data.pagination?.total || 0;
        renderCompletedOutstandingTable();
    } catch (error) {
        if (version !== completedOutstandingRequestVersion) return;
        completedOutstandingInvoices = [];
        tbody.innerHTML = `<tr><td colspan="8" class="empty-row payment-error" role="alert">${escapeHTML(error.message)}</td></tr>`;
        document.getElementById("completedOutstandingPagination").textContent = "";
    }
}

async function showCompletedInvoiceDetails(invoiceId) {
    const dialog = document.getElementById("completedInvoiceDialog");
    const details = document.getElementById("completedInvoiceDetails");
    const title = document.getElementById("completedInvoiceTitle");
    dialog.hidden = false;
    details.innerHTML = '<p class="empty-row" role="status">Loading invoice details…</p>';
    try {
        const data = await paymentApi(`/api/payments/invoices/${encodeURIComponent(invoiceId)}`);
        const sale = data.sale;
        title.textContent = `Invoice ${sale.invoiceNo}`;
        details.innerHTML = `<div class="payment-detail-meta">
            <div><span>Customer</span><strong>${escapeHTML(window.RiceCustomerDisplay.format(sale))}</strong></div>
            <div><span>Customer ID</span><strong>${escapeHTML(sale.customerId ?? "—")}</strong></div>
            <div><span>Invoice Date</span><strong>${escapeHTML(completedDateTime(sale.date))}</strong></div>
            <div><span>Original Invoice Number</span><strong>${escapeHTML(sale.invoiceNo)}</strong></div>
        </div><div class="payment-table-wrapper"><table class="payment-table"><thead><tr>
            <th>Product</th><th>Quantity</th><th>Unit Price</th><th>Line Total</th></tr></thead><tbody>
            ${(sale.items || []).map(item => `<tr><td>${escapeHTML(item.product)}${item.variety ? ` (${escapeHTML(item.variety)})` : ""}</td>
                <td>${escapeHTML(item.quantity)} ${escapeHTML(item.unit || "")}</td><td>${formatMoney(item.price)}</td>
                <td>${formatMoney(item.lineTotal)}</td></tr>`).join("") || '<tr><td colspan="4">No invoice items found.</td></tr>'}
        </tbody></table></div><div class="outstanding-total"><span>Original invoice total</span><strong>${formatMoney(sale.amount)}</strong></div>`;
    } catch (error) {
        details.innerHTML = `<p class="payment-error" role="alert">${escapeHTML(error.message)}</p>`;
    }
}
async function loadPaymentOverview() {
    try {
        const data = await paymentApi("/api/payments/overview");
        const summary = data.summary || {};
        document.getElementById("totalOutstanding").textContent = formatMoney(summary.totalOutstanding);
        document.getElementById("todayCollections").textContent = formatMoney(summary.todayCollections);
        document.getElementById("outstandingCustomers").textContent = summary.outstandingCustomers || 0;
        document.getElementById("partialInvoices").textContent = summary.partialInvoices || 0;
    } catch (error) {
        document.getElementById("totalOutstanding").textContent = "Unavailable";
        showPaymentMessage(error.message, "error");
    }
}

async function stableSignature(payload) {
    const source = JSON.stringify(payload);
    if (window.crypto?.subtle && window.TextEncoder) {
        try {
            const digest = await window.crypto.subtle.digest("SHA-256", new TextEncoder().encode(source));
            return Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, "0")).join("");
        } catch (_) { /* Use the deterministic local fallback if SubtleCrypto is unavailable. */}
    }
    let hash = 2166136261;
    for (let index = 0; index < source.length; index += 1) {
        hash ^= source.charCodeAt(index);
        hash = Math.imul(hash, 16777619);
    }
    return `${source.length}-${(hash >>> 0).toString(16)}`;
}

function newIdempotencyKey() {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    const bytes = new Uint8Array(24);
    window.crypto.getRandomValues(bytes);
    return Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
}

async function getPaymentIdempotencyKey(payload) {
    const signature = await stableSignature(payload);
    if (pendingPaymentOperation?.signature === signature) return pendingPaymentOperation.key;
    const storageKey = "riceMill.pendingPaymentKey";
    try {
        const saved = JSON.parse(sessionStorage.getItem(storageKey) || "null");
        if (saved?.signature === signature && typeof saved.key === "string") {
            pendingPaymentOperation = saved;
            return saved.key;
        }
    } catch (_) { /* Ignore unavailable or malformed session storage. */}
    pendingPaymentOperation = { signature, key: newIdempotencyKey() };
    try {
        sessionStorage.setItem(storageKey, JSON.stringify(pendingPaymentOperation));
    } catch (_) { /* In-memory retry remains stable. */}
    return pendingPaymentOperation.key;
}

function clearPendingPaymentKey() {
    pendingPaymentOperation = null;
    try {
        sessionStorage.removeItem("riceMill.pendingPaymentKey");
    } catch (_) { /* Ignore storage restrictions. */}
}

function collectPaymentPayload() {
    if (!selectedPaymentCustomer) throw new Error("Select an active Registered Customer first.");
    const amountText = document.getElementById("paymentAmount").value.trim();
    const amountCents = cents(amountText);
    if (amountCents === null || amountCents <= 0) throw new Error("Enter a payment amount with no more than two decimal places.");
    const method = document.getElementById("paymentMethod").value;
    if (!["Cash", "Bank Transfer"].includes(method)) throw new Error("Choose Cash or Bank Transfer.");
    const bankReference = document.getElementById("bankReference").value.trim();
    if (method === "Bank Transfer" && !bankReference) throw new Error("Enter the bank reference number for Bank Transfer.");
    if (bankReference.length > 100) throw new Error("Bank reference must be 100 characters or fewer.");
    const note = document.getElementById("paymentNote").value.trim();
    if (note.length > 2000) throw new Error("Payment note must be 2000 characters or fewer.");
    const allocationMethod = document.getElementById("allocationMethod").value;
    if (!["oldest", "manual"].includes(allocationMethod)) throw new Error("Choose FIFO or manual invoice allocation.");
    const availableCents = availablePaymentCents();
    if (amountCents > availableCents) throw new Error("Payment cannot exceed the available outstanding balance.");
    let allocations = [];
    if (allocationMethod === "manual") {
        if (!selectedPaymentInvoices.length) throw new Error("Select at least one invoice for manual allocation.");
        let allocatedCents = 0;
        allocations = selectedPaymentInvoices.map(invoiceId => {
            const invoice = paymentInvoices.find(item => String(item.id) === String(invoiceId));
            const input = document.querySelector(`.manual-allocation-input[data-invoice-id="${CSS.escape(String(invoiceId))}"]`);
            if (!invoice || !input) throw new Error("Refresh the invoice list and review manual allocations.");
            const partCents = cents(input.value);
            const dueCents = cents(String(invoice.due));
            if (partCents === null || partCents <= 0 || partCents > dueCents) throw new Error(`Allocation for ${invoice.invoiceNo} must be positive and no greater than its outstanding amount.`);
            allocatedCents += partCents;
            return { invoiceId: Number(invoice.id), amount: fromCents(partCents) };
        });
        if (allocatedCents !== amountCents) throw new Error("Manual invoice allocations must equal the payment amount exactly.");
    }
    const date = document.getElementById("paymentDate").value;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || Number.isNaN(Date.parse(`${date}T00:00:00Z`)) ||
        new Date(`${date}T00:00:00Z`).toISOString().slice(0, 10) !== date) throw new Error("Select a valid payment date.");
    return { customerId: Number(selectedPaymentCustomer.id), amount: fromCents(amountCents), method,
        bankReference: bankReference || null, allocationMethod, allocations, date, note: note || null };
}

function setPaymentSubmitting(value) {
    paymentSubmitting = value;
    const button = document.querySelector("#paymentForm button[type=submit]");
    button.disabled = value;
    button.textContent = value ? "Saving Payment…" : "✔ Confirm Payment";
    document.getElementById("clearPaymentBtn").disabled = value;
}

async function submitPayment(event) {
    event.preventDefault();
    if (paymentSubmitting) return;
    hidePaymentMessage();
    let payload;
    try {
        payload = collectPaymentPayload();
    }
    catch (error) {
        showPaymentMessage(error.message, "error");
        return;
    }
    setPaymentSubmitting(true);
    try {
        const key = await getPaymentIdempotencyKey(payload);
        payload.idempotencyKey = key;
        const result = await paymentApi("/api/payments", { method: "POST",
            headers: { "Idempotency-Key": key }, body: JSON.stringify(payload) });
        latestReceipt = result.payment;
        showPaymentReceipt(latestReceipt);
        clearPendingPaymentKey();
        showPaymentMessage(result.replayed
            ? `Payment already recorded. Receipt ${latestReceipt.receiptNo} was loaded.`
            : `Payment saved. Receipt ${latestReceipt.receiptNo}.`, "success");
        document.getElementById("paymentAmount").value = "";
        document.getElementById("paymentNote").value = "";
        document.getElementById("bankReference").value = "";
        selectedPaymentInvoices = [];
        if (selectedPaymentCustomer) await loadCustomerInvoices(selectedPaymentCustomer.id);
        const refreshResults = await Promise.allSettled([
            loadPaymentCustomers(), loadOutstanding(outstandingPage),
            loadCompletedOutstanding(completedOutstandingPage), loadPaymentOverview()
        ]);
        const failed = refreshResults.find(result => result.status === "rejected");
        if (failed) showPaymentMessage(`Payment saved as ${latestReceipt.receiptNo}; some balances could not refresh. ${failed.reason.message}`, "error");
    } catch (error) {
        showPaymentMessage(`${error.message} Your form values and retry key are preserved.`, "error");
    } finally {
        setPaymentSubmitting(false);
    }
}

function showPaymentReceipt(payment) {
    if (!payment) return;
    const allocations = payment.allocations || [];
    const allocationRows = allocations.map(item => `<div class="receipt-allocation">
        <div class="receipt-line"><strong>Original Invoice Number</strong><strong>${escapeHTML(item.invoiceNo)}</strong></div>
        <div class="receipt-line"><span>Allocated in this payment</span><span>${formatMoney(item.amount)}</span></div>
        <div class="receipt-line"><span>Original invoice amount</span><span>${formatMoney(item.total)}</span></div>
        <div class="receipt-line"><span>Total paid after this payment</span><span>${formatMoney(item.paidAfterPayment)}</span></div>
        <div class="receipt-line"><span>Remaining on invoice</span><span>${formatMoney(item.dueAfterPayment)}</span></div>
        <div class="receipt-line"><span>Settlement status</span><strong>${escapeHTML(item.settlementStatus || "Partial")}</strong></div>
        </div>`).join("");
    const remaining = allocations.reduce((sum, item) => sum + Number(item.dueAfterPayment || 0), 0);
    const time = payment.createdAt ? new Date(payment.createdAt).toLocaleTimeString("en-LK", {
        timeZone : "Asia/Colombo", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false
    }): "—";
    document.getElementById("receiptDetails").innerHTML = `<div class="receipt-line"><span>Receipt Number</span><strong>${escapeHTML(payment.receiptNo)}</strong></div>
        <div class="receipt-line"><span>Payment Date</span><strong>${escapeHTML(payment.date)}</strong></div>
        <div class="receipt-line"><span>Payment Time (Asia/Colombo)</span><strong>${escapeHTML(time)}</strong></div>
        <div class="receipt-line"><span>Customer</span><strong>${escapeHTML(window.RiceCustomerDisplay.format(payment))}</strong></div>
        <div class="receipt-line"><span>Customer ID</span><strong>${escapeHTML(payment.customerId)}</strong></div>
        <div class="receipt-line"><span>Payment Method</span><strong>${escapeHTML(payment.method)}</strong></div>
        ${payment.method === "Bank Transfer" ? `<div class="receipt-line"><span>Bank Reference</span><strong>${escapeHTML(payment.bankReference || "-")}</strong></div>` : ""}
        <p><strong>Invoice Allocation</strong></p>${allocationRows}
        <div class="receipt-line"><strong>Total Paid</strong><strong>${formatMoney(payment.amount)}</strong></div>
        <div class="receipt-line"><strong>Remaining Outstanding Amount</strong><strong>${formatMoney(remaining)}</strong></div>
        <p>Note: ${escapeHTML(payment.note || "-")}</p>`;
    document.getElementById("paymentReceipt").style.display = "block";
}

function printPaymentReceipt() {
    if (latestReceipt) window.print();
}
window.printPaymentReceipt = printPaymentReceipt;

function switchPaymentTab(tabName) {
    document.querySelectorAll(".payment-tab").forEach(button => button.classList.toggle("active", button.dataset.tab === tabName));
    document.querySelectorAll(".payment-panel").forEach(panel => panel.classList.toggle("active", panel.id === "panel-" + tabName));
    if (tabName === "outstanding") {
        loadOutstanding(outstandingPage);
        loadCompletedOutstanding(completedOutstandingPage);
    }
    if (tabName === "customers") renderCustomerOutstanding(document.getElementById("customerOutstandingSearch").value);
}

document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("paymentForm");
    document.getElementById("paymentDate").value = todayDate();
    form.addEventListener("submit", submitPayment);
    document.getElementById("paymentCustomerSearch").addEventListener("input", event => {
        const term = event.target.value.trim();
        if (selectedPaymentCustomer && term !== `${selectedPaymentCustomer.customerCode} — ${getCustomerName(selectedPaymentCustomer)}`) {
            selectedPaymentCustomer = null;
            paymentInvoices = [];
            selectedPaymentInvoices = [];
            document.getElementById("paymentCustomerInfo").style.display = "none";
            document.getElementById("paymentCustomerId").value = "";
            document.getElementById("paymentCustomerName").value = "";
            renderPaymentInvoices();
        }
        window.clearTimeout(customerSearchTimer);
        customerSearchTimer = window.setTimeout(async() => {
            try {
                await loadPaymentCustomers(term);
            }
            catch (error) {
                showPaymentMessage(error.message, "error");
            }
        }, 180);
    });
    document.getElementById("paymentCustomerSearch").addEventListener("keydown", event => {
        if (event.key === "Enter" && document.getElementById("paymentCustomerMatches").querySelector("[data-customer-id]")) {
            event.preventDefault();
            choosePaymentCustomer(document.getElementById("paymentCustomerMatches").querySelector("[data-customer-id]").dataset.customerId);
        }
    });
    document.getElementById("paymentCustomerMatches").addEventListener("click", event => {
        const option = event.target.closest("[data-customer-id]");
        if (option) choosePaymentCustomer(option.dataset.customerId);
    });
    document.getElementById("paymentInvoiceList").addEventListener("change", event => {
        if (event.target.matches('input[type="checkbox"]')) togglePaymentInvoice(event.target.value, event.target.checked);
    });
    document.getElementById("paymentMethod").addEventListener("change", event => {
        const bank = event.target.value === "Bank Transfer";
        document.getElementById("bankReferenceField").style.display = bank ? "flex" : "none";
        document.getElementById("bankReference").required = bank;
        if (!bank) document.getElementById("bankReference").value = "";
        pendingPaymentOperation = null;
    });
    document.getElementById("paymentAmount").addEventListener("input", () => {
        document.getElementById("paymentPreview").textContent = formatMoney(document.getElementById("paymentAmount").value);
        pendingPaymentOperation = null;
        updateManualAllocationTotal();
    });
    document.getElementById("paymentDate").addEventListener("change", () => {
        pendingPaymentOperation = null;
    });
    document.getElementById("bankReference").addEventListener("input", () => {
        pendingPaymentOperation = null;
    });
    document.getElementById("paymentNote").addEventListener("input", () => {
        pendingPaymentOperation = null;
    });
    document.getElementById("allocationMethod").addEventListener("change", event => {
        const manual = event.target.value === "manual";
        document.getElementById("manualAllocationArea").style.display = manual ? "block" : "none";
        pendingPaymentOperation = null;
        renderPaymentInvoices();
    });
    document.getElementById("manualAllocationList").addEventListener("input", event => {
        if (event.target.matches(".manual-allocation-input")) {
            pendingPaymentOperation = null;
            updateManualAllocationTotal();
        }
    });
    document.getElementById("clearPaymentBtn").addEventListener("click", () => {
        form.reset();
        document.getElementById("paymentDate").value = todayDate();
        document.getElementById("bankReferenceField").style.display = "none";
        document.getElementById("bankReference").required = false;
        document.getElementById("manualAllocationArea").style.display = "none";
        document.getElementById("paymentReceipt").style.display = "none";
        document.getElementById("paymentCustomerSearch").value = "";
        document.getElementById("paymentCustomerMatches").hidden = true;
        document.getElementById("paymentCustomerId").value = "";
        document.getElementById("paymentCustomerName").value = "";
        document.getElementById("paymentCustomerInfo").style.display = "none";
        selectedPaymentCustomer = null;
        paymentInvoices = [];
        selectedPaymentInvoices = [];
        latestReceipt = null;
        clearPendingPaymentKey();
        document.getElementById("paymentPreview").textContent = formatMoney(0);
        hidePaymentMessage();
        renderPaymentInvoices();
    });
    document.getElementById("customerOutstandingSearch").addEventListener("input", event => renderCustomerOutstanding(event.target.value));
    const customerOutstandingSearch = document.getElementById("customerOutstandingSearch");
    if (window.RiceAutocomplete) window.RiceAutocomplete.attach(customerOutstandingSearch,
        document.getElementById("customerOutstandingSuggestions"), {
            minChars: 1, getItems: () => paymentCustomers.filter(customer => Number(customer.outstanding || 0) > 0),
            searchText: customer => paymentSearchText(customer),
            label: customer => `${customer.customerCode} · ${getCustomerName(customer)}`,
            key: customer => customer.id,
            onSelect: customer => {
                customerOutstandingSearch.value = customer.customerCode;
                renderCustomerOutstanding(customer.customerCode);
            }
    });
    const outstandingSearchInput = document.getElementById("outstandingSearch");
    if (window.RiceAutocomplete) window.RiceAutocomplete.attach(outstandingSearchInput,
        document.getElementById("outstandingSearchSuggestions"), {
            minChars: 1, getItems: () => outstandingInvoices,
            searchText: invoice => `${invoice.invoiceNo} ${invoice.customerName || ""} ${invoice.customerId || ""}`,
            label: invoice => `${invoice.invoiceNo} · ${window.RiceCustomerDisplay.format(invoice)}`,
            key: invoice => invoice.id,
            onSelect: invoice => {
                outstandingSearchInput.value = invoice.invoiceNo;
                outstandingQuery = invoice.invoiceNo;
                loadOutstanding(1);
                loadCompletedOutstanding(1);
            }
    });
    document.getElementById("outstandingSearch").addEventListener("input", event => {
        outstandingQuery = event.target.value.trim();
        window.clearTimeout(window.outstandingSearchTimer);
        window.outstandingSearchTimer = window.setTimeout(() => {
            loadOutstanding(1);
            loadCompletedOutstanding(1);
        }, 200);
    });
    document.getElementById("outstandingPagination").addEventListener("click", event => {
        const button = event.target.closest("[data-page]");
        if (button && !button.disabled) loadOutstanding(Number(button.dataset.page));
    });
    document.getElementById("completedOutstandingPagination").addEventListener("click", event => {
        const button = event.target.closest("[data-completed-page]");
        if (button && !button.disabled) loadCompletedOutstanding(Number(button.dataset.completedPage));
    });
    document.getElementById("completedOutstandingBody").addEventListener("click", event => {
        const button = event.target.closest('[data-action="completed-details"]');
        if (button) showCompletedInvoiceDetails(button.dataset.invoiceId);
    });
    document.getElementById("completedInvoiceClose").addEventListener("click", () => {
        document.getElementById("completedInvoiceDialog").hidden = true;
    });
    document.getElementById("completedInvoiceDialog").addEventListener("click", event => {
        if (event.target.id === "completedInvoiceDialog") event.currentTarget.hidden = true;
    });
    document.getElementById("outstandingTableBody").addEventListener("click", event => {
        const button = event.target.closest('[data-action="receive"]');
        if (!button) return;
        switchPaymentTab("receive");
        const customer = paymentCustomers.find(row => String(row.id) === String(button.dataset.customerId));
        if (customer) choosePaymentCustomer(customer.id).then(() => {
            const invoice = paymentInvoices.find(row => row.id === String(button.dataset.invoiceId));
            if (invoice && document.getElementById("allocationMethod").value === "manual") {
                selectedPaymentInvoices = [invoice.id];
                renderPaymentInvoices();
            }
            document.getElementById("paymentAmount").focus();
        });
    });
    document.querySelectorAll(".payment-tab").forEach(button => button.addEventListener("click", () => switchPaymentTab(button.dataset.tab)));
    document.getElementById("paymentForm").addEventListener("keydown", event => {
        if (event.key === "Enter" && event.target.tagName !== "TEXTAREA" && event.target.type !== "submit") {
            const fields = Array.from(form.querySelectorAll("input:not([type=hidden]):not([disabled]),select,button[type=submit]"))
                .filter(field => field.offsetParent !== null && !field.readOnly);
            const index = fields.indexOf(event.target);
            if (index !== -1 && index < fields.length - 1) {
                event.preventDefault();
                fields[index + 1].focus();
            }
        }
    });
    renderPaymentInvoices();
    document.getElementById("outstandingTableBody").innerHTML = '<tr><td colspan="8" class="empty-row" role="status">Loading outstanding invoices…</td></tr>';
    document.getElementById("completedOutstandingBody").innerHTML = '<tr><td colspan="8" class="empty-row" role="status">Loading completed bills…</td></tr>';
    document.getElementById("customerOutstandingBody").innerHTML = '<tr><td colspan="5" class="empty-row" role="status">Loading customer balances…</td></tr>';
    Promise.allSettled([loadPaymentCustomers(), loadOutstanding(), loadCompletedOutstanding(), loadPaymentOverview()])
        .then(results => {
            const failure = results.find(result => result.status === "rejected");
            if (failure) showPaymentMessage(failure.reason.message, "error");
        });
});

function openPaymentForCustomer(customerId, invoiceId) {
    switchPaymentTab("receive");
    const customer = paymentCustomers.find(row => String(row.id) === String(customerId));
    if (!customer) {
        showPaymentMessage("Active customer data is unavailable. Refresh the page and try again.", "error");
        return;
    }
    choosePaymentCustomer(customer.id).then(() => {
        if (invoiceId && document.getElementById("allocationMethod").value === "manual") {
            const invoice = paymentInvoices.find(row => String(row.id) === String(invoiceId));
            if (invoice) {
                selectedPaymentInvoices = [String(invoice.id)];
                renderPaymentInvoices();
            }
        }
        document.getElementById("paymentAmount").focus();
    });
}
window.openPaymentForCustomer = openPaymentForCustomer;
