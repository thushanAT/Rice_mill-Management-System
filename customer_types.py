"""Customer-type display mapping for current and legacy sales records.

Database values remain unchanged. This module only classifies values for display
and cleans up historical generic walk-in names in API responses.
"""

from copy import deepcopy
import re


REGISTERED_CUSTOMER_LABEL = "Registered Customer"
RETAIL_CUSTOMER_LABEL = "Retail Customer"


def _key(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")

_REGISTERED_TYPES = {"registered", "registered_customer", "customer",}

_RETAIL_TYPES = {
    "retail",
    "retail_customer",
    "walk_in",
    "walkin",
    "walk_in_customer",
    "walkin_customer",
    "unregistered",
    "unregistered_customer",
}

_LEGACY_RETAIL_NAMES = _RETAIL_TYPES | {"registered_customer"}


def customer_type_label(value, registered_customer=False, retail_customer=False):
    """Return one of the two supported visible customer-type labels."""
    if registered_customer:
        return REGISTERED_CUSTOMER_LABEL
    if retail_customer:
        return RETAIL_CUSTOMER_LABEL
    if _key(value) in _REGISTERED_TYPES:
        return REGISTERED_CUSTOMER_LABEL
    # Unknown historical values are not evidence of a registered customer.
    return RETAIL_CUSTOMER_LABEL


def customer_name_for_display(
    name,
    customer_type,
    registered_customer=False,
    retail_customer=False,
):
    """Preserve entered/registered names while replacing generic legacy labels."""
    label = customer_type_label(customer_type, registered_customer, retail_customer)
    value = str(name or "").strip()
    if label == REGISTERED_CUSTOMER_LABEL:
        return value or label
    if label == RETAIL_CUSTOMER_LABEL and (not value or _key(value) in _LEGACY_RETAIL_NAMES):
        return RETAIL_CUSTOMER_LABEL
    return value or label


def customer_display(name, customer_type, registered_customer=False, retail_customer=False):
    """Return the name-only history value; classification is a separate field."""
    return customer_name_for_display(name, customer_type, registered_customer, retail_customer)


def customer_fields(name, customer_type, registered_customer=False, retail_customer=False):
    """Return standardized display fields while retaining the stored value."""
    label = customer_type_label(customer_type, registered_customer, retail_customer)
    return {
        "customerName": customer_name_for_display(
            name,
            customer_type,
            registered_customer,
            retail_customer,
        ),
        "customerType": customer_type,
        "customerTypeLabel": label,
        "isRegisteredCustomer": label == REGISTERED_CUSTOMER_LABEL,
        "customerDisplay": customer_display(
            name,
            customer_type,
            registered_customer,
            retail_customer,
        ),
    }


def resolve_transaction_customer(registered_customer, fallback_name, stored_type):
    """Resolve one transaction's customer from its valid Customer row or retail snapshot."""
    if registered_customer is not None:
        return customer_fields(registered_customer.full_name, stored_type, registered_customer=True)
    return customer_fields(fallback_name, stored_type, retail_customer=True)


def normalize_transaction_snapshot(snapshot, registered_customer=None):
    """Standardize customer labels in a copy of a stored return document."""
    result = deepcopy(snapshot) if isinstance(snapshot, dict) else {}
    invoice = result.get("originalInvoice")
    if isinstance(invoice, dict):
        customer_type = invoice.get("customerType")
        invoice.update(resolve_transaction_customer(
            registered_customer,
            invoice.get("customerName"),
            customer_type,
        ))
    return result
