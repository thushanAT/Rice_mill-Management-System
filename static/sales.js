// ==========================================
// PRODUCT CATEGORIES
// ==========================================

let salesCatalog = null;
let selectedCustomer = null;
let customerLookupTimer = null;
let customerMatchIndex = -1;
let customerSearchSequence = 0;
let saleSubmitting = false;
let returnExchangeContext = null;
let returnExchangeSubmitting = false;
let returnExchangeRequest = null;
let unresolvedSaleRequest = null;
let loadedInvoiceSales = new Map();
let serverHeldSales = new Map();
let holdSubmitting = false;
let unresolvedHoldRequest = null;
let heldReturnBillDraftId = null;
let unresolvedReturnBillHold = null;


// ==========================================
// ELEMENTS
// ==========================================

const itemsContainer = document.getElementById("salesItems");

const customerType = document.getElementById("customerType");
const customerName = document.getElementById("customerName");
const customerNameWrapper = document.getElementById("customerNameWrapper");
const customerResults = document.getElementById("salesCustomerResults");

const paymentType = document.getElementById("paymentType");
const paymentMethod = document.getElementById("paymentMethod");
const amountReceived = document.getElementById("amountReceived");
const balanceDue = document.getElementById("balanceDue");
const customerBalance = document.getElementById("customerBalance");

const salesPage = document.querySelector(".sales-page");
const confirmSaleButton = document.getElementById("confirmSaleBtn");
const holdSaleButton = document.getElementById("holdSaleBtn");
const clearSaleButton = document.getElementById("clearSaleBtn");
const salesActions = document.querySelector(".sales-actions");
const returnHoldButtonSlot = document.getElementById("returnHoldButtonSlot");
const paymentDetailsCard = document.getElementById("paymentDetailsCard");
const returnExchangeBanner = document.getElementById("returnExchangeBanner");
const exchangePaymentSection = document.getElementById("exchangePaymentSection");
const exchangeRefundSection = document.getElementById("exchangeRefundSection");
const exchangeDifference = document.getElementById("exchangeDifference");
const exchangeModeError = document.getElementById("exchangeModeError");
const exchangeCollectionControls = document.getElementById("exchangeCollectionControls");
const exchangeCollectionMethod = document.getElementById("exchangeCollectionMethod");
const exchangeCustomerBalanceDueWrap = document.getElementById("exchangeCustomerBalanceDueWrap");
const exchangeCustomerBalanceDue = document.getElementById("exchangeCustomerBalanceDue");
const exchangeCashReceivedWrap = document.getElementById("exchangeCashReceivedWrap");
const exchangeCashReceived = document.getElementById("exchangeCashReceived");
const exchangeBalanceChangeWrap = document.getElementById("exchangeBalanceChangeWrap");
const exchangeBalanceChange = document.getElementById("exchangeBalanceChange");
const exchangeCollectionReference = document.getElementById("exchangeCollectionReference");
const exchangeCollectionReferenceWrap = document.getElementById("exchangeCollectionReferenceWrap");
const exchangeRefundControls = document.getElementById("exchangeRefundControls");
const exchangeRefundMethod = document.getElementById("exchangeRefundMethod");
const exchangeRefundDifference = document.getElementById("exchangeRefundDifference");
const exchangeRefundReference = document.getElementById("exchangeRefundReference");
const exchangeRefundReferenceWrap = document.getElementById("exchangeRefundReferenceWrap");

const heldBillsButton = document.getElementById("heldBillsButton");
const heldBillsPanel = document.getElementById("heldBillsPanel");
const heldBillsList = document.getElementById("heldBillsList");
const heldBillsBadge = document.getElementById("heldBillsBadge");
const heldCount = document.getElementById("heldCount");

const heldBillPreview = document.getElementById("heldBillPreview");
const heldBillPreviewContent = document.getElementById("heldBillPreviewContent");

const heldNotification = document.getElementById("heldNotification");

const HELD_SALES_KEY = "riceMillHeldSales";
const BILL_STORAGE_KEY = "riceMillCurrentSalesBill";
const EDIT_STORAGE_KEY = "riceMillSalesEditData";

let previewHeldSaleId = null;
let notificationTimer = null;


// ==========================================
// COMMON FUNCTIONS
// ==========================================

function money(value) {
    return "Rs. " + Number(value || 0).toLocaleString("en-LK", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });
}

async function readApiJson(response, actionLabel = "The request") {
    const raw = await response.text();
    try {
        const data = JSON.parse(raw);
        if (data && typeof data === "object") return data;
    } catch (error) {}
    let detail = "";
    const contentType = response.headers?.get?.("content-type") || "";
    if (/text\/html/i.test(contentType) && typeof DOMParser !== "undefined") {
        const parsed = new DOMParser().parseFromString(raw, "text/html");
        parsed.querySelectorAll("script,style,template,noscript").forEach(node => node.remove());
        detail = (parsed.body?.textContent || parsed.title || "").replace(/\s+/g, " ").trim().slice(0, 320);
    } else if (/^text\//i.test(contentType)) {
        detail = raw.replace(/[<>]/g, " ").replace(/\s+/g, " ").trim().slice(0, 320);
    }
    const message = response.status === 401 ? "Your session expired. Sign in again, then retry."
        : response.status === 403 ? "Your account is not allowed to complete this action."
        : detail ? `${actionLabel} failed (HTTP ${response.status}): ${detail}`
        : response.status === 404 ? `${actionLabel} failed with HTTP 404 at ${response.url||"the requested API path"}. The running server did not return a JSON route response.`
        : `${actionLabel} failed with HTTP ${response.status}${response.statusText?`: ${response.statusText}`:""}.`;
    return { success: false, message };
}

function returnExchangeSettlementUrl(returnId) {
    const routeTemplate = returnExchangeBanner?.dataset.settlementUrl || "";
    const id = Number(returnId);
    if (!Number.isSafeInteger(id) || id < 1 || !routeTemplate.endsWith("/0/settle")) {
        throw new Error("The Return Exchange settlement route is unavailable. Reload the Sales page.");
    }
    return routeTemplate.replace(/\/0\/settle$/, `/${encodeURIComponent(String(id))}/settle`);
}

function requireArrayProperty(owner, property, label, optional = false) {
    if (!owner || typeof owner !== "object") throw new Error(`${label} response must be an object.`);
    const value = owner[property];
    if (optional && (value === undefined || value === null)) return [];
    if (!Array.isArray(value)) throw new Error(`${label} response is invalid: "${property}" must be an array.`);
    return value;
}


function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, function (char) {
        const entities = {
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#39;"
        };

        return entities[char];
    });
}


function getHeldSales() {
    try {
        const saved = JSON.parse(
            localStorage.getItem(HELD_SALES_KEY)
        );

        return Array.isArray(saved) ? saved : [];
    } catch (error) {
        return [];
    }
}


function saveHeldSales(sales) {
    try {
        localStorage.setItem(
            HELD_SALES_KEY,
            JSON.stringify(sales)
        );

        return true;
    } catch (error) {
        alert("Unable to save held sale in this browser.");

        return false;
    }
}


// ==========================================
// HELD BILLS PANEL
// ==========================================

function openHeldBillsPanel() {
    renderHeldSales();

    heldBillsPanel.hidden = false;

    heldBillsButton.setAttribute("aria-expanded", "true");
}


function closeHeldBillsPanel() {
    heldBillsPanel.hidden = true;

    heldBillsButton.setAttribute("aria-expanded", "false");
}


heldBillsButton.addEventListener("click", function (event) {
    event.stopPropagation();

    if (heldBillsPanel.hidden) {
        openHeldBillsPanel();
    } else {
        closeHeldBillsPanel();
    }
});


document.getElementById("closeHeldBillsButton")
    .addEventListener("click", closeHeldBillsPanel);


document.addEventListener("click", function (event) {
    if (
        !heldBillsPanel.contains(event.target) &&
        !heldBillsButton.contains(event.target)
    ) {
        closeHeldBillsPanel();
    }
});


document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;

    if (returnApprovalNotificationDetail?.open) {
        event.preventDefault();
        returnApprovalNotificationDetail.close();
        return;
    }

    closeHeldBillsPanel();
    closeHeldBillPreview();
    hideHeldNotification();

    if (returnBillModal && !returnBillModal.hidden) closeReturnBillModal();
});


// ==========================================
// HELD SALE NOTIFICATION
// ==========================================

function showHeldNotification() {
    heldNotification.hidden = false;

    clearTimeout(notificationTimer);

    notificationTimer = setTimeout(function () {
        heldNotification.hidden = true;
    }, 5000);
}


function hideHeldNotification() {
    heldNotification.hidden = true;

    clearTimeout(notificationTimer);
}


heldNotification.addEventListener("click", function () {
    hideHeldNotification();

    openHeldBillsPanel();
});


// ==========================================
// ADD PRODUCT ROW
// ==========================================

function addItemRow(item = null) {
    const row = document.createElement("tr");

    row.className = "sales-item-row";

    row.innerHTML = `
        <td>
            <select class="item-category">
                <option value="">Select Category</option>
                <option value="bulk">Bulk Rice</option>
                <option value="packed">Packed Rice</option>
                <option value="bran">Rice Bran</option>
                <option value="other">Other Sellable Products</option>
            </select>
        </td>

        <td>
            <select class="item-product" disabled aria-label="Product / Variety">
                <option value="">Select Product / Variety</option>
            </select>
        </td>

        <td>
            <select class="item-package" disabled hidden>
                <option value="">Package Size</option>
                <option value="10">10 kg</option>
                <option value="25">25 kg</option>
                <option value="50">50 kg</option>
            </select>

            <span class="package-not-required">\u2014</span>
        </td>

        <td>
            <input type="number" class="item-quantity"
                   min="0.01" step="0.01" placeholder="0">
            <small class="quantity-unit">kg</small>
        </td>

        <td>
            <input type="number" class="item-price"
                   min="0.01" step="0.01" placeholder="0.00">
            <small class="price-unit">per kg</small>
        </td>

        <td class="item-total">Rs. 0.00</td>

        <td>
            <button type="button" class="remove-item-btn"
                    title="Remove item" aria-label="Remove item"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 7h16M10 11v6m4-6v6M6 7l1 14h10l1-14M9 7V4h6v3"/></svg></button>
        </td>
    `;

    itemsContainer.appendChild(row);

    if (item) {
        row.querySelector(".item-category").value =
            item.category || "";

        updateProductOptions(row, item.product || "", item.variety || "");

        if (item.category === "packed") {
            row.querySelector(".item-package").value =
                item.package || "";
        }

        row.querySelector(".item-quantity").value =
            item.quantity || "";

        if (!row.querySelector(".item-price").readOnly) {
            row.querySelector(".item-price").value = item.price || "";
        }
        refreshItemDetails(row);
    }

    return row;
}


// ==========================================
// CATEGORY / PRODUCT OPTIONS
// ==========================================

function stockRowsFor(category, product, variety = "", packageSize = null, stockType = "") {
    if (!salesCatalog || !category || !product) return [];
    const type = category === "other" ? (stockType || "other") :
        { bulk: "rice", packed: "packed", bran: "bran" }[category];
    return salesCatalog.stocks.filter(stock => {
        if (stock.type !== type || stock.item !== product) return false;
        if ((category !== "other" || type === "bran") && stock.variety !== variety) return false;
        if (category === "packed" && packageSize && Number(stock.packageSize) !== Number(packageSize)) return false;
        return true;
    });
}

function selectedProductDetails(row) {
    const select = row.querySelector(".item-product");
    const option = select.selectedOptions[0];
    return { product: option?.dataset.product || "", variety: option?.dataset.variety || "",
        productId: option?.dataset.productId || "", stockType: option?.dataset.stockType || "" };
}

function availableForRow(row) {
    const category = row.querySelector(".item-category").value;
    const { product, variety, stockType } = selectedProductDetails(row);
    const packageSize = row.querySelector(".item-package").value;
    const stocks = stockRowsFor(category, product, variety, packageSize || null, stockType);
    if (category === "packed") return stocks.reduce((sum, stock) => sum + Number(stock.quantityBags || 0), 0);
    return stocks.reduce((sum, stock) => sum + Number(stock.quantityKg || 0), 0);
}

function populatePackageOptions(row) {
    const select = row.querySelector(".item-package");
    const selected = select.value;
    const configured = (salesCatalog?.packageSizes || [])
        .map(value => Number(String(value).match(/\d+/)?.[0]))
        .filter(size => Number.isInteger(size) && size > 0);
    const stocked = (salesCatalog?.stocks || []).map(stock => Number(stock.packageSize))
        .filter(size => Number.isInteger(size) && size > 0);
    const sizes = [...new Set(configured.length ? configured : stocked.length ? stocked : [10, 25, 50])].sort((a, b) => a - b);
    select.innerHTML = '<option value="">Package Size</option>';
    sizes.forEach(size => {
        const option = document.createElement("option");
        option.value = String(size);
        option.textContent = `${size} kg`;
        select.appendChild(option);
    });
    if (sizes.includes(Number(selected))) select.value = selected;
}

function updateProductOptions(row, selectedProduct = "", selectedVariety = "") {
    const category = row.querySelector(".item-category").value;
    const productSelect = row.querySelector(".item-product");
    const packageSelect = row.querySelector(".item-package");
    const packageNote = row.querySelector(".package-not-required");
    productSelect.innerHTML = '<option value="">Select Product / Variety</option>';
    row.dataset.variety = "";
    productSelect.disabled = !category || !salesCatalog;
    packageSelect.value = "";
    populatePackageOptions(row);
    packageSelect.hidden = category !== "packed";
    packageSelect.disabled = category !== "packed";
    packageNote.hidden = category === "packed";
    row.querySelector(".item-quantity").value = "";
    row.querySelector(".item-price").value = "";
    row.querySelector(".item-quantity").disabled = true;
    row.querySelector(".item-price").readOnly = false;

    if (category && salesCatalog) {
        let choices = [];
        if (category === "bulk" || category === "packed") {
            choices = (salesCatalog.varietyProducts || salesCatalog.varieties.map(name => ({ id: "", name, categories: ["bulk", "packed"] })))
                .filter(entry => entry.categories.includes(category))
                .map(entry => ({ product: `${entry.name} Rice`, variety: entry.name, productId: entry.id,
                    stockType: category === "packed" ? "packed" : "rice" }));
        } else {
            const productRows = salesCatalog.sellableProducts || salesCatalog.products.map(name => ({ id: "", name, categories: ["other"] }));
            const seen = new Set();
            productRows.filter(entry => entry.categories.includes("other")).forEach(entry => {
                const branStock = (salesCatalog.stocks || []).filter(stock => stock.type === "bran" &&
                    stock.item === entry.name && salesCatalog.varieties.includes(stock.variety));
                if (branStock.length) branStock.forEach(stock => {
                    const key = JSON.stringify([entry.id, stock.variety, "bran"]);
                    if (!seen.has(key)) {
                        seen.add(key);
                        choices.push({ product: entry.name, variety: stock.variety,
                            productId: entry.id, stockType: "bran" });
                    }
                });
                const hasOther = (salesCatalog.stocks || []).some(stock => stock.type === "other" && stock.item === entry.name);
                if (hasOther || !branStock.length) choices.push({ product: entry.name, variety: "", productId: entry.id, stockType: "other" });
            });
        }
        if (category === "bran") choices = choices.filter(choice => choice.stockType === "bran");
        choices.forEach(({ product, variety, productId, stockType }) => {
            const option = document.createElement("option");
            option.value = JSON.stringify([product, variety, productId, stockType]);
            option.dataset.product = product;
            option.dataset.variety = variety;
            option.dataset.productId = productId || "";
            option.dataset.stockType = stockType || "";
            const stockChoices = stockRowsFor(category, product, variety, null, stockType);
            const available = category === "packed"
                ? stockChoices.reduce((sum, stock) => sum + Number(stock.quantityBags || 0), 0)
                : stockChoices.reduce((sum, stock) => sum + Number(stock.quantityKg || 0), 0);
            option.disabled = available <= 0;
            const label = variety ? `${product} | Variety: ${variety}` : product;
            option.textContent = `${label} | Available: ${available.toLocaleString("en-LK")} ${category === "packed" ? "bags" : "kg"}`;
            productSelect.appendChild(option);
        });
        productSelect.disabled = false;
    }

    if (selectedProduct) {
        const restored = [...productSelect.options].find(option =>
            option.dataset.product === selectedProduct &&
            (category !== "other" || option.dataset.variety === selectedVariety));
        if (restored) {
            if (restored.disabled) {
                restored.disabled = false;
                restored.textContent += " (currently unavailable)";
            }
            productSelect.value = restored.value;
            row.dataset.variety = restored.dataset.variety || "";
        }
    }
    row.querySelector(".quantity-unit").textContent = category === "packed" ? "bags" : "kg";
    row.querySelector(".price-unit").textContent = "per kg";
    refreshItemDetails(row);
}

