(function (global) {
    "use strict";

    const REGISTERED = "Registered Customer";
    const RETAIL = "Retail Customer";
    const registeredTypes = new Set(["registered", "registered_customer", "customer"]);
    const legacyRetailTypes = new Set([
        "retail", "retail_customer", "walk_in", "walkin", "walk_in_customer",
        "walkin_customer", "unregistered", "unregistered_customer"
    ]);
    const genericRetailNames = new Set([
        "retail_customer", "walk_in", "walkin", "walk_in_customer", "walkin_customer",
        "unregistered", "unregistered_customer", "registered_customer"
    ]);

    function key(value) {
        return String(value ?? "").trim().toLowerCase()
            .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
    }

    function recordType(record) {
        if (!record || typeof record !== "object") return record;
        const storedType = record.customerType ?? record.customer_type;
        if (storedType !== undefined && storedType !== null && String(storedType).trim() !== "") {
            return storedType;
        }
        return record.customerTypeLabel ?? record.customer_type_label;
    }

    function typeLabel(value) {
        if (value && typeof value === "object" && typeof value.isRegisteredCustomer === "boolean") {
            return value.isRegisteredCustomer ? REGISTERED : RETAIL;
        }
        if (value && typeof value === "object") {
            const resolvedLabel = value.customerTypeLabel ?? value.customer_type_label;
            if (resolvedLabel !== undefined && resolvedLabel !== null && String(resolvedLabel).trim() !== "") {
                const labelKey = key(resolvedLabel);
                if (registeredTypes.has(labelKey)) return REGISTERED;
                if (legacyRetailTypes.has(labelKey) || labelKey === key(RETAIL)) return RETAIL;
            }
        }
        const rawType = recordType(value);
        return registeredTypes.has(key(rawType)) ? REGISTERED : RETAIL;
    }

    function customerName(record) {
        if (!record || typeof record !== "object") return "";
        const candidates = [record.customerName, record.customer_name, record.name, record.customer];
        let value = candidates.find(candidate => candidate !== undefined && candidate !== null &&
            String(candidate).trim() !== "");
        return String(value ?? "").trim();
    }

    function format(record) {
        const label = typeLabel(record);
        const name = customerName(record);
        if (label === RETAIL && (!name || genericRetailNames.has(key(name)))) return RETAIL;
        return name;
    }

    global.RiceCustomerDisplay = Object.freeze({ typeLabel, format });
})(window);