function refreshItemDetails(row) {
    if (!salesCatalog) return;
    const category = row.querySelector(".item-category").value;
    const { product, variety } = selectedProductDetails(row);
    const priceKey = category === "bulk" || category === "packed" ? variety : product;
    const priceInput = row.querySelector(".item-price");
    const quantityInput = row.querySelector(".item-quantity");
    const packageSize = row.querySelector(".item-package").value;
    const available = availableForRow(row);
    const hasItem = Boolean(product && (category !== "bran" || variety));
    const packedNeedsSize = category === "packed" && !packageSize;
    quantityInput.disabled = !hasItem || packedNeedsSize || available <= 0;
    quantityInput.max = hasItem && !packedNeedsSize ? String(available) : "";
    quantityInput.step = category === "packed" ? "1" : "0.001";
    const configured = salesCatalog.prices.find(price => price.item === priceKey && price.price !== null);
    const selectionChanged = row.dataset.priceKey !== priceKey;
    if (configured) {
        if (selectionChanged || configured.locked) priceInput.value = Number(configured.price).toFixed(2);
        priceInput.readOnly = !!configured.locked;
        priceInput.title = configured.locked
            ? "Locked by Administration; employees cannot override this price."
            : "Default from Administration; this sale may use an edited transaction price.";
    } else {
        if (selectionChanged) priceInput.value = "";
        priceInput.readOnly = false;
        priceInput.title = "No configured default price. Enter a selling price per kg.";
    }
    row.dataset.priceKey = priceKey || "";
    priceInput.disabled = !hasItem;
    calculateTotals();
}


// ==========================================
// CATEGORY CHANGE
// ==========================================

itemsContainer.addEventListener("change", function (event) {
    const row = event.target.closest(".sales-item-row");
    if (!row) return;
    if (event.target.classList.contains("item-category")) {
        updateProductOptions(row);
        calculateTotals();
    } else if (event.target.classList.contains("item-product")) {
        row.dataset.variety = event.target.selectedOptions[0]?.dataset.variety || "";
        refreshItemDetails(row);
    } else if (event.target.classList.contains("item-package")) {
        refreshItemDetails(row);
    }
});


// ==========================================
// ADD ITEM
// ==========================================

document.getElementById("addItemBtn").addEventListener("click", function () {
    const row = addItemRow();

    row.querySelector(".item-category").focus();
});


// ==========================================
// REMOVE ITEM
// ==========================================

itemsContainer.addEventListener("click", function (event) {
    if (event.target.closest(".remove-item-btn")) {
        const rows = document.querySelectorAll(".sales-item-row");

        if (rows.length === 1) {
            alert("At least one item row is required.");

            return;
        }

        event.target.closest("tr").remove();

        calculateTotals();
    }
});


// ==========================================
// ITEM TOTAL CALCULATION
// ==========================================

function getItemTotal(row) {
    const category = row.querySelector(".item-category").value;

    const quantity =
        Number(row.querySelector(".item-quantity").value) || 0;

    const price =
        Number(row.querySelector(".item-price").value) || 0;

    const packageSize =
        Number(row.querySelector(".item-package").value) || 0;

    const totalKg = category === "packed"
        ? quantity * packageSize
        : quantity;

    return totalKg * price;
}


// ==========================================
// GET TOTAL
// ==========================================

function getGrandTotal() {
    let total = 0;

    document.querySelectorAll(".sales-item-row").forEach(row => {
        total += getItemTotal(row);
    });

    return total;
}


// ==========================================
// CALCULATE TOTALS
// ==========================================

function calculateTotals() {
    document.querySelectorAll(".sales-item-row").forEach(row => {
        const lineTotal = getItemTotal(row);

        row.querySelector(".item-total").textContent =
            money(lineTotal);
    });

    document.getElementById("grandTotal").textContent =
        money(getGrandTotal());

    if (customerType.value === "walkin" && amountReceived.dataset.manual !== "true") {
        amountReceived.value = getGrandTotal().toFixed(2);
    }

    updatePayment();
    updateReturnExchangeSummaryDisplay();
}


itemsContainer.addEventListener("input", function (event) {
    if (event.target.matches(".item-quantity, .item-price")) {
        calculateTotals();
    }
});


itemsContainer.addEventListener("change", function (event) {
    if (event.target.classList.contains("item-package")) {
        refreshItemDetails(event.target.closest(".sales-item-row"));
    }
});


// ==========================================
// CUSTOMER TYPE
// ==========================================

function renderCustomerMatches(customers) {
    customerMatchIndex = -1;
    customerResults.replaceChildren();
    if (!customers.length) {
        const empty = document.createElement("div");
        empty.className = "sales-customer-empty";
        empty.textContent = "No active Registered Customers found.";
        customerResults.appendChild(empty);
    } else {
        customers.forEach(customer => {
            const button = document.createElement("button");
            button.type = "button";
            button.role = "option";
            const name = document.createElement("strong");
            name.textContent = customer.name;
            const detail = document.createElement("small");
            detail.textContent = `${window.RiceCustomerDisplay.typeLabel(customer)} | ${customer.customerCode} | ${customer.phone}`;
            button.append(name, detail);
            button.addEventListener("click", () => selectSalesCustomer(customer));
            customerResults.appendChild(button);
        });
    }
    customerResults.hidden = false;
    customerName.setAttribute("aria-expanded", "true");
}

async function searchSalesCustomers(term) {
    if (term.trim().length < 2) {
        customerResults.hidden = true;
        return;
    }
    const sequence = ++customerSearchSequence;
    customerResults.replaceChildren();
    const loading = document.createElement("div");
    loading.className = "sales-customer-empty";
    loading.textContent = "Loading...";
    customerResults.appendChild(loading);
    customerResults.hidden = false;
    customerName.setAttribute("aria-expanded", "true");
    try {
        const response = await window.csrfFetch(`/api/sales/customers?q=${encodeURIComponent(term.trim())}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.message || "Customer search failed.");
        if (sequence === customerSearchSequence && customerName.value.trim() === term.trim()) renderCustomerMatches(data.customers || []);
    } catch (error) {
        if (sequence === customerSearchSequence) {
            renderCustomerMatches([]);
            customerResults.firstElementChild.textContent = error.message || "Unable to search customers.";
        }
    }
}

function selectSalesCustomer(customer) {
    selectedCustomer = customer;
    customerName.value = customer.name;
    customerResults.hidden = true;
    customerName.setAttribute("aria-expanded", "false");
    document.getElementById("salesCreditLimit").textContent = money(customer.creditLimit);
    document.getElementById("salesOutstanding").textContent = money(customer.outstanding);
}

customerName.addEventListener("input", function () {
    customerSearchSequence++;
    selectedCustomer = null;
    document.getElementById("salesCreditLimit").textContent = "Select a Registered Customer";
    document.getElementById("salesOutstanding").textContent = "Select a Registered Customer";
    clearTimeout(customerLookupTimer);
    customerLookupTimer = setTimeout(() => searchSalesCustomers(customerName.value), 250);
});

customerName.addEventListener("keydown", function (event) {
    const options = [...customerResults.querySelectorAll('[role="option"]')];
    if (customerResults.hidden) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        if (!options.length) return;
        event.preventDefault();
        const delta = event.key === "ArrowDown" ? 1 : -1;
        customerMatchIndex = (customerMatchIndex + delta + options.length) % options.length;
        options.forEach((option, index) => option.setAttribute("aria-selected", String(index === customerMatchIndex)));
    } else if (event.key === "Enter" && customerMatchIndex >= 0) {
        event.preventDefault();
        options[customerMatchIndex].click();
    } else if (event.key === "Escape") {
        customerResults.hidden = true;
        customerName.setAttribute("aria-expanded", "false");
        customerMatchIndex = -1;
    }
});

customerName.addEventListener("focus", function () {
    if (customerName.value.trim().length >= 2) searchSalesCustomers(customerName.value);
});

document.addEventListener("click", function (event) {
    if (!customerNameWrapper.contains(event.target)) {
        customerResults.hidden = true;
        customerName.setAttribute("aria-expanded", "false");
    }
});

function updateCustomerType() {
    if (returnExchangeContext) {
        customerType.value = returnExchangeContext.customerId ? "registered" : "walkin";
        customerType.disabled = true;
        customerName.value = returnExchangeContext.customerName || "Retail Customer";
        customerName.disabled = true;
        customerNameWrapper.hidden = !returnExchangeContext.customerId;
        document.getElementById("customerCreditPanel").hidden = true;
        document.getElementById("paymentTypeWrapper").hidden = true;
        document.getElementById("balanceDueWrapper").hidden = true;
        document.getElementById("customerBalanceWrapper").hidden = true;
        document.getElementById("amountReceivedWrapper").hidden = false;
        document.getElementById("paymentMethodWrapper").hidden = false;
        paymentType.value = "full";
        paymentType.disabled = true;
        amountReceived.readOnly = true;
        document.getElementById("customerNote").textContent = "The original customer is fixed for this Return Exchange.";
        document.getElementById("paymentNote").textContent = "Only an immediate exchange difference can be collected. No customer credit or advance is created.";
        return;
    }

    const isRegistered = customerType.value === "registered";

    customerNameWrapper.hidden = !isRegistered;

    document.getElementById("customerCreditPanel").hidden =
        !isRegistered;

    document.getElementById("paymentTypeWrapper").hidden =
        !isRegistered;

    document.getElementById("amountReceivedWrapper").hidden = false;

    document.getElementById("balanceDueWrapper").hidden = false;

    document.getElementById("paymentMethodWrapper").hidden = false;

    if (!isRegistered) {
        customerName.value = "";
        selectedCustomer = null;
        customerResults.hidden = true;

        paymentType.value = "full";

        paymentType.disabled = true;
        amountReceived.value = getGrandTotal().toFixed(2);
        amountReceived.dataset.manual = "false";
    } else {
        paymentType.disabled = false;
    }

    document.getElementById("customerNote").textContent =

        isRegistered
        ? "Registered Customer selected. Partial payment and credit sale are available."
        : "Retail Customer sales require full payment only.";

    updatePayment();
}


customerType.addEventListener("change", updateCustomerType);


// ==========================================
// PAYMENT DETAILS
// ==========================================

function updatePayment() {
    const total = getGrandTotal();

    const isWalkIn = customerType.value === "walkin";

    const type = paymentType.value;

    if (type === "credit") {
        amountReceived.value = "0.00";

        amountReceived.readOnly = true;
    } else {
        amountReceived.readOnly = false;
    }

    paymentMethod.disabled = type === "credit";

    if (type === "credit") {
        paymentMethod.value = "cash";
    }

    const received = Number(amountReceived.value) || 0;

    const due = Math.max(0, total - received);
    const returned = Math.max(0, received - total);

    balanceDue.value = money(due);
    customerBalance.value = money(returned);

    document.getElementById("approvalWarning").hidden =
        !(type === "partial" || type === "credit") || isWalkIn;

    if (type === "partial") {
        document.getElementById("paymentNote").textContent =
            "The amount received will be recorded, and the remaining balance will be added to customer outstanding.";
    } else if (type === "credit") {
        document.getElementById("paymentNote").textContent =
            "No payment is received now. The full invoice amount will be recorded as customer outstanding.";
    } else {
        document.getElementById("paymentNote").textContent =
            "Any amount received above the invoice total must be returned to the customer.";
    }
}


paymentType.addEventListener("change", function () {
    if (paymentType.value === "full") amountReceived.value = getGrandTotal().toFixed(2);
    updatePayment();
});

paymentMethod.addEventListener("change", function () {
    if (returnExchangeContext) updateReturnExchangeSummaryDisplay();
    else updatePayment();
});

amountReceived.addEventListener("input", function () {
    amountReceived.dataset.manual = "true";
    updatePayment();
});


// ==========================================
// COLLECT CURRENT SALE DATA
// ==========================================

function collectSale() {
    const items = [];

    document.querySelectorAll(".sales-item-row").forEach(row => {
        const category = row.querySelector(".item-category").value;

        if (!category) return;

        items.push({
            category: category,

            product: selectedProductDetails(row).product,
            productId: selectedProductDetails(row).productId,
            stockType: selectedProductDetails(row).stockType,
            variety: selectedProductDetails(row).variety,

            package: row.querySelector(".item-package").value,

            quantity: row.querySelector(".item-quantity").value,

            price: row.querySelector(".item-price").value
        });
    });

    return {
        customerType: customerType.value,

        customerName: customerName.value,

        customerId: customerType.value === "registered" && selectedCustomer
            ? selectedCustomer.id : null,

        paymentType: paymentType.value,

        paymentMethod: paymentMethod.value,

        amountReceived: amountReceived.value,

        items: items
    };
}


// ==========================================
// RESTORE SALE FORM
// ==========================================

function restoreSale(data) {
    customerType.value = data.customerType || "walkin";

    customerName.value = data.customerName || "";

    paymentType.value = data.paymentType || "full";

    paymentMethod.value = data.paymentMethod || "cash";

    customerNameWrapper.hidden =
        customerType.value !== "registered";

    document.getElementById("customerCreditPanel").hidden =
        customerType.value !== "registered";

    paymentType.disabled =
        customerType.value === "walkin";

    itemsContainer.innerHTML = "";

    if (data.items && data.items.length > 0) {
        data.items.forEach(item => addItemRow(item));
    } else {
        addItemRow();
    }

    updateCustomerType();

    selectedCustomer = null;
    if (customerType.value === "registered" && data.customerId) {
        selectedCustomer = { id: Number(data.customerId), name: data.customerName || customerName.value };
        window.csrfFetch(`/api/sales/customers?q=${encodeURIComponent(customerName.value)}`)
            .then(response => response.json())
            .then(result => {
                const match = (result.customers || []).find(customer => Number(customer.id) === Number(data.customerId));
                if (match) selectSalesCustomer(match);
            }).catch (() => {});
    }

    if (paymentType.value === "partial") {
        amountReceived.readOnly = false;

        amountReceived.value = data.amountReceived || "0";
    } else if (paymentType.value === "credit") {
        amountReceived.value = "0";

        amountReceived.readOnly = true;
    }

    calculateTotals();

    return itemsContainer.querySelectorAll(".sales-item-row").length === (data.items || []).length;
}


// ==========================================
// HELD BILL TOTAL
// ==========================================

function calculateHeldSaleTotal(data) {
    if (!data || !Array.isArray(data.items)) return 0;

    return data.items.reduce((sum, item) => {
        const quantity = Number(item.quantity) || 0;

        const price = Number(item.price) || 0;

        const packageSize = Number(item.package) || 0;

        const totalKg = item.category === "packed"
            ? quantity * packageSize
            : quantity;

        return sum + (totalKg * price);
    }, 0);
}


// ==========================================
// RENDER HELD BILLS
// ==========================================

async function renderHeldSales() {
    let loadError = "";
    try {
        const response = await window.csrfFetch("/api/held-sales");
        const result = await readApiJson(response, "Loading Held Bills");
        if (!response.ok || !result.success) throw new Error(result.message || "Unable to load held sales.");
        serverHeldSales = new Map((result.heldSales || []).map(record => [
            `db-${record.id}`, { ...record, id: `db-${record.id}`, source: "database", heldAt: record.createdAt }
        ]));
    } catch (error) {
        loadError = error.message || "Unable to load held sales from the database.";
    }
    const localHeldSales = getHeldSales().map(record => ({
        ...record, id: `local-${record.id}`, source: "localStorage", localId: record.id
    }));
    const heldSales = [...serverHeldSales.values(), ...localHeldSales];

    const count = heldSales.length;

    heldBillsBadge.textContent = count;

    heldBillsBadge.hidden = count === 0;

    heldCount.textContent = count + (count === 1 ? " Held" : " Held");

    if (count === 0) {
        heldBillsList.innerHTML = `
            <div class="held-empty">
                <span class="held-empty-icon" aria-hidden="true">&#128203;</span>
            ${loadError ? escapeHTML(loadError) : "No held bills available."}
            </div>
        `;

        return;
    }

    heldBillsList.innerHTML = "";

    heldSales.forEach(sale => {
        const isReturnExchange = sale.type === "return_exchange" || sale.data?.kind === "return_exchange";
        const isReturnBill = sale.type === "return_bill";
        const total = isReturnExchange ? Number(sale.data.replacementValue || 0) : isReturnBill ? Number(sale.data.returnValue || 0) : calculateHeldSaleTotal(sale.data);
        const customerDisplay = isReturnExchange ? `${sale.data.returnNo} | Invoice ${sale.data.invoiceNo}` : isReturnBill ? `${sale.data.customerName || "Retail Customer"} | Invoice ${sale.data.invoiceNo}` : window.RiceCustomerDisplay.format(sale.data);

        const card = document.createElement("div");

        card.className = "held-bill-card";

        card.innerHTML = `

            <div class="held-bill-card-top">

                <span class="held-bill-reference">
                    ${escapeHTML(sale.reference)}
                </span>

                <span class="held-bill-status">
                    ${isReturnExchange ? "RETURN EXCHANGE" : isReturnBill ? "RETURN BILL DRAFT" : "SALES BILL"}
                </span>

            </div>

            <div class="held-bill-info">

                <div>
                    <span>${isReturnExchange ? "Type | Return Exchange" : isReturnBill ? "Type | Return Bill Draft" : "Type | Sales Bill"}</span>
                    <strong>${escapeHTML(customerDisplay)}</strong>
                </div>

                <div>
                    <span>Held Date & Time</span>
                    <strong>${escapeHTML(sale.heldAt || sale.data.updatedAt || "")}</strong>
                </div>

                <div>
                    <span>${isReturnExchange ? "Replacement Value" : isReturnBill ? "Return Value" : "Invoice Amount"}</span>
                    <strong class="held-bill-amount">${isReturnExchange && sale.data.replacementValue == null ? "Incomplete" : money(total)}</strong>
                </div>

                <div>
                    <span>${isReturnExchange ? "Approved Return Value" : isReturnBill ? "Draft Status" : "Payment Type"}</span>
                    <strong>${escapeHTML(
                        isReturnExchange ? money(sale.data.returnValue) : isReturnBill ? "Held | Not submitted" : (sale.data.paymentType || "Full Payment")
                    )}</strong>
                </div>

            </div>

            <div class="held-bill-actions">

                <button type="button"
                        class="held-view-btn"
                        data-action="view"
                        data-id="${escapeHTML(sale.id)}">
                    Preview
                </button>

                <button type="button"
                        class="held-retrieve-btn"
                        data-action="retrieve"
                        data-id="${escapeHTML(sale.id)}">
                    Retrieve
                </button>

                <button type="button"
                        class="held-delete-btn"
                        data-action="delete"
                        data-id="${escapeHTML(sale.id)}">
                    Delete
                </button>

            </div>

        `;

        heldBillsList.appendChild(card);
    });
}


// ==========================================
// HOLD CURRENT SALE
// ==========================================

async function holdCurrentReturnBill() {
    if (holdSubmitting) return;
    if (!selectedReturnInvoiceData) {
        showReturnSearchMessage("Search for and select the original invoice before holding this Return Bill.");
        return;
    }
    const inputs = [...returnItemsList.querySelectorAll(".return-quantity-input")];
    for (const input of inputs) {
        if (!input.checkValidity()) {
            input.reportValidity();
            return;
        }
    }
    const items = [];
    inputs.forEach(input => {
        const quantity = Number(input.value) || 0;
        if (quantity <= 0) return;
        const item = selectedReturnInvoiceData.items[Number(input.dataset.index)];
        items.push({ saleItemId: item.saleItemId, quantity: String(quantity) });
    });
    const draft = { invoiceNo: selectedReturnInvoiceData.invoiceNumber, items,
        returnValue: Number(String(returnGrandTotal?.textContent || "").replace(/[^0-9.]/g, "")) || 0 };
    const requestData = { draftType: "return_bill", draft,
        ...(heldReturnBillDraftId ? { heldDraftId : heldReturnBillDraftId } : {}) };
    const serialized = JSON.stringify(requestData);
    if (unresolvedReturnBillHold && unresolvedReturnBillHold.serialized !== serialized) {
        showReturnSearchMessage("The previous hold result is unknown. Retry the unchanged draft before editing it.");
        return;
    }
    const pending = unresolvedReturnBillHold || { key: makeIdempotencyKey(), serialized };
    holdSubmitting = true;
    unresolvedReturnBillHold = pending;
    const oldText = holdSaleButton.textContent;
    holdSaleButton.disabled = true;
    holdSaleButton.textContent = "Saving Return Draft...";
    try {
        const response = await window.csrfFetch("/api/held-sales", { method: "POST",
            headers: { "Content-Type": "application/json", "Idempotency-Key": pending.key },
            body: JSON.stringify({ ...requestData, idempotencyKey: pending.key }) });
        const result = await readApiJson(response, "Saving the Return Bill draft");
        if (!response.ok || !result.success) throw new Error(result.message || "The Return Bill draft could not be saved.");
        heldReturnBillDraftId = Number(result.heldSale.id);
        unresolvedReturnBillHold = null;
        await renderHeldSales();
        showReturnSearchMessage(`Saved as ${result.heldSale.reference}. No approval, stock or financial changes have been made.`);
        holdSaleButton.textContent = "Update Held Return Bill";
    } catch (error) {
        unresolvedReturnBillHold = pending;
        showReturnSearchMessage(error.message || "The Return Bill draft could not be saved. Your entries remain available to retry.");
        holdSaleButton.textContent = oldText;
    } finally {
        holdSubmitting = false;
        holdSaleButton.disabled = false;
    }
}

holdSaleButton.addEventListener("click", async function () {
    if (returnBillModal && !returnBillModal.hidden) {
        await holdCurrentReturnBill();
        return;
    }

    if (returnExchangeContext) {
        await holdCurrentReturnExchange();
        return;
    }

    if (holdSubmitting) return;

    const data = collectSale();

    if (!validateSalesItems() || !validatePayment()) return;

    const hasProduct = data.items.some(item =>
        item.category && item.product
    );

    if (!hasProduct) {
        alert("Please select at least one product before holding the sale.");

        return;
    }

    if (data.customerType === "registered" &&
        !data.customerName.trim()) {
            alert("Please enter the Registered Customer name before holding.");

            customerName.focus();

            return;
        }

    const serialized = JSON.stringify(data);
    if (unresolvedHoldRequest && unresolvedHoldRequest.serialized !== serialized) {
        alert("The previous hold outcome is unknown. Retry the unchanged draft first to avoid duplicates.");
        return;
    }
    const pending = unresolvedHoldRequest || { key: makeIdempotencyKey(), draft: data, serialized };
    const button = document.getElementById("holdSaleBtn");
    const oldText = button.textContent;
    holdSubmitting = true;
    unresolvedHoldRequest = pending;
    button.disabled = true;
    button.textContent = "Saving Draft…";
    try {
        const response = await window.csrfFetch("/api/held-sales", {
            method: "POST",
            headers: { "Content-Type": "application/json", "Idempotency-Key": pending.key },
            body: JSON.stringify({ draft: pending.draft })
        });
        const result = await response.json();
        if (!response.ok || !result.success) {
            if (response.status < 500 && !(result.message || "").toLowerCase().includes("idempotency key")) unresolvedHoldRequest = null;
            alert(result.message || "The sale draft could not be held.");
            return;
        }
        unresolvedHoldRequest = null;
        resetSaleForm();
        closeHeldBillPreview();
        await renderHeldSales();
        showHeldNotification();
    } catch (error) {
        unresolvedHoldRequest = pending;
        alert("The server response was not received. Your sale details are preserved. Click Hold Sale again without changing them to safely retry.");
    } finally {
        holdSubmitting = false;
        button.disabled = false;
        button.textContent = oldText;
    }
});


// ==========================================
// FIND HELD SALE
// ==========================================

function findHeldSale(id) {
    const key = String(id);
    if (key.startsWith("db-")) return serverHeldSales.get(key) || null;
    const localId = key.startsWith("local-") ? key.slice(6) : key;
    const sale = getHeldSales().find(item => String(item.id) === localId);
    return sale ? { ...sale, id : `local-${sale.id}`, source: "localStorage", localId: sale.id } : null;
}


// ==========================================
// VIEW HELD BILL
// ==========================================

async function viewHeldBill(id) {
    let sale = findHeldSale(id);

    if (!sale) return;

    if (sale.source === "database") {
        try {
            const response = await window.csrfFetch(`/api/held-sales/${encodeURIComponent(sale.id.slice(3))}`);
            const result = await response.json();
            if (!response.ok || !result.success) throw new Error(result.message || "Held sale could not be loaded.");
            sale = { ...result.heldSale, id: `db-${result.heldSale.id}`, source: "database", heldAt: result.heldSale.createdAt };
        } catch (error) {
            alert(error.message || "Held sale could not be loaded.");
            return;
        }
    }

    previewHeldSaleId = String(id);

    const data = sale.data;

    if (sale.type === "return_bill") {
        const rows = (data.items || []).map(item => `<tr><td>${escapeHTML(item.product||"")}${item.variety?` | ${escapeHTML(item.variety)}`:""}</td><td>${escapeHTML(item.quantity||"")} ${escapeHTML(item.unit||"")}</td><td>${money(item.originalPrice)}</td><td>${money(item.returnValue)}</td></tr>`).join("");
        heldBillPreviewContent.innerHTML = `<div class="held-preview-meta">
            <div><span>Draft Reference</span><strong>${escapeHTML(sale.reference)}</strong></div>
            <div><span>Draft Status</span><strong>Held | Not submitted for approval</strong></div>
            <div><span>Original Invoice</span><strong>${escapeHTML(data.invoiceNo)}</strong></div>
            <div><span>Customer</span><strong>${escapeHTML(data.customerName||"Retail Customer")}</strong></div>
        </div><h3>Returned Products</h3><div class="held-preview-table-wrap"><table class="held-preview-table">
            <thead><tr><th>Product</th><th>Quantity</th><th>Original Price / kg</th><th>Return Value</th></tr></thead>
            <tbody>${rows||'<tr><td colspan="4">No return quantities selected yet.</td></tr>'}</tbody></table></div>
            <div class="held-preview-total"><span>Return Value</span><strong>${money(data.returnValue)}</strong></div>`;
        document.getElementById("retrievePreviewHeldBill").textContent = "Retrieve Return Bill";
        previewHeldSaleId = String(id);
        heldBillPreview.hidden = false;
        return;
    }

    if (sale.type === "return_exchange" || data?.kind === "return_exchange") {
        const returned = data.returnedItem || {};
        const replacements = requireArrayProperty(data, "replacements", "Held exchange draft", true);
        const replacementRows = replacements.map(line => `
            <tr><td>${escapeHTML(line.product || "—")}</td><td>${escapeHTML(line.quantity || "—")} ${escapeHTML(line.unit || "")}</td>
            <td>${escapeHTML(line.packageSize || "—")}</td><td>${escapeHTML(line.price || "—")}</td></tr>`).join("");
        const difference = data.difference == null ? "Incomplete" :
            (Number(data.difference) > 0 ? `Payable ${money(data.difference)}` : Number(data.difference) < 0 ? `Refundable ${money(Math.abs(Number(data.difference)))}` : "No difference");
        heldBillPreviewContent.innerHTML = `<div class="held-preview-meta">
            <div><span>Return Exchange Reference</span><strong>${escapeHTML(sale.reference)}</strong></div>
            <div><span>Return Bill</span><strong>${escapeHTML(data.returnNo)}</strong></div>
            <div><span>Original Invoice</span><strong>${escapeHTML(data.invoiceNo)}</strong></div>
            <div><span>Draft Status</span><strong>Held  |  no stock or money moved</strong></div>
            <div><span>Returned Item</span><strong>${escapeHTML(returned.product)}  |  ${escapeHTML(returned.quantity)} ${escapeHTML(returned.unit)}</strong></div>
            <div><span>Return Value</span><strong>${money(data.returnValue)}</strong></div>
            <div><span>Replacement Value</span><strong>${data.replacementValue == null ? "Incomplete" : money(data.replacementValue)}</strong></div>
            <div><span>Difference</span><strong>${escapeHTML(difference)}</strong></div>
            </div><h3>Replacement Products</h3><div class="held-preview-table-wrap"><table class="held-preview-table">
            <thead><tr><th>Product</th><th>Quantity</th><th>Package</th><th>Saved Price / kg</th></tr></thead>
            <tbody>${replacementRows || '<tr><td colspan="4">No replacement products selected yet.</td></tr>'}</tbody></table></div>`;
        document.getElementById("retrievePreviewHeldBill").textContent = "Retrieve Exchange";
        previewHeldSaleId = String(id);
        heldBillPreview.hidden = false;
        return;
    }

    document.getElementById("retrievePreviewHeldBill").textContent = "Retrieve Sale";

    const total = calculateHeldSaleTotal(data);

    const received = Number(data.amountReceived) || 0;

    const due = Math.max(0, total - received);
    const returned = Math.max(0, received - total);

    const customerDisplay = window.RiceCustomerDisplay.format(data);

    const paymentTypes = {
        full: "Full Payment",
        partial: "Partial Payment",
        credit: "Credit Sale"
    };

    const paymentMethods = {
        cash: "Cash",
        bank: "Bank Transfer"
    };

    const paymentTypeLabel =
        paymentTypes[data.paymentType] || "Full Payment";

    const paymentMethodLabel =
        data.paymentType === "credit"
        ? "Not Applicable"
        : paymentMethods[data.paymentMethod] || "Cash";

    let itemRows = "";

    (data.items || []).forEach((item, index) => {
        const quantity = Number(item.quantity) || 0;

        const price = Number(item.price) || 0;

        const packageSize = Number(item.package) || 0;

        const isPacked = item.category === "packed";

        const totalKg = isPacked
            ? quantity * packageSize
            : quantity;

        const lineTotal = totalKg * price;

        const categoryNames = {
            bulk: "Bulk Rice",
            packed: "Packed Rice",
            bran: "Rice Bran",
            other: "Other Sellable Products"
        };

        const quantityDisplay = isPacked
            ? quantity + " bags"
            : quantity + " kg";

        const packageDisplay = isPacked
            ? packageSize + " kg"
            : "—";

        itemRows += `
            <tr>
                <td>${index + 1}</td>
                <td>${escapeHTML(categoryNames[item.category] || item.category)}</td>
                <td>${escapeHTML(item.product || "-")}</td>
                <td>${escapeHTML(packageDisplay)}</td>
                <td>${escapeHTML(quantityDisplay)}</td>
                <td>${money(price)}</td>
                <td>${money(lineTotal)}</td>
            </tr>
        `;
    });

    heldBillPreviewContent.innerHTML = `

        <div class="held-preview-meta">

            <div>
                <span>Hold Reference</span>
                <strong>${escapeHTML(sale.reference)}</strong>
            </div>

            <div>
                <span>Held Date & Time</span>
                <strong>${escapeHTML(sale.heldAt)}</strong>
            </div>

            <div>
                <span>Customer</span>
                <strong>${escapeHTML(customerDisplay)}</strong>
            </div>

            <div>
                <span>Payment Type</span>
                <strong>${escapeHTML(paymentTypeLabel)}</strong>
            </div>

            <div>
                <span>Payment Method</span>
                <strong>${escapeHTML(paymentMethodLabel)}</strong>
            </div>

            <div>
                <span>Amount Received</span>
                <strong>${money(received)}</strong>
            </div>

        </div>

        <div class="held-preview-table-wrap">

            <table class="held-preview-table">

                <thead>
                    <tr>
                        <th>#</th>
                        <th>Category</th>
                        <th>Product</th>
                        <th>Package</th>
                        <th>Quantity</th>
                        <th>Unit Price</th>
                        <th>Total</th>
                    </tr>
                </thead>

                <tbody>
                    ${itemRows || `
                        <tr>
                            <td colspan="7">No items found.</td>
                        </tr>
                    `}
                </tbody>

            </table>

        </div>

        <div class="held-preview-total">
                <span>Invoice Grand Total</span>
            <strong>${money(total)}</strong>
        </div>

        <div class="held-preview-meta" style="margin-top:12px;margin-bottom:0;">

            <div>
                <span>Amount Received</span>
                <strong>${money(received)}</strong>
            </div>

            <div>
                <span>Customer Balance</span>
                <strong>${money(returned)}</strong>
            </div>

            <div>
                <span>Outstanding Balance</span>
                <strong>${money(due)}</strong>
            </div>

        </div>

    `;

    heldBillPreview.hidden = false;
}


// ==========================================
// CLOSE HELD BILL PREVIEW
// ==========================================

function closeHeldBillPreview() {
    heldBillPreview.hidden = true;

    previewHeldSaleId = null;
}


document.getElementById("closeHeldPreviewButton")
    .addEventListener("click", closeHeldBillPreview);


document.getElementById("closeHeldPreviewFooter")
    .addEventListener("click", closeHeldBillPreview);


heldBillPreview.addEventListener("click", function (event) {
    if (event.target === heldBillPreview) {
        closeHeldBillPreview();
    }
});


// ==========================================
// DELETE HELD SALE
// ==========================================

async function deleteHeldSale(id) {
    const sale = findHeldSale(id);
    if (!sale) return;
    if (!confirm(`Are you sure you want to delete ${sale.reference}?`)) return;
    if (sale.source === "database") {
        try {
            const response = await window.csrfFetch(`/api/held-sales/${encodeURIComponent(sale.id.slice(3))}`, { method: "DELETE" });
            const result = await response.json();
            if (!response.ok || !result.success) throw new Error(result.message || "Held sale could not be deleted.");
        } catch (error) {
            alert(error.message || "Held sale could not be deleted.");
            return;
        }
    } else {
        const localId = String(sale.localId);
        const remaining = getHeldSales().filter(item => String(item.id) !== localId);
        if (!saveHeldSales(remaining)) return;
    }
    closeHeldBillPreview();
    await renderHeldSales();
}


// ==========================================
// CHECK CURRENT FORM DATA
// ==========================================

function hasCurrentSaleData() {
    const currentData = collectSale();

    return (

        currentData.items.some(item =>
            item.category || item.quantity || item.price
        ) ||

        currentData.customerName.trim()

    );
}


// ==========================================
// RETRIEVE HELD SALE
// ==========================================

async function retrieveHeldSale(id) {
    let heldSale = findHeldSale(id);
    if (!heldSale) return;
    if (heldSale.source === "database") {
        try {
            const response = await window.csrfFetch(`/api/held-sales/${encodeURIComponent(heldSale.id.slice(3))}`);
            const result = await response.json();
            if (!response.ok || !result.success) throw new Error(result.message || "Held sale could not be retrieved.");
            heldSale = { ...result.heldSale, id: `db-${result.heldSale.id}`, source: "database" };
        } catch (error) {
            alert(error.message || "Held sale could not be retrieved.");
            return;
        }
    }
    if (heldSale.type === "return_exchange" || heldSale.data?.kind === "return_exchange") {
        closeHeldBillPreview();
        closeHeldBillsPanel();
        const returnId = Number(heldSale.data?.returnId);
        if (!Number.isInteger(returnId) || returnId < 1) {
            alert("This held Return Exchange is missing its original Return Bill context.");
            return;
        }
        window.location.assign(`/sales?returnExchange=${returnId}&heldExchange=${Number(heldSale.id.replace(/^db-/,""))}`);
        return;
    }
    if (heldSale.type === "return_bill") {
        if (heldSale.source !== "database") {
            alert("Return Bill drafts are stored in the database; this draft cannot be retrieved from browser storage.");
            return;
        }
        if (returnBillModal && !returnBillModal.hidden && selectedReturnInvoiceData &&
            !confirm("The current Return Bill form will be replaced. Continue?")) return;
        const data = heldSale.data || {};
        closeHeldBillPreview();
        closeHeldBillsPanel();
        openReturnBillModal();
        returnSearchMethod.value = "invoice";
        returnSearchInput.value = data.invoiceNo || "";
        await searchReturnInvoices();
        const invoice = serverReturnInvoices.get(data.invoiceNo);
        if (!invoice) {
            alert("The original invoice could not be loaded. The held Return Bill remains saved.");
            return;
        }
        selectReturnInvoice(invoice.invoiceNumber);
        const heldItems = new Map((data.items || []).map(item => [Number(item.saleItemId), item]));
        returnItemsList.querySelectorAll(".return-quantity-input").forEach(input => {
            const row = selectedReturnInvoiceData.items[Number(input.dataset.index)];
            const saved = heldItems.get(Number(row.saleItemId));
            if (saved) input.value = String(saved.quantity);
        });
        heldReturnBillDraftId = Number(String(heldSale.id).replace(/^db-/, ""));
        holdSaleButton.textContent = "Update Held Return Bill";
        calculateReturnTotal();
        closeHeldBillPreview();
        showReturnSearchMessage(`Restored held draft ${heldSale.reference}. Review the values and submit when ready.`);
        return;
    }
    if (hasCurrentSaleData() && !confirm("Current sale details will be replaced. Continue?")) return;
    let restored = false;
    try {
        restored = restoreSale(heldSale.data);
    }
    catch (error) {
        restored = false;
    }
    if (!restored) {
        alert("The held sale could not be restored completely. The held record has been kept.");
        return;
    }

    if (heldSale.source === "database") {
        try {
            const response = await window.csrfFetch(`/api/held-sales/${encodeURIComponent(heldSale.id.slice(3))}`, { method: "DELETE" });
            const result = await response.json();
            if (!response.ok || !result.success) throw new Error(result.message || "Held record could not be removed.");
        } catch (error) {
            alert("The draft was restored, but its held record remains in the database. You can delete it later from Held Bills.");
            await renderHeldSales();
            return;
        }
    } else {
        alert("This browser-held draft was restored and remains in localStorage as a backup. Delete it manually only when you no longer need that copy.");
    }

    closeHeldBillPreview();
    closeHeldBillsPanel();
    await renderHeldSales();
    window.scrollTo({ top: 0, behavior: "smooth" });
    if (heldSale.source === "database") alert("Held sale retrieved successfully.");
}


// ==========================================
// HELD BILLS PANEL ACTIONS
// ==========================================

heldBillsList.addEventListener("click", function (event) {
    const button = event.target.closest("button[data-action]");

    if (!button) return;

    const id = button.dataset.id;

    const action = button.dataset.action;

    if (action === "view") {
        viewHeldBill(id);
    } else if (action === "retrieve") {
        retrieveHeldSale(id);
    } else if (action === "delete") {
        deleteHeldSale(id);
    }
});


// ==========================================
// HELD BILL PREVIEW ACTIONS
// ==========================================

document.getElementById("retrievePreviewHeldBill")
    .addEventListener("click", function () {
        if (previewHeldSaleId) {
            retrieveHeldSale(previewHeldSaleId);
        }
    });


document.getElementById("deletePreviewHeldBill")
    .addEventListener("click", function () {
        if (previewHeldSaleId) {
            deleteHeldSale(previewHeldSaleId);
        }
    });


// ==========================================
// RESET SALE FORM
// ==========================================

function resetSaleForm() {
    sessionStorage.removeItem(EDIT_STORAGE_KEY);

    customerType.value = "walkin";

    customerName.value = "";

    paymentType.value = "full";

    paymentMethod.value = "cash";

    amountReceived.value = "0";

    itemsContainer.innerHTML = "";

    addItemRow();

    updateCustomerType();

    calculateTotals();

    customerType.focus();
}


// ==========================================
// CLEAR CURRENT SALE
// ==========================================

document.getElementById("clearSaleBtn").addEventListener("click", function () {
    if (returnExchangeContext || new URLSearchParams(window.location.search).has("returnExchange")) {
        window.location.assign("/sales");
        return;
    }

    if (!confirm("Clear all current sale details?")) return;

    resetSaleForm();
});


// ==========================================
// VALIDATE SALE ITEMS
// ==========================================

function reportSalesValidationError(message, focusTarget = null) {
    if (returnExchangeContext) {
        showReturnExchangeError(message);
        if (focusTarget) focusTarget.focus({ preventScroll: true });
    } else {
        alert(message);
        if (focusTarget) focusTarget.focus();
    }
    return false;
}

function validateSalesItems() {
    const rows = [...document.querySelectorAll(".sales-item-row")];

    const selectedRows = rows.filter(row =>
        row.querySelector(".item-category").value
    );

    if (selectedRows.length === 0) {
        return reportSalesValidationError(returnExchangeContext ?
            "Add at least one replacement product before confirming." : "Please add at least one sales item.");
    }

    for (const row of selectedRows) {
        const category = row.querySelector(".item-category").value;

        const product = row.querySelector(".item-product").value;

        const quantity =
            Number(row.querySelector(".item-quantity").value);

        const price =
            Number(row.querySelector(".item-price").value);

        if (!product) {
            return reportSalesValidationError("Please select a product for every item.", row.querySelector(".item-product"));
        }

        if (!salesCatalog) {
            return reportSalesValidationError("Product and stock data is still loading. Please try again.");
        }

        if (selectedProductDetails(row).stockType === "bran" && !selectedProductDetails(row).variety) {
            return reportSalesValidationError("Please select a rice bran product and variety.", row.querySelector(".item-product"));
        }

        if (category === "packed" &&
            !row.querySelector(".item-package").value) {
                return reportSalesValidationError("Please select a package size.", row.querySelector(".item-package"));
            }

        if (!Number.isFinite(quantity) || quantity <= 0 ||
            !Number.isFinite(price) || price <= 0) {
                return reportSalesValidationError("Please enter a valid quantity and price for every item.");
            }

        if (category === "packed" && !Number.isInteger(quantity)) {
            return reportSalesValidationError("Packed rice quantity must be a whole number of bags.", row.querySelector(".item-quantity"));
        }

        if (quantity > availableForRow(row)) {
            return reportSalesValidationError("The requested quantity exceeds current available stock.", row.querySelector(".item-quantity"));
        }
    }

    return true;
}


// ==========================================
// VALIDATE PAYMENT
// ==========================================

function validatePayment() {
    const total = getGrandTotal();

    const received = Number(amountReceived.value) || 0;

    if (total <= 0) {
        alert("Invoice total must be greater than zero.");

        return false;
    }

    if (customerType.value === "registered" &&
        (!customerName.value.trim() || !selectedCustomer?.id)) {
            alert("Please search for and select an active Registered Customer.");

            customerName.focus();

            return false;
        }

    if (!Number.isFinite(received) || received < 0) {
        alert("Please check the amount received.");

        return false;
    }

    if (paymentType.value === "full" && Math.round(received * 100) < Math.round(total * 100)) {
        alert("Full payment must be at least the invoice total.");

        return false;
    }

    if (paymentType.value === "partial" &&
        (received <= 0 || Math.round(received * 100) >= Math.round(total * 100))) {
            alert("Partial payment must be greater than zero and less than the invoice total.");

            amountReceived.focus();

            return false;
        }

    if (paymentType.value === "credit" && received !== 0) {
        alert("Credit sale amount received must be zero.");

        return false;
    }

    return true;
}


// ==========================================
// OPEN SALES BILL IN A NEW TAB
// ==========================================

function makeIdempotencyKey() {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    const bytes = window.crypto.getRandomValues(new Uint8Array(24));
    return Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
}

function makeSalePayload(data) {
    return {
        customerType: data.customerType === "walkin" ? "walk_in" : "registered",
        ...(data.customerType === "registered" ? { customerId : Number(data.customerId) } : {}),
        paymentType: data.paymentType,
        paymentMethod: data.paymentMethod === "bank" ? "Bank Transfer" : "Cash",
        amountPaid: String(data.amountReceived),
        items: data.items.map(item => ({
            category: item.category,
            product: item.product,
            ...(item.productId ? { productId : Number(item.productId) } : {}),
            ...(item.stockType ? { stockType : item.stockType } : {}),
            ...(item.variety ? { variety : item.variety } : {}),
            quantity: String(item.quantity),
            unit: item.category === "packed" ? "bags" : "kg",
            ...(item.category === "packed" ? { packageSize : Number(item.package) } : {}),
            price: String(item.price)
        }))
    };
}

function rememberConfirmedSale(sale) {
    const invoices = getReturnInvoiceHistory();
    const invoice = {
        invoiceNumber: sale.invoiceNo,
        customerId: sale.customerId,
        customerName: sale.customerName,
        invoiceDate: sale.date,
        total: sale.amount,
        items: (sale.items || []).map(item => ({
            category: item.category, product: item.product,
            package: item.packageSize || "", quantity: item.quantity,
            price: item.price, returnedQuantity: 0
        }))
    };
    const existing = invoices.findIndex(row => row.invoiceNumber === invoice.invoiceNumber);
    if (existing >= 0) invoices[existing] = invoice;
    else invoices.unshift(invoice);
    try {
        saveReturnInvoiceHistory(invoices);
    }
    catch (error) {
        console.warn("Local return-search history could not be updated.");
    }
}

function openSalesBill(sale, data, billWindow) {
    rememberConfirmedSale(sale);
    billWindow.location.replace(`${salesPage.dataset.salesBillUrl}?saleId=${encodeURIComponent(sale.id)}`);
    return true;
}

// ==========================================
// RETURN BILL - DEMO FRONTEND
// ==========================================

const RETURN_HISTORY_KEY = "riceMillSalesInvoiceHistory";
const PENDING_RETURN_KEY = "riceMillPendingInspection";

const returnBillButton = document.getElementById("returnBillButton");
const returnBillModal = document.getElementById("returnBillModal");

const closeReturnModal = document.getElementById("closeReturnBillModal");
const cancelReturnBtn = document.getElementById("cancelReturnBillButton");

const returnSearchInput =
    document.getElementById("returnSearchValue");

const returnSearchMethod =
    document.getElementById("returnSearchType");

if (window.RiceAutocomplete && returnSearchInput) {
    window.RiceAutocomplete.attach(returnSearchInput, document.getElementById("returnInvoiceSuggestions"), {
        minChars: 2, debounceMs: 250,
        fetchItems: async term => {
            const method = returnSearchMethod?.value || "invoice";
            const response = await window.csrfFetch(`/api/returns/invoices?by=${encodeURIComponent(method)}&q=${encodeURIComponent(term)}`);
            const data = await response.json().catch (() => ({}));
            if (!response.ok) throw new Error(data.message || "Invoice suggestions could not be loaded.");
            return data.invoices || [];
        },
        key: invoice => invoice.id || invoice.invoiceNo,
        label: invoice => `${invoice.invoiceNo}  |  ${window.RiceCustomerDisplay.format(invoice)}`,
        searchText: invoice => `${invoice.invoiceNo} ${invoice.customerName || ""} ${window.RiceCustomerDisplay.typeLabel(invoice)} ${invoice.customerId || ""}`,
        onSelect: invoice => {
            returnSearchInput.value = returnSearchMethod?.value === "customer_name" ? invoice.customerName : invoice.invoiceNo;
            searchReturnInvoices();
        }
    });
}

const returnCustomerType =
    document.getElementById("returnCustomerType");

const searchReturnInvoice =
    document.getElementById("searchReturnInvoiceButton");

const returnSearchResults =
    document.getElementById("returnSearchResults") ||
    document.getElementById("returnInvoiceList");

const selectedReturnInvoice =
    document.getElementById("returnInvoiceSection");

const returnInvoiceDetails =
    document.querySelector(".return-invoice-details");

const returnItemsList =
    document.getElementById("returnInvoiceItems");

const returnGrandTotal =
    document.getElementById("returnGrandTotal");

const confirmReturnBtn =
    document.getElementById("confirmReturnBillButton");
const returnSettlementQueue = document.getElementById("returnSettlementQueue");
const returnSettlementModal = document.getElementById("returnSettlementModal");
const returnSettlementContent = document.getElementById("returnSettlementContent");
const returnSettlementLoading = document.getElementById("returnSettlementLoading");
const returnSettlementError = document.getElementById("returnSettlementError");
const returnSettlementBillSummary = document.getElementById("returnSettlementBillSummary");
const returnSettlementItemRows = document.getElementById("returnSettlementItemRows");
const returnSettlementInventoryMessage = document.getElementById("returnSettlementInventoryMessage");
const returnSettlementInventoryNote = document.getElementById("returnSettlementInventoryNote");
const returnSettlementInventoryHeading = document.getElementById("returnSettlementInventoryHeading");
const returnSettlementModalTitle = document.getElementById("returnSettlementModalTitle");
const returnSettlementQueueTitle = document.getElementById("returnSettlementQueueTitle");
const returnSettlementModalSubtitle = document.getElementById("returnSettlementModalSubtitle");
const returnSettlementClose = document.getElementById("closeReturnSettlementModal");
const returnSettlementDone = document.getElementById("returnSettlementDone");
const returnApprovalBadge = document.getElementById("returnApprovalBadge");
const returnApprovalNotifications = document.getElementById("returnApprovalNotifications");
let returnApprovalNotificationDetail = document.getElementById("returnApprovalNotificationDetail");
if (returnApprovalNotificationDetail && !(returnApprovalNotificationDetail instanceof HTMLDialogElement)) {
    const detailDialog = document.createElement("dialog");
    detailDialog.id = "returnApprovalNotificationDetail";
    detailDialog.className = "return-notification-detail";
    detailDialog.setAttribute("aria-labelledby", "returnNotificationDetailTitle");
    returnApprovalNotificationDetail.replaceWith(detailDialog);
    returnApprovalNotificationDetail = detailDialog;
}

async function refreshReturnApprovalCount() {
    if (!returnApprovalBadge) return;
    try {
        const response = await window.csrfFetch("/api/returns/approval-notifications/unread-count");
        const data = await response.json();
        if (!response.ok) throw new Error(data.message || "Notification count unavailable.");
        const count = Number(data.activeCount || 0);
        returnApprovalBadge.textContent = String(count);
        returnApprovalBadge.hidden = count < 1;
        returnApprovalBadge.setAttribute("aria-label", `${count} unresolved Return Bill notification${count === 1 ? "" : "s"}`);
    } catch (error) {
        console.warn("Return approval notification count could not be loaded.");
    }
}

function formatReturnDecisionDate(value) {
    const date = value ? new Date(value) : null;
    return date && !Number.isNaN(date.valueOf()) ? date.toLocaleString() : "—";
}

function returnNotificationHasSettlement(item) {
    // This flag is computed by the API from linked settlement and financial rows.
    // A display label or older payload is not sufficient evidence of completion.
    return item?.settlementCompleted === true;
}

function isReturnApprovalNotificationVisible(item) {
    if (!item || returnNotificationHasSettlement(item)) return false;
    if (item.decision === "approved") {
        return ["pending_settlement", "partially_settled"].includes(item.financialStatus);
    }
    if (item.decision === "rejected" || item.decision === "cancelled") return item.isRead !== true;
    return item.decision === "pending" || !item.decision;
}

async function loadReturnApprovalNotifications() {
    if (!returnApprovalNotifications) return;
    returnApprovalNotifications.setAttribute("aria-busy", "true");
    returnApprovalNotifications.innerHTML = '<div class="return-placeholder">Loading approval history…</div>';
    try {
        const response = await window.csrfFetch("/api/returns/approval-notifications");
        const data = await response.json();
        if (!response.ok) throw new Error(data.message || "Approval history could not be loaded.");
        const notifications = requireArrayProperty(data, "notifications", "Approval notifications", true);
        const activeCount = Number(data.activeCount || 0);
        returnApprovalBadge.textContent = String(activeCount);
        returnApprovalBadge.hidden = activeCount < 1;
        returnApprovalBadge.setAttribute("aria-label", `${activeCount} unresolved Return Bill notification${activeCount===1?"":"s"}`);
        // One card represents one Return Bill, even if a bad legacy payload repeats its event.
        const uniqueNotifications = [...new Map(notifications.map(item => [String(item.returnId), item])).values()];
        const visibleNotifications = uniqueNotifications.filter(isReturnApprovalNotificationVisible);
        if (!visibleNotifications.length) {
            returnApprovalNotifications.innerHTML = '<div class="return-placeholder">No unresolved Return Bill notifications. Settled returns remain available in Sales History; physical inspection remains in Inventory.</div>';
            return;
        }
        const panelScrollTop = returnApprovalNotifications.scrollTop;
        const list = document.createElement("div");
        list.className = "return-notification-list";
        visibleNotifications.forEach(item => {
            const rejected = item.decision === "rejected" || item.decision === "cancelled";
            const canSettle = item.decision === "approved" &&
                !returnNotificationHasSettlement(item) &&
                ["pending_settlement", "partially_settled"].includes(item.financialStatus) &&
                Number.isInteger(Number(item.returnId)) && Number(item.returnId) > 0;
            const canRefund = canSettle;
            const canExchange = canSettle;
            const heading = item.notificationType || (rejected ? "Return Bill Rejected" : item.decision === "approved" ? "Return Bill Approved" : "Return Bill Pending Admin Approval");
            const card = document.createElement("article");
            card.className = `return-notification-card${item.isRead ? "" : " is-unread"}${rejected ? " is-rejected" : ""}`;
            card.innerHTML = `<header><strong>${escapeHTML(heading)}</strong><span class="return-notification-state">${escapeHTML(item.returnStatus || (rejected ? "Rejected / Cancelled" : item.decision === "approved" ? "Approved" : "Pending Admin Approval"))}</span></header>
                <p><strong>Customer:</strong> ${escapeHTML(item.customerName || "Retail Customer")}</p>
                <p><strong>Original Invoice:</strong> ${escapeHTML(item.invoiceNo || "—")}</p>
                <p><strong>Return Reference:</strong> ${escapeHTML(item.returnNo || "—")}</p>
                <p><strong>Original Return Value:</strong> ${money(item.returnValue)}</p>
                <p><strong>Admin:</strong> ${escapeHTML(item.adminName || "—")}</p>
                <p><strong>Decision Date:</strong> ${escapeHTML(formatReturnDecisionDate(item.decisionAt))}</p>
                ${returnNotificationHasSettlement(item)?`<p><strong>Completed:</strong> ${escapeHTML((item.settlementTypes||[]).map(type=>type==="refund"?"Cash Refund":type==="exchange"?"Item Exchange":type).join(", ")||"Settlement")} | ${escapeHTML(item.settlementReference||item.combinedDocumentNo||"Saved transaction")} | ${escapeHTML(formatReturnDecisionDate(item.settledAt))}</p><p><strong>Settlement value:</strong> ${money(item.settlementAmount||item.returnValue)}</p>`:""}
                ${item.decision==="approved"?`<p><strong>Physical inspection:</strong> ${escapeHTML(item.status==="Inventory Approved"?"Inspection Approved":item.status==="Inventory Rejected"?"Inspection Rejected":"Inspection Pending")}</p>`:""}
                ${rejected && item.reason ? `<p class="return-notification-reason"><strong>Reason:</strong> ${escapeHTML(item.reason)}</p>` : ""}
                <div class="return-notification-actions"><button type="button" class="return-notification-view" data-notification-id="${Number(item.id)||""}" data-return-id="${Number(item.returnId)}">View Details</button>${canRefund ? `<button type="button" class="return-notification-settlement" data-return-action="refund" data-return-id="${Number(item.returnId)}">Cash Refund</button>` : ""}${canExchange ? `<button type="button" class="return-notification-settlement" data-return-action="exchange" data-return-id="${Number(item.returnId)}">Item Exchange</button>` : ""}</div>`;
            list.appendChild(card);
            const viewButton = card.querySelector("button[data-notification-id]");
            viewButton.addEventListener("click", event => {
                event.preventDefault();
                event.stopPropagation();
                void openReturnApprovalNotification(viewButton.dataset.returnId, viewButton);
            });
        });
        returnApprovalNotifications.replaceChildren(list);
        returnApprovalNotifications.scrollTop = panelScrollTop;
    } catch (error) {
        returnApprovalNotifications.innerHTML = `<div class="return-placeholder">${escapeHTML(error.message || "Approval history could not be loaded.")}</div>`;
    } finally {
        returnApprovalNotifications.setAttribute("aria-busy", "false");
    }
}

let returnNotificationFocusTarget = null;
let returnNotificationScrollPosition = null;
let returnNotificationRefreshAfterClose = false;
returnApprovalNotifications?.addEventListener("click", event => {
    if (!(event.target instanceof Element)) return;
    const settlementButton = event.target.closest("[data-return-action]");
    if (settlementButton) {
        const returnId = Number(settlementButton.dataset.returnId);
        if (!Number.isInteger(returnId) || returnId < 1) return;
        if (settlementButton.dataset.returnAction === "exchange") {
            window.location.assign(`/sales?returnExchange=${returnId}`);
            return;
        }
        if (settlementButton.dataset.returnAction === "refund") {
            window.location.assign(`/return-refund-preview/${returnId}`);
            return;
        }
    }
    return;
});

async function openReturnApprovalNotification(returnId, button) {
    const id = Number(returnId);
    if (!returnApprovalNotificationDetail || !Number.isInteger(id) || id < 1 || !button?.isConnected) return;
    button.disabled = true;
    try {
        const detailResponse = await window.csrfFetch(`/api/returns/approval-notifications/return/${id}`);
        const detailData = await readApiJson(detailResponse, "Loading Return Bill notification details");
        if (!detailResponse.ok) throw new Error(detailData.message || "Notification details could not be loaded.");
        const item = detailData.notification;
        if (Number(item.returnId) !== id) throw new Error("The selected notification details did not match the Return Bill.");
        returnNotificationFocusTarget = button;
        returnNotificationScrollPosition = { x: window.scrollX, y: window.scrollY };
        const notificationItems = requireArrayProperty(item, "items", "Return notification");
        const approved = item.decision === "approved", pending = item.decision === "pending" || !item.decision;
        const rejected = item.decision === "rejected" || item.decision === "cancelled";
        const stage = pending ? "Waiting for Admin Approval" : rejected ? "Return Bill Rejected" : approved ? "Return Bill Approved" : "Return Bill";
        const inspectionLabel = item.status === "Inventory Approved" ? "Inspection Approved" : item.status === "Inventory Rejected" ? "Inspection Rejected" : "Inspection Pending";
        const products = `<div class="return-notification-items"><table class="return-table"><thead><tr><th>Product</th><th>Quantity</th><th>Return Value</th><th>Physical Inspection</th></tr></thead><tbody>${notificationItems.map(returnItem=>{
            const inspection=returnItem.decision==="approved"?"Inspection Approved":returnItem.decision==="rejected"?"Inspection Rejected":"Inspection Pending";
            const quantity=`${escapeHTML(returnItem.quantity)} ${escapeHTML(returnItem.unit)}${returnItem.packageSize?` | ${escapeHTML(returnItem.packageSize)} kg/bag`:""}`;
            return `<tr><td>${escapeHTML(returnItem.product)}${returnItem.variety?` | ${escapeHTML(returnItem.variety)}`:""}</td><td>${quantity}</td><td>${money(returnItem.returnValue)}</td><td>${inspection}</td></tr>`;
        }).join("")||'<tr><td colspan="4">No returned products available.</td></tr>'}</tbody></table></div>`;
        const settlementCompleted = returnNotificationHasSettlement(item);
        const canSettle = approved && !rejected && !pending && item.settlementCompleted !== true &&
            ["pending_settlement", "partially_settled"].includes(item.financialStatus) &&
            Number.isInteger(Number(item.returnId)) && Number(item.returnId) > 0;
        const settlementTypes = (item.settlementTypes || []).map(type => type === "refund" ? "Cash Refund" : type === "exchange" ? "Item Exchange" : type).join(", ");
        const settlement = settlementCompleted ? `<section><h3>Completed settlement</h3><p><strong>Type:</strong> ${escapeHTML(settlementTypes||"Settlement")}</p><p><strong>Reference:</strong> ${escapeHTML(item.settlementReference||item.combinedDocumentNo||"Saved transaction")}</p><p><strong>Completed:</strong> ${escapeHTML(formatReturnDecisionDate(item.settledAt))}</p><p><strong>Settlement amount:</strong> ${money(item.settlementAmount||item.returnValue)}</p>${item.combinedDocumentNo?`<a class="return-notification-receipt" href="/return-receipt/${encodeURIComponent(item.combinedDocumentNo)}" target="_blank" rel="noopener">View customer receipt</a>`:""}</section>` : "";
        const action = canSettle ? `<footer class="return-notification-actions return-notification-detail-actions" aria-label="Return Bill settlement actions"><button type="button" class="return-notification-settlement" data-detail-return-action="refund" data-return-id="${Number(item.returnId)}">Cash Refund</button><button type="button" class="return-notification-settlement" data-detail-return-action="exchange" data-return-id="${Number(item.returnId)}">Item Exchange</button></footer>` : "";
        const financialLabel = settlementCompleted ? "Settled" : item.financialStatus === "partially_settled" ? "Partially Settled" : "Awaiting Settlement";
        returnApprovalNotificationDetail.innerHTML = `<div class="return-notification-detail-head"><h2 id="returnNotificationDetailTitle">Return Bill ${escapeHTML(item.returnNo||"")}</h2><button type="button" class="return-notification-detail-close" data-close-return-detail>Close</button></div><p><strong>Approval status:</strong> ${escapeHTML(item.returnStatus||stage)}</p><p><strong>Original invoice:</strong> ${escapeHTML(item.invoiceNo||"?")}</p><p><strong>Customer:</strong> ${escapeHTML(item.customerName||"Retail Customer")}</p><p><strong>Return value:</strong> ${money(item.returnValue)}</p><p><strong>Approval decision:</strong> ${escapeHTML(stage)}</p><p><strong>Admin:</strong> ${escapeHTML(item.adminName||"?")}</p><p><strong>Decision date:</strong> ${escapeHTML(formatReturnDecisionDate(item.decisionAt))}</p><p><strong>Financial settlement:</strong> ${escapeHTML(financialLabel)}</p><p><strong>Physical inspection:</strong> ${escapeHTML(inspectionLabel)}</p>${rejected&&item.reason?`<p><strong>Rejection reason:</strong> ${escapeHTML(item.reason)}</p>`:""}<h3>Returned products</h3>${products}${settlement}${action}`;
        returnApprovalNotificationDetail.hidden = false;
        if (returnApprovalNotificationDetail.parentElement?.closest("[hidden]")) throw new Error("Return Bill details are inside a hidden panel.");
        if (returnApprovalNotificationDetail.open) returnApprovalNotificationDetail.close();
        delete returnApprovalNotificationDetail.dataset.settlementStarting;
        returnApprovalNotificationDetail.showModal();
        returnApprovalNotificationDetail.scrollTop = 0;
        returnApprovalNotificationDetail.querySelector("[data-close-return-detail]")?.focus({ preventScroll: true });
        window.scrollTo(returnNotificationScrollPosition.x, returnNotificationScrollPosition.y);
        try {
            const readResponse = await window.csrfFetch(`/api/returns/approval-notifications/return/${id}/read`, { method: "POST" });
            const readData = await readApiJson(readResponse, "Marking the Return Bill notification as viewed");
            if (readResponse.ok && readData.success === true) {
                item.isRead = true;
                button.closest(".return-notification-card")?.classList.remove("is-unread");
                if (rejected && returnApprovalNotificationDetail.open) returnNotificationRefreshAfterClose = true;
                else if (rejected) {
                    document.getElementById("returnBillButton")?.focus({ preventScroll: true });
                    void loadReturnApprovalNotifications();
                    void refreshReturnApprovalCount();
                }
            }
        } catch (readError) {
            console.warn("The Return Bill notification could not be marked as viewed.");
        }
    } catch (error) {
        returnNotificationFocusTarget = button;
        returnNotificationScrollPosition = { x: window.scrollX, y: window.scrollY };
        returnApprovalNotificationDetail.innerHTML = `<div class="return-notification-detail-head"><h2 id="returnNotificationDetailTitle">Return Bill Details</h2><button type="button" class="return-notification-detail-close" data-close-return-detail>Close</button></div><p role="alert">${escapeHTML(error.message||"Notification details could not be loaded.")}</p>`;
        returnApprovalNotificationDetail.hidden = false;
        if (!returnApprovalNotificationDetail.open) returnApprovalNotificationDetail.showModal();
        returnApprovalNotificationDetail.querySelector("[data-close-return-detail]")?.focus({ preventScroll: true });
        window.scrollTo(returnNotificationScrollPosition.x, returnNotificationScrollPosition.y);
    } finally {
        if (button.isConnected) button.disabled = false;
    }
}
returnApprovalNotificationDetail?.addEventListener("click", async event => {
    if (!(event.target instanceof Element)) return;
    if (event.target.closest("[data-close-return-detail]")) {
        returnApprovalNotificationDetail.close();
        return;
    }
    const action = event.target.closest("[data-detail-return-action]");
    if (action) {
        event.preventDefault();
        event.stopPropagation();
        if (action.disabled || returnApprovalNotificationDetail.dataset.settlementStarting === "true") return;
        const id = Number(action.dataset.returnId);
        if (!Number.isInteger(id) || id < 1 || !returnApprovalNotificationDetail.open ||
            !["refund", "exchange"].includes(action.dataset.detailReturnAction)) return;
        returnApprovalNotificationDetail.dataset.settlementStarting = "true";
        returnApprovalNotificationDetail.querySelectorAll("[data-detail-return-action]").forEach(button => button.disabled = true);
        const destination = action.dataset.detailReturnAction === "refund" ?
            `/return-refund-preview/${id}` : `/sales?returnExchange=${id}`;
        returnApprovalNotificationDetail.close();
        window.location.assign(destination);
        return;
    }
    const button = event.target.closest("[data-open-return-settlement]");
    if (!button) return;
    if (button.dataset.returnClosed === "true") {
        const documentNo = returnApprovalNotificationDetail.dataset.documentNo;
        if (documentNo) window.open(`/return-receipt/${encodeURIComponent(documentNo)}`, "_blank", "noopener");
        return;
    }
    button.disabled = true;
    try {
        await openReturnSettlementForReturn(Number(button.dataset.openReturnSettlement));
    }
    catch (error) {
        alert(error.message || "The approved Return Bill could not be opened for settlement.");
    }
    finally {
        button.disabled = false;
    }
});
returnApprovalNotificationDetail?.addEventListener("close", () => {
    const refreshAfterClose = returnNotificationRefreshAfterClose;
    if (refreshAfterClose) document.getElementById("returnBillButton")?.focus({ preventScroll: true });
    else if (returnNotificationFocusTarget?.isConnected) returnNotificationFocusTarget.focus({ preventScroll: true });
    returnNotificationFocusTarget = null;
    if (returnNotificationScrollPosition) window.scrollTo(returnNotificationScrollPosition.x, returnNotificationScrollPosition.y);
    returnNotificationScrollPosition = null;
    if (refreshAfterClose) {
        returnNotificationRefreshAfterClose = false;
        void loadReturnApprovalNotifications();
        void refreshReturnApprovalCount();
    }
});

let selectedReturnInvoiceData = null;
let serverReturnInvoices = new Map();
let returnRequestKey = "";
let returnSubmitting = false;


// ==========================================
// GET SAVED INVOICES
// ==========================================

function getReturnInvoiceHistory() {
    try {
        const data = JSON.parse(
            localStorage.getItem(RETURN_HISTORY_KEY)
        );

        return Array.isArray(data) ? data : [];
    } catch (error) {
        return [];
    }
}


// ==========================================
// SAVE INVOICE HISTORY
// ==========================================

function saveReturnInvoiceHistory(data) {
    localStorage.setItem(
        RETURN_HISTORY_KEY,
        JSON.stringify(data)
    );
}


// ==========================================
// OPEN RETURN MODAL
// ==========================================

function openReturnBillModal(options = {}) {
    if (!returnBillModal) return;

    returnBillModal.hidden = false;
    if (returnHoldButtonSlot && holdSaleButton) {
        returnHoldButtonSlot.appendChild(holdSaleButton);
        holdSaleButton.textContent = "Hold Return Bill";
    }
    returnBillButton?.setAttribute("aria-expanded", "true");
    document.body.style.overflow = "hidden";

    selectedReturnInvoiceData = null;
    heldReturnBillDraftId = null;
    unresolvedReturnBillHold = null;
    if (holdSaleButton) holdSaleButton.textContent = "Hold Return Bill";
    returnRequestKey = makeIdempotencyKey();

    if (returnSearchInput) {
        returnSearchInput.value = "";
    }

    if (returnSearchResults) {
        returnSearchResults.innerHTML = "";
    }

    const searchMessage = document.getElementById("returnSearchMessage");
    if (searchMessage) searchMessage.hidden = true;

    if (selectedReturnInvoice) {
        selectedReturnInvoice.hidden = true;
    }

    if (confirmReturnBtn) {
        confirmReturnBtn.disabled = true;
    }

    updateReturnSearchOptions();
    if (returnApprovalNotificationDetail) {
        returnNotificationFocusTarget = null;
        if (returnApprovalNotificationDetail.open) returnApprovalNotificationDetail.close();
        returnApprovalNotificationDetail.hidden = false;
    }
    loadReturnApprovalNotifications();
}

refreshReturnApprovalCount();
window.addEventListener("focus", refreshReturnApprovalCount);
window.setInterval(refreshReturnApprovalCount, 30000);


// ==========================================
// CLOSE RETURN MODAL
// ==========================================

function closeReturnBillModal() {
    if (!returnBillModal) return;

    returnBillModal.hidden = true;
    if (salesActions && holdSaleButton) {
        salesActions.insertBefore(holdSaleButton, clearSaleButton || null);
        holdSaleButton.textContent = returnExchangeContext ? "Hold Exchange" : "Hold Sale";
    }
    returnBillButton?.setAttribute("aria-expanded", "false");
    document.body.style.overflow = "";

    selectedReturnInvoiceData = null;
    heldReturnBillDraftId = null;
    unresolvedReturnBillHold = null;
}


// ==========================================
// RETURN MODAL BUTTONS
// ==========================================

if (returnBillButton) {
    returnBillButton.addEventListener(
        "click",
        openReturnBillModal
    );
}

if (closeReturnModal) {
    closeReturnModal.addEventListener(
        "click",
        closeReturnBillModal
    );
}

if (cancelReturnBtn) {
    cancelReturnBtn.addEventListener(
        "click",
        closeReturnBillModal
    );
}

if (returnBillModal) {
    returnBillModal.addEventListener("click", function (event) {
        if (event.target === returnBillModal) {
            closeReturnBillModal();
        }
    });
}


// ==========================================
// UPDATE SEARCH OPTIONS
// ==========================================

function updateReturnSearchOptions() {
    if (!returnCustomerType || !returnSearchMethod) return;

    const isRetail =
        returnCustomerType.value === "walkin";

    const methods = isRetail
        ? [
            ["invoice", "Invoice Number"]
        ]
        : [
            ["invoice", "Invoice Number"],
            ["name", "Customer Name"],
            ["customer_id", "Customer ID"]
        ];

    returnSearchMethod.innerHTML = "";

    methods.forEach(method => {
        const option = document.createElement("option");

        option.value = method[0];
        option.textContent = method[1];

        returnSearchMethod.appendChild(option);
    });

    updateReturnSearchLabel();
}


function updateReturnSearchLabel() {
    const label =
        document.getElementById("returnSearchInputLabel");

    const help =
        document.getElementById("returnSearchHelp");

    if (!returnSearchMethod) return;

    const method = returnSearchMethod.value;

    const labels = {
        invoice: "Invoice Number",
        name: "Customer Name",
        customer_id: "Customer ID"
    };

    if (label) {
        label.textContent = labels[method] || "Search";
    }

    if (help) {
        help.textContent =
            method === "invoice"
            ? "Enter the original invoice number."
            : method === "name"
            ? "Enter the Registered Customer name."
            : "Enter the Registered Customer ID.";
    }
}


if (returnCustomerType) {
    returnCustomerType.addEventListener(
        "change",
        updateReturnSearchOptions
    );
}

if (returnSearchMethod) {
    returnSearchMethod.addEventListener(
        "change",
        updateReturnSearchLabel
    );
}


// ==========================================
// SEARCH MESSAGE
// ==========================================

function showReturnSearchMessage(message) {
    const messageBox =
        document.getElementById("returnSearchMessage");

    if (messageBox) {
        messageBox.textContent = message;
        messageBox.hidden = false;
    }
}


// ==========================================
// SEARCH INVOICES
// ==========================================

async function searchReturnInvoices() {
    if (!returnSearchInput || !returnSearchResults) return;

    const searchValue = returnSearchInput.value.trim();

    if (!searchValue) {
        showReturnSearchMessage(
            "Please enter a search value."
        );

        return;
    }

    const method =
        returnSearchMethod
        ? returnSearchMethod.value
        : "invoice";

    try {
        const response = await window.csrfFetch(`/api/returns/invoices?by=${encodeURIComponent(method)}&q=${encodeURIComponent(searchValue)}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.message || "Invoices could not be searched.");
        const results = data.invoices || [];
        serverReturnInvoices = new Map(results.map(invoice => [invoice.invoiceNo, {
            invoiceNumber: invoice.invoiceNo, customerId: invoice.customerId, customerName: invoice.customerName,
            customerType: invoice.customerType, customerTypeLabel: invoice.customerTypeLabel,
            customerDisplay: window.RiceCustomerDisplay.format(invoice),
            invoiceDate: invoice.invoiceDate, total: invoice.total,
            items: (invoice.items || []).map(item => ({ saleItemId: item.saleItemId, category: item.category,
                product: item.product, variety: item.variety, package: item.packageSize || "", quantity: item.quantity,
                returnedQuantity: item.returnedQuantity, availableQuantity: item.availableQuantity,
                price: item.price, unit: item.unit }))
        }]));

        returnSearchResults.innerHTML = "";

        if (results.length === 0) {
            showReturnSearchMessage(
                "No matching invoices found."
            );

            return;
        }

        showReturnSearchMessage(
            results.length + " invoice(s) found. Select an invoice."
        );

        results.forEach(invoice => {
            const button = document.createElement("button");

            button.type = "button";
            button.className = "return-invoice-result";

            button.innerHTML = `
            <strong>${escapeHTML(invoice.invoiceNo)}</strong>
            <span>${escapeHTML(window.RiceCustomerDisplay.format(invoice))}</span>
            <span>${escapeHTML(invoice.invoiceDate)}</span>
        `;

            button.addEventListener("click", () => selectReturnInvoice(invoice.invoiceNo));

            returnSearchResults.appendChild(button);
        });
    } catch (error) {
        showReturnSearchMessage(error.message || "Invoices could not be searched.");
    }
}


if (searchReturnInvoice) {
    searchReturnInvoice.addEventListener(
        "click",
        searchReturnInvoices
    );
}


// ==========================================
// SELECT ORIGINAL INVOICE
// ==========================================

function selectReturnInvoice(invoiceNumber) {
    const invoice = serverReturnInvoices.get(invoiceNumber);

    if (!invoice) return;

    selectedReturnInvoiceData = invoice;

    if (selectedReturnInvoice) {
        selectedReturnInvoice.hidden = false;
    }

    document.getElementById("returnOriginalInvoice").textContent = invoice.invoiceNumber;
    document.getElementById("returnOriginalCustomer").textContent = window.RiceCustomerDisplay.format(invoice);
    document.getElementById("returnOriginalDate").textContent = invoice.invoiceDate;
    document.getElementById("returnOriginalTotal").textContent = money(invoice.total);

    renderReturnInvoiceItems();
}


// ==========================================
// DISPLAY ORIGINAL INVOICE PRODUCTS
// ==========================================

function renderReturnInvoiceItems() {
    if (!selectedReturnInvoiceData || !returnItemsList) return;

    returnItemsList.innerHTML = "";

    selectedReturnInvoiceData.items.forEach((item, index) => {
        const soldQty = Number(item.quantity) || 0;

        const returnedQty =
            Number(item.returnedQuantity) || 0;

        const availableQty =
            Math.max(0, soldQty - returnedQty);

        const isPacked = item.category === "packed";

        const row = document.createElement("tr");

        row.innerHTML = `
            <td>${escapeHTML(item.product)}</td>
            <td>${isPacked ? `${escapeHTML(item.package)} kg` : "—"}</td>
            <td>${soldQty}</td>

            <td>${returnedQty}</td>

            <td>${availableQty}</td>

            <td>
                <input
                    type="number"
                    class="return-quantity-input"
                    data-index="${index}"
                    min="0"
                    max="${availableQty}"
                    step="0.01"
                    value="0"
                    ${availableQty <= 0 ? "disabled" : ""}
                >
                <small>${isPacked ? "bags" : "kg"}</small>
            </td>
        `;

        returnItemsList.appendChild(row);
    });

    returnItemsList.addEventListener(
        "input",
        calculateReturnTotal
    );

    calculateReturnTotal();
}


// ==========================================
// CALCULATE RETURN TOTAL
// ==========================================

function calculateReturnTotal() {
    if (!selectedReturnInvoiceData || !returnItemsList) return;

    let total = 0;

    const inputs = returnItemsList.querySelectorAll(
        ".return-quantity-input"
    );

    inputs.forEach(input => {
        const index = Number(input.dataset.index);

        const item =
            selectedReturnInvoiceData.items[index];

        const quantity = Number(input.value) || 0;

        const soldQty = Number(item.quantity) || 0;

        const alreadyReturned =
            Number(item.returnedQuantity) || 0;

        const available =
            soldQty - alreadyReturned;

        if (quantity < 0 || quantity > available) {
            input.setCustomValidity(
                "Return quantity cannot exceed available quantity."
            );
        } else {
            input.setCustomValidity("");
        }

        const packageSize = Number(item.package) || 0;

        const totalKg = item.category === "packed"
            ? quantity * packageSize
            : quantity;

        total += totalKg * Number(item.price);
    });

    if (returnGrandTotal) {
        returnGrandTotal.textContent = money(total);
    }

    if (confirmReturnBtn) {
        const hasQuantity = [...inputs].some(input =>
            Number(input.value) > 0
        );

        confirmReturnBtn.disabled =
            !hasQuantity ||
            [...inputs].some(input => !input.checkValidity());
    }
}


// ==========================================
// CONFIRM RETURN
// ==========================================

async function confirmReturnBill() {
    if (returnSubmitting) return;

    if (!selectedReturnInvoiceData) {
        alert("Please select an original invoice.");

        return;
    }

    const inputs = [
        ...returnItemsList.querySelectorAll(
            ".return-quantity-input"
        )
    ];

    const returnedItems = [];

    for (const input of inputs) {
        if (!input.checkValidity()) {
            input.reportValidity();

            return;
        }

        const quantity = Number(input.value) || 0;

        if (quantity <= 0) continue;

        const index = Number(input.dataset.index);

        const item =
            selectedReturnInvoiceData.items[index];

        returnedItems.push({
            saleItemId: item.saleItemId,
            quantity: String(quantity)
        });
    }

    if (returnedItems.length === 0) {
        alert("Please enter at least one return quantity.");

        return;
    }

    if (!returnRequestKey) returnRequestKey = makeIdempotencyKey();
    returnSubmitting = true;
    if (confirmReturnBtn) confirmReturnBtn.disabled = true;
    try {
        const response = await window.csrfFetch("/api/returns", {
            method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": returnRequestKey },
            body: JSON.stringify({ invoiceNo: selectedReturnInvoiceData.invoiceNumber,
                idempotencyKey: returnRequestKey, items: returnedItems,
                ...(heldReturnBillDraftId ? { heldDraftId : heldReturnBillDraftId } : {}) })
        });
        const data = await readApiJson(response, "Submitting the Return Bill");
        if (!response.ok || !data.success) throw new Error(data.message || "Return Bill could not be saved.");
        alert(`Return Bill ${data.return.returnNo} was saved and is Pending Admin Approval.`);
        returnRequestKey = makeIdempotencyKey();
        heldReturnBillDraftId = null;
        unresolvedReturnBillHold = null;
        await renderHeldSales();
        await loadReturnApprovalNotifications();
        await refreshReturnApprovalCount();
        closeReturnBillModal();
    } catch (error) {
        alert(error.message || "Return Bill could not be saved. Your entries are still available to retry.");
        calculateReturnTotal();
    } finally {
        returnSubmitting = false;
        if (confirmReturnBtn) confirmReturnBtn.disabled = true;
        if (selectedReturnInvoiceData) calculateReturnTotal();
    }
}


// ==========================================
// CONFIRM BUTTON
// ==========================================

if (confirmReturnBtn) {
    confirmReturnBtn.addEventListener(
        "click",
        confirmReturnBill
    );
}

async function openReturnSettlementForReturn(returnId, heldDraft = null, preferredSettlementType = null) {
    if (preferredSettlementType === "refund") {
        window.location.assign(`/return-refund-preview/${Number(returnId)}`);
        return;
    }
    if (!returnSettlementModal) throw new Error("Return settlement panel is unavailable.");
    const id = Number(returnId);
    if (!Number.isInteger(id) || id < 1) throw new Error("A valid Return Bill reference is required.");
    returnSettlementModal.dataset.restoreReturnModal = returnBillModal && !returnBillModal.hidden ? "true" : "false";
    if (returnBillModal) returnBillModal.hidden = true;
    returnSettlementModal.hidden = false;
    document.body.style.overflow = "hidden";
    returnSettlementLoading.hidden = false;
    returnSettlementLoading.textContent = "Loading saved Return Bill details...";
    returnSettlementContent.hidden = true;
    returnSettlementError.hidden = true;
    returnSettlementError.textContent = "";
    try {
        const response = await window.csrfFetch(`/api/returns/${id}`);
        const data = await response.json();
        if (!response.ok || !data.success) throw new Error(data.message || "Return Bill details could not be loaded.");
        const ret = data.return;
        if (Number(ret.id) !== id) throw new Error("The returned Return Bill does not match the selected reference.");
        if (ret.approvalStatus !== "approved") throw new Error(ret.approvalStatus === "rejected" ? "This Return Bill was rejected and cannot be settled." : "This Return Bill is waiting for Admin approval.");
        const returnItems = requireArrayProperty(ret, "items", "Return Bill");
        returnSettlementModalTitle.textContent = preferredSettlementType === "refund" ? "Confirm Cash Refund" : "Settle Return";
        returnSettlementModalSubtitle.textContent = `${ret.returnNo} | Original invoice ${ret.invoiceNo||"-"}`;
        const refundMode = preferredSettlementType === "refund";
        const refundableItems = returnItems.filter(item => item.settlementStatus !== "settled");
        const approvedReturnValue = (refundMode ? refundableItems : returnItems).reduce((sum, item) => sum + (Number(item.returnValue) || 0), 0);
        returnSettlementBillSummary.innerHTML = refundMode
            ? `<div class="return-detail-item"><span>Customer</span><strong>${escapeHTML(ret.customerName||"Retail Customer")}</strong></div><div class="return-detail-item"><span>Original Invoice</span><strong>${escapeHTML(ret.invoiceNo||"-")}</strong></div><div class="return-detail-item"><span>Return Bill</span><strong>${escapeHTML(ret.returnNo)}</strong></div><div class="return-detail-item"><span>Refund Amount</span><strong>${money(approvedReturnValue)}</strong></div>`
            : `<div class="return-detail-item"><span>Return Reference</span><strong>${escapeHTML(ret.returnNo)}</strong></div><div class="return-detail-item"><span>Original Invoice</span><strong>${escapeHTML(ret.invoiceNo||"-")}</strong></div><div class="return-detail-item"><span>Customer</span><strong>${escapeHTML(ret.customerName||"Retail Customer")}</strong></div><div class="return-detail-item"><span>Approved Return Value</span><strong>${money(approvedReturnValue)}</strong></div><div class="return-detail-item"><span>Admin Decision</span><strong>${escapeHTML(ret.approvedBy||"Admin")} | ${escapeHTML(formatReturnDecisionDate(ret.approvedAt))}</strong></div><div class="return-detail-item"><span>Financial Status</span><strong>${escapeHTML(formatReturnFinancialStatus(ret.financialStatus))}</strong></div><div class="return-detail-item"><span>Inventory Status</span><strong>${escapeHTML(ret.status||"Pending Inspection")} (independent decision)</strong></div><div class="return-detail-item"><span>Return Items</span><strong>${returnItems.length} item(s)</strong></div>`;
        returnSettlementInventoryMessage.textContent = inventorySettlementMessage(ret.status);
        returnSettlementInventoryNote.hidden = refundMode;
        returnSettlementInventoryHeading.hidden = refundMode;
        returnSettlementQueueTitle.textContent = refundMode ? "Refund Confirmation" : "Settlement";
        returnSettlementItemRows.innerHTML = (refundMode ? refundableItems : returnItems).map(item => {
            const inventory = item.decision === "approved" ? "Inventory Approved" : item.decision === "rejected" ? "Inventory Rejected" : "Pending Inspection";
            const qty = `${escapeHTML(item.quantity)} ${escapeHTML(item.unit)}${item.packageSize?` | ${escapeHTML(item.packageSize)} kg/bag`:""}`;
            return `<tr><td>${escapeHTML(item.product)}${item.variety?` | ${escapeHTML(item.variety)}`:""}</td><td>${qty}</td><td>${money(item.originalPrice)}/kg</td><td>${money(item.returnValue)}</td>${refundMode?"":`<td>${inventory}</td>`}</tr>`;
        }).join("") || '<tr><td colspan="5">This Return Bill has no items.</td></tr>';
        await loadReturnSettlementQueue(id, heldDraft, ret, preferredSettlementType);
        returnSettlementLoading.hidden = true;
        returnSettlementContent.hidden = false;
    } catch (error) {
        returnSettlementLoading.hidden = true;
        returnSettlementError.textContent = error.message || "The Return Bill could not be loaded for settlement.";
        returnSettlementError.hidden = false;
    }
}

function formatReturnFinancialStatus(status) {
    return ({ pending_settlement: "Awaiting Settlement", partially_settled: "Partially Settled", settled: "Fully Settled / Closed", rejected: "Rejected", cancelled: "Cancelled" })[status] || status || "Awaiting Settlement";
}

function inventorySettlementMessage(status) {
    const messages = {
        "Pending Inspection": "Returned goods are awaiting inventory inspection. They are not available for sale yet.",
        "Inventory Approved": "Inventory inspection approved. Returned goods have been added to sellable stock.",
        "Inventory Rejected": "Inventory inspection rejected. Returned goods have not been added to sellable stock.",
        "Mixed Inspection": "Inventory inspection has mixed decisions. Only approved returned goods have been added to sellable stock."
    };
    return `${messages[status]||"Inventory status is recorded separately from financial settlement."} Financial settlement remains available after Admin approval.`;
}

async function loadReturnSettlementQueue(returnId, heldDraft = null, ret = null, preferredSettlementType = null) {
    if (!returnSettlementQueue) return;
    returnSettlementQueue.replaceChildren();
    if (!ret || ret.financialStatus === "settled") {
        returnSettlementQueue.innerHTML = '<div class="return-placeholder">This Return Bill is already completed.</div>';
        return;
    }
    const choices = document.createElement("div");
    choices.className = "return-settlement-choice-row";
    const availableTypes = ["refund", "exchange"];
    if (availableTypes.includes("refund")) {
        const refund = document.createElement("button");
        refund.type = "button";
        refund.className = "return-settlement-choice";
        refund.textContent = "Cash Refund";
        refund.addEventListener("click", () => window.location.assign(`/return-refund-preview/${Number(returnId)}`));
        choices.appendChild(refund);
    }
    if (availableTypes.includes("exchange")) {
        const exchange = document.createElement("button");
        exchange.type = "button";
        exchange.className = "return-settlement-choice";
        exchange.textContent = "Item Exchange";
        exchange.addEventListener("click", () => {
            window.location.assign(`/sales?returnExchange=${Number(returnId)}${heldDraft?`&heldExchange=${Number(heldDraft.id)}`:""}`);
        });
        choices.appendChild(exchange);
    }
    returnSettlementQueue.appendChild(choices);
    const hint = document.createElement("p");
    hint.className = "return-status-note";
    hint.textContent = "Settlement applies to the entire approved Return Bill. Physical stock inspection remains a separate Inventory decision.";
    returnSettlementQueue.appendChild(hint);
}

function closeReturnSettlementModal() {
    if (!returnSettlementModal) return;
    const restoreParent = returnSettlementModal.dataset.restoreReturnModal === "true";
    returnSettlementModal.hidden = true;
    returnSettlementModal.dataset.restoreReturnModal = "false";
    document.body.style.overflow = restoreParent ? "hidden" : "";
    if (restoreParent && returnBillModal) returnBillModal.hidden = false;
}

returnSettlementClose?.addEventListener("click", closeReturnSettlementModal);
returnSettlementDone?.addEventListener("click", closeReturnSettlementModal);
returnSettlementModal?.addEventListener("click", event => {
    if (event.target === returnSettlementModal) closeReturnSettlementModal();
});


// ==========================================
// RETURN SEARCH ENTER KEY
// ==========================================

if (returnSearchInput) {
    returnSearchInput.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
            event.preventDefault();

            searchReturnInvoices();
        }
    });
}


// ==========================================
// CONFIRM SALE
// ==========================================

confirmSaleButton.addEventListener("click", async function () {
    if (returnExchangeContext) {
        await confirmReturnExchange();
        return;
    }
    if (saleSubmitting) return;
    if (!validateSalesItems() || !validatePayment()) return;

    const saleData = collectSale();
    const payload = makeSalePayload(saleData);
    const serialized = JSON.stringify(payload);
    if (unresolvedSaleRequest && unresolvedSaleRequest.serialized !== serialized) {
        alert("The previous confirmation outcome is unknown. Retry the unchanged sale first to avoid creating a duplicate invoice.");
        return;
    }
    const pending = unresolvedSaleRequest || {
        key: makeIdempotencyKey(), payload, serialized
    };

    try {
        sessionStorage.setItem(EDIT_STORAGE_KEY, JSON.stringify(saleData));
    }
    catch (error) {
        console.warn("Previous sale draft could not be stored locally.");
    }

    // Reserve the user-initiated tab now; it is navigated to the persisted bill
    // only after the server confirms the transaction.
    const billWindow = window.open("about:blank", "_blank");
    if (!billWindow) {
        alert("Please allow pop-ups for this website before confirming the sale.");
        return;
    }
    try {
        billWindow.opener = null;
    } catch (error) {}

    unresolvedSaleRequest = pending;
    saleSubmitting = true;
    const controlStates = [...salesPage.querySelectorAll("input, select, button")]
        .map(element => [element, element.disabled]);
    controlStates.forEach(([element]) => {
        element.disabled = true;
    });
    confirmSaleButton.disabled = true;
    const oldText = confirmSaleButton.textContent;
    confirmSaleButton.textContent = "Saving Sale…";
    try {
        const response = await window.csrfFetch("/api/sales", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Idempotency-Key": pending.key
            },
            body: JSON.stringify(pending.payload)
        });
        const result = await response.json();
        if (!response.ok || !result.success || !result.sale) {
            billWindow.close();
            const message = result.message || "The sale could not be confirmed.";
            if (response.status < 500 && !message.toLowerCase().includes("idempotency key")) {
                unresolvedSaleRequest = null;
            }
            alert(message);
            return;
        }

        if (typeof window.loadSalesHistory === "function") window.loadSalesHistory(1);
        if (!openSalesBill(result.sale, saleData, billWindow)) return;
        resetSaleForm();
        unresolvedSaleRequest = null;
        loadSalesCatalog();
    } catch (error) {
        billWindow.close();
        alert("The server response was not received. Your sale details are preserved. Click Confirm Sale again without changing them to safely retry.");
    } finally {
        saleSubmitting = false;
        controlStates.forEach(([element, wasDisabled]) => {
            element.disabled = wasDisabled;
        });
        confirmSaleButton.textContent = oldText;
    }
});


// ==========================================
// KEYBOARD CONTROLS
// ==========================================

function getSalesKeyboardControls() {
    return [
        ...document.querySelectorAll(
            ".sales-page input:not([disabled]):not([readonly]), " +
            ".sales-page select:not([disabled])"
        )
    ].filter(element => {
        return element.getClientRects().length > 0;
    });
}


function moveToNextSalesControl(current) {
    const controls = getSalesKeyboardControls();

    const index = controls.indexOf(current);

    if (index === -1) return;

    if (current.classList.contains("item-price")) {
        const row = current.closest(".sales-item-row");

        if (row === itemsContainer.lastElementChild) {
            const newRow = addItemRow();

            newRow.querySelector(".item-category").focus();

            return;
        }
    }

    const next = controls[index + 1];

    if (next) {
        next.focus();
    } else {
        document.getElementById("confirmSaleBtn").focus();
    }
}


function moveToPreviousSalesControl(current) {
    const controls = getSalesKeyboardControls();

    const index = controls.indexOf(current);

    if (index > 0) {
        controls[index - 1].focus();
    }
}


function isCursorAtStart(input) {
    if (input.type === "number") return true;

    try {
        return input.selectionStart === 0 &&
            input.selectionEnd === 0;
    } catch (error) {
        return true;
    }
}


// ==========================================
// ENTER / ARROW KEY HANDLING
// ==========================================

salesPage.addEventListener("keydown", function (event) {
    const current = event.target;

    if (!current.matches("input, select")) return;

    if (current.disabled || current.readOnly) return;

    if (event.key === "Enter") {
        event.preventDefault();

        moveToNextSalesControl(current);

        return;
    }

    if (event.key === "ArrowLeft") {
        if (
            current.tagName === "INPUT" &&
            !isCursorAtStart(current)
        ) {
            return;
        }

        event.preventDefault();

        moveToPreviousSalesControl(current);

        return;
    }
});


// ==========================================
// RESTORE SALE AFTER PREVIOUS BUTTON
// ==========================================

function restoreSaleFromBill() {
    const editMode = new URLSearchParams(
        window.location.search
    ).get("edit");

    if (editMode !== "1") return false;

    const savedData = sessionStorage.getItem(EDIT_STORAGE_KEY);

    if (!savedData) return false;

    try {
        const data = JSON.parse(savedData);

        restoreSale(data);

        document.querySelector(".sales-heading").scrollIntoView({
            behavior: "smooth",
            block: "start"
        });

        return true;
    } catch (error) {
        sessionStorage.removeItem(EDIT_STORAGE_KEY);

        alert("Unable to restore the previous sale details.");

        return false;
    }
}


// ==========================================
// INITIAL PAGE SETUP
// ==========================================

function updateReturnExchangeSummaryDisplay() {
    if (!returnExchangeContext || !exchangeDifference) return;
    if (exchangeModeError && !exchangeModeError.hidden) {
        exchangeModeError.hidden = true;
        exchangeModeError.style.color = "";
    }
    const replacement = Number(getGrandTotal()) || 0;
    const returnValue = Number(returnExchangeContext.returnValue) || 0;
    const delta = Math.round((replacement - returnValue) * 100) / 100;
    const hasReplacement = replacement > 0;
    const collectDifference = hasReplacement && delta > 0;
    const refundDifference = hasReplacement && delta < 0;
    exchangePaymentSection.hidden = !collectDifference;
    exchangeRefundSection.hidden = !refundDifference;
    exchangeCollectionControls.hidden = !collectDifference;
    exchangeCustomerBalanceDueWrap.hidden = !collectDifference;
    exchangeRefundControls.hidden = !refundDifference;
    const collectByBank = exchangeCollectionMethod.value === "Bank Transfer";
    const refundByBank = exchangeRefundMethod.value === "Bank Transfer";
    exchangeCashReceivedWrap.hidden = !collectDifference || collectByBank;
    exchangeBalanceChangeWrap.hidden = !collectDifference || collectByBank;
    exchangeCollectionReferenceWrap.hidden = !collectDifference || !collectByBank;
    exchangeRefundReferenceWrap.hidden = !refundDifference || !refundByBank;
    exchangeCollectionReference.required = !exchangeCollectionReferenceWrap.hidden;
    exchangeRefundReference.required = !exchangeRefundReferenceWrap.hidden;
    if (exchangeCustomerBalanceDue) exchangeCustomerBalanceDue.value = money(collectDifference ? delta : 0);
    const received = Number(exchangeCashReceived.value);
    if (exchangeBalanceChange) exchangeBalanceChange.value = money((Number.isFinite(received) ? received : 0) - delta);
    if (exchangeRefundDifference) exchangeRefundDifference.textContent = money(refundDifference ? Math.abs(delta) : 0);
    if (!hasReplacement) {
        exchangeDifference.textContent = "Add replacement products to calculate the difference.";
    } else if (delta > 0) {
        exchangeDifference.textContent = `Replacement Total: ${money(replacement)} | Customer Balance Due: ${money(delta)}`;
    } else if (delta < 0) {
        exchangeDifference.textContent = `Replacement Total: ${money(replacement)} | Refund Difference: ${money(Math.abs(delta))}`;
    } else {
        exchangeDifference.textContent = "Equal Exchange \u2014 No Additional Payment Required";
    }
}

exchangeCollectionMethod?.addEventListener("change", updateReturnExchangeSummaryDisplay);
exchangeRefundMethod?.addEventListener("change", updateReturnExchangeSummaryDisplay);
exchangeCashReceived?.addEventListener("input", updateReturnExchangeSummaryDisplay);

function showReturnExchangeError(message, disableActions = false) {
    if (!exchangeModeError) return;
    exchangeModeError.style.color = "";
    exchangeModeError.textContent = message;
    exchangeModeError.hidden = false;
    if (disableActions) {
        confirmSaleButton.disabled = true;
        document.getElementById("holdSaleBtn").disabled = true;
    }
}

function exchangeReceiptDraftItems(source) {
    return (source || []).map(item => ({ category: item.category, product: item.product, productId: item.productId,
        stockType: item.stockType, variety: item.variety, quantity: item.quantity, price: item.price,
        package: item.packageSize ?? item.package, unit: item.unit }));
}

async function initializeReturnExchangeMode(returnId, heldId = null) {
    returnExchangeBanner.hidden = false;
    document.getElementById("returnBillButton").hidden = true;
    try {
        const response = await window.csrfFetch(`/api/returns/${encodeURIComponent(returnId)}`);
        const data = await readApiJson(response, "Loading Return Exchange context");
        if (!response.ok || !data.success) throw new Error(data.message || "Return Exchange context could not be validated.");
        const ret = data.return;
        if (Number(ret.id) !== Number(returnId) || ret.approvalStatus !== "approved") throw new Error("Only an Admin-approved Return Bill can enter Exchange mode.");
        if (!["pending_settlement", "partially_settled"].includes(ret.financialStatus)) {
            if (ret.financialStatus === "settled" && (ret.settlementTypes || []).length === 1 && ret.settlementTypes[0] === "exchange") {
                const historyResponse = await window.csrfFetch(`/api/returns/${encodeURIComponent(returnId)}/history`);
                const historyData = await readApiJson(historyResponse, "Recovering the completed Return Exchange");
                if (historyResponse.ok && historyData.success && historyData.return?.settlementType === "exchange" && historyData.return.documentNo) {
                    window.location.replace(`/sales-bill?returnExchangeDocument=${encodeURIComponent(historyData.return.documentNo)}`);
                    return;
                }
            }
            throw new Error("This Return Bill has already completed settlement.");
        }
        if (!Array.isArray(ret.items) || !ret.items.length) throw new Error("This Return Bill has no returned items.");
        const remainingItems = ret.items.filter(item => item.settlementStatus !== "settled");
        if (!remainingItems.length) throw new Error("All returned items have already been settled.");
        const returnValue = remainingItems.reduce((sum, item) => sum + (Number(item.returnValue) || 0), 0);
        returnExchangeContext = { id: Number(ret.id), returnNo: ret.returnNo, invoiceNo: ret.invoiceNo,
            customerId: ret.customerId ? Number(ret.customerId) : null, customerName: ret.customerName || "Retail Customer",
            returnValue: Math.round(returnValue * 100) / 100, heldExchangeId: null };
        document.getElementById("exchangeReturnNo").textContent = ret.returnNo;
        document.getElementById("exchangeInvoiceNo").textContent = ret.invoiceNo || "—";
        document.getElementById("exchangeCustomerName").textContent = returnExchangeContext.customerName;
        document.getElementById("exchangeReturnValue").textContent = money(returnExchangeContext.returnValue);
        customerType.value = returnExchangeContext.customerId ? "registered" : "walkin";
        customerType.disabled = true;
        customerName.value = returnExchangeContext.customerName;
        customerName.disabled = true;
        selectedCustomer = returnExchangeContext.customerId ?
            { id : returnExchangeContext.customerId, name: returnExchangeContext.customerName } : null;
        paymentDetailsCard.hidden = true;
        document.querySelector(".sales-grand-total span").textContent = "Replacement Value";
        paymentType.value = "full";
        paymentType.disabled = true;
        amountReceived.readOnly = true;
        document.getElementById("holdSaleBtn").textContent = "Hold Exchange";
        document.getElementById("clearSaleBtn").textContent = "Exit Exchange";
        confirmSaleButton.textContent = "Confirm Exchange";
        itemsContainer.replaceChildren();
        addItemRow();
        updateCustomerType();
        calculateTotals();
        if (heldId) {
            const heldResponse = await window.csrfFetch(`/api/held-sales/${encodeURIComponent(heldId)}`);
            const heldData = await readApiJson(heldResponse, "Restoring the held Return Exchange");
            if (!heldResponse.ok || !heldData.success) throw new Error(heldData.message || "Held Exchange could not be restored.");
            const held = heldData.heldSale;
            if (held.type !== "return_exchange" || Number(held.data?.returnId) !== Number(returnId)) throw new Error("The held draft does not match this Return Bill.");
            returnExchangeContext.heldExchangeId = Number(held.id);
            exchangeCollectionMethod.value = held.data.paymentMethod || "Cash";
            exchangeCashReceived.value = String(Math.max(0, Number(held.data.cashReceived) || 0));
            exchangeRefundMethod.value = held.data.refundMethod || "Cash";
            exchangeCollectionReference.value = held.data.paymentReference || "";
            exchangeRefundReference.value = held.data.refundReference || "";
            const rows = exchangeReceiptDraftItems(held.data.replacements);
            itemsContainer.replaceChildren();
            if (rows.length) rows.forEach(row => addItemRow(row));
            else addItemRow();
            calculateTotals();
        }
        updateCustomerType();
        returnExchangeBanner.scrollIntoView({ block: "start" });
    } catch (error) {
        showReturnExchangeError(error.message || "Exchange context could not be loaded.", true);
    }
}

async function holdCurrentReturnExchange() {
    if (!returnExchangeContext || holdSubmitting) return;
    if (!validateSalesItems()) return;
    const data = makeSalePayload(collectSale());
    const replacements = data.items;
    const button = document.getElementById("holdSaleBtn");
    const original = button.textContent;
    button.disabled = true;
    holdSubmitting = true;
    button.textContent = "Saving Exchange…";
    try {
        const body = { returnId: returnExchangeContext.id, replacements,
            paymentMethod: exchangeCollectionMethod.value,
            cashReceived: exchangeCollectionMethod.value === "Cash" ? Math.max(0, Number(exchangeCashReceived.value) || 0) : 0,
            paymentReference: exchangeCollectionReference.value.trim(),
            refundMethod: exchangeRefundMethod.value,
            refundReference: exchangeRefundReference.value.trim() };
        const response = await window.csrfFetch("/api/held-sales/return-exchanges", { method: "POST",
            headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        const result = await readApiJson(response, "Holding the Return Exchange");
        if (!response.ok || !result.success) throw new Error(result.message || "Return Exchange draft could not be held.");
        returnExchangeContext.heldExchangeId = Number(result.heldSale.id);
        await renderHeldSales();
        exchangeModeError.style.color = "#2f6b3c";
        exchangeModeError.hidden = false;
        exchangeModeError.textContent = `Held as ${result.heldSale.reference}. No money or stock has moved.`;
    } catch (error) {
        showReturnExchangeError(error.message || "Return Exchange draft could not be held.");
    }
    finally {
        holdSubmitting = false;
        button.disabled = false;
        button.textContent = original;
    }
}

async function confirmReturnExchange() {
    if (!returnExchangeContext || returnExchangeSubmitting) return;
    if (!validateSalesItems()) return;
    const data = makeSalePayload(collectSale());
    const replacementValue = getGrandTotal();
    if (replacementValue <= 0) {
        showReturnExchangeError("Add at least one replacement product before confirming.");
        return;
    }
    const delta = Math.round((replacementValue - returnExchangeContext.returnValue) * 100) / 100;
    const cashReceived = exchangeCollectionMethod.value === "Cash" ? Number(exchangeCashReceived.value) : 0;
    if (!Number.isFinite(cashReceived) || cashReceived < 0) {
        showReturnExchangeError("Cash received cannot be negative or invalid.");
        return;
    }
    if (delta > 0 && exchangeCollectionMethod.value === "Cash" && cashReceived < delta) {
        showReturnExchangeError(`Collect ${money(delta-cashReceived)} more before confirming the exchange.`);
        return;
    }
    if (delta > 0 && exchangeCollectionMethod.value === "Bank Transfer" && !exchangeCollectionReference.value.trim()) {
        showReturnExchangeError("Enter the bank transfer reference before confirming the exchange.");
        return;
    }
    if (delta < 0 && exchangeRefundMethod.value === "Bank Transfer" && !exchangeRefundReference.value.trim()) {
        showReturnExchangeError("Enter the refund bank transfer reference before confirming the exchange.");
        return;
    }
    if (!confirm(delta > 0 ? `Confirm exchange and collect ${money(delta)} now?` : delta < 0 ? `Confirm exchange and refund ${money(Math.abs(delta))} now?` : "Confirm equal-value exchange?")) return;
    const payload = { settlementType: "exchange", items: data.items,
        expectedReplacementTotal: replacementValue,
        cashReceived: delta > 0 && exchangeCollectionMethod.value === "Cash" ? cashReceived : 0,
        paymentMethod: delta > 0 ? exchangeCollectionMethod.value : "Cash",
        paymentReference: delta > 0 ? exchangeCollectionReference.value.trim() : "",
        refundMethod: delta < 0 ? exchangeRefundMethod.value : "Cash",
        refundReference: delta < 0 ? exchangeRefundReference.value.trim() : "" };
    if (returnExchangeContext.heldExchangeId) payload.heldExchangeId = returnExchangeContext.heldExchangeId;
    const serialized = JSON.stringify(payload);
    const idempotencyKey = returnExchangeRequest?.serialized === serialized ? returnExchangeRequest.key : makeIdempotencyKey();
    payload.idempotencyKey = idempotencyKey;
    returnExchangeRequest = { key: idempotencyKey, serialized };
    returnExchangeSubmitting = true;
    confirmSaleButton.disabled = true;
    document.getElementById("holdSaleBtn").disabled = true;
    try {
        const response = await window.csrfFetch(returnExchangeSettlementUrl(returnExchangeContext.id), { method: "POST",
            headers: { "Content-Type": "application/json", "Idempotency-Key": payload.idempotencyKey }, body: JSON.stringify(payload) });
        const result = await readApiJson(response, "Confirming the Return Exchange");
        if (!response.ok || !result.success) throw new Error(result.message || "Return Exchange could not be completed.");
        returnExchangeRequest = null;
        window.location.replace(result.salesBillUrl || `/sales-bill?returnExchangeDocument=${encodeURIComponent(result.documentNo)}`);
    } catch (error) {
        showReturnExchangeError(error.message || "Exchange failed. Your selected items are still on this page.");
    }
    finally {
        returnExchangeSubmitting = false;
        confirmSaleButton.disabled = false;
        document.getElementById("holdSaleBtn").disabled = false;
    }
}

async function loadSalesCatalog() {
    try {
        const response = await window.csrfFetch("/api/sales/catalog");
        const data = await readApiJson(response, "Loading the Sales catalogue");
        if (!response.ok) throw new Error(data.message || "Product data could not be loaded.");
        salesCatalog = {
            varieties: requireArrayProperty(data, "varieties", "Sales catalog", true),
            products: requireArrayProperty(data, "products", "Sales catalog", true),
            varietyProducts: requireArrayProperty(data, "varietyProducts", "Sales catalog", true),
            sellableProducts: requireArrayProperty(data, "sellableProducts", "Sales catalog", true),
            packageSizes: requireArrayProperty(data, "packageSizes", "Sales catalog", true),
            prices: requireArrayProperty(data, "prices", "Sales catalog", true),
            stocks: requireArrayProperty(data, "stocks", "Sales catalog", true)
        };
        document.querySelectorAll(".sales-item-row").forEach(row => {
            const { product, variety } = selectedProductDetails(row);
            updateProductOptions(row, product, variety || row.dataset.variety || "");
        });
        return true;
    } catch (error) {
        console.error("Sales catalogue could not be loaded:", error);
        return false;
    }
}

addItemRow();
updateCustomerType();
calculateTotals();
renderHeldSales();
const initialSalesParams = new URLSearchParams(window.location.search);
const initialReturnExchangeId = Number(initialSalesParams.get("returnExchange"));
if (initialReturnExchangeId > 0) {
    returnExchangeBanner.hidden = false;
    document.getElementById("returnBillButton").hidden = true;
    paymentDetailsCard.hidden = true;
    document.querySelector(".sales-grand-total span").textContent = "Replacement Value";
}
loadSalesCatalog().then(() => {
    const returnId = initialReturnExchangeId;
    if (returnId > 0) initializeReturnExchangeMode(returnId, Number(initialSalesParams.get("heldExchange")) || null);
    else {
        const restoredFromBill = restoreSaleFromBill();
        if (!restoredFromBill) customerType.focus();
    }
});
