from datetime import datetime, date, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
import hashlib
import hmac
import json
import re
import secrets
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from flask import Blueprint, request, jsonify, session, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import and_, case, cast, func, literal, or_, String
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from config.extensions import limiter
from models import (
    db,
    User,
    Setting,
    MasterItem,
    MainCategory,
    PriceSetting,
    PurchasePriceSetting,
    Supplier,
    Customer,
    Purchase,
    PurchaseCorrection,
    purchase_effective_values,
    Stock,
    StockMovement,
    ProductionRecord,
    Sale,
    SaleItem,
    HeldSale,
    SalesReturn,
    ReturnItem,
    ReturnApprovalEvent,
    ReturnApprovalNotification,
    ReturnInspectionEvent,
    ReturnSettlement,
    ReturnReplacementItem,
    ReturnFinancialEntry,
    CombinedTransactionDocument,
    Payment,
    PaymentAllocation,
    PaymentReversal,
    PaymentReversalAllocation,
    CustomerBalanceAdjustment,
    PasswordRecoveryToken,
    AuditLog,
)
from customer_types import (
    customer_fields,
    normalize_transaction_snapshot,
    resolve_transaction_customer,
)
from email_service import send_email as send_smtp_email, log_diagnostic as log_email_diagnostic
from timezone_utils import COLOMBO as COLOMBO_TIMEZONE, colombo_date, colombo_iso
from config.validation import (
    validate_person_name,
    validate_username,
    validate_and_normalize_email,
    validate_and_normalize_phone,
    validate_password_complexity,
    validate_positive_number,
)

api = Blueprint("api", __name__, url_prefix="/api")


def fail(message, status=400):
    return jsonify({"success": False, "message": message}), status


def val(data, key, default=None):
    return data.get(key, default)


def dec(value, label="value", minimum=None):
    try:
        n = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValueError(f"Invalid {label}.")
    if not n.is_finite():
        raise ValueError(f"Invalid {label}.")
    if minimum is not None and n < Decimal(str(minimum)):
        raise ValueError(f"{label} must be at least {minimum}.")
    return n


def as_float(v):
    return float(v or 0)


def current_user():
    uid = session.get("user_id")
    return db.session.get(User, uid) if uid else None


def login_required_api(fn):
    @wraps(fn)
    def wrapped(*a, **kw):
        user = current_user()
        if not user or not user.is_active or session.get(
            "auth_version",
            0,
        ) != (user.auth_version or 0):
            session.clear()
            return fail("Authentication required or account inactive.", 401)
        return fn(*a, **kw)
    return wrapped


def tab_required_api(module_name):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*a, **kw):
            user = current_user()
            if not user or not user.is_active or session.get(
                "auth_version",
                0,
            ) != (user.auth_version or 0):
                session.clear()
                return fail("Authentication required or account inactive.", 401)
            if user.role == "admin":
                return fn(*a, **kw)
            allowed = (user.permissions_json or {}).get("tabs", [])
            if module_name not in allowed:
                return fail("You do not have permission to access this module.", 403)
            return fn(*a, **kw)
        return wrapped
    return decorator

INVENTORY_SUBPERMISSION_KEYS = (
    "manageOpeningStockAdjustments",
    "inspectReturnedItems",
    "approveReturnBills",
)


def inventory_permission_required(permission_key):
    """Require Inventory Management plus one explicit employee capability."""

    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user or not user.is_active or session.get(
                "auth_version",
                0,
            ) != (user.auth_version or 0):
                session.clear()
                return fail("Authentication required or account inactive.", 401)
            if user.role == "admin":
                return fn(*args, **kwargs)
            permissions = user.permissions_json if isinstance(user.permissions_json, dict) else {}
            tabs = permissions.get("tabs", [])
            if not isinstance(tabs, list) or "Inventory Management" not in tabs:
                return fail("You do not have permission to access Inventory Management.", 403)
            inventory_permissions = permissions.get("inventory", {})
            if not isinstance(
                inventory_permissions,
                dict,
            ) or inventory_permissions.get(permission_key) is not True:
                return fail("You do not have permission for this Inventory action.", 403)
            return fn(*args, **kwargs)
        return wrapped
    return decorator


def admin_required_api(fn):
    @wraps(fn)
    def wrapped(*a, **kw):
        u = current_user()
        if not u or not u.is_active or session.get("auth_version", 0) != (u.auth_version or 0):
            session.clear()
            return fail("Authentication required or account inactive.", 401)
        if u.role != "admin":
            return fail("Administrator permission required.", 403)
        return fn(*a, **kw)
    return wrapped


def account_settings_required_api(fn):
    @wraps(fn)
    def wrapped(*a, **kw):
        u = current_user()
        if not u or not u.is_active or session.get("auth_version", 0) != (u.auth_version or 0):
            session.clear()
            return fail("Authentication required or account inactive.", 401)
        if u.role == "admin":
            return fn(*a, **kw)
        perms = u.permissions_json if isinstance(u.permissions_json, dict) else {}
        if perms.get("account_settings") is True or perms.get("accountSettings") is True:
            return fn(*a, **kw)
        return fail("You do not have permission to access Account Settings.", 403)
    return wrapped


def audit(action, entity, entity_id=None, details=None):
    db.session.add(AuditLog(
        user_id=current_user().id if current_user() else None,
        action=action,
        entity=entity,
        entity_id=entity_id,
        details=details or {},
    ))


def get_stock(item_name, stock_type, variety="", package_size=None):
    q = Stock.query.filter(
        Stock.item_name == item_name,
        Stock.stock_type == stock_type,
        func.coalesce(Stock.variety, "") == (variety or ""),
        func.coalesce(Stock.package_size_kg, 0) == (package_size or 0),
    )
    # Lock existing balances. The normalized unique key protects the absent-row
    # race; a savepoint lets the losing creator continue against the winner.
    stock = q.with_for_update().first()
    product = _stock_product_master(item_name, stock_type, variety)
    if stock is None and product:
        stock = Stock.query.filter_by(
            product_id=product.id,
            stock_type=stock_type,
            variety=variety or "",
            package_size_kg=package_size,
        ).with_for_update().first()
        if stock:
            stock.item_name = item_name
    if stock and stock.product_id is None and product:
        stock.product_id = product.id
    if not stock:
        try:
            with db.session.begin_nested():
                stock = Stock(
                    item_name=item_name,
                    stock_type=stock_type,
                    variety=variety or "",
                    quantity_kg=0,
                    quantity_bags=0,
                    package_size_kg=package_size,
                    product_id=product.id if product else None,
                )
                db.session.add(stock)
                db.session.flush()
        except IntegrityError:
            stock = q.with_for_update().first()
            if stock is None:
                raise
    return stock


def _stock_product_master(item_name, stock_type, variety=""):
    if stock_type in {"rice", "packed"}:
        return MasterItem.query.filter_by(
            group_name="rice_variety",
            name=variety,
            is_active=True,
        ).first()
    if stock_type in {"wet_paddy", "dry_paddy"}:
        return (MasterItem.query.filter_by(
            group_name="paddy_type",
            name=variety,
            is_active=True,
        ).first() or MasterItem.query.filter_by(group_name="rice_variety", name=variety, is_active=True).first())
    if stock_type in {"bran", "other"}:
        return MasterItem.query.filter_by(group_name="product", name=item_name).first()
    return None


def move_stock(
    item_name,
    stock_type,
    variety,
    kg_delta,
    bags_delta=0,
    package_size=None,
    movement="adjustment",
    ref_type=None,
    ref_id=None,
    notes=None,
    return_item_id=None,
    idempotency_key=None,
    production_record_id=None,
):
    stock = get_stock(item_name, stock_type, variety, package_size)
    new_kg = dec(stock.quantity_kg) + dec(kg_delta)
    new_bags = dec(stock.quantity_bags) + dec(bags_delta)
    if new_kg < 0 or new_bags < 0:
        raise ValueError(f"Insufficient stock for {item_name} ({stock_type}).")
    stock.quantity_kg = new_kg
    stock.quantity_bags = new_bags
    db.session.add(StockMovement(
        stock_id=stock.id,
        movement_type=movement,
        quantity_kg=kg_delta,
        quantity_bags=bags_delta,
        reference_type=ref_type,
        reference_id=ref_id,
        notes=notes,
        created_by=current_user().id if current_user() else None,
        return_item_id=return_item_id,
        idempotency_key=idempotency_key,
        production_record_id=production_record_id,
    ))
    return stock


def customer_json(c, outstanding=0):
    return {
        "id": c.id,
        "customerId": c.customer_code,
        "firstName": c.first_name,
        "lastName": c.last_name,
        "name": c.full_name,
        **customer_fields(c.full_name, "registered", registered_customer=True),
        "phone": c.phone,
        "shopName": c.shop_name,
        "creditLimit": str(Decimal(str(c.credit_limit or 0))),
        "outstanding": str(Decimal(str(outstanding or 0))),
        "address1": c.address1,
        "address2": c.address2,
        "city": c.city,
        "regDate": c.registered_on.isoformat() if c.registered_on else None,
        "active": c.is_active,
    }


def _sale_customer_map(sales):
    """Load valid registered customer relationships for a group of sales."""
    customer_ids = {sale.customer_id for sale in sales if sale.customer_id is not None}
    if not customer_ids:
        return {}
    return {customer.id: customer for customer in Customer.query.filter(Customer.id.in_(customer_ids)).all()}


def resolve_snapshot_customer(snapshot):
    """Resolve a saved document through its invoice relationship before snapshot fallbacks."""
    invoice = snapshot.get("originalInvoice") if isinstance(snapshot, dict) else None
    if not isinstance(invoice, dict):
        return None
    invoice_no = invoice.get("invoiceNo")
    if invoice_no:
        sale = Sale.query.filter_by(invoice_no=invoice_no).first()
        if sale and sale.customer_id is not None:
            customer = db.session.get(Customer, sale.customer_id)
            if customer:
                return customer
    customer_id = invoice.get("customerId")
    if isinstance(customer_id, bool) or customer_id in (None, ""):
        return None
    try:
        customer_id = int(customer_id)
    except (TypeError, ValueError):
        return None
    return db.session.get(Customer, customer_id) if customer_id > 0 else None


def _sale_customer_fields(sale, customers=None):
    """Use the linked Customer row as display authority, preserving saved type."""
    if customers is None:
        customer = db.session.get(
            Customer,
            sale.customer_id,
        ) if sale.customer_id is not None else None
    else:
        customer = customers.get(sale.customer_id) if sale.customer_id is not None else None
    return resolve_transaction_customer(customer, sale.customer_name, sale.customer_type)


def customer_outstanding(customer_id):
    return db.session.query(func.coalesce(
        func.sum(Sale.amount_due),
        0,
    )).filter_by(customer_id=customer_id).scalar()


def customer_account_balance(customer_id):
    """Invoice receivables plus separately recorded customer-level adjustments.

    Adjustments never change or allocate against an invoice. Sales credit-limit
    checks intentionally continue to use invoice outstanding only.
    """
    invoice_balance = Decimal(str(customer_outstanding(customer_id) or 0))
    adjustments= (db.session.query(func.coalesce(
        func.sum(CustomerBalanceAdjustment.amount),
        0,
    )).filter_by(customer_id=customer_id).scalar())
    return (invoice_balance + Decimal(str(adjustments or 0))).quantize(Decimal("0.01"))


def normalize_customer_phone(value):
    if not isinstance(value, str) or len(value.strip()) > 30:
        raise ValueError("Contact number must be text of 30 characters or fewer.")
    digits = re.sub(r"\D", "", value)
    if digits.startswith("00"):
        digits = digits[2:]
    if not 7 <= len(digits) <= 15:
        raise ValueError("Enter a contact number containing 7 to 15 digits.")
    if digits.startswith("94") and len(digits) == 11:
        return "+" + digits
    if digits.startswith("0") and len(digits) == 10:
        return "+94" + digits[1:]
    return digits


def customer_money(value, label="credit limit"):
    amount = dec(value, label, 0)
    if amount > Decimal("999999999999.99"):
        raise ValueError(f"{label} exceeds the supported amount range.")
    if amount.quantize(Decimal("0.01")) != amount:
        raise ValueError(f"{label} may have at most two decimal places.")
    return amount

PASSWORD_RECOVERY_PURPOSE = "password_reset"
PASSWORD_OTP_TTL = timedelta(minutes=5)
PASSWORD_RESET_TTL = timedelta(minutes=10)
PASSWORD_OTP_ATTEMPT_LIMIT = 5
PASSWORD_RECOVERY_COOLDOWN = timedelta(seconds=60)


def _recovery_email(value):
    if not isinstance(value, str):
        raise ValueError("Enter a valid email address.")
    email = value.strip().lower()
    if len(email) > 160 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("Enter a valid email address.")
    return email


def _recovery_hash(value):
    secret = str(current_app.config["SECRET_KEY"]).encode("utf-8")
    return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()


def _generic_recovery_response():
    return jsonify({
        "success": True,
        "message": ("If an active account uses that email address, the request was received. " "If delivery is available, recovery instructions may arrive shortly."),
    }), 202


def _issue_password_otp(data):
    if not isinstance(data, dict) or set(data) != {"email"}:
        return fail("A valid email address is required.")
    try:
        email = _recovery_email(data.get("email"))
    except ValueError as error:
        return fail(str(error))

    now = datetime.utcnow()
    try:
        user = User.query.filter(func.lower(User.email) == email).with_for_update().first()
        if not user or not user.is_active:
            db.session.rollback()
            return _generic_recovery_response()

        latest= (PasswordRecoveryToken.query.filter_by(
            user_id=user.id,
            purpose=PASSWORD_RECOVERY_PURPOSE,
        ).order_by(PasswordRecoveryToken.id.desc()).with_for_update().first())
        if latest and latest.requested_at and now - latest.requested_at < PASSWORD_RECOVERY_COOLDOWN:
            db.session.rollback()
            return _generic_recovery_response()

        otp = f"{secrets.randbelow(1_000_000):06d}"
        recovery = PasswordRecoveryToken(
            user_id=user.id,
            purpose=PASSWORD_RECOVERY_PURPOSE,
            status="pending",
            otp_hash=_recovery_hash(otp),
            requested_at=now,
            created_at=now,
            attempt_count=0,
        )
        db.session.add(recovery)
        db.session.flush()
        recovery_id = recovery.id
        user_id = user.id
        recipient = user.email
        db.session.commit()
    except SQLAlchemyError as error:
        db.session.rollback()
        current_app.logger.error(
            "Password recovery request failed; exception_type=%s",
            type(error).__name__,
        )
        return _generic_recovery_response()

    try:
        log_email_diagnostic("request")
        send_smtp_email(
            recipient,
            "Rice Mill password recovery code",
            f"Your password recovery code is {otp}. It expires in 5 minutes. " "If you did not request this code, you can ignore this email.",
        )
    except Exception as error:
        current_app.logger.warning(
            "Password recovery email delivery failed; exception_type=%s",
            type(error).__name__,
        )
        try:
            pending = PasswordRecoveryToken.query.filter_by(id=recovery_id).with_for_update().first()
            if pending and pending.status == "pending":
                pending.status = "failed"
                pending.otp_hash = None
                db.session.commit()
            else:
                db.session.rollback()
        except SQLAlchemyError:
            db.session.rollback()
        return _generic_recovery_response()

    try:
        user = User.query.filter_by(id=user_id).with_for_update().first()
        recovery = PasswordRecoveryToken.query.filter_by(id=recovery_id).with_for_update().first()
        if not user or not user.is_active or not recovery or recovery.status != "pending":
            if recovery and recovery.status == "pending":
                recovery.status = "revoked"
                recovery.revoked_at = datetime.utcnow()
                recovery.otp_hash = None
            db.session.commit()
            return _generic_recovery_response()

        issued_at = datetime.utcnow()
        (PasswordRecoveryToken.query.filter(
            PasswordRecoveryToken.user_id == user_id,
            PasswordRecoveryToken.purpose == PASSWORD_RECOVERY_PURPOSE,
            PasswordRecoveryToken.id != recovery_id,
            PasswordRecoveryToken.status.in_(("pending", "active", "verified")),
        ).update({PasswordRecoveryToken.status: "revoked", PasswordRecoveryToken.revoked_at: issued_at, PasswordRecoveryToken.otp_hash: None, PasswordRecoveryToken.reset_token_hash: None}, synchronize_session=False))
        recovery.status = "active"
        recovery.sent_at = issued_at
        recovery.otp_expires_at = issued_at + PASSWORD_OTP_TTL
        db.session.commit()
    except SQLAlchemyError as error:
        db.session.rollback()
        current_app.logger.error(
            "Password recovery issuance failed; exception_type=%s",
            type(error).__name__,
        )
    return _generic_recovery_response()


@api.post("/auth/forgot-password")
@limiter.limit("5 per hour")
def password_forgot_request():
    return _issue_password_otp(request.get_json(silent=True))


@api.post("/auth/forgot-password/resend")
@limiter.limit("3 per hour")
def password_forgot_resend():
    return _issue_password_otp(request.get_json(silent=True))


@api.post("/auth/verify-otp")
@limiter.limit("15 per hour")
def password_verify_otp():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"email", "otp"}:
        return fail("Enter the email address and 6-digit verification code.")
    try:
        email = _recovery_email(data.get("email"))
    except ValueError as error:
        return fail(str(error))
    otp = data.get("otp")
    if not isinstance(otp, str) or not re.fullmatch(r"\d{6}", otp):
        return fail("The code is invalid or expired. Request a new code.")

    try:
        user = User.query.filter(func.lower(User.email) == email).with_for_update().first()
        if not user or not user.is_active:
            db.session.rollback()
            return fail("The code is invalid or expired. Request a new code.", 400)
        recovery= (PasswordRecoveryToken.query.filter_by(
            user_id=user.id,
            purpose=PASSWORD_RECOVERY_PURPOSE,
            status="active",
        ).order_by(PasswordRecoveryToken.id.desc()).with_for_update().first())
        now = datetime.utcnow()
        if not recovery or not recovery.otp_expires_at or recovery.otp_expires_at <= now:
            if recovery:
                recovery.status = "expired"
                recovery.otp_hash = None
            db.session.commit()
            return fail("The code is invalid or expired. Request a new code.", 400)
        if recovery.attempt_count >= PASSWORD_OTP_ATTEMPT_LIMIT:
            recovery.status = "locked"
            recovery.otp_hash = None
            db.session.commit()
            return fail("The code is invalid or expired. Request a new code.", 400)
        if not hmac.compare_digest(recovery.otp_hash or "", _recovery_hash(otp)):
            recovery.attempt_count += 1
            if recovery.attempt_count >= PASSWORD_OTP_ATTEMPT_LIMIT:
                recovery.status = "locked"
                recovery.otp_hash = None
            db.session.commit()
            return fail("The code is invalid or expired. Request a new code.", 400)

        reset_token = secrets.token_urlsafe(32)
        recovery.status = "verified"
        recovery.otp_hash = None
        recovery.consumed_at = now
        recovery.reset_token_hash = _recovery_hash(reset_token)
        recovery.reset_expires_at = now + PASSWORD_RESET_TTL
        db.session.commit()
        return jsonify({
            "success": True,
            "resetToken": reset_token,
            "message": "Code verified. You can now choose a new password.",
        })
    except SQLAlchemyError as error:
        db.session.rollback()
        current_app.logger.error(
            "Password code verification failed; exception_type=%s",
            type(error).__name__,
        )
        return fail("The request could not be completed. Please try again.", 500)


@api.post("/auth/reset-password")
@limiter.limit("10 per hour")
def password_reset_complete():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"resetToken", "newPassword", "confirmPassword"}:
        return fail("A valid reset authorization and matching passwords are required.")
    token = data.get("resetToken")
    password = data.get("newPassword")
    confirmation = data.get("confirmPassword")
    if not isinstance(token, str) or not 32 <= len(token) <= 128:
        return fail("Reset authorization is invalid or expired. Request a new code.", 400)
    ok_p, err_p = validate_password_complexity(password)
    if not ok_p:
        return fail(err_p)
    if password != confirmation:
        return fail("Passwords do not match.")

    token_hash = _recovery_hash(token)
    try:
        candidate = PasswordRecoveryToken.query.filter_by(
            reset_token_hash=token_hash,
            purpose=PASSWORD_RECOVERY_PURPOSE,
        ).first()
        if not candidate:
            return fail("Reset authorization is invalid or expired. Request a new code.", 400)
        user = User.query.filter_by(id=candidate.user_id).with_for_update().first()
        recovery = PasswordRecoveryToken.query.filter_by(
            id=candidate.id,
            reset_token_hash=token_hash,
        ).with_for_update().first()
        now = datetime.utcnow()
        if (not user or not user.is_active or not recovery or recovery.status != "verified" or not recovery.reset_expires_at or recovery.reset_expires_at <= now):
            db.session.rollback()
            return fail("Reset authorization is invalid or expired. Request a new code.", 400)

        user.password_hash = generate_password_hash(password)
        user.auth_version = (user.auth_version or 0) + 1
        recovery.status = "consumed"
        recovery.consumed_at = now
        recovery.reset_token_hash = None
        (PasswordRecoveryToken.query.filter(
            PasswordRecoveryToken.user_id == user.id,
            PasswordRecoveryToken.purpose == PASSWORD_RECOVERY_PURPOSE,
            PasswordRecoveryToken.id != recovery.id,
            PasswordRecoveryToken.status.in_(("pending", "active", "verified")),
        ).update({PasswordRecoveryToken.status: "revoked", PasswordRecoveryToken.revoked_at: now, PasswordRecoveryToken.otp_hash: None, PasswordRecoveryToken.reset_token_hash: None}, synchronize_session=False))
        db.session.add(AuditLog(
            user_id=user.id,
            action="password_reset",
            entity="user",
            entity_id=user.id,
            details={"method": "email_recovery", "recovery_id": recovery.id},
        ))
        db.session.commit()
        return jsonify({"success": True, "message": "Password reset successfully. Please sign in."})
    except SQLAlchemyError as error:
        db.session.rollback()
        current_app.logger.error("Password reset failed; exception_type=%s", type(error).__name__)
        return fail("The password could not be reset. Please try again.", 500)


def _customer_text(data, key, current=None, required=False, limit=200):
    value = data.get(key, current)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text.")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{key} is required.")
    if len(value) > limit:
        raise ValueError(f"{key} must be {limit} characters or fewer.")
    return value


def _customer_phone_in_use(normalized, exclude_id=None):
    query = Customer.query.filter(
        Customer.phone_normalized == normalized,
        Customer.is_active.is_(True),
    )
    if exclude_id is not None:
        query = query.filter(Customer.id != exclude_id)
    return query.first() is not None


def _customer_history_params():
    try:
        page = int(request.args.get("page", "1"))
        per_page = int(request.args.get("perPage", "25"))
    except (TypeError, ValueError):
        raise ValueError("Page and perPage must be whole numbers.")
    if page < 1 or not 1 <= per_page <= 100:
        raise ValueError("Page must be positive and perPage must be between 1 and 100.")

    def parse_date(name):
        raw = request.args.get(name)
        if not raw:
            return None
        try:
            parsed = date.fromisoformat(raw)
            if parsed.isoformat() != raw:
                raise ValueError
            return parsed
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be a valid ISO date.")
    start, end = parse_date("dateFrom"), parse_date("dateTo")
    if start and end and start > end:
        raise ValueError("dateFrom cannot be after dateTo.")
    return page, per_page, start, end


def sale_json(s, customers=None):
    return {
        "id": s.id,
        "invoiceNo": s.invoice_no,
        "customerId": s.customer_id,
        **_sale_customer_fields(s, customers),
        "date": _payment_local_datetime(s.sale_date),
        "subtotal": as_float(s.subtotal),
        "amount": as_float(s.total_amount),
        "paid": as_float(s.amount_paid),
        "amountAppliedToInvoice": as_float(s.amount_paid),
        "received": as_float(getattr(
            s,
            "amount_received",
            None,
        ) if getattr(s, "amount_received", None) is not None else s.amount_paid),
        "customerBalance": as_float(max(
            Decimal("0"),
            Decimal(str((getattr(s, "amount_received", None) if getattr(s, "amount_received", None) is not None else s.amount_paid))) - Decimal(str(s.total_amount)),
        )),
        "due": as_float(s.amount_due),
        "paymentType": s.payment_type,
        "paymentMethod": s.payment_method,
        "status": s.status,
        "items": [{
            "id": i.id,
            "productId": i.product_id,
            "stockType": i.stock_type,
            "category": i.category,
            "product": i.product_name,
            "variety": i.variety,
            "quantity": as_float(i.quantity),
            "unit": i.unit,
            "packageSize": i.package_size_kg,
            "totalKg": as_float(i.total_kg),
            "price": as_float(i.price_per_kg),
            "lineTotal": as_float(i.line_total),
        } for i in s.items],
    }


def sale_money(value, label="amount", minimum=None):
    amount = dec(value, label, minimum)
    try:
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise ValueError(f"Invalid {label}.")


def _sale_idempotency_values(data, user_id):
    header_key = (request.headers.get("Idempotency-Key") or "").strip()
    body_key = (data.get("idempotencyKey") or data.get("invoiceNo") or "")
    if not isinstance(body_key, str):
        raise ValueError("Idempotency key must be text.")
    body_key = body_key.strip()
    if header_key and body_key and header_key != body_key:
        raise ValueError("Header and body idempotency keys do not match.")
    key = header_key or body_key
    if not 16 <= len(key) <= 200:
        raise ValueError("A stable idempotency key of 16 to 200 characters is required.")

    namespace = uuid5(NAMESPACE_URL, f"rice-mill-user:{user_id}")
    invoice_no = f"INV-{uuid5(namespace,key).hex.upper()}"
    fingerprint_data = {k: v for k, v in data.items() if k not in {"invoiceNo", "idempotencyKey"}}
    canonical = json.dumps(
        fingerprint_data,
        sort_keys=True,
        separators= (",", ":"),
        ensure_ascii=False,
    )
    request_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return invoice_no, request_hash


def _find_idempotent_sale(invoice_no, user_id, request_hash):
    existing = Sale.query.filter_by(invoice_no=invoice_no).first()
    if not existing:
        return None
    if existing.created_by != user_id:
        return fail("This invoice request conflicts with an existing invoice.", 409)
    records = AuditLog.query.filter_by(
        entity="sale",
        entity_id=existing.id,
        action="create",
    ).order_by(AuditLog.id.desc()).all()
    matching = any((row.details or {}).get("request_hash") == request_hash for row in records)
    if not matching:
        return fail("This idempotency key was already used with different sale details.", 409)
    return jsonify({"success": True, "sale": sale_json(existing), "replayed": True}), 200


def _active_master_names(group_name):
    return {row.name for row in MasterItem.query.filter_by(
        group_name=group_name,
        is_active=True,
    ).all()}


def _configured_package_sizes():
    sizes = set()
    for label in _active_master_names("package_size"):
        match = re.fullmatch(r"\s*(\d+)\s*(?:kg)?\s*", label, re.IGNORECASE)
        if match and int(match.group(1)) > 0:
            sizes.add(Decimal(match.group(1)))
    return sizes or {Decimal("10"), Decimal("25"), Decimal("50")}


def _prepare_sale_items(items):
    if not isinstance(items, list) or not items:
        raise ValueError("At least one sale item is required.")
    rice_varieties = _active_master_names("rice_variety")
    products = _active_master_names("product")
    prepared = []
    subtotal = Decimal("0.00")

    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Sale item {index} must be an object.")
        category = item.get("category")
        if not isinstance(category, str) or category not in {"bulk", "packed", "bran", "other"}:
            raise ValueError(f"Sale item {index} has an invalid category.")
        product = item.get("product") or item.get("productName")
        if not isinstance(product, str) or not product.strip():
            raise ValueError(f"Select a valid product for sale item {index}.")
        product = product.strip()
        try:
            product_id = int(item.get("productId")) if item.get("productId") not in (
                None,
                "",
            ) else None
        except (TypeError, ValueError):
            raise ValueError(f"Sale item {index} has an invalid product ID.")

        variety = item.get("variety")
        if variety is not None and not isinstance(variety, str):
            raise ValueError(f"Sale item {index} variety must be text.")
        variety = variety.strip() if isinstance(variety, str) else ""

        if category in {"bulk", "packed"}:
            master = db.session.get(MasterItem, product_id) if product_id else None
            if (
                master
                and (master.group_name != "rice_variety" or master.name != variety or not master.is_active or category not in {c.code for c in master.main_categories if c.is_active})
            ):
                raise ValueError(f"Product {product} is not active under the selected category.")
            if not variety:
                if product in rice_varieties:
                    variety = product
                elif product.endswith(" Rice") and product[:-5] in rice_varieties:
                    variety = product[:-5]
                else:
                    raise ValueError(f"Product {product} is not an active rice variety.")
            if variety not in rice_varieties or product not in {variety, f"{variety} Rice"}:
                raise ValueError(f"Product and variety do not match an active rice variety: {product}.")
            master = master or MasterItem.query.filter_by(
                group_name="rice_variety",
                name=variety,
                is_active=True,
            ).first()
            if not master:
                raise ValueError(f"Product {product} is not active in Administration.")
            stock_item = f"{variety} Rice"
            stock_type = "packed" if category == "packed" else "rice"
            price_key = variety
        else:
            master = db.session.get(MasterItem, product_id) if product_id else MasterItem.query.filter_by(
                group_name="product",
                name=product,
                is_active=True,
            ).first()
            if (
                product not in products
                or not master
                or master.group_name != "product"
                or master.name != product
                or not master.is_active
            ):
                raise ValueError(f"Product {product} is not active in Administration.")
            if "other" not in {c.code for c in master.main_categories if c.is_active}:
                raise ValueError(f"Product {product} is not assigned to Other Sellable Products.")
            stock_item = product
            stock_type = "bran" if category == "bran" else item.get("stockType", "other")
            if stock_type not in {"bran", "other"}:
                raise ValueError("Select valid inventory stock for this sellable product.")
            if stock_type == "bran":
                if not variety or variety not in rice_varieties:
                    raise ValueError("Select an active rice variety for bran stock.")
            elif variety:
                raise ValueError("Variety is not applicable to this product.")
            price_key = product

        quantity = dec(item.get("quantity"), f"sale item {index} quantity", Decimal("0.001"))
        package_size = None
        if category == "packed":
            package_value = item.get("packageSize", item.get("package"))
            if (
                "packageSize" in item
                and "package" in item
                and str(item["packageSize"]) != str(item["package"])
            ):
                raise ValueError(f"Sale item {index} has conflicting package sizes.")
            try:
                package_decimal = dec(package_value, f"sale item {index} package size")
            except ValueError:
                raise ValueError("Packed rice package size must be 10, 25 or 50 kg.")
            if package_decimal not in _configured_package_sizes():
                raise ValueError("Packed rice package size must match an active configured package size.")
            package_size = int(package_decimal)
            if quantity != quantity.to_integral_value():
                raise ValueError("Packed rice quantity must be a whole number of bags.")
            unit = "bags"
        else:
            if item.get("packageSize") not in (None, "") or item.get("package") not in (None, ""):
                raise ValueError(f"Package size is not valid for sale item {index}.")
            unit = "kg"

        supplied_unit = item.get("unit")
        if supplied_unit is not None and str(supplied_unit).strip().lower() not in ({
            "bag",
            "bags",
        } if category == "packed" else {"kg"}):
            raise ValueError(f"Sale item {index} unit must be {unit}.")

        price_setting = PriceSetting.query.filter_by(
            item_name=price_key,
            price_type="selling",
            is_active=True,
        ).with_for_update().first()
        configured_price= (sale_money(
            price_setting.default_price,
            "configured selling price",
            Decimal("0.01"),
        ) if price_setting and price_setting.default_price is not None else None)
        supplied_price = item.get("price")
        price_locked = bool(price_setting and price_setting.selling_locked and configured_price is not None)
        if configured_price is not None:
            if supplied_price not in (None, ""):
                submitted = sale_money(supplied_price, f"sale item {index} price", Decimal("0.01"))
                if price_locked and submitted != configured_price:
                    raise ValueError(f"Configured selling price for {price_key} has changed. Refresh the sale and try again.")
                price = configured_price if price_locked or supplied_price in (
                    None,
                    "",
                ) else submitted
            else:
                price = configured_price
        else:
            if supplied_price in (None, ""):
                raise ValueError(f"Enter a selling price for {price_key}; no default price is configured.")
            price = sale_money(supplied_price, f"sale item {index} price", Decimal("0.01"))

        total_kg = quantity * Decimal(package_size) if category == "packed" else quantity
        line_total = sale_money(total_kg * price, f"sale item {index} total", Decimal("0.01"))
        subtotal += line_total
        prepared.append({
            "category": category,
            "product": product,
            "product_id": master.id,
            "stock_item": stock_item,
            "stock_type": stock_type,
            "variety": variety,
            "quantity": quantity,
            "unit": unit,
            "package_size": package_size,
            "total_kg": total_kg,
            "price": price,
            "line_total": line_total,
        })
    return prepared, subtotal


@api.get("/me")
@login_required_api
def me():
    u = current_user()
    return jsonify({
        "success": True,
        "user": {
            "id": u.id,
            "username": u.username,
            "firstName": u.first_name,
            "lastName": u.last_name,
            "email": u.email,
            "contactNumber": u.contact_number,
            "role": u.role,
            "active": u.is_active,
        },
    })


@api.get("/customers")
@login_required_api
@tab_required_api("Customer Management")
def customers_list():
    rows = db.session.query(
        Customer,
        func.coalesce(func.sum(Sale.amount_due), 0),
    ).outerjoin(Sale, Sale.customer_id == Customer.id).group_by(Customer.id).order_by(Customer.id.desc()).all()
    return jsonify({"success": True, "customers": [customer_json(c, due) for c, due in rows]})


@api.post("/customers")
@limiter.limit("20 per minute")
@login_required_api
@tab_required_api("Customer Management")
def customer_create():
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return fail("A JSON customer request is required.")
    try:
        allowed = {
            "firstName",
            "lastName",
            "phone",
            "shopName",
            "creditLimit",
            "address1",
            "address2",
            "city",
            "regDate",
        }
        if set(d) - allowed:
            raise ValueError("Unexpected customer request field.")
        ok_f, err_f, first = validate_person_name(d.get("firstName"), "First Name", required=True)
        if not ok_f:
            raise ValueError(err_f)
        ok_l, err_l, last = validate_person_name(d.get("lastName"), "Last Name", required=True)
        if not ok_l:
            raise ValueError(err_l)
        normalized = normalize_customer_phone(d.get("phone"))
        shop = _customer_text(d, "shopName", limit=160)
        address1 = _customer_text(d, "address1", limit=200)
        address2 = _customer_text(d, "address2", limit=200)
        city = _customer_text(d, "city", limit=100)
        credit_limit = customer_money(d.get("creditLimit", 0))
        raw_date = d.get("regDate")
        if raw_date:
            if not isinstance(raw_date, str):
                raise ValueError("Registration date must be a valid ISO date.")
            registered_on = date.fromisoformat(raw_date)
            if registered_on.isoformat() != raw_date:
                raise ValueError("Registration date must be a valid ISO date.")
        else:
            registered_on = colombo_date()
        if _customer_phone_in_use(normalized):
            db.session.rollback()
            return fail("This contact number is already registered to an active customer.", 409)
        c = Customer(
            customer_code=f"CUS-{uuid4().hex[:12].upper()}",
            first_name=first,
            last_name=last,
            phone=normalized,
            phone_normalized=normalized,
            shop_name=shop or None,
            credit_limit=credit_limit,
            address1=address1 or None,
            address2=address2 or None,
            city=city or None,
            registered_on=registered_on,
        )
        db.session.add(c)
        db.session.flush()
        audit("create", "customer", c.id)
        db.session.commit()
        return jsonify({"success": True, "customer": customer_json(c, 0)}), 201
    except ValueError as e:
        db.session.rollback()
        return fail(str(e))
    except IntegrityError:
        db.session.rollback()
        return fail(
            "This contact number or customer code conflicts with an existing customer.",
            409,
        )
    except SQLAlchemyError as error:
        db.session.rollback()
        current_app.logger.error(
            "Customer creation failed; exception_type=%s",
            type(error).__name__,
        )
        return fail("The customer could not be saved. Please try again.", 500)


@api.put("/customers/<int:cid>")
@limiter.limit("30 per minute")
@login_required_api
@tab_required_api("Customer Management")
def customer_update(cid):
    c = Customer.query.filter_by(id=cid).with_for_update().first()
    if not c:
        return fail("Customer not found.", 404)
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        db.session.rollback()
        return fail("A JSON customer request is required.")
    try:
        allowed = {
            "firstName",
            "lastName",
            "phone",
            "shopName",
            "creditLimit",
            "address1",
            "address2",
            "city",
            "regDate",
        }
        if set(d) - allowed:
            raise ValueError("Unexpected customer request field.")
        raw_first = d.get("firstName", c.first_name)
        ok_f, err_f, first = validate_person_name(raw_first, "First Name", required=True)
        if not ok_f:
            raise ValueError(err_f)
        raw_last = d.get("lastName", c.last_name)
        ok_l, err_l, last = validate_person_name(raw_last, "Last Name", required=True)
        if not ok_l:
            raise ValueError(err_l)
        normalized = normalize_customer_phone(d.get("phone", c.phone))
        if c.is_active and _customer_phone_in_use(normalized, c.id):
            db.session.rollback()
            return fail("This contact number is already registered to an active customer.", 409)
        c.first_name = first
        c.last_name = last
        c.phone = normalized
        c.phone_normalized = normalized
        for key, attr, limit in [
            ("shopName", "shop_name", 160),
            ("address1", "address1", 200),
            ("address2", "address2", 200),
            ("city", "city", 100),
        ]:
            if key in d:
                value = _customer_text(d, key, limit=limit)
                setattr(c, attr, value or None)
        if "creditLimit" in d:
            c.credit_limit = customer_money(d["creditLimit"])
        if "regDate" in d:
            raw_date = d["regDate"]
            if not isinstance(raw_date, str):
                raise ValueError("Registration date must be a valid ISO date.")
            c.registered_on = date.fromisoformat(raw_date)
            if c.registered_on.isoformat() != raw_date:
                raise ValueError("Registration date must be a valid ISO date.")
        due = customer_outstanding(c.id)
        if Decimal(str(due)) > Decimal(c.credit_limit):
            db.session.rollback()
            return fail("Credit limit cannot be lower than the current outstanding balance.", 409)
        audit("update", "customer", c.id)
        db.session.commit()
        return jsonify({"success": True, "customer": customer_json(c, due)})
    except ValueError as e:
        db.session.rollback()
        return fail(str(e))
    except IntegrityError:
        db.session.rollback()
        return fail("This contact number conflicts with an active customer.", 409)
    except SQLAlchemyError as error:
        db.session.rollback()
        current_app.logger.error("Customer update failed; exception_type=%s", type(error).__name__)
        return fail("The customer could not be updated. Please try again.", 500)


@api.delete("/customers/<int:cid>")
@login_required_api
@tab_required_api("Customer Management")
def customer_deactivate(cid):
    return _set_customer_active(cid, False)


@api.patch("/customers/<int:cid>/status")
@login_required_api
@tab_required_api("Customer Management")
def customer_status_update(cid):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("active"), bool):
        return fail("A boolean active value is required.")
    return _set_customer_active(cid, data["active"])


def _set_customer_active(cid, active):
    customer = Customer.query.filter_by(id=cid).with_for_update().first()
    if not customer:
        db.session.rollback()
        return fail("Customer not found.", 404)
    if active and not customer.is_active and _customer_phone_in_use(
        customer.phone_normalized,
        customer.id,
    ):
        db.session.rollback()
        return fail("Another active customer already uses this contact number.", 409)
    try:
        customer.is_active = active
        audit("activate" if active else "deactivate", "customer", cid, {"active": active})
        db.session.commit()
        return jsonify({
            "success": True,
            "customer": customer_json(customer, customer_outstanding(cid)),
        })
    except IntegrityError:
        db.session.rollback()
        return fail("Another active customer already uses this contact number.", 409)
    except SQLAlchemyError as error:
        db.session.rollback()
        current_app.logger.error(
            "Customer status change failed; exception_type=%s",
            type(error).__name__,
        )
        return fail("The customer status could not be changed. Please try again.", 500)


@api.get("/customers/<int:cid>/invoices")
@login_required_api
@tab_required_api("Customer Management")
def customer_invoice_history(cid):
    registered_customer = db.session.get(Customer, cid)
    if not registered_customer:
        return fail("Customer not found.", 404)
    try:
        page, per_page, date_from, date_to = _customer_history_params()
    except ValueError as error:
        return fail(str(error))
    query = Sale.query.filter(Sale.customer_id == cid)
    if date_from:
        query = query.filter(Sale.sale_date >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        query = query.filter(Sale.sale_date <= datetime.combine(date_to, datetime.max.time()))
    total = query.count()
    rows = query.order_by(
        Sale.sale_date.desc(),
        Sale.id.desc(),
    ).offset((page - 1) * per_page).limit(per_page).all()
    return jsonify({
        "success": True,
        "customerId": cid,
        **customer_fields(registered_customer.full_name, "registered", registered_customer=True),
        "invoices": [{
            "id": sale.id,
            "invoiceNo": sale.invoice_no,
            "date": sale.sale_date.date().isoformat(),
            "customerId": sale.customer_id,
            **_sale_customer_fields(sale, {cid: registered_customer}),
            "status": sale.status,
            "paymentType": sale.payment_type,
            "paymentMethod": sale.payment_method,
            "total": str(sale.total_amount),
            "paid": str(sale.amount_paid),
            "due": str(sale.amount_due),
            "items": [{"product": item.product_name, "category": item.category, "variety": item.variety, "quantity": str(item.quantity), "unit": item.unit, "lineTotal": str(item.line_total)} for item in sale.items],
        } for sale in rows],
        "pagination": {
            "page": page,
            "perPage": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page,
        },
    })


@api.get("/customers/<int:cid>/payments")
@login_required_api
@tab_required_api("Customer Management")
def customer_payment_history(cid):
    customer = db.session.get(Customer, cid)
    if not customer:
        return fail("Customer not found.", 404)
    try:
        page, per_page, date_from, date_to = _customer_history_params()
    except ValueError as error:
        return fail(str(error))
    query = Payment.query.filter(Payment.customer_id == cid)
    if date_from:
        query = query.filter(Payment.payment_date >= date_from)
    if date_to:
        query = query.filter(Payment.payment_date <= date_to)
    total = query.count()
    payments = query.order_by(
        Payment.payment_date.desc(),
        Payment.id.desc(),
    ).offset((page - 1) * per_page).limit(per_page).all()
    result = []
    for payment in payments:
        allocations= (db.session.query(PaymentAllocation, Sale).join(Sale, Sale.id == PaymentAllocation.sale_id).filter(
            PaymentAllocation.payment_id == payment.id,
            Sale.customer_id == cid,
        ).order_by(Sale.sale_date, Sale.id, PaymentAllocation.id).all())
        result.append({
            "id": payment.id,
            "receiptNo": payment.receipt_no,
            "date": payment.payment_date.isoformat(),
            "amount": str(payment.amount),
            "method": payment.method,
            "bankReference": payment.bank_reference,
            "allocationMethod": payment.allocation_method,
            "note": payment.note,
            "allocations": [{
                "invoiceId": sale.id,
                "invoiceNo": sale.invoice_no,
                "amount": str(allocation.amount),
            } for allocation, sale in allocations],
        })
    return jsonify({
        "success": True,
        "customerId": cid,
        **customer_fields(customer.full_name, "registered", registered_customer=True),
        "payments": result,
        "pagination": {
            "page": page,
            "perPage": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page,
        },
    })


@api.get("/customers/<int:cid>/statement")
@login_required_api
@tab_required_api("Customer Management")
def customer_statement(cid):
    customer = db.session.get(Customer, cid)
    if not customer:
        return fail("Customer not found.", 404)
    try:
        page, per_page, date_from, date_to = _customer_history_params()
    except ValueError as error:
        return fail(str(error))

    events = []
    sales = Sale.query.filter(Sale.customer_id == cid).all()
    sale_by_id = {sale.id: sale for sale in sales}
    for sale in sales:
        events.append({
            "sortDate": sale.sale_date,
            "sortType": 0,
            "sortId": sale.id,
            "date": sale.sale_date.date().isoformat(),
            "type": "invoice",
            "reference": sale.invoice_no,
            "invoiceNo": sale.invoice_no,
            "amount": str(sale.total_amount),
            "debit": sale.total_amount,
            "credit": Decimal("0.00"),
            "method": None,
            "balanceAffecting": True,
        })

    allocations= (db.session.query(PaymentAllocation, Payment, Sale).join(Payment, Payment.id == PaymentAllocation.payment_id).join(Sale, Sale.id == PaymentAllocation.sale_id).filter(
        Payment.customer_id == cid,
        Sale.customer_id == cid,
    ).all())
    for allocation, payment, sale in allocations:
        when = datetime.combine(payment.payment_date, datetime.min.time())
        events.append({
            "sortDate": when,
            "sortType": 1,
            "sortId": allocation.id,
            "date": payment.payment_date.isoformat(),
            "type": "payment",
            "reference": payment.receipt_no,
            "invoiceNo": sale.invoice_no,
            "amount": str(allocation.amount),
            "debit": Decimal("0.00"),
            "credit": allocation.amount,
            "method": payment.method,
            "balanceAffecting": True,
        })

    reversals= (db.session.query(PaymentReversalAllocation, PaymentReversal, Sale).join(PaymentReversal, PaymentReversal.id == PaymentReversalAllocation.reversal_id).join(Sale, Sale.id == PaymentReversalAllocation.sale_id).join(Payment, Payment.id == PaymentReversal.original_payment_id).filter(
        Payment.customer_id == cid,
        Sale.customer_id == cid,
    ).all())
    for reversal_allocation, reversal, sale in reversals:
        when = reversal.created_at
        events.append({
            "sortDate": when,
            "sortType": 2,
            "sortId": reversal_allocation.id,
            "date": when.date().isoformat(),
            "type": "payment_reversal",
            "reference": reversal.reversal_no,
            "invoiceNo": sale.invoice_no,
            "amount": str(reversal_allocation.amount),
            "debit": reversal_allocation.amount,
            "credit": Decimal("0.00"),
            "method": None,
            "balanceAffecting": True,
        })

    adjustments = CustomerBalanceAdjustment.query.filter_by(customer_id=cid).all()
    for adjustment in adjustments:
        when = adjustment.created_at
        debit = max(Decimal("0.00"), Decimal(str(adjustment.amount)))
        credit = max(Decimal("0.00"), -Decimal(str(adjustment.amount)))
        events.append({
            "sortDate": when,
            "sortType": 4,
            "sortId": adjustment.id,
            "date": when.date().isoformat(),
            "type": "customer_balance_adjustment",
            "reference": f"ADJ-{adjustment.id}",
            "invoiceNo": None,
            "amount": str(adjustment.amount),
            "debit": debit,
            "credit": credit,
            "method": None,
            "balanceAffecting": True,
        })

    return_entries= (db.session.query(ReturnFinancialEntry, ReturnSettlement, ReturnItem, SalesReturn).join(
        ReturnSettlement,
        ReturnSettlement.id == ReturnFinancialEntry.settlement_id,
    ).join(ReturnItem, ReturnItem.id == ReturnSettlement.return_item_id).join(SalesReturn, SalesReturn.id == ReturnItem.return_id).filter(SalesReturn.customer_id == cid).all())
    for entry, settlement, item, return_record in return_entries:
        when = entry.created_at or return_record.return_date
        affects_balance = entry.entry_type == "outstanding_adjustment"
        sale = sale_by_id.get(entry.sale_id or return_record.sale_id)
        events.append({
            "sortDate": when,
            "sortType": 3,
            "sortId": entry.id,
            "date": when.date().isoformat(),
            "type": "return_" + entry.entry_type,
            "reference": return_record.return_no,
            "invoiceNo": sale.invoice_no if sale else None,
            "amount": str(entry.amount),
            "debit": Decimal("0.00"),
            "credit": entry.amount if affects_balance else Decimal("0.00"),
            "method": entry.method,
            "balanceAffecting": affects_balance,
        })

    events.sort(key=lambda row: (row["sortDate"], row["sortType"], row["sortId"]))
    balance = Decimal("0.00")
    for row in events:
        balance += row["debit"] - row["credit"]
        row["balanceAfter"] = balance
    filtered = [row for row in events if (not date_from or row["date"] >= date_from.isoformat()) and (not date_to or row["date"] <= date_to.isoformat())]
    total = len(filtered)
    selected = filtered[(page - 1) * per_page:page * per_page]
    current_outstanding = Decimal(str(customer_outstanding(cid) or 0))
    account_balance = customer_account_balance(cid)
    statement_balance = balance
    difference = account_balance - statement_balance
    records=[{
        "date": row["date"],
        "type": row["type"],
        "reference": row["reference"],
        "invoiceNo": row["invoiceNo"],
        "amount": row["amount"],
        "debit": str(row["debit"]),
        "credit": str(row["credit"]),
        "balanceAfter": str(row["balanceAfter"]),
        "method": row["method"],
        "balanceAffecting": row["balanceAffecting"],
    } for row in selected]
    return jsonify({
        "success": True,
        "customerId": cid,
        **customer_fields(customer.full_name, "registered", registered_customer=True),
        "records": records,
        "currentOutstanding": str(current_outstanding),
        "statementBalance": str(statement_balance),
        "customerAdjustmentsBalance": str(account_balance - current_outstanding),
        "customerAccountBalance": str(account_balance),
        "reconciliationDifference": str(difference),
        "pagination": {
            "page": page,
            "perPage": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page,
        },
    })


@api.post("/purchases")
@limiter.limit("20 per minute")
@login_required_api
@tab_required_api("Paddy Purchase")
def purchase_create():
    d = request.get_json(silent=True) or {}
    if not isinstance(d, dict):
        return fail("A JSON purchase request is required.")
    user = current_user()
    try:
        header_key = (request.headers.get("Idempotency-Key") or "").strip()
        body_key = d.get("idempotencyKey", "")
        if not isinstance(body_key, str):
            raise ValueError("Idempotency key must be text.")
        body_key = body_key.strip()
        if header_key and body_key and header_key != body_key:
            raise ValueError("Header and body idempotency keys do not match.")
        key = header_key or body_key
        if not 16 <= len(key) <= 100:
            raise ValueError("A stable idempotency key of 16 to 100 characters is required.")
        canonical = json.dumps(
            {k: v for k, v in d.items() if k != "idempotencyKey"},
            sort_keys=True,
            separators= (",", ":"),
            ensure_ascii=False,
        )
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        existing = Purchase.query.filter_by(idempotency_key=key).first()
        if existing:
            if existing.created_by != user.id or existing.request_fingerprint != fingerprint:
                return fail(
                    "This idempotency key was already used for different purchase details.",
                    409,
                )
            return jsonify({
                "success": True,
                "purchase": _purchase_json(existing),
                "replayed": True,
            }), 200

        qty = dec(d.get("quantity"), "quantity", 0.001)
        if d.get("paymentMethod", "Cash") != "Cash":
            return fail("Paddy purchases must be paid in cash.")
        ptype = d.get("purchaseType", "Wet Paddy")
        variety = d.get("variety", "")
        seller = d.get("sellerName", "")
        if not isinstance(ptype, str) or ptype not in {"Wet Paddy", "Dry Paddy"}:
            return fail("Select a valid paddy type.")
        if not isinstance(variety, str) or not variety.strip():
            return fail("Paddy variety is required.")
        if not isinstance(seller, str):
            return fail("Seller name must be text.")
        raw_variety_id = d.get("varietyId")
        if type(raw_variety_id) is not int or raw_variety_id <= 0:
            return fail("Select a rice variety from Administration and submit its variety ID.")
        variety_master = MasterItem.query.filter_by(
            id=raw_variety_id,
            group_name="rice_variety",
            is_active=True,
        ).with_for_update().first()
        if not variety_master:
            return fail("The selected rice variety is inactive or unavailable.")
        if variety_master.name != variety.strip():
            return fail("The selected variety does not match its Administration record.")
        setting= (PurchasePriceSetting.query.filter_by(
            variety_id=variety_master.id,
            paddy_type=ptype,
            is_active=True,
        ).with_for_update().first())
        configured= (dec(
            setting.default_price,
            "configured purchase price",
            Decimal("0.01"),
        ) if setting and setting.default_price is not None else None)
        supplied = d.get("price")
        if configured is None:
            if supplied in (None, ""):
                return fail(f"Enter a {ptype.lower()} purchase price for {variety.strip()}; no default price is configured.")
            rate = dec(supplied, "price", Decimal("0.01"))
        elif setting.is_locked:
            if supplied not in (None, "") and dec(supplied, "price", Decimal("0.01")) != configured:
                return fail(f"Configured {ptype.lower()} purchase price for {variety.strip()} is locked. Refresh the purchase and try again.")
            rate = configured
        else:
            rate = dec(
                supplied,
                "price",
                Decimal("0.01"),
            ) if supplied not in (None, "") else configured
        p = Purchase(
            purchase_no=f"PUR-{datetime.now(COLOMBO_TIMEZONE):%Y%m%d%H%M%S%f}",
            idempotency_key=key,
            request_fingerprint=fingerprint,
            purchase_type=ptype,
            variety_id=variety_master.id,
            variety=variety_master.name,
            seller_name=seller.strip() or "Not Provided",
            quantity_kg=qty,
            rate_per_kg=rate,
            total_amount=qty * rate,
            purchase_date=date.fromisoformat(d["date"]) if d.get("date") else colombo_date(),
            payment_method=d.get("paymentMethod", "Cash"),
            notes=d.get("notes"),
            created_by=current_user().id,
        )
        if not p.variety or not p.seller_name:
            return fail("Paddy variety and seller name are required.")
        db.session.add(p)
        db.session.flush()
        stype = "wet_paddy" if "wet" in p.purchase_type.lower() else "dry_paddy"
        move_stock(
            p.variety,
            stype,
            p.variety,
            qty,
            movement="purchase",
            ref_type="purchase",
            ref_id=p.id,
        )
        audit("create", "purchase", p.id)
        db.session.commit()
        return jsonify({"success": True, "purchase": _purchase_json(p), "replayed": False}), 201
    except (ValueError, KeyError) as e:
        db.session.rollback()
        return fail(str(e) if isinstance(e, ValueError) else "Required request data is missing.")
    except IntegrityError:
        db.session.rollback()
        existing = Purchase.query.filter_by(idempotency_key=key).first() if "key" in locals() else None
        if existing:
            if existing.created_by == user.id and existing.request_fingerprint == fingerprint:
                return jsonify({
                    "success": True,
                    "purchase": _purchase_json(existing),
                    "replayed": True,
                }), 200
            return fail(
                "This idempotency key was already used for different purchase details.",
                409,
            )
        return fail("The request conflicts with existing data. Check the submitted details.", 409)


def _purchase_json(p, correction_pending=None):
    values = purchase_effective_values(p)
    result = {
        "id": p.id,
        "purchaseNo": p.purchase_no,
        "type": values["purchase_type"],
        "varietyId": p.variety_id,
        "variety": values["variety"],
        "seller": values["seller_name"],
        "quantity": as_float(values["quantity_kg"]),
        "rate": as_float(values["rate_per_kg"]),
        "total": as_float(values["total_amount"]),
        "date": values["purchase_date"].isoformat(),
        "createdAt": p.created_at.isoformat(),
        "paymentMethod": p.payment_method,
        "paymentStatus": "Paid",
    }
    if correction_pending is not None:
        result["correctionPending"] = bool(correction_pending)
    return result


@api.get("/purchases")
@login_required_api
@tab_required_api("Paddy Purchase")
def purchases_list():
    try:
        page, per_page, date_from, date_to = _customer_history_params()
    except ValueError as error:
        return fail(str(error))
    query = Purchase.query.outerjoin(
        PurchaseCorrection,
        Purchase.effective_correction_id == PurchaseCorrection.id,
    )

    def effective_value(key, original):
        return func.coalesce(PurchaseCorrection.proposed_values[key].as_string(), original)
    effective_date = func.coalesce(
        func.str_to_date(effective_value("purchaseDate", None), "%Y-%m-%d"),
        Purchase.purchase_date,
    )
    if date_from:
        query = query.filter(effective_date >= date_from)
    if date_to:
        query = query.filter(effective_date <= date_to)
    search = request.args.get("q", "").strip()
    if len(search) > 120:
        return fail("Search text must be 120 characters or fewer.")
    if search:
        pattern = f"%{search}%"
        query=query.filter(or_(
            Purchase.purchase_no.ilike(pattern),
            effective_value("sellerName", Purchase.seller_name).ilike(pattern),
            effective_value("variety", Purchase.variety).ilike(pattern),
            effective_value("purchaseType", Purchase.purchase_type).ilike(pattern),
        ))
    total = query.count()
    rows= (query.options(joinedload(Purchase.effective_correction)).order_by(
        effective_date.desc(),
        Purchase.id.desc(),
    ).offset((page - 1) * per_page).limit(per_page).all())
    purchase_ids = [p.id for p in rows]
    pending_ids = set()
    if purchase_ids:
        pending_ids={row[0] for row in db.session.query(PurchaseCorrection.purchase_id).filter(
            PurchaseCorrection.purchase_id.in_(purchase_ids),
            PurchaseCorrection.status == "pending",
        ).all()}
    return jsonify({
        "success": True,
        "purchases": [_purchase_json(p, p.id in pending_ids) for p in rows],
        "pagination": {
            "page": page,
            "perPage": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page,
        },
    })


@api.get("/purchases/<int:purchase_id>")
@login_required_api
@tab_required_api("Paddy Purchase")
def purchase_detail(purchase_id):
    purchase = Purchase.query.options(joinedload(Purchase.effective_correction)).filter_by(id=purchase_id).first()
    if not purchase:
        return fail("Purchase not found.", 404)
    data = _purchase_json(purchase)
    data["notes"] = purchase_effective_values(purchase)["notes"] or ""
    return jsonify({"success": True, "purchase": data})


def _purchase_correction_snapshot(values):
    return {
        "purchaseType": values["purchase_type"],
        "variety": values["variety"],
        "sellerName": values["seller_name"],
        "quantityKg": format(Decimal(str(values["quantity_kg"])).quantize(Decimal("0.001")), ".3f"),
        "ratePerKg": format(Decimal(str(values["rate_per_kg"])).quantize(Decimal("0.01")), ".2f"),
        "totalAmount": format(
            Decimal(str(values["total_amount"])).quantize(Decimal("0.01")),
            ".2f",
        ),
        "purchaseDate": values["purchase_date"].isoformat(),
        "notes": values.get("notes"),
    }


def _purchase_correction_json(correction, include_requester=False):
    result = {
        "id": correction.id,
        "purchaseId": correction.purchase_id,
        "reason": correction.reason,
        "before": correction.before_values,
        "proposed": correction.proposed_values,
        "status": correction.status,
        "createdAt": correction.created_at.isoformat(),
        "reviewedAt": correction.reviewed_at.isoformat() if correction.reviewed_at else None,
        "decisionNotes": correction.decision_notes or "",
    }
    if include_requester:
        result["purchaseNo"] = correction.purchase.purchase_no
        result["requester"] = correction.requester.username
    return result


def _purchase_correction_request_values(data, current_values):
    purchase_type = data.get("purchaseType", current_values["purchase_type"])
    variety = data.get("variety", current_values["variety"])
    seller = data.get("sellerName", current_values["seller_name"])
    if not isinstance(purchase_type, str) or purchase_type not in {"Wet Paddy", "Dry Paddy"}:
        raise ValueError("Select a valid paddy type.")
    if not isinstance(variety, str) or not variety.strip() or len(variety.strip()) > 120:
        raise ValueError("Paddy variety must contain 1 to 120 characters.")
    if not isinstance(seller, str) or len(seller.strip()) > 160:
        raise ValueError("Seller name must be 160 characters or fewer.")
    variety = variety.strip()
    seller = seller.strip() or "Not Provided"
    quantity = dec(
        data.get("quantity", current_values["quantity_kg"]),
        "quantity",
        0.001,
    ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    rate = dec(
        data.get("price", current_values["rate_per_kg"]),
        "price",
        0.01,
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total = (quantity * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    raw_date = data.get("date", current_values["purchase_date"].isoformat())
    if not isinstance(raw_date, str):
        raise ValueError("Purchase date is invalid.")
    try:
        purchase_date = date.fromisoformat(raw_date)
    except ValueError:
        raise ValueError("Purchase date must use YYYY-MM-DD format.")
    if purchase_date.isoformat() != raw_date:
        raise ValueError("Purchase date must use YYYY-MM-DD format.")
    notes = data.get("notes", current_values.get("notes"))
    if notes is not None and (not isinstance(notes, str) or len(notes) > 4000):
        raise ValueError("Purchase notes must be text of 4000 characters or fewer.")
    return {
        "purchase_type": purchase_type,
        "variety": variety,
        "seller_name": seller,
        "quantity_kg": quantity,
        "rate_per_kg": rate,
        "total_amount": total,
        "purchase_date": purchase_date,
        "notes": notes,
    }


@api.post("/purchases/<int:purchase_id>/corrections")
@limiter.limit("20 per minute")
@login_required_api
@tab_required_api("Paddy Purchase")
def purchase_correction_request(purchase_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return fail("A JSON correction request is required.")
    user = current_user()
    if user.role != "employee":
        return fail(
            "Only employees with Paddy Purchase access can submit correction requests.",
            403,
        )
    allowed = {
        "idempotencyKey",
        "reason",
        "purchaseType",
        "variety",
        "sellerName",
        "quantity",
        "price",
        "date",
        "notes",
    }
    if set(data) - allowed:
        return fail("The correction request contains unsupported fields.")
    reason = data.get("reason")
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
        return fail("A correction reason of 1 to 500 characters is required.")
    header_key = (request.headers.get("Idempotency-Key") or "").strip()
    body_key = data.get("idempotencyKey", "")
    if not isinstance(body_key, str):
        return fail("Idempotency key must be text.")
    body_key = body_key.strip()
    if header_key and body_key and header_key != body_key:
        return fail("Header and body idempotency keys do not match.")
    key = header_key or body_key
    if not 16 <= len(key) <= 100:
        return fail("A stable idempotency key of 16 to 100 characters is required.")
    purchase = Purchase.query.filter_by(id=purchase_id).with_for_update().first()
    if not purchase:
        return fail("Purchase not found.", 404)
    try:
        current_values = purchase_effective_values(purchase)
        proposed_values = _purchase_correction_request_values(data, current_values)
        before_snapshot = _purchase_correction_snapshot(current_values)
        proposed_snapshot = _purchase_correction_snapshot(proposed_values)
        fingerprint_data = {
            "purchase_id": purchase.id,
            "reason": reason.strip(),
            "proposed": proposed_snapshot,
        }
        fingerprint=hashlib.sha256(json.dumps(
            fingerprint_data,
            sort_keys=True,
            separators= (",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        existing = PurchaseCorrection.query.filter_by(idempotency_key=key).first()
        if existing:
            if existing.created_by != user.id or existing.request_fingerprint != fingerprint:
                return fail(
                    "This idempotency key was already used for a different correction request.",
                    409,
                )
            return jsonify({
                "success": True,
                "correction": _purchase_correction_json(existing),
                "replayed": True,
            }), 200
        if before_snapshot == proposed_snapshot:
            return fail("No purchase values changed.")
        pending = PurchaseCorrection.query.filter_by(
            purchase_id=purchase.id,
            status="pending",
        ).first()
        if pending:
            return fail(
                "A correction request for this purchase is already awaiting Admin review.",
                409,
            )
        correction = PurchaseCorrection(
            purchase_id=purchase.id,
            idempotency_key=key,
            request_fingerprint=fingerprint,
            reason=reason.strip(),
            before_values=before_snapshot,
            proposed_values=proposed_snapshot,
            status="pending",
            created_by=user.id,
        )
        db.session.add(correction)
        db.session.flush()
        audit(
            "request",
            "purchase_correction",
            correction.id,
            {"purchase_id": purchase.id, "status": "pending"},
        )
        db.session.commit()
        return jsonify({
            "success": True,
            "correction": _purchase_correction_json(correction),
            "replayed": False,
        }), 201
    except ValueError as error:
        db.session.rollback()
        return fail(str(error))
    except IntegrityError as error:
        db.session.rollback()
        existing = PurchaseCorrection.query.filter_by(idempotency_key=key).first() if "key" in locals() else None
        if existing and existing.created_by == user.id and existing.request_fingerprint == fingerprint:
            return jsonify({
                "success": True,
                "correction": _purchase_correction_json(existing),
                "replayed": True,
            }), 200
        if existing:
            return fail(
                "This idempotency key was already used for a different correction request.",
                409,
            )
        return fail("A correction request is already awaiting Admin review.", 409)
    except SQLAlchemyError as error:
        db.session.rollback()
        current_app.logger.error(
            "Purchase correction request failed; exception_type=%s",
            type(error).__name__,
        )
        return fail("The purchase correction request could not be saved.", 500)
    except Exception as error:
        db.session.rollback()
        current_app.logger.error(
            "Purchase correction request failed; exception_type=%s",
            type(error).__name__,
        )
        return fail("The purchase correction request could not be saved.", 500)


@api.get("/purchases/corrections/pending")
@login_required_api
@admin_required_api
def purchase_correction_pending():
    rows= (PurchaseCorrection.query.filter_by(status="pending").options(
        joinedload(PurchaseCorrection.purchase),
        joinedload(PurchaseCorrection.requester),
    ).order_by(PurchaseCorrection.created_at, PurchaseCorrection.id).all())
    return jsonify({
        "success": True,
        "corrections": [_purchase_correction_json(row, True) for row in rows],
    })


def _apply_purchase_correction_stock(purchase, before, proposed, correction_id):
    old_type = "wet_paddy" if before["purchaseType"] == "Wet Paddy" else "dry_paddy"
    new_type = "wet_paddy" if proposed["purchaseType"] == "Wet Paddy" else "dry_paddy"
    deltas = {}
    old_key = (before["variety"], old_type)
    new_key = (proposed["variety"], new_type)
    deltas[old_key] = deltas.get(old_key, Decimal("0")) - Decimal(before["quantityKg"])
    deltas[new_key] = deltas.get(new_key, Decimal("0")) + Decimal(proposed["quantityKg"])
    for index, (stock_key, delta) in enumerate(sorted(deltas.items())):
        if delta == 0:
            continue
        item_name, stock_type = stock_key
        move_stock(
            item_name,
            stock_type,
            item_name,
            delta,
            movement="purchase_correction",
            ref_type="purchase_correction",
            ref_id=correction_id,
            notes=f"Approved purchase correction {correction_id}",
            idempotency_key=f"purchase-correction-{correction_id}-{index}",
        )


@api.post("/purchases/corrections/<int:correction_id>/decision")
@limiter.limit("20 per minute")
@login_required_api
@admin_required_api
def purchase_correction_decision(correction_id):
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    notes = data.get("notes", "")
    if action not in {"approve", "reject"}:
        return fail("Decision must be approve or reject.")
    if not isinstance(notes, str) or len(notes) > 2000:
        return fail("Decision notes must be text of 2000 characters or fewer.")
    correction = PurchaseCorrection.query.filter_by(id=correction_id).with_for_update().first()
    if not correction:
        return fail("Purchase correction request not found.", 404)
    purchase = Purchase.query.filter_by(id=correction.purchase_id).with_for_update().first()
    if not purchase:
        return fail("Purchase not found.", 404)
    if correction.status != "pending":
        return fail("This correction request is no longer awaiting an Admin decision.", 409)
    user = current_user()
    now = datetime.utcnow()
    try:
        if action == "approve":
            current_snapshot = _purchase_correction_snapshot(purchase_effective_values(purchase))
            if current_snapshot != correction.before_values:
                db.session.rollback()
                return fail(
                    "The effective purchase version changed after this request was submitted.",
                    409,
                )
            _apply_purchase_correction_stock(
                purchase,
                correction.before_values,
                correction.proposed_values,
                correction.id,
            )
            correction.status = "approved"
            purchase.effective_correction_id = correction.id
            action_name = "approve"
        else:
            correction.status = "rejected"
            action_name = "reject"
        correction.reviewed_by = user.id
        correction.reviewed_at = now
        correction.decision_notes = notes.strip() or None
        audit(
            action_name,
            "purchase_correction",
            correction.id,
            {
                "purchase_id": purchase.id,
                "status": correction.status,
                "stock_adjusted": action == "approve",
            },
        )
        db.session.commit()
        return jsonify({"success": True, "correction": _purchase_correction_json(correction)})
    except ValueError:
        db.session.rollback()
        return fail(
            "The correction would make aggregate paddy stock negative; the request remains pending.",
            409,
        )
    except SQLAlchemyError as error:
        db.session.rollback()
        current_app.logger.error(
            "Purchase correction decision failed; exception_type=%s",
            type(error).__name__,
        )
        return fail(
            "The Admin decision could not be saved. No purchase or stock changes were committed.",
            500,
        )
    except Exception as error:
        db.session.rollback()
        current_app.logger.error(
            "Purchase correction decision failed; exception_type=%s",
            type(error).__name__,
        )
        return fail(
            "The Admin decision could not be saved. No purchase or stock changes were committed.",
            500,
        )


@api.get("/inventory")
@login_required_api
@tab_required_api("Inventory Management")
def inventory_list():
    rows = Stock.query.order_by(Stock.stock_type, Stock.item_name).all()
    return jsonify({
        "success": True,
        "stocks": [{
            "id": s.id,
            "productId": s.product_id,
            "item": s.item_name,
            "type": s.stock_type,
            "section": _inventory_balance_json(s)["section"],
            "variety": s.variety,
            "quantityKg": as_float(s.quantity_kg),
            "quantityBags": as_float(s.quantity_bags),
            "packageSize": s.package_size_kg,
        } for s in rows],
    })

INVENTORY_STOCK_TYPES = {"wet_paddy", "dry_paddy", "rice", "packed", "bran", "other"}
INVENTORY_ADJUSTMENT_REASONS = (
    "Physical count increase",
    "Physical count decrease",
    "Damaged stock",
    "Spoilage",
    "Wastage",
    "Data correction",
    "Other",
)


def _inventory_balance_json(stock):
    section = None
    if stock.product:
        sections = {category.inventory_section for category in stock.product.main_categories if category.is_active}
        section = stock.stock_type if stock.stock_type in {
            "wet_paddy",
            "dry_paddy",
        } else("packed" if stock.stock_type == "packed" else "rice" if stock.stock_type == "rice" else "other" if "other" in sections else stock.stock_type)
    return {
        "id": stock.id,
        "productId": stock.product_id,
        "item": stock.item_name,
        "type": stock.stock_type,
        "section": section or ("other" if stock.stock_type == "bran" else stock.stock_type),
        "variety": stock.variety or "",
        "quantityKg": str(stock.quantity_kg),
        "quantityBags": str(stock.quantity_bags),
        "packageSize": stock.package_size_kg,
    }


def _stock_movement_json(movement, stock, username):
    return {
        "id": movement.id,
        "productId": stock.product_id,
        "date": movement.created_at.isoformat() if movement.created_at else None,
        "item": stock.item_name,
        "type": stock.stock_type,
        "variety": stock.variety or "",
        "packageSize": stock.package_size_kg,
        "movementType": movement.movement_type,
        "quantityKg": str(movement.quantity_kg),
        "quantityBags": str(movement.quantity_bags),
        "reason": movement.notes or "",
        "referenceType": movement.reference_type,
        "referenceId": movement.reference_id,
        "user": username,
        "idempotencyKey": movement.idempotency_key,
    }


def _inventory_mutation_from_request(d, opening=False):
    if not isinstance(d, dict):
        raise ValueError("A JSON inventory request is required.")
    key = d.get("idempotencyKey")
    if not isinstance(key, str) or not 8 <= len(key.strip()) <= 100:
        raise ValueError("A valid idempotency key is required.")
    key = key.strip()
    item = d.get("item")
    if not isinstance(item, str) or not item.strip() or len(item.strip()) > 80:
        raise ValueError("Product name is required and must be 80 characters or fewer.")
    item = item.strip()
    kind = d.get("stockType")
    if kind not in INVENTORY_STOCK_TYPES:
        raise ValueError("Select a valid stock category.")
    variety = d.get("variety", "")
    if not isinstance(variety, str) or len(variety.strip()) > 80:
        raise ValueError("Variety must be text of 80 characters or fewer.")
    variety = variety.strip()
    if kind in {"rice", "packed"}:
        master = MasterItem.query.filter_by(group_name="rice_variety", name=variety).first()
        expected = "packed" if kind == "packed" else "bulk"
        if master and expected not in {category.code for category in master.main_categories}:
            raise ValueError("This rice product is not assigned to the selected inventory category.")
    elif kind in {"bran", "other"}:
        master = MasterItem.query.filter_by(group_name="product", name=item).first()
        if master and "other" not in {category.code for category in master.main_categories}:
            raise ValueError("This product is not assigned to Other Sellable Products.")
    unit = d.get("unit")
    try:
        quantity = dec(d.get("quantity"), "quantity", Decimal("0.001"))
    except ValueError:
        raise
    if quantity > Decimal("9999999999999.999"):
        raise ValueError("Quantity exceeds the supported stock balance range.")
    if quantity.quantize(Decimal("0.001")) != quantity:
        raise ValueError("Quantity may have at most three decimal places.")
    package_size = None
    if kind == "packed":
        if unit != "bags":
            raise ValueError("Packed rice quantity must use bags.")
        try:
            package_size = int(d.get("packageSize"))
        except (TypeError, ValueError):
            raise ValueError("Select a valid package size.")
        if package_size not in (10, 25, 50):
            raise ValueError("Package size must be 10, 25 or 50 kg.")
        if quantity != quantity.to_integral_value():
            raise ValueError("Packed rice quantity must be a whole number of bags.")
        kg = quantity * Decimal(package_size)
        bags = quantity
    else:
        if unit != "kg":
            raise ValueError("This stock category must use kilograms.")
        if d.get("packageSize") not in (None, ""):
            raise ValueError("Package size is only valid for packed rice.")
        kg = quantity
        bags = Decimal("0")
    if kg > Decimal("9999999999999.999"):
        raise ValueError("Quantity exceeds the supported stock balance range.")
    direction = "add" if opening else d.get("direction")
    if direction not in {"add", "remove"}:
        raise ValueError("Select Add or Remove for the adjustment.")
    reason = "Opening stock"
    if not opening:
        reason = d.get("reason")
        if reason not in INVENTORY_ADJUSTMENT_REASONS:
            raise ValueError("Select a valid stock adjustment reason.")
        if reason == "Physical count increase" and direction != "add":
            raise ValueError("Physical count increase must add stock.")
        if reason in {
            "Physical count decrease",
            "Damaged stock",
            "Spoilage",
            "Wastage",
        } and direction != "remove":
            raise ValueError(f"{reason} must remove stock.")
        if reason == "Other":
            explanation = d.get("otherReason")
            if not isinstance(
                explanation,
                str,
            ) or not explanation.strip() or len(explanation.strip()) > 500:
                raise ValueError("Explain the Other adjustment reason in 500 characters or fewer.")
            reason = f"Other: {explanation.strip()}"
        elif d.get("otherReason") not in (None, ""):
            raise ValueError("An explanation is accepted only for the Other reason.")
    if direction == "remove":
        kg = -kg
        bags = -bags
    return {
        "key": key,
        "item": item,
        "kind": kind,
        "variety": variety,
        "packageSize": package_size,
        "kg": kg,
        "bags": bags,
        "reason": reason,
        "direction": direction,
        "movement": "opening_stock" if opening else "stock_adjustment",
    }


def _existing_inventory_request(key, payload):
    movement = StockMovement.query.filter_by(idempotency_key=key).first()
    if not movement:
        return None
    stock = db.session.get(Stock, movement.stock_id)
    same = (movement.movement_type == payload["movement"] and Decimal(str(movement.quantity_kg)) == payload["kg"] and Decimal(str(movement.quantity_bags)) == payload["bags"] and (movement.notes or "") == payload["reason"] and stock is not None and stock.item_name == payload["item"] and stock.stock_type == payload["kind"] and (stock.variety or "") == payload["variety"] and stock.package_size_kg == payload["packageSize"])
    if not same:
        raise ValueError("This idempotency key was already used for a different inventory request.")
    return movement, stock


def _perform_inventory_mutation(payload):
    existing = _existing_inventory_request(payload["key"], payload)
    if existing:
        return existing[1], existing[0], True
    stock = move_stock(
        payload["item"],
        payload["kind"],
        payload["variety"],
        payload["kg"],
        payload["bags"],
        payload["packageSize"],
        payload["movement"],
        "inventory",
        None,
        payload["reason"],
        idempotency_key=payload["key"],
    )
    db.session.flush()
    audit(
        payload["movement"],
        "stock",
        stock.id,
        {
            "movement_id": db.session.query(StockMovement.id).filter_by(idempotency_key=payload["key"]).scalar(),
            "item": payload["item"],
            "stock_type": payload["kind"],
            "variety": payload["variety"],
            "package_size_kg": payload["packageSize"],
            "quantity_kg": str(payload["kg"]),
            "quantity_bags": str(payload["bags"]),
            "reason": payload["reason"],
            "idempotency_key": payload["key"],
        },
    )
    movement = StockMovement.query.filter_by(idempotency_key=payload["key"]).one()
    return stock, movement, False


@api.get("/inventory/summary")
@login_required_api
@tab_required_api("Inventory Management")
def inventory_summary():
    rows = Stock.query.order_by(
        Stock.stock_type,
        Stock.item_name,
        Stock.variety,
        Stock.package_size_kg,
    ).all()
    totals={kind: {
        "quantityKg": Decimal("0"),
        "quantityBags": Decimal("0"),
    } for kind in INVENTORY_STOCK_TYPES}
    for row in rows:
        if row.stock_type in totals:
            totals[row.stock_type]["quantityKg"] += Decimal(str(row.quantity_kg))
            totals[row.stock_type]["quantityBags"] += Decimal(str(row.quantity_bags))
    inventory_rows = [_inventory_balance_json(row) for row in rows]
    existing = {(row["productId"], row["type"]) for row in inventory_rows}
    for item in MasterItem.query.filter(
        MasterItem.is_active.is_(True),
        MasterItem.group_name.in_(("rice_variety", "product")),
    ).all():
        for category in item.main_categories:
            if not category.is_active:
                continue
            stock_type = category.inventory_section
            if stock_type not in {"rice", "packed", "other"}:
                continue
            stock_item = f"{item.name} Rice" if stock_type in {"rice", "packed"} else item.name
            variant = item.name if stock_type in {"rice", "packed"} else ""
            key = (item.id, stock_type)
            if key not in existing:
                inventory_rows.append({
                    "id": None,
                    "productId": item.id,
                    "item": stock_item,
                    "type": stock_type,
                    "section": stock_type,
                    "variety": variant,
                    "quantityKg": "0",
                    "quantityBags": "0",
                    "packageSize": None,
                })
                existing.add(key)
    return jsonify({
        "success": True,
        "totals": {kind: {k: str(v) for k, v in amount.items()} for kind, amount in totals.items()},
        "stocks": inventory_rows,
        "adjustmentReasons": list(INVENTORY_ADJUSTMENT_REASONS),
    })


@api.get("/inventory/movements")
@login_required_api
@tab_required_api("Inventory Management")
def inventory_movements():
    try:
        from_value = request.args.get("dateFrom")
        to_value = request.args.get("dateTo")
        date_from = date.fromisoformat(from_value) if from_value else None
        date_to = date.fromisoformat(to_value) if to_value else None
        if date_from and date_to and date_from > date_to:
            return fail("Date From must not be after Date To.")
        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
        if limit < 1 or limit > 200 or offset < 0:
            return fail("Movement pagination is invalid.")
    except (ValueError, TypeError):
        return fail("Date filters or pagination values are invalid.")
    query = db.session.query(StockMovement, Stock, User).join(
        Stock,
        Stock.id == StockMovement.stock_id,
    ).outerjoin(User, User.id == StockMovement.created_by)
    # Sri Lanka uses UTC+05:30 without daylight-saving time; avoid requiring
    # external OS tzdata for these local calendar-date filters.
    local_zone = timezone(timedelta(hours=5, minutes=30))
    if date_from:
        start_utc = datetime.combine(
            date_from,
            datetime.min.time(),
            tzinfo=local_zone,
        ).astimezone(timezone.utc).replace(tzinfo=None)
        query = query.filter(StockMovement.created_at >= start_utc)
    if date_to:
        end_utc = datetime.combine(
            date_to + timedelta(days=1),
            datetime.min.time(),
            tzinfo=local_zone,
        ).astimezone(timezone.utc).replace(tzinfo=None)
        query = query.filter(StockMovement.created_at < end_utc)
    total = query.count()
    rows = query.order_by(
        StockMovement.created_at.desc(),
        StockMovement.id.desc(),
    ).offset(offset).limit(limit).all()
    return jsonify({
        "success": True,
        "total": total,
        "limit": limit,
        "offset": offset,
        "movements": [_stock_movement_json(m, s, u.username if u else None) for m, s, u in rows],
    })


def _inventory_write_response(payload):
    try:
        stock, movement, duplicate = _perform_inventory_mutation(payload)
        db.session.commit()
        return jsonify({
            "success": True,
            "duplicate": duplicate,
            "stock": _inventory_balance_json(stock),
            "movement": _stock_movement_json(movement, stock, current_user().username),
        }), (200 if duplicate else 201)
    except ValueError as error:
        db.session.rollback()
        return fail(str(error), 409 if "idempotency key" in str(error) else 400)
    except IntegrityError:
        db.session.rollback()
        try:
            existing = _existing_inventory_request(payload["key"], payload)
            if existing:
                stock, movement = existing
                return jsonify({
                    "success": True,
                    "duplicate": True,
                    "stock": _inventory_balance_json(stock),
                    "movement": _stock_movement_json(movement, stock, current_user().username),
                }), 200
        except ValueError:
            return fail("This idempotency key conflicts with a different inventory request.", 409)
        return fail(
            "The inventory request conflicts with existing data. Please retry with a new request key.",
            409,
        )
    except SQLAlchemyError as error:
        db.session.rollback()
        current_app.logger.error("Inventory write failed; exception_type=%s", type(error).__name__)
        return fail("The inventory change could not be saved. Please try again.", 500)


@api.post("/inventory/opening-stock")
@limiter.limit("30 per minute")
@login_required_api
@inventory_permission_required("manageOpeningStockAdjustments")
def inventory_opening_stock():
    try:
        payload = _inventory_mutation_from_request(request.get_json(silent=True), opening=True)
    except ValueError as error:
        return fail(str(error))
    return _inventory_write_response(payload)


@api.post("/inventory/adjustments")
@limiter.limit("30 per minute")
@login_required_api
@inventory_permission_required("manageOpeningStockAdjustments")
def inventory_adjustment():
    try:
        payload = _inventory_mutation_from_request(request.get_json(silent=True), opening=False)
    except ValueError as error:
        return fail(str(error))
    return _inventory_write_response(payload)


@api.post("/production/<kind>")
@limiter.limit("20 per minute")
@login_required_api
@tab_required_api("Production Management")
def production_create(kind):
    if kind not in {"drying", "milling", "packaging"}:
        return fail("Invalid production type.")
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return fail("A JSON production request is required.")
    try:
        allowed = {
            "idempotencyKey",
            "variety",
            "inputKg",
            "outputKg",
            "riceOutput",
            "secondaryOutputKg",
            "branOutput",
            "packageSize",
            "packetCount",
            "date",
            "batchNo",
            "operator",
            "notes",
        }
        extra = set(d) - allowed
        if extra:
            raise ValueError("Unexpected production request field.")
        key = d.get("idempotencyKey")
        if not isinstance(key, str) or not 8 <= len(key.strip()) <= 100:
            raise ValueError("A valid idempotency key is required.")
        key = key.strip()
        variety = d.get("variety")
        if not isinstance(variety, str) or not variety.strip() or len(variety.strip()) > 80:
            raise ValueError("A valid rice variety is required.")
        variety = variety.strip()
        active_variety = MasterItem.query.filter(
            MasterItem.name == variety,
            MasterItem.is_active.is_(True),
            MasterItem.group_name.in_(("rice_variety", "paddy_type")),
        ).first()
        if not active_variety:
            raise ValueError("Select an active paddy variety.")

        def quantity(value, label, minimum=Decimal("0")):
            amount = dec(value, label, minimum)
            if amount > Decimal("9999999999999.999"):
                raise ValueError(f"{label} exceeds the supported quantity range.")
            if amount.quantize(Decimal("0.001")) != amount:
                raise ValueError(f"{label} may have at most three decimal places.")
            return amount

        input_kg = quantity(d.get("inputKg"), "Input quantity", Decimal("0.001"))
        if "outputKg" in d and "riceOutput" in d and d["outputKg"] != d["riceOutput"]:
            raise ValueError("Conflicting output quantity fields were submitted.")
        if "secondaryOutputKg" in d and "branOutput" in d and d["secondaryOutputKg"] != d["branOutput"]:
            raise ValueError("Conflicting secondary output fields were submitted.")
        out = quantity(d.get("outputKg", d.get("riceOutput", "0")), "Output quantity")
        secondary = quantity(
            d.get("secondaryOutputKg", d.get("branOutput", "0")),
            "Secondary output",
        )
        raw_date = d.get("date")
        try:
            record_date = date.fromisoformat(raw_date) if raw_date else colombo_date()
            if raw_date and (not isinstance(raw_date, str) or record_date.isoformat() != raw_date):
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("Date must be a valid ISO calendar date.")

        def optional_text(field, limit):
            value = d.get(field)
            if value is None or value == "":
                return None
            if not isinstance(value, str) or len(value.strip()) > limit:
                raise ValueError(f"{field} must be text of {limit} characters or fewer.")
            return value.strip() or None
        batch_no = optional_text("batchNo", 80)
        operator = optional_text("operator", 120)
        notes = optional_text("notes", 4000)
        size = None
        count = 0
        waste = Decimal("0")
        remaining = Decimal("0")
        if kind == "drying":
            if secondary != 0 or d.get("packageSize") not in (None, "") or d.get("packetCount") not in (
                None,
                "",
                0,
            ):
                raise ValueError("Drying requests may contain only drying quantities.")
            if "outputKg" not in d:
                raise ValueError("Drying requires outputKg.")
            if out > input_kg:
                raise ValueError("Dry output cannot exceed wet input.")
            waste = input_kg - out
        elif kind == "milling":
            if d.get("packageSize") not in (None, "") or d.get("packetCount") not in (None, "", 0):
                raise ValueError("Milling requests may not contain packaging fields.")
            if "riceOutput" not in d and "outputKg" not in d:
                raise ValueError("Milling requires riceOutput.")
            if out + secondary > input_kg:
                raise ValueError("Rice and bran output cannot exceed dry paddy input.")
            waste = input_kg - out - secondary
            if waste < 0:
                raise ValueError("Milling waste/loss cannot be negative.")
        else:
            if d.get("outputKg") not in (
                None,
                "",
                0,
                "0",
            ) or d.get("riceOutput") not in (None, "", 0, "0") or secondary != 0:
                raise ValueError("Packaging output is calculated from package size and packet count.")
            try:
                size = int(d.get("packageSize"))
                count = int(d.get("packetCount"))
            except (TypeError, ValueError):
                raise ValueError("Package size and whole packet count are required.")
            if size not in (10, 25, 50):
                raise ValueError("Package size must be 10, 25 or 50 kg.")
            if isinstance(
                d.get("packetCount"),
                bool,
            ) or str(count) != str(d.get("packetCount")) or count < 1 or count > 999999999:
                raise ValueError("Packet count must be a positive whole number.")
            packed = Decimal(size) * Decimal(count)
            if packed > input_kg:
                raise ValueError("Packed quantity exceeds bulk rice input.")
            out = packed
            secondary = Decimal("0")
            remaining = input_kg - packed

        canonical = {
            "kind": kind,
            "variety": variety,
            "inputKg": str(input_kg),
            "outputKg": str(out),
            "secondaryOutputKg": str(secondary),
            "packageSize": size,
            "packetCount": count,
            "date": record_date.isoformat(),
            "batchNo": batch_no,
            "operator": operator,
            "notes": notes,
        }
        fingerprint=hashlib.sha256(json.dumps(
            canonical,
            sort_keys=True,
            separators= (",", ":"),
        ).encode()).hexdigest()
        existing = ProductionRecord.query.filter_by(idempotency_key=key).first()
        if existing:
            if existing.request_fingerprint != fingerprint:
                return fail(
                    "This idempotency key was already used for a different production request.",
                    409,
                )
            return jsonify({
                "success": True,
                "duplicate": True,
                "recordId": existing.id,
                "wasteKg": str(existing.waste_kg),
                "remainingKg": str(existing.remaining_kg),
            }), 200

        rec = ProductionRecord(
            record_type=kind,
            record_date=record_date,
            variety=variety,
            input_kg=input_kg,
            primary_output_kg=out,
            secondary_output_kg=secondary,
            waste_kg=waste,
            remaining_kg=remaining,
            package_size_kg=size,
            packet_count=count,
            batch_no=batch_no,
            operator=operator,
            notes=notes,
            created_by=current_user().id,
            idempotency_key=key,
            request_fingerprint=fingerprint,
        )
        db.session.add(rec)
        db.session.flush()
        common = {"ref_type": "production", "ref_id": rec.id, "production_record_id": rec.id}
        if kind == "drying":
            move_stock(variety, "wet_paddy", variety, -input_kg, movement="drying_input", **common)
            move_stock(variety, "dry_paddy", variety, out, movement="drying_output", **common)
        elif kind == "milling":
            move_stock(variety, "dry_paddy", variety, -input_kg, movement="milling_input", **common)
            move_stock(variety + " Rice", "rice", variety, out, movement="milling_rice", **common)
            if secondary:
                move_stock(
                    "Red Rice Bran",
                    "bran",
                    variety,
                    secondary,
                    movement="milling_bran",
                    **common,
                )
        else:
            bulk_stock = get_stock(variety + " Rice", "rice", variety)
            if dec(bulk_stock.quantity_kg) < input_kg:
                raise ValueError("Insufficient bulk rice stock for the submitted packaging input.")
            move_stock(
                variety + " Rice",
                "rice",
                variety,
                -out,
                movement="packaging_input",
                **common,
            )
            move_stock(
                variety + " Rice",
                "packed",
                variety,
                out,
                count,
                size,
                "packaging_output",
                **common,
            )
        audit("create", "production", rec.id, {"type": kind, "idempotencyKey": key})
        db.session.commit()
        return jsonify({
            "success": True,
            "duplicate": False,
            "recordId": rec.id,
            "wasteKg": str(waste),
            "remainingKg": str(remaining),
        }), 201
    except ValueError as error:
        db.session.rollback()
        return fail(str(error))
    except IntegrityError:
        db.session.rollback()
        try:
            existing = ProductionRecord.query.filter_by(idempotency_key=key).first()
            if existing:
                if existing.request_fingerprint != fingerprint:
                    return fail(
                        "This idempotency key was already used for a different production request.",
                        409,
                    )
                return jsonify({
                    "success": True,
                    "duplicate": True,
                    "recordId": existing.id,
                    "wasteKg": str(existing.waste_kg),
                    "remainingKg": str(existing.remaining_kg),
                }), 200
        except Exception:
            db.session.rollback()
        return fail(
            "The production request conflicts with existing data. Please retry with a new request key.",
            409,
        )
    except SQLAlchemyError as error:
        db.session.rollback()
        current_app.logger.error("Production write failed; exception_type=%s", type(error).__name__)
        return fail("The production record could not be saved. Please try again.", 500)


@api.get("/production/options")
@login_required_api
@tab_required_api("Production Management")
def production_options():
    varieties = MasterItem.query.filter(
        MasterItem.group_name.in_(("rice_variety", "paddy_type")),
        MasterItem.is_active.is_(True),
    ).order_by(MasterItem.name).all()
    stocks = Stock.query.filter(
        Stock.stock_type.in_(("wet_paddy", "dry_paddy", "rice")),
        Stock.quantity_kg > 0,
    ).order_by(Stock.variety, Stock.stock_type).all()
    active_names = {row.name for row in varieties}
    available=[{
        "type": row.stock_type,
        "variety": row.variety or "",
        "item": row.item_name,
        "quantityKg": str(row.quantity_kg),
    } for row in stocks if (row.variety or "") in active_names and row.item_name == (f"{row.variety} Rice" if row.stock_type == "rice" else row.variety)]
    return jsonify({
        "success": True,
        "varieties": [row.name for row in varieties],
        "stocks": available,
    })


@api.get("/production")
@login_required_api
@tab_required_api("Production Management")
def production_list():
    def query_date(name):
        value = request.args.get(name)
        if not value:
            return None
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError
            return parsed
        except ValueError:
            raise ValueError(f"{name} must be a valid ISO calendar date.")
    try:
        date_from, date_to = query_date("dateFrom"), query_date("dateTo")
    except ValueError as error:
        return fail(str(error))
    record_type = request.args.get("type")
    if record_type and record_type not in {"drying", "milling", "packaging"}:
        return fail("Select a valid production type.")
    if date_from and date_to and date_from > date_to:
        return fail("Start date cannot be after end date.")

    all_param= (request.args.get("all") in {
        "true",
        "1",
        "all",
    } or request.args.get("allRecords") in {"true", "1"} or request.args.get("perPage") in {"all", "0"})
    if all_param:
        per_page = None
        page = 1
    else:
        try:
            page = int(request.args.get("page", "1"))
            per_page = int(request.args.get("perPage", "5"))
        except (TypeError, ValueError):
            return fail("Page and perPage must be whole numbers.")
        if page < 1 or not 1 <= per_page <= 100:
            return fail("Page must be positive and perPage must be between 1 and 100.")
    query = ProductionRecord.query
    if date_from:
        query = query.filter(ProductionRecord.record_date >= date_from)
    if date_to:
        query = query.filter(ProductionRecord.record_date <= date_to)
    if record_type:
        query = query.filter_by(record_type=record_type)
    total = query.count()
    query_ordered = query.order_by(ProductionRecord.record_date.desc(), ProductionRecord.id.desc())
    if per_page is None:
        rows = query_ordered.all()
        pagination = {"page": 1, "perPage": total, "total": total, "pages": 1}
    else:
        rows = query_ordered.offset((page - 1) * per_page).limit(per_page).all()
        pagination = {
            "page": page,
            "perPage": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page,
        }
    all_rows = ProductionRecord.query.all()
    summary = {
        "records": len(all_rows),
        "wetProcessedKg": str(sum(
            (r.input_kg for r in all_rows if r.record_type == "drying"),
            Decimal("0"),
        )),
        "dryProducedKg": str(sum(
            (r.primary_output_kg for r in all_rows if r.record_type == "drying"),
            Decimal("0"),
        )),
        "riceProducedKg": str(sum(
            (r.primary_output_kg for r in all_rows if r.record_type == "milling"),
            Decimal("0"),
        )),
        "dryingLossKg": str(sum(
            (r.waste_kg for r in all_rows if r.record_type == "drying"),
            Decimal("0"),
        )),
        "millingWasteKg": str(sum(
            (r.waste_kg for r in all_rows if r.record_type == "milling"),
            Decimal("0"),
        )),
        "packedKg": str(sum(
            (r.primary_output_kg for r in all_rows if r.record_type == "packaging"),
            Decimal("0"),
        )),
        "packets": sum((r.packet_count or 0 for r in all_rows if r.record_type == "packaging"), 0),
    }
    return jsonify({
        "success": True,
        "summary": summary,
        "pagination": pagination,
        "records": [{
            "id": r.id,
            "type": r.record_type,
            "date": r.record_date.isoformat(),
            "variety": r.variety,
            "inputKg": str(r.input_kg),
            "outputKg": str(r.primary_output_kg),
            "secondaryOutputKg": str(r.secondary_output_kg),
            "wasteKg": str(r.waste_kg),
            "remainingKg": str(r.remaining_kg),
            "packageSize": r.package_size_kg,
            "packetCount": r.packet_count,
            "operator": r.operator,
            "batchNo": r.batch_no,
            "stockMovementIds": [m.id for m in r.movements],
        } for r in rows],
    })


@api.post("/sales")
@limiter.limit("20 per minute")
@login_required_api
@tab_required_api("Sales & Invoicing")
def sale_create():
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return fail("A JSON sale request is required.")
    raw_items = d.get("items")
    if "discount" in d or (isinstance(
        raw_items,
        list,
    ) and any(isinstance(i, dict) and "discount" in i for i in raw_items)):
        return fail("Discounts are not supported for sales.")
    user = current_user()
    invoice_no = None
    request_hash = None
    try:
        invoice_no, request_hash = _sale_idempotency_values(d, user.id)
        replay = _find_idempotent_sale(invoice_no, user.id, request_hash)
        if replay is not None:
            return replay

        ctype = d.get("customerType", "walk_in")
        if not isinstance(ctype, str):
            return fail("Select Retail Customer or Registered Customer.")
        if ctype == "walkin":
            ctype = "walk_in"  # Existing Sales UI spelling.
        if ctype not in {"walk_in", "registered"}:
            return fail("Select Retail Customer or Registered Customer.")

        payment_type = d.get("paymentType", "full")
        if not isinstance(payment_type, str):
            return fail("Invalid payment type.")
        payment_type = payment_type.strip().lower()
        if payment_type not in {"full", "partial", "credit"}:
            return fail("Payment type must be Full, Partial or Credit.")
        if ctype == "walk_in" and payment_type != "full":
            return fail("Retail Customers must make full payment.")

        customer_id = d.get("customerId")
        c = None
        if ctype == "registered":
            if customer_id in (None, ""):
                return fail("Select a Registered Customer account.")
            if isinstance(customer_id, bool):
                return fail("Customer ID must be a valid registered account.")
            try:
                customer_id = int(customer_id)
            except (TypeError, ValueError):
                return fail("Customer ID must be a valid registered account.")
            c = Customer.query.filter_by(id=customer_id, is_active=True).with_for_update().first()
            if not c:
                return fail("Active Registered Customer not found.", 404)
        elif customer_id not in (None, ""):
            return fail("Retail Customer sales cannot be linked to a registered customer.")

        payment_method = d.get("paymentMethod", d.get("payment_method", "Cash"))
        if not isinstance(payment_method, str):
            return fail("Payment method must be Cash or Bank Transfer.")
        method_key = payment_method.strip().lower().replace("_", " ")
        method_map = {"cash": "Cash", "bank": "Bank Transfer", "bank transfer": "Bank Transfer"}
        if method_key not in method_map:
            return fail("Payment method must be Cash or Bank Transfer.")
        payment_method = method_map[method_key]
        bank_reference = d.get("bankReference")
        if bank_reference is not None and (not isinstance(
            bank_reference,
            str,
        ) or len(bank_reference.strip()) > 100):
            return fail("Bank reference must be text of at most 100 characters.")

        amount_paid_value = d.get("amountPaid", d.get("amountReceived", 0))
        if "amountPaid" in d and "amountReceived" in d:
            if sale_money(
                d["amountPaid"],
                "amount paid",
                0,
            ) != sale_money(d["amountReceived"], "amount received", 0):
                return fail("amountPaid and amountReceived values do not match.")
        paid = sale_money(amount_paid_value, "amount received", 0)
        prepared, subtotal = _prepare_sale_items(d.get("items"))
        if subtotal <= 0:
            return fail("Invoice total must be greater than zero.")
        if payment_type == "full" and paid < subtotal:
            return fail("Full payment must be at least the invoice total.")
        if payment_type == "partial" and not (0 < paid < subtotal):
            return fail("Partial payment must be greater than zero and less than invoice total.")
        if payment_type == "credit" and paid != 0:
            return fail("Credit sale must have zero amount received.")

        applied = min(paid, subtotal)
        due = sale_money(max(Decimal("0.00"), subtotal - applied), "amount due", 0)
        if c and due:
            # A locking read is required here: the request may already have
            # established a REPEATABLE READ snapshot while checking idempotency.
            # Locking the customer serializes credit sales for that account, and
            # this current read observes any sale committed while we waited.
            outstanding= (db.session.query(func.coalesce(
                func.sum(Sale.amount_due),
                0,
            )).filter_by(customer_id=c.id).with_for_update().scalar())
            if Decimal(str(outstanding)) + due > dec(c.credit_limit):
                return fail("Customer credit limit exceeded. The sale is blocked.", 409)

        sale_payment_method = None if payment_type == "credit" else payment_method
        s = Sale(
            invoice_no=invoice_no,
            customer_id=c.id if c else None,
            customer_name=c.full_name if c else "Retail Customer",
            customer_type=ctype,
            subtotal=subtotal,
            total_amount=subtotal,
            amount_paid=applied,
            amount_received=paid,
            amount_due=due,
            payment_type=payment_type,
            payment_method=sale_payment_method,
            created_by=user.id,
        )
        db.session.add(s)
        db.session.flush()
        for item in prepared:
            stock = get_stock(
                item["stock_item"],
                item["stock_type"],
                item["variety"],
                item["package_size"],
            )
            if item["stock_type"] == "packed":
                if dec(stock.quantity_bags) < item["quantity"]:
                    raise ValueError(f"Insufficient packed stock for {item['product']}, {item['package_size']}kg bags.")
                move_stock(
                    item["stock_item"],
                    "packed",
                    item["variety"],
                    -item["total_kg"],
                    -item["quantity"],
                    item["package_size"],
                    "sale",
                    "sale",
                    s.id,
                )
            else:
                if dec(stock.quantity_kg) < item["total_kg"]:
                    raise ValueError(f"Insufficient stock for {item['product']}.")
                move_stock(
                    item["stock_item"],
                    item["stock_type"],
                    item["variety"],
                    -item["total_kg"],
                    movement="sale",
                    ref_type="sale",
                    ref_id=s.id,
                )
            db.session.add(SaleItem(
                sale_id=s.id,
                category=item["category"],
                stock_type=item["stock_type"],
                product_name=item["product"],
                product_id=item.get("product_id"),
                variety=item["variety"] or None,
                quantity=item["quantity"],
                unit=item["unit"],
                package_size_kg=item["package_size"],
                total_kg=item["total_kg"],
                price_per_kg=item["price"],
                line_total=item["line_total"],
            ))
        if applied and c:
            p = Payment(
                receipt_no=f"PAY-{datetime.now(COLOMBO_TIMEZONE):%Y%m%d%H%M%S%f}",
                customer_id=c.id,
                amount=applied,
                method=payment_method,
                payment_date=colombo_date(),
                bank_reference= (bank_reference or "").strip() or None,
                allocation_method="oldest_first",
                created_by=user.id,
            )
            db.session.add(p)
            db.session.flush()
            db.session.add(PaymentAllocation(payment_id=p.id, sale_id=s.id, amount=applied))
        db.session.add(AuditLog(
            user_id=user.id,
            action="create",
            entity="sale",
            entity_id=s.id,
            details={
                "request_hash": request_hash,
                "total": str(subtotal),
                "payment_type": payment_type,
            },
        ))
        db.session.commit()
        return jsonify({"success": True, "sale": sale_json(s)}), 201
    except (ValueError, InvalidOperation) as e:
        db.session.rollback()
        return fail(str(e))
    except IntegrityError:
        db.session.rollback()
        if invoice_no and request_hash:
            try:
                replay = _find_idempotent_sale(invoice_no, user.id, request_hash)
                if replay is not None:
                    return replay
            except SQLAlchemyError:
                db.session.rollback()
        return fail("The request conflicts with existing data. No sale was confirmed.", 409)
    except SQLAlchemyError as exc:
        db.session.rollback()
        current_app.logger.error("Sale transaction failed; exception_type=%s", type(exc).__name__)
        return fail("The sale could not be saved. No sale was confirmed. Please try again.", 500)


@api.get("/sales")
@login_required_api
@tab_required_api("Sales & Invoicing")
def sales_list():
    sales = Sale.query.order_by(Sale.id.desc()).all()
    customers = _sale_customer_map(sales)
    return jsonify({"success": True, "sales": [sale_json(s, customers) for s in sales]})


@api.get("/sales/history")
@login_required_api
@tab_required_api("Sales & Invoicing")
def sales_history():
    try:
        page, per_page, start, end = _customer_history_params()
    except ValueError as exc:
        return fail(str(exc))
    search = request.args.get("search", "").strip()
    payment_type = request.args.get("paymentType", "").strip().lower()
    transaction_type = request.args.get("transactionType", "").strip().lower()
    if len(search) > 120:
        return fail("Search must be 120 characters or fewer.")
    if payment_type and payment_type not in {"full", "partial", "credit"}:
        return fail("Payment type must be Full, Partial or Credit.")
    if transaction_type and transaction_type not in {"sale", "return"}:
        return fail("Transaction type must be Sale or Return.")

    query = Sale.query.outerjoin(
        Customer,
        Customer.id == Sale.customer_id,
    ).filter(Sale.status == "confirmed")
    if search:
        pattern = f"%{search}%"
        name_parts = search.split(maxsplit=1)
        customer_name_match = or_(
            Customer.first_name.ilike(pattern),
            Customer.last_name.ilike(pattern),
            and_(
                Customer.first_name.ilike(f"%{name_parts[0]}%"),
                Customer.last_name.ilike(f"%{name_parts[1]}%"),
            ) if len(name_parts) == 2 else literal(False),
        )
        query=query.filter(or_(
            Sale.invoice_no.ilike(pattern),
            Sale.customer_name.ilike(pattern),
            customer_name_match,
        ))
    if start:
        query = query.filter(Sale.sale_date >= datetime.combine(start, datetime.min.time()))
    if end:
        query=query.filter(Sale.sale_date < datetime.combine(
            end + timedelta(days=1),
            datetime.min.time(),
        ))
    if payment_type:
        query = query.filter(Sale.payment_type == payment_type)

    sales_summary = query.with_entities(
        func.count(Sale.id),
        func.coalesce(func.sum(Sale.total_amount), 0),
        func.coalesce(func.sum(Sale.amount_paid), 0),
        func.coalesce(func.sum(Sale.amount_due), 0),
    ).one()
    sale_rows = query.order_by(
        Sale.sale_date.desc(),
        Sale.id.desc(),
    ).all() if transaction_type != "return" else[]
    customers = _sale_customer_map(sale_rows)
    transactions=[{
        "id": s.id,
        "recordType": "sale",
        "reference": s.invoice_no,
        "invoiceNo": s.invoice_no,
        "date": _payment_local_datetime(s.sale_date),
        "customerId": s.customer_id,
        **_sale_customer_fields(s, customers),
        "transactionType": "Sale",
        "amount": as_float(s.total_amount),
        "status": "Completed",
        "paymentType": s.payment_type,
        "paymentMethod": s.payment_method,
        "paid": as_float(s.amount_paid),
        "due": as_float(s.amount_due),
    } for s in sale_rows]
    return_count = 0
    return_value_total = Decimal("0.00")
    if transaction_type != "sale" and not payment_type:
        return_query= (SalesReturn.query.join(
            Sale,
            Sale.id == SalesReturn.sale_id,
        ).outerjoin(Customer, Customer.id == Sale.customer_id).filter(SalesReturn.financial_status == "settled"))
        if search:
            pattern = f"%{search}%"
            parts = search.split(maxsplit=1)
            customer_match = or_(
                Customer.first_name.ilike(pattern),
                Customer.last_name.ilike(pattern),
                Sale.customer_name.ilike(pattern),
                and_(
                    Customer.first_name.ilike(f"%{parts[0]}%"),
                    Customer.last_name.ilike(f"%{parts[1]}%"),
                ) if len(parts) == 2 else literal(False),
            )
            return_query=return_query.filter(or_(
                SalesReturn.return_no.ilike(pattern),
                Sale.invoice_no.ilike(pattern),
                customer_match,
            ))
        if start:
            return_query=return_query.filter(SalesReturn.return_date >= datetime.combine(
                start,
                datetime.min.time(),
            ))
        if end:
            return_query=return_query.filter(SalesReturn.return_date < datetime.combine(
                end + timedelta(days=1),
                datetime.min.time(),
            ))
        closed_returns = return_query.order_by(
            SalesReturn.return_date.desc(),
            SalesReturn.id.desc(),
        ).all()
        return_count = len(closed_returns)
        for ret in closed_returns:
            sale = db.session.get(Sale, ret.sale_id)
            settlements= (ReturnSettlement.query.join(
                ReturnItem,
                ReturnItem.id == ReturnSettlement.return_item_id,
            ).filter(ReturnItem.return_id == ret.id).order_by(ReturnSettlement.id).all())
            labels = {settlement.settlement_type for settlement in settlements}
            label = "Return Exchange" if labels == {"exchange"} else "Return Bill"
            final_document = CombinedTransactionDocument.query.filter_by(
                final_return_id=ret.id,
                is_final=True,
            ).first()
            return_value = Decimal(str(ret.settlement_amount or 0)).quantize(Decimal("0.01"))
            return_value_total += return_value
            exchange_replacements = []
            replacement_subtotal = Decimal("0.00")
            net_amount_payable = Decimal("0.00")
            refund_due = Decimal("0.00")
            settlement_date = None
            if label == "Return Exchange":
                for settlement in settlements:
                    replacement_subtotal += Decimal(str(settlement.replacement_value or 0))
                    settlement_date = max(
                        settlement_date,
                        settlement.created_at,
                    ) if settlement_date else settlement.created_at
                    exchange_replacements.extend({
                        "product": row.product_name,
                        "variety": row.variety,
                        "category": row.category,
                        "quantity": str(row.quantity),
                        "unit": row.unit,
                        "packageSize": row.package_size_kg,
                        "pricePerKg": str(row.price_per_kg),
                        "lineTotal": str(row.line_total),
                    } for row in ReturnReplacementItem.query.filter_by(settlement_id=settlement.id).order_by(ReturnReplacementItem.id).all())
                exchange_difference = replacement_subtotal - return_value
                net_amount_payable = max(exchange_difference, Decimal("0.00"))
                refund_due = max(-exchange_difference, Decimal("0.00"))
            transactions.append({
                "id": ret.id,
                "recordType": "return",
                "reference": ret.return_no,
                "returnNo": ret.return_no,
                "invoiceNo": sale.invoice_no if sale else "",
                "date": _payment_local_datetime(ret.return_date),
                "customerId": sale.customer_id if sale else None,
                ** (_sale_customer_fields(sale) if sale else customer_fields(
                    "Retail Customer",
                    "retail",
                    retail_customer=True,
                )),
                "transactionType": label,
                "amount": as_float(return_value),
                "returnValue": as_float(return_value),
                "status": "Closed",
                "financialStatus": ret.financial_status,
                "inventoryStatus": _return_inventory_status(ret),
                "documentNo": final_document.document_no if final_document else None,
                "settlementDate": _payment_local_datetime(settlement_date),
                "replacementItems": exchange_replacements,
                "replacementSubtotal": as_float(replacement_subtotal),
                "approvedReturnValue": as_float(return_value),
                "netAmountPayable": as_float(net_amount_payable),
                "refundDue": as_float(refund_due),
                "receiptUrl": f"/return-receipt/{final_document.document_no}" if final_document else None,
            })
    transactions.sort(key=lambda row: (row["date"], row["recordType"], row["id"]), reverse=True)
    total = len(transactions)
    pages = (total + per_page - 1) // per_page
    paged = transactions[(page - 1) * per_page:page * per_page]
    return jsonify({
        "success": True,
        "sales": paged,
        "summary": {
            "count": total,
            "salesCount": int(sales_summary[0]) if transaction_type != "return" else 0,
            "returnCount": return_count,
            "amount": as_float(sales_summary[1]) if transaction_type != "return" else 0,
            "returnValue": as_float(return_value_total),
            "paid": as_float(sales_summary[2]) if transaction_type != "return" else 0,
            "due": as_float(sales_summary[3]) if transaction_type != "return" else 0,
        },
        "pagination": {"page": page, "perPage": per_page, "total": total, "pages": pages},
    })


@api.get("/sales/customers")
@limiter.limit("60 per minute")
@login_required_api
@tab_required_api("Sales & Invoicing")
def sales_customer_lookup():
    term = request.args.get("q", "").strip()
    if len(term) < 2:
        return jsonify({"success": True, "customers": []})
    parts = term.split(maxsplit=1)
    predicates = [
        Customer.first_name.ilike(f"%{term}%"),
        Customer.last_name.ilike(f"%{term}%"),
        Customer.phone.ilike(f"%{term}%"),
        Customer.customer_code.ilike(f"%{term}%"),
    ]
    if len(parts) == 2:
        predicates.append(and_(
            Customer.first_name.ilike(f"%{parts[0]}%"),
            Customer.last_name.ilike(f"%{parts[1]}%"),
        ))
    rows= (db.session.query(
        Customer,
        func.coalesce(func.sum(Sale.amount_due), 0),
    ).outerjoin(Sale, Sale.customer_id == Customer.id).filter(Customer.is_active.is_(True), or_(*predicates)).group_by(Customer.id).order_by(Customer.first_name, Customer.last_name, Customer.id).limit(30).all())
    return jsonify({
        "success": True,
        "customers": [{
            "id": customer.id,
            "name": customer.full_name,
            "customerCode": customer.customer_code,
            **customer_fields(customer.full_name, "registered", registered_customer=True),
            "phone": customer.phone,
            "creditLimit": as_float(customer.credit_limit),
            "outstanding": as_float(due),
        } for customer, due in rows],
    })


@api.get("/sales/catalog")
@limiter.limit("30 per minute")
@login_required_api
@tab_required_api("Sales & Invoicing")
def sales_catalog():
    masters = MasterItem.query.filter(
        MasterItem.is_active.is_(True),
        MasterItem.group_name.in_(("rice_variety", "product", "package_size")),
    ).order_by(MasterItem.group_name, MasterItem.name).all()
    prices = PriceSetting.query.filter_by(price_type="selling", is_active=True).all()
    stocks = Stock.query.filter(Stock.stock_type.in_(("rice", "packed", "bran", "other"))).order_by(
        Stock.stock_type,
        Stock.item_name,
        Stock.variety,
        Stock.package_size_kg,
    ).all()
    return jsonify({
        "success": True,
        "varieties": [row.name for row in masters if row.group_name == "rice_variety"],
        "products": [row.name for row in masters if row.group_name == "product"],
        "varietyProducts": [{
            "id": row.id,
            "name": row.name,
            "unit": row.unit or "kg",
            "categories": [category.code for category in row.main_categories if category.is_active],
        } for row in masters if row.group_name == "rice_variety"],
        "sellableProducts": [{
            "id": row.id,
            "name": row.name,
            "unit": row.unit or "kg",
            "categories": [category.code for category in row.main_categories if category.is_active],
        } for row in masters if row.group_name == "product"],
        "packageSizes": [row.name for row in masters if row.group_name == "package_size"],
        "prices": [{
            "item": row.item_name,
            "price": as_float(row.default_price) if row.default_price is not None else None,
            "locked": bool(row.selling_locked and row.default_price is not None),
        } for row in prices],
        "stocks": [{
            "productId": row.product_id,
            "item": row.item_name,
            "type": row.stock_type,
            "section": _inventory_balance_json(row)["section"],
            "variety": row.variety or "",
            "quantityKg": as_float(row.quantity_kg),
            "quantityBags": as_float(row.quantity_bags),
            "packageSize": row.package_size_kg,
        } for row in stocks],
    })


@api.get("/sales/<int:sid>")
@login_required_api
@tab_required_api("Sales & Invoicing")
def sale_detail(sid):
    s = db.session.get(Sale, sid)
    return (jsonify({
        "success": True,
        "sale": sale_json(s),
    }) if s else fail("Invoice not found.", 404))


@api.post("/held-sales")
@limiter.limit("10 per minute")
@login_required_api
@tab_required_api("Sales & Invoicing")
def held_sale_create():
    request_data = request.get_json(silent=True)
    if not isinstance(request_data, dict) or not isinstance(request_data.get("draft"), dict):
        return fail("A complete held draft is required.")
    user = current_user()
    draft_type = request_data.get("draftType", "sales_bill")
    if draft_type not in {"sales_bill", "return_bill"}:
        return fail("Select a supported held draft type.")
    raw_key = request.headers.get("Idempotency-Key") or request_data.get("idempotencyKey") or ""
    body_key = request_data.get("idempotencyKey")
    header_key = request.headers.get("Idempotency-Key")
    if header_key and body_key and header_key != body_key:
        return fail("Header and body idempotency keys do not match.")
    if not isinstance(raw_key, str):
        return fail("Idempotency key must be text.")
    raw_key = raw_key.strip()
    if not 16 <= len(raw_key) <= 200:
        return fail("A stable idempotency key of 16 to 200 characters is required.")
    reference = None
    request_hash = None
    entity_name = "held_return_bill" if draft_type == "return_bill" else "held_sale"
    try:
        canonical = json.dumps(
            {"draftType": draft_type, "draft": request_data["draft"]},
            sort_keys=True,
            separators= (",", ":"),
            ensure_ascii=False,
        )
        request_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        namespace = uuid5(NAMESPACE_URL, f"rice-mill-user:{user.id}:held-sale")
        uuid_part = uuid5(namespace, raw_key).hex.upper()
        reference = (f"HOLD-RET-{uuid_part[:24]}" if draft_type == "return_bill" else f"HOLD-{uuid_part}")
        held_draft_id = request_data.get("heldDraftId")
        existing = None
        if held_draft_id not in (None, ""):
            try:
                held_draft_id = int(held_draft_id)
            except (TypeError, ValueError):
                return fail("A valid held Return Bill reference is required.")
            query = HeldSale.query.filter_by(id=held_draft_id, draft_type=draft_type)
            if user.role != "admin":
                query = query.filter_by(created_by=user.id)
            existing = query.with_for_update().first()
            if not existing:
                return fail("Held Return Bill not found.", 404)
            if draft_type != "return_bill":
                return fail("Only a held Return Bill can be updated from this workflow.", 409)
            draft = _validate_held_return_bill_draft(request_data["draft"])
            existing.payload = draft
            db.session.add(AuditLog(
                user_id=user.id,
                action="update",
                entity=entity_name,
                entity_id=existing.id,
                details={"reference": existing.hold_ref, "request_hash": request_hash},
            ))
            db.session.commit()
            return jsonify({
                "success": True,
                "heldSale": held_sale_json(existing),
                "updated": True,
            }), 200
        existing = HeldSale.query.filter_by(hold_ref=reference).first()
        if existing:
            if existing.created_by != user.id:
                return fail("This held sale request conflicts with an existing record.", 409)
            log = AuditLog.query.filter_by(
                entity=entity_name,
                entity_id=existing.id,
                action="create",
            ).order_by(AuditLog.id.desc()).first()
            if not log or (log.details or {}).get("request_hash") != request_hash:
                return fail(
                    "This idempotency key was already used with different draft details.",
                    409,
                )
            return jsonify({
                "success": True,
                "heldSale": held_sale_json(existing),
                "replayed": True,
            }), 200
        draft = (_validate_held_return_bill_draft(request_data["draft"]) if draft_type == "return_bill" else _validate_held_draft(request_data["draft"]))
        held = HeldSale(
            hold_ref=reference,
            payload=draft,
            created_by=user.id,
            draft_type=draft_type,
        )
        db.session.add(held)
        db.session.flush()
        db.session.add(AuditLog(
            user_id=user.id,
            action="create",
            entity=entity_name,
            entity_id=held.id,
            details={"request_hash": request_hash},
        ))
        db.session.commit()
        return jsonify({"success": True, "heldSale": held_sale_json(held)}), 201
    except ValueError as exc:
        db.session.rollback()
        return fail(str(exc))
    except IntegrityError:
        db.session.rollback()
        if reference and request_hash:
            existing = HeldSale.query.filter_by(hold_ref=reference).first()
            if existing and existing.created_by == user.id:
                log = AuditLog.query.filter_by(
                    entity=entity_name,
                    entity_id=existing.id,
                    action="create",
                ).order_by(AuditLog.id.desc()).first()
                if log and (log.details or {}).get("request_hash") == request_hash:
                    return jsonify({
                        "success": True,
                        "heldSale": held_sale_json(existing),
                        "replayed": True,
                    }), 200
        return fail(
            "The held sale could not be saved because the request conflicts with an existing record. Retry with the same key.",
            409,
        )
    except SQLAlchemyError as exc:
        db.session.rollback()
        current_app.logger.error(
            "Held sale transaction failed; exception_type=%s",
            type(exc).__name__,
        )
        return fail("The held sale could not be saved. Please try again.", 500)


def _validate_held_return_bill_draft(source):
    invoice_no = source.get("invoiceNo")
    if not isinstance(invoice_no, str) or not invoice_no.strip():
        raise ValueError("Select an original invoice before holding the Return Bill.")
    sale = Sale.query.filter_by(invoice_no=invoice_no.strip(), status="confirmed").first()
    if not sale:
        raise ValueError("A confirmed original invoice is required.")
    source_items = source.get("items", [])
    if not isinstance(source_items, list) or len(source_items) > 100:
        raise ValueError("A held Return Bill can contain at most 100 item rows.")
    normalized = []
    seen = set()
    total = Decimal("0.00")
    for row in source_items:
        if not isinstance(row, dict):
            raise ValueError("Each held return item must be an object.")
        try:
            sale_item_id = int(row.get("saleItemId"))
        except (TypeError, ValueError):
            raise ValueError("A valid original invoice item is required.")
        if sale_item_id in seen:
            raise ValueError("An invoice item can appear only once in a held Return Bill.")
        seen.add(sale_item_id)
        si = SaleItem.query.filter_by(id=sale_item_id, sale_id=sale.id).first()
        if not si:
            raise ValueError("A held item does not belong to the original invoice.")
        qty = dec(row.get("quantity"), "return quantity", Decimal("0.001"))
        if si.category == "packed" and qty != qty.to_integral_value():
            raise ValueError("Packed returns must use whole bags.")
        available = Decimal(str(si.quantity)) - Decimal(str(_returnable_quantity(si.id) or 0))
        if qty > available:
            raise ValueError("Return quantity exceeds the remaining quantity on the original invoice.")
        total_kg = qty * Decimal(str(si.package_size_kg or 1))
        line = (total_kg * Decimal(str(si.price_per_kg))).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        total += line
        normalized.append({
            "saleItemId": si.id,
            "category": si.category,
            "product": si.product_name,
            "variety": si.variety or "",
            "quantity": str(qty),
            "unit": si.unit,
            "packageSize": si.package_size_kg,
            "originalPrice": str(si.price_per_kg),
            "totalKg": str(total_kg),
            "returnValue": str(line),
        })
    customer = _sale_customer_fields(sale)
    return {
        "kind": "return_bill",
        "invoiceId": sale.id,
        "invoiceNo": sale.invoice_no,
        "invoiceDate": _payment_local_datetime(sale.sale_date),
        "invoiceTotal": str(sale.total_amount),
        "customerId": sale.customer_id,
        "customerName": customer.get("customerName") or sale.customer_name or "Retail Customer",
        "customerType": customer.get("customerType") or ("registered" if sale.customer_id else "walkin"),
        "items": normalized,
        "returnValue": str(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "draftStatus": "held",
    }


def _validate_held_draft(source):
    source_items = source.get("items")
    if "discount" in source or (isinstance(
        source_items,
        list,
    ) and any(isinstance(item, dict) and "discount" in item for item in source_items)):
        raise ValueError("Discounts are not supported for sales drafts.")
    customer_type = source.get("customerType", "walkin")
    if customer_type == "walk_in":
        customer_type = "walkin"
    if not isinstance(customer_type, str) or customer_type not in {"walkin", "registered"}:
        raise ValueError("Select Retail Customer or Registered Customer.")
    customer = None
    customer_id = source.get("customerId")
    if customer_type == "registered":
        if customer_id in (None, "") or isinstance(customer_id, bool):
            raise ValueError("Select an active Registered Customer before holding the sale.")
        try:
            customer_id = int(customer_id)
        except (TypeError, ValueError):
            raise ValueError("Select an active Registered Customer before holding the sale.")
        customer = Customer.query.filter_by(id=customer_id, is_active=True).first()
        if not customer:
            raise ValueError("Active Registered Customer not found.")
    elif customer_id not in (None, ""):
        raise ValueError("Retail Customer sales cannot be linked to a registered customer.")

    payment_type = source.get("paymentType", "full")
    if not isinstance(payment_type, str) or payment_type not in {"full", "partial", "credit"}:
        raise ValueError("Payment type must be Full, Partial or Credit.")
    if customer_type == "walkin" and payment_type != "full":
        raise ValueError("Retail Customers must make full payment.")
    method = source.get("paymentMethod", source.get("payment_method", "cash"))
    if not isinstance(method, str):
        raise ValueError("Payment method must be Cash or Bank Transfer.")
    method_key = method.strip().lower().replace("_", " ")
    method_map = {"cash": "cash", "bank": "bank", "bank transfer": "bank"}
    if method_key not in method_map:
        raise ValueError("Payment method must be Cash or Bank Transfer.")

    if not isinstance(source_items, list) or not source_items:
        raise ValueError("At least one complete sale item is required before holding.")
    api_items = []
    for item in source_items:
        if not isinstance(item, dict):
            raise ValueError("Every held sale item must be an object.")
        api_item={key: item.get(key) for key in (
            "category",
            "product",
            "variety",
            "quantity",
            "price",
            "unit",
        ) if key in item}
        package = item.get("packageSize", item.get("package"))
        if package not in (None, ""):
            api_item["packageSize"] = package
        api_items.append(api_item)
    prepared, subtotal = _prepare_sale_items(api_items)
    requirements = {}
    for item in prepared:
        key = (item["stock_item"], item["stock_type"], item["variety"] or "", item["package_size"])
        required = requirements.setdefault(key, {"kg": Decimal("0"), "bags": Decimal("0")})
        required["kg"] += item["total_kg"]
        if item["stock_type"] == "packed":
            required["bags"] += item["quantity"]
    for (stock_item, stock_type, variety, package_size), required in requirements.items():
        stock = Stock.query.filter_by(
            item_name=stock_item,
            stock_type=stock_type,
            variety=variety,
            package_size_kg=package_size,
        ).first()
        if (
            not stock
            or Decimal(str(stock.quantity_kg)) < required["kg"]
            or Decimal(str(stock.quantity_bags)) < required["bags"]
        ):
            raise ValueError(f"Insufficient stock for {stock_item}; the draft was not held.")

    if "amountReceived" in source and "amountPaid" in source and sale_money(
        source["amountReceived"],
        "amount received",
        0,
    ) != sale_money(source["amountPaid"], "amount paid", 0):
        raise ValueError("amountReceived and amountPaid values do not match.")
    paid = sale_money(
        source.get("amountReceived", source.get("amountPaid", 0)),
        "amount received",
        0,
    )
    if payment_type == "full" and paid < subtotal:
        raise ValueError("Full payment must be at least the invoice total.")
    if payment_type == "partial" and not (0 < paid < subtotal):
        raise ValueError("Partial payment must be greater than zero and less than invoice total.")
    if payment_type == "credit" and paid != 0:
        raise ValueError("Credit sale amount received must be zero.")

    normalized = []
    for item in prepared:
        normalized.append({
            "category": item["category"],
            "product": item["product"],
            "variety": item["variety"] or "",
            "package": str(item["package_size"] or ""),
            "quantity": str(item["quantity"]),
            "price": str(item["price"]),
        })
    return {
        "customerType": customer_type,
        "customerName": customer.full_name if customer else "",
        "customerId": customer.id if customer else None,
        "paymentType": payment_type,
        "paymentMethod": method_map[method_key],
        "amountReceived": str(paid),
        "items": normalized,
    }


def held_sale_json(held):
    data = dict(held.payload) if isinstance(held.payload, dict) else {}
    if held.draft_type == "return_exchange":
        return {
            "id": held.id,
            "reference": held.hold_ref,
            "type": "return_exchange",
            "data": data,
            "createdAt": held.created_at.isoformat() if held.created_at else None,
            "updatedAt": held.updated_at.isoformat() if held.updated_at else None,
        }
    if held.draft_type == "return_bill":
        return {
            "id": held.id,
            "reference": held.hold_ref,
            "type": "return_bill",
            "data": data,
            "createdAt": held.created_at.isoformat() if held.created_at else None,
            "updatedAt": held.updated_at.isoformat() if held.updated_at else None,
        }
    customer_id = data.get("customerId")
    customer = db.session.get(Customer, customer_id) if customer_id is not None else None
    data.update(resolve_transaction_customer(
        customer,
        data.get("customerName"),
        data.get("customerType"),
    ))
    return {
        "id": held.id,
        "reference": held.hold_ref,
        "type": "sales_bill",
        "data": data,
        "createdAt": held.created_at.isoformat() if held.created_at else None,
    }


def _held_return_exchange_data(source, ret, item):
    if not isinstance(source, dict):
        raise ValueError("A Return Exchange draft object is required.")
    replacements = source.get("replacements", [])
    if not isinstance(replacements, list) or len(replacements) > 100:
        raise ValueError("A Return Exchange draft can contain at most 100 replacement lines.")
    clean = []
    allowed = {
        "category",
        "product",
        "productId",
        "stockType",
        "variety",
        "quantity",
        "unit",
        "packageSize",
        "price",
        "manualPriceReason",
        "confirmManualPrice",
    }
    for row in replacements:
        if not isinstance(row, dict):
            raise ValueError("Each replacement line must be an object.")
        clean_row = {key: row.get(key) for key in allowed if key in row}
        for key in ("category", "product", "variety", "unit", "price", "manualPriceReason"):
            value = clean_row.get(key)
            if value is not None and (not isinstance(value, str) or len(value) > 2000):
                raise ValueError(f"Replacement {key} is invalid.")
        clean.append(clean_row)
    payment_method = source.get("paymentMethod", "Cash")
    payment_reference = source.get("paymentReference", "")
    refund_method = source.get("refundMethod", "Cash")
    refund_reference = source.get("refundReference", "")
    if payment_method not in {"Cash", "Bank Transfer"}:
        raise ValueError("Select Cash or Bank Transfer for the exchange difference.")
    if not isinstance(payment_reference, str) or len(payment_reference) > 100:
        raise ValueError("The bank transfer reference is invalid.")
    if refund_method not in {"Cash", "Bank Transfer"}:
        raise ValueError("Select Cash or Bank Transfer for the exchange refund.")
    if not isinstance(refund_reference, str) or len(refund_reference) > 100:
        raise ValueError("The refund bank transfer reference is invalid.")
    cash_received = sale_money(source.get("cashReceived", 0), "cash received", Decimal("0.00"))
    sale = db.session.get(Sale, ret.sale_id)
    sale_item = db.session.get(SaleItem, item.sale_item_id)
    return_value = (Decimal(str(item.total_kg)) * Decimal(str(sale_item.price_per_kg))).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    quoted_value = None
    try:
        if clean and all(row.get("category") and row.get("product") and row.get("quantity") not in (
            None,
            "",
        ) for row in clean):
            prepared, _ = _prepare_sale_items(clean)
            quoted = Decimal("0.00")
            for part in prepared:
                price_key = part["variety"] if part["category"] in {
                    "bulk",
                    "packed",
                } else part["product"]
                setting = PriceSetting.query.filter_by(
                    item_name=price_key,
                    price_type="selling",
                    is_active=True,
                ).first()
                price= (sale_money(
                    setting.default_price,
                    "current replacement selling price",
                    Decimal("0.01"),
                ) if setting and setting.default_price is not None else Decimal(str(part["price"])))
                quoted += sale_money(
                    part["total_kg"] * price,
                    "replacement line total",
                    Decimal("0.01"),
                )
            quoted_value = str(quoted)
    except (ValueError, TypeError):
        quoted_value = None
    difference = (Decimal(quoted_value) - return_value) if quoted_value is not None else None
    return {
        "returnId": ret.id,
        "returnItemId": item.id,
        "returnNo": ret.return_no,
        "invoiceNo": sale.invoice_no,
        "returnValue": str(return_value),
        "returnedItem": {
            "product": sale_item.product_name,
            "variety": sale_item.variety,
            "quantity": str(item.quantity),
            "unit": sale_item.unit,
            "packageSize": sale_item.package_size_kg,
            "returnValue": str(return_value),
        },
        "replacements": clean,
        "paymentMethod": payment_method,
        "paymentReference": payment_reference,
        "cashReceived": str(cash_received),
        "refundMethod": refund_method,
        "refundReference": refund_reference,
        "replacementValue": quoted_value,
        "difference": str(difference) if difference is not None else None,
        "draftStatus": "Held",
    }


@api.post("/held-sales/return-exchanges")
@limiter.limit("20 per minute")
@login_required_api
@tab_required_api("Sales & Invoicing")
def held_return_exchange_save():
    d = request.get_json(silent=True) or {}
    if not isinstance(d, dict):
        return fail("A Return Exchange draft is required.")
    user = current_user()
    try:
        return_id = d.get("returnId")
        if return_id not in (None, ""):
            try:
                return_id = int(return_id)
            except (TypeError, ValueError):
                return fail("A valid approved Return Bill is required.")
            ret = SalesReturn.query.filter_by(id=return_id).with_for_update().first()
            if not ret:
                return fail("Return Bill not found.", 404)
            if ret.approval_status != "approved" or ret.financial_status not in {
                "pending_settlement",
                "partially_settled",
            }:
                return fail("Only an approved, open Return Bill can be held for exchange.", 409)
            items = ReturnItem.query.filter_by(return_id=ret.id).order_by(ReturnItem.id).with_for_update().all()
            remaining = [item for item in items if item.settlement_status != "settled" and not ReturnSettlement.query.filter_by(return_item_id=item.id).first()]
            if not remaining:
                return fail("No unsettled Return Bill items remain for exchange.", 409)
            item = remaining[0]
            item_id = item.id
            existing= (HeldSale.query.join(ReturnItem, HeldSale.return_item_id == ReturnItem.id).filter(
                ReturnItem.return_id == ret.id,
                HeldSale.draft_type == "return_exchange",
            ).with_for_update().first())
        else:
            try:
                item_id = int(d.get("returnItemId"))
            except (TypeError, ValueError):
                return fail("A valid approved return item is required.")
            existing = None
        item = ReturnItem.query.filter_by(id=item_id).with_for_update().first()
        if not item:
            return fail("Return item not found.", 404)
        ret = SalesReturn.query.filter_by(id=item.return_id).with_for_update().first()
        if ret.approval_status != "approved":
            return fail("The Return Bill must be Admin approved before exchange.", 409)
        if (
            item.settlement_status == "settled"
            or ReturnSettlement.query.filter_by(return_item_id=item.id).first()
        ):
            return fail("This return item has already been settled.", 409)
        if existing is None:
            existing = HeldSale.query.filter_by(
                return_item_id=item.id,
                draft_type="return_exchange",
            ).with_for_update().first()
        if existing and existing.created_by != user.id:
            return fail("This return item is already held by another user.", 409)
        payload = _held_return_exchange_data(d, ret, item)
        if return_id not in (None, ""):
            sale = db.session.get(Sale, ret.sale_id)
            total_return_value = sum(
                (Decimal(str(row.get("returnValue") or 0)) for row in (_return_item_json(part) for part in remaining)),
                Decimal("0.00"),
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            payload.update({
                "kind": "return_exchange",
                "returnId": ret.id,
                "returnNo": ret.return_no,
                "invoiceNo": sale.invoice_no,
                "customerId": sale.customer_id,
                "customerName": sale.customer_name,
                "returnValue": str(total_return_value),
                "returnItemId": item.id,
            })
        if existing:
            existing.payload = payload
            db.session.add(AuditLog(
                user_id=user.id,
                action="update",
                entity="held_return_exchange",
                entity_id=existing.id,
                details={"reference": existing.hold_ref},
            ))
            db.session.commit()
            return jsonify({"success": True, "heldSale": held_sale_json(existing), "updated": True})
        reference = f"HOLD-RET-{uuid4().hex[:12].upper()}"
        held = HeldSale(
            hold_ref=reference,
            payload=payload,
            created_by=user.id,
            draft_type="return_exchange",
            return_item_id=item.id,
        )
        db.session.add(held)
        db.session.flush()
        db.session.add(AuditLog(
            user_id=user.id,
            action="create",
            entity="held_return_exchange",
            entity_id=held.id,
            details={"reference": reference, "return_item_id": item.id},
        ))
        db.session.commit()
        return jsonify({"success": True, "heldSale": held_sale_json(held)}), 201
    except (ValueError, TypeError) as exc:
        db.session.rollback()
        return fail(str(exc))
    except IntegrityError:
        db.session.rollback()
        return fail("This return item is held by another active exchange draft.", 409)
    except SQLAlchemyError as exc:
        db.session.rollback()
        current_app.logger.error(
            "Return Exchange hold failed; exception_type=%s",
            type(exc).__name__,
        )
        return fail("The Return Exchange draft could not be saved. Please try again.", 500)


@api.get("/held-sales")
@login_required_api
@tab_required_api("Sales & Invoicing")
def held_sales_list():
    query = HeldSale.query
    user = current_user()
    if user.role != "admin":
        query = query.filter_by(created_by=user.id)
    return jsonify({
        "success": True,
        "heldSales": [held_sale_json(h) for h in query.order_by(
            HeldSale.created_at.desc(),
            HeldSale.id.desc(),
        ).all()],
    })


@api.get("/held-sales/<int:hid>")
@login_required_api
@tab_required_api("Sales & Invoicing")
def held_sale_detail(hid):
    held = HeldSale.query.filter_by(id=hid).first()
    if not held or (current_user().role != "admin" and held.created_by != current_user().id):
        return fail("Held sale not found.", 404)
    return jsonify({"success": True, "heldSale": held_sale_json(held)})


@api.delete("/held-sales/<int:hid>")
@limiter.limit("20 per minute")
@login_required_api
@tab_required_api("Sales & Invoicing")
def held_sale_delete(hid):
    query = HeldSale.query.filter_by(id=hid)
    if current_user().role != "admin":
        query = query.filter_by(created_by=current_user().id)
    held = query.with_for_update().first()
    if not held:
        return fail("Held sale not found.", 404)
    try:
        db.session.delete(held)
        db.session.flush()
        db.session.add(AuditLog(
            user_id=current_user().id,
            action="delete",
            entity={
                "return_exchange": "held_return_exchange",
                "return_bill": "held_return_bill",
            }.get(held.draft_type, "held_sale"),
            entity_id=hid,
            details={"reference": held.hold_ref},
        ))
        db.session.commit()
        return jsonify({"success": True})
    except SQLAlchemyError as exc:
        db.session.rollback()
        current_app.logger.error("Held sale deletion failed; exception_type=%s", type(exc).__name__)
        return fail("The held sale could not be deleted. Please try again.", 500)


def _return_item_json(item):
    sale_item = db.session.get(SaleItem, item.sale_item_id)
    value = (Decimal(str(item.total_kg)) * Decimal(str(sale_item.price_per_kg))).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return {
        "id": item.id,
        "saleItemId": item.sale_item_id,
        "product": sale_item.product_name,
        "category": sale_item.category,
        "variety": sale_item.variety,
        "quantity": as_float(item.quantity),
        "unit": sale_item.unit,
        "packageSize": sale_item.package_size_kg,
        "totalKg": as_float(item.total_kg),
        "originalPrice": as_float(sale_item.price_per_kg),
        "returnValue": str(value),
        "decision": item.decision,
        "inspectionNotes": item.inspection_notes,
        "inspectedAt": item.inspected_at.isoformat() if item.inspected_at else None,
        "settlementStatus": item.settlement_status,
    }


def _return_inventory_status(ret):
    decisions = [item.decision for item in ret.items]
    if any(value == "pending" for value in decisions):
        return "Pending Inspection"
    if decisions and all(value == "approved" for value in decisions):
        return "Inventory Approved"
    if decisions and all(value == "rejected" for value in decisions):
        return "Inventory Rejected"
    return "Mixed Inspection"


def _return_settlement_state(ret):
    """Return settlement state backed by the saved settlement and financial records."""
    items = list(ret.items)
    if not items:
        return False, [], None, Decimal("0.00"), None
    settlements= (ReturnSettlement.query.join(
        ReturnItem,
        ReturnItem.id == ReturnSettlement.return_item_id,
    ).filter(ReturnItem.return_id == ret.id).order_by(ReturnSettlement.created_at, ReturnSettlement.id).all())
    valid = []
    item_ids = {item.id for item in items}
    for settlement in settlements:
        returned = db.session.get(ReturnItem, settlement.return_item_id)
        if not returned or returned.id not in item_ids or returned.settlement_status != "settled":
            continue
        entries = ReturnFinancialEntry.query.filter_by(settlement_id=settlement.id).all()
        if settlement.settlement_type == "refund":
            valid_financial = (Decimal(str(settlement.cash_refund or 0)) == Decimal(str(settlement.return_value)) and any(row.entry_type == "cash_refund" and Decimal(str(row.amount)) == Decimal(str(settlement.cash_refund)) for row in entries))
        elif settlement.settlement_type == "exchange":
            replacements = ReturnReplacementItem.query.filter_by(settlement_id=settlement.id).all()
            replacement_total = sum(
                (Decimal(str(row.line_total)) for row in replacements),
                Decimal("0.00"),
            )
            delta = replacement_total - Decimal(str(settlement.return_value))
            if delta > 0:
                valid_financial = any(row.entry_type == "exchange_difference_collection" and Decimal(str(row.amount)) == delta for row in entries)
            elif delta < 0:
                valid_financial = any(row.entry_type == "exchange_difference_refund" and Decimal(str(row.amount)) == abs(delta) for row in entries)
            else:
                valid_financial = not entries
            valid_financial = valid_financial and bool(replacements) and replacement_total == Decimal(str(settlement.replacement_value or 0))
        else:
            valid_financial = False
        if valid_financial:
            valid.append(settlement)
    fully_settled = all(item.settlement_status == "settled" for item in items) and bool(valid)
    if fully_settled:
        values = sum((Decimal(str(row.return_value)) for row in valid), Decimal("0.00"))
        expected = sum(
            (Decimal(str(_return_item_json(item).get("returnValue") or 0)) for item in items),
            Decimal("0.00"),
        )
        fully_settled = values == expected
    final_document= (CombinedTransactionDocument.query.filter_by(
        final_return_id=ret.id,
        is_final=True,
    ).order_by(CombinedTransactionDocument.id.desc()).first()) if fully_settled else None
    if fully_settled:
        fully_settled = bool(final_document and any(row.id == final_document.settlement_id for row in valid))
    types = sorted({row.settlement_type for row in valid})
    amount = sum(
        (Decimal(str(row.cash_refund or 0)) + Decimal(str(row.difference_refund or 0)) for row in valid),
        Decimal("0.00"),
    )
    return fully_settled, valid, types, amount, final_document


def _return_workflow_active(ret):
    """Whether a Return Bill still needs approval or financial settlement."""
    if ret.approval_status in {None, "pending"}:
        return True
    if ret.approval_status != "approved":
        return False
    settled, _, _, _, _ = _return_settlement_state(ret)
    # Physical inspection is an independent Inventory workflow. Once the
    # financial settlement is complete, the Return Bill leaves Sales alerts.
    return not settled


def _return_refund_preview(ret, include_settled=False):
    sale = Sale.query.filter_by(id=ret.sale_id).first()
    if not sale:
        raise ValueError("The original invoice could not be found.")
    settlements= (ReturnSettlement.query.join(
        ReturnItem,
        ReturnItem.id == ReturnSettlement.return_item_id,
    ).filter(ReturnItem.return_id == ret.id).all())
    settled_item_ids = {row.return_item_id for row in settlements}
    items = []
    total = Decimal("0.00")
    for returned in ReturnItem.query.filter_by(return_id=ret.id).order_by(ReturnItem.id).all():
        if (
            not include_settled
            and (returned.id in settled_item_ids or returned.settlement_status == "settled")
        ):
            continue
        original = SaleItem.query.filter_by(id=returned.sale_item_id, sale_id=sale.id).first()
        if not original:
            raise ValueError("A returned product no longer matches the original invoice.")
        unit_price = Decimal(str(original.price_per_kg)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        value = (Decimal(str(returned.total_kg)) * unit_price).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        total += value
        items.append({
            "product": original.product_name,
            "variety": original.variety or "",
            "quantity": str(returned.quantity),
            "unit": original.unit,
            "packageSize": original.package_size_kg,
            "price": str(unit_price),
            "value": str(value),
        })
    total = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    mill_setting = Setting.query.filter_by(key="mill_name").first()
    return {
        "returnId": ret.id,
        "refundReference": f"REF-{ret.return_no}",
        "returnNo": ret.return_no,
        "invoiceNo": sale.invoice_no,
        "customerName": _sale_customer_fields(sale).get("customerName") or sale.customer_name or "Retail Customer",
        "items": items,
        "totalRefund": str(total),
        "paymentMethod": "Cash",
        "date": datetime.utcnow().isoformat(),
        "millName": mill_setting.value if mill_setting else "Rice Mill",
    }


def _return_json(ret):
    sale = db.session.get(Sale, ret.sale_id)
    customer_fields_for_display = _sale_customer_fields(sale) if sale else customer_fields(
        "Retail Customer",
        "retail",
        retail_customer=True,
    )
    settlement_types=sorted({row[0] for row in db.session.query(ReturnSettlement.settlement_type).join(
        ReturnItem,
        ReturnItem.id == ReturnSettlement.return_item_id,
    ).filter(ReturnItem.return_id == ret.id).all()})
    return {
        "id": ret.id,
        "returnNo": ret.return_no,
        "invoiceNo": sale.invoice_no if sale else None,
        "customerId": sale.customer_id if sale else None,
        **customer_fields_for_display,
        "approvalStatus": ret.approval_status,
        "approvedAt": ret.approved_at.isoformat() if ret.approved_at else None,
        "approvedBy": ((db.session.get(
            User,
            ret.approved_by,
        ).first_name + " " + (db.session.get(User, ret.approved_by).last_name or "")).strip() if ret.approved_by and db.session.get(User, ret.approved_by) else None),
        "approvalNotes": ret.approval_notes,
        "settlementTypes": settlement_types,
        "status": _return_inventory_status(ret),
        "financialStatus": ret.financial_status,
        "date": ret.return_date.isoformat(),
        "items": [_return_item_json(i) for i in ret.items],
    }


def _return_approval_notification_json(notification, ret_override=None):
    ret=ret_override or (db.session.get(
        SalesReturn,
        notification.return_id,
    ) if notification else None)
    sale = db.session.get(Sale, ret.sale_id) if ret else None
    admin = db.session.get(User, ret.approved_by) if ret and ret.approved_by else None
    settled, settlements, settlement_types, refund_total, final_document = _return_settlement_state(ret) if ret else(
        False,
        [],
        [],
        Decimal("0.00"),
        None,
    )
    refund_total = refund_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    decision_label = {
        "approved": "Return Bill Approved",
        "rejected": "Return Bill Rejected",
        "cancelled": "Return Bill Cancelled",
    }.get(ret.approval_status, "Return Bill Pending Admin Approval") if ret else "Return Bill"
    return_status = {
        "approved": "Approved",
        "rejected": "Rejected / Cancelled",
        "cancelled": "Rejected / Cancelled",
    }.get(ret.approval_status, "Pending Admin Approval") if ret else "Unknown"
    return_value = sum(
        (Decimal(str(_return_item_json(row).get("returnValue") or 0)) for row in ret.items),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if ret else Decimal("0.00")
    return {
        "id": notification.id if notification else None,
        "returnId": ret.id,
        "returnNo": ret.return_no,
        "invoiceNo": sale.invoice_no if sale else None,
        "customerId": sale.customer_id if sale else None,
        ** (_sale_customer_fields(sale) if sale else customer_fields(
            "Retail Customer",
            "retail",
            retail_customer=True,
        )),
        "notificationType": decision_label,
        "returnStatus": return_status,
        "decision": ret.approval_status,
        "adminName": ((admin.first_name + " " + (admin.last_name or "")).strip() if admin else("Pending Admin" if ret.approval_status in {
            None,
            "pending",
        } else "Admin")),
        "decisionAt": ret.approved_at.isoformat() if ret and ret.approved_at else(notification.created_at.isoformat() if notification and notification.created_at else None),
        "reason": ret.approval_notes if ret.approval_status == "rejected" else None,
        "isRead": bool(notification.is_read) if notification else False,
        "status": _return_inventory_status(ret),
        "financialStatus": "settled" if settled else("partially_settled" if settlements else "pending_settlement"),
        "settlementCompleted": settled,
        "settlementTypes": settlement_types,
        "workflowActive": _return_workflow_active(ret),
        "settlementType": settlement_types[0] if len(settlement_types) == 1 else("mixed" if settlement_types else None),
        "settlementReference": final_document.document_no if final_document else None,
        "settledAt": max(
            (row.created_at for row in settlements if row.created_at),
            default=None,
        ).isoformat() if settlements else None,
        "settlementAmount": str(refund_total),
        "returnValue": str(return_value),
        "combinedDocumentNo": final_document.document_no if final_document else None,
        "items": [_return_item_json(item) for item in ret.items],
    }


def _return_notification_is_eligible(ret, notification):
    """Apply the notification-panel rule to one unique Return Bill."""
    if ret.approval_status in {None, "pending"}:
        return True
    if ret.approval_status == "approved":
        settled, _, _, _, _ = _return_settlement_state(ret)
        return not settled
    if ret.approval_status in {"rejected", "cancelled"}:
        return not notification or not notification.is_read
    return False


def _return_approval_notification_rows(user_id):
    """Return one eligible notification payload per owned Return Bill.

    Some older approved/unsettled Returns predate persisted notification rows.
    Use SalesReturn as the source of truth and synthesize a read-only payload for
    those rows so the badge and list cannot disagree. A notification row is
    created only when the user opens and marks that Return Bill as read.
    """
    returns= (SalesReturn.query.filter_by(created_by=user_id).options(selectinload(SalesReturn.items)).order_by(
        SalesReturn.return_date.desc(),
        SalesReturn.id.desc(),
    ).all())
    notifications = (ReturnApprovalNotification.query.filter_by(user_id=user_id).order_by(ReturnApprovalNotification.id.desc()).all())
    notification_by_return = {}
    for notification in notifications:
        notification_by_return.setdefault(notification.return_id, notification)
    result = []
    for ret in returns:
        notification = notification_by_return.get(ret.id)
        if _return_notification_is_eligible(ret, notification):
            result.append(_return_approval_notification_json(notification, ret_override=ret))
    return result


@api.get("/returns/approval-notifications")
@login_required_api
@tab_required_api("Sales & Invoicing")
def return_approval_notifications():
    user = current_user()
    rows = _return_approval_notification_rows(user.id)
    unique_ids = {row["returnId"] for row in rows}
    return jsonify({
        "success": True,
        "activeCount": len(unique_ids),
        "unreadCount": sum(not row["isRead"] for row in rows),
        "notifications": rows,
    })


@api.get("/returns/approval-notifications/unread-count")
@login_required_api
@tab_required_api("Sales & Invoicing")
def return_approval_notification_count():
    user = current_user()
    rows = _return_approval_notification_rows(user.id)
    unique_ids = {row["returnId"] for row in rows}
    return jsonify({
        "success": True,
        "activeCount": len(unique_ids),
        "unreadCount": sum(not row["isRead"] for row in rows),
    })


@api.get("/returns/approval-notifications/return/<int:return_id>")
@login_required_api
@tab_required_api("Sales & Invoicing")
def return_approval_notification_detail_by_return(return_id):
    user = current_user()
    ret= (SalesReturn.query.filter_by(
        id=return_id,
        created_by=user.id,
    ).options(selectinload(SalesReturn.items)).first())
    if not ret:
        return fail("Approval notification not found.", 404)
    notification = ReturnApprovalNotification.query.filter_by(
        return_id=ret.id,
        user_id=user.id,
    ).first()
    if not _return_notification_is_eligible(ret, notification):
        return fail("Approval notification not found.", 404)
    return jsonify({
        "success": True,
        "notification": _return_approval_notification_json(notification, ret),
    })


@api.get("/returns/approval-notifications/<int:nid>")
@login_required_api
@tab_required_api("Sales & Invoicing")
def return_approval_notification_detail(nid):
    notification = ReturnApprovalNotification.query.filter_by(
        id=nid,
        user_id=current_user().id,
    ).first()
    if not notification:
        return fail("Approval notification not found.", 404)
    return jsonify({
        "success": True,
        "notification": _return_approval_notification_json(notification),
    })


@api.post("/returns/approval-notifications/<int:nid>/read")
@login_required_api
@tab_required_api("Sales & Invoicing")
def mark_return_approval_notification_read(nid):
    notification = ReturnApprovalNotification.query.filter_by(
        id=nid,
        user_id=current_user().id,
    ).with_for_update().first()
    if not notification:
        return fail("Approval notification not found.", 404)
    if not notification.is_read:
        notification.is_read = True
        notification.read_at = datetime.utcnow()
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            return fail("The notification could not be marked as read. Please try again.", 500)
    return jsonify({
        "success": True,
        "notification": _return_approval_notification_json(notification),
    })


@api.post("/returns/approval-notifications/return/<int:return_id>/read")
@login_required_api
@tab_required_api("Sales & Invoicing")
def mark_return_approval_notification_read_by_return(return_id):
    user = current_user()
    ret = (SalesReturn.query.filter_by(id=return_id, created_by=user.id).with_for_update().first())
    if not ret:
        return fail("Approval notification not found.", 404)
    notification = ReturnApprovalNotification.query.filter_by(return_id=ret.id).with_for_update().first()
    if notification and notification.user_id != user.id:
        return fail("Approval notification not found.", 404)
    now = datetime.utcnow()
    if not notification:
        notification = ReturnApprovalNotification(
            user_id=user.id,
            return_id=ret.id,
            is_read=True,
            created_at=now,
            read_at=now,
        )
        db.session.add(notification)
    elif not notification.is_read:
        notification.is_read = True
        notification.read_at = now
    try:
        db.session.commit()
        return jsonify({
            "success": True,
            "notification": _return_approval_notification_json(notification, ret),
        })
    except IntegrityError:
        db.session.rollback()
        notification = ReturnApprovalNotification.query.filter_by(
            return_id=ret.id,
            user_id=user.id,
        ).first()
        if not notification:
            return fail("The notification could not be marked as read. Please try again.", 500)
        return jsonify({
            "success": True,
            "notification": _return_approval_notification_json(notification),
        })
    except SQLAlchemyError:
        db.session.rollback()
        return fail("The notification could not be marked as read. Please try again.", 500)


def _returnable_quantity(sale_item_id):
    return db.session.query(func.coalesce(func.sum(ReturnItem.quantity), 0)).join(SalesReturn).filter(
        ReturnItem.sale_item_id == sale_item_id,
        or_(
            SalesReturn.approval_status.is_(None),
            SalesReturn.approval_status.notin_(("rejected", "cancelled")),
        ),
    ).scalar()


@api.get("/returns/invoices")
@login_required_api
@tab_required_api("Sales & Invoicing")
def return_invoice_search():
    term = (request.args.get("q") or "").strip()
    method = (request.args.get("by") or "invoice").strip()
    if len(term) < 2:
        return jsonify({"success": True, "invoices": []})
    query = Sale.query.filter_by(status="confirmed")
    if method == "invoice":
        query = query.filter(Sale.invoice_no.ilike(f"%{term}%"))
    elif method == "customer_name":
        query=query.outerjoin(Customer, Customer.id == Sale.customer_id).filter(or_(
            Sale.customer_name.ilike(f"%{term}%"),
            Customer.first_name.ilike(f"%{term}%"),
            Customer.last_name.ilike(f"%{term}%"),
        ))
    elif method == "customer_id":
        customer_query = Customer.query.filter(Customer.customer_code.ilike(f"%{term}%"))
        if term.isdigit():
            customer_query = customer_query.union(Customer.query.filter(Customer.id == int(term)))
        customer_ids = [row.id for row in customer_query.all()]
        query = query.filter(Sale.customer_id.in_(customer_ids)) if customer_ids else query.filter(Sale.id == -1)
    else:
        return fail("Select a valid invoice search method.")
    sales = query.order_by(Sale.sale_date.desc(), Sale.id.desc()).limit(25).all()
    customers = _sale_customer_map(sales)
    result = []
    for sale in sales:
        items = []
        for si in sale.items:
            returned = Decimal(str(_returnable_quantity(si.id) or 0))
            items.append({
                "saleItemId": si.id,
                "category": si.category,
                "product": si.product_name,
                "variety": si.variety,
                "quantity": as_float(si.quantity),
                "returnedQuantity": as_float(returned),
                "availableQuantity": as_float(max(
                    Decimal("0"),
                    Decimal(str(si.quantity)) - returned,
                )),
                "unit": si.unit,
                "packageSize": si.package_size_kg,
                "price": as_float(si.price_per_kg),
                "lineTotal": as_float(si.line_total),
            })
        result.append({
            "id": sale.id,
            "invoiceNo": sale.invoice_no,
            "customerId": sale.customer_id,
            **_sale_customer_fields(sale, customers),
            "invoiceDate": _payment_local_datetime(sale.sale_date),
            "total": as_float(sale.total_amount),
            "items": items,
        })
    return jsonify({"success": True, "invoices": result})


@api.post("/returns")
@limiter.limit("15 per minute")
@login_required_api
@tab_required_api("Sales & Invoicing")
def return_create():
    d = request.get_json(silent=True) or {}
    if not isinstance(d, dict):
        return fail("A Return Bill request object is required.")
    header_key = request.headers.get("Idempotency-Key")
    body_key = d.get("idempotencyKey")
    if header_key and body_key and header_key != body_key:
        return fail("Return request keys in the header and body do not match.")
    key = (header_key or body_key or "")
    if not isinstance(key, str) or not 16 <= len(key) <= 200:
        return fail("A stable return request key is required.")
    idem = hashlib.sha256(f"return:{current_user().id}:{key}".encode()).hexdigest()
    items = d.get("items")
    if not isinstance(d.get("invoiceNo"), str) or not d["invoiceNo"].strip():
        return fail("A saved invoice number is required.")
    if not isinstance(items, list) or not items or len(items) > 100:
        return fail("Select between one and 100 returned items.")
    try:
        # Settlement is deliberately selected only after an Admin approves the request.
        sale = Sale.query.filter_by(invoice_no=d.get("invoiceNo")).with_for_update().first()
        if not sale or sale.status != "confirmed":
            return fail("A confirmed original invoice is required.", 404)
        normalized = []
        seen = set()
        for row in items:
            if not isinstance(row, dict):
                raise ValueError("Each return item must be an object.")
            try:
                sale_item_id = int(row.get("saleItemId"))
            except (TypeError, ValueError):
                raise ValueError("A valid original invoice item is required.")
            if sale_item_id in seen:
                raise ValueError("An invoice item can appear only once in a Return Bill.")
            seen.add(sale_item_id)
            si = SaleItem.query.filter_by(
                id=sale_item_id,
                sale_id=sale.id,
            ).with_for_update().first()
            if not si:
                raise ValueError("Returned item does not belong to the selected invoice.")
            qty = dec(row.get("quantity"), "return quantity", Decimal("0.001"))
            if si.category == "packed" and qty != qty.to_integral_value():
                raise ValueError("Packed returns must use whole bags.")
            kg = qty * Decimal(str(si.package_size_kg or 1))
            normalized.append((si, qty, kg))
        existing = SalesReturn.query.filter_by(idempotency_key=idem).first()
        normalized_key = [(si.id, qty.normalize()) for si, qty, _ in normalized]
        if existing:
            saved=sorted((
                item.sale_item_id,
                Decimal(str(item.quantity)).normalize(),
            ) for item in existing.items)
            if existing.sale_id == sale.id and saved == sorted(normalized_key):
                return jsonify({
                    "success": True,
                    "return": _return_json(existing),
                    "replayed": True,
                }), 200
            return fail("This return request key was already used for different details.", 409)
        held_draft = None
        if d.get("heldDraftId") not in (None, ""):
            try:
                held_id = int(d["heldDraftId"])
            except (TypeError, ValueError):
                return fail("The held Return Bill reference is invalid.")
            held_query = HeldSale.query.filter_by(id=held_id, draft_type="return_bill")
            if current_user().role != "admin":
                held_query = held_query.filter_by(created_by=current_user().id)
            held_draft = held_query.with_for_update().first()
            if not held_draft:
                return fail("Held Return Bill not found.", 404)
            if int((held_draft.payload or {}).get("invoiceId") or 0) != sale.id:
                return fail(
                    "The held Return Bill does not match the selected original invoice.",
                    409,
                )
        for si, qty, _ in normalized:
            already = Decimal(str(_returnable_quantity(si.id) or 0))
            if already + qty > Decimal(str(si.quantity)):
                raise ValueError("Return quantity exceeds the remaining quantity on the original invoice.")
        ret = SalesReturn(
            return_no=f"RET-{datetime.now(COLOMBO_TIMEZONE):%Y%m%d%H%M%S%f}",
            sale_id=sale.id,
            customer_id=sale.customer_id,
            settlement_type=None,
            notes=d.get("notes"),
            created_by=current_user().id,
            status="pending_inspection",
            approval_status="pending",
            idempotency_key=idem,
        )
        db.session.add(ret)
        db.session.flush()
        for si, qty, kg in normalized:
            db.session.add(ReturnItem(
                return_id=ret.id,
                sale_item_id=si.id,
                quantity=qty,
                total_kg=kg,
                decision="pending",
                settlement_status="unsettled",
            ))
        if ret.created_by:
            db.session.add(ReturnApprovalNotification(
                user_id=ret.created_by,
                return_id=ret.id,
                created_at=datetime.utcnow(),
            ))
        audit(
            "request",
            "sales_return",
            ret.id,
            {"approval_status": "pending", "item_count": len(normalized)},
        )
        if held_draft:
            db.session.delete(held_draft)
            db.session.add(AuditLog(
                user_id=current_user().id,
                action="submit",
                entity="held_return_bill",
                entity_id=held_draft.id,
                details={"reference": held_draft.hold_ref, "return_id": ret.id},
            ))
        db.session.commit()
        return jsonify({"success": True, "return": _return_json(ret)}), 201
    except ValueError as e:
        db.session.rollback()
        return fail(str(e))
    except IntegrityError:
        db.session.rollback()
        return fail(
            "This return request was already submitted or conflicts with existing data.",
            409,
        )
    except SQLAlchemyError as exc:
        db.session.rollback()
        current_app.logger.error("Return request failed; exception_type=%s", type(exc).__name__)
        return fail("The Return Bill could not be saved. Please try again.", 500)


@api.get("/returns")
@login_required_api
@tab_required_api("Sales & Invoicing")
def returns_list():
    rows = SalesReturn.query.order_by(SalesReturn.id.desc()).all()
    return jsonify({"success": True, "returns": [_return_json(r) for r in rows]})


@api.get("/returns/<int:rid>")
@login_required_api
@tab_required_api("Sales & Invoicing")
def return_detail(rid):
    ret = SalesReturn.query.filter_by(id=rid).first()
    if not ret:
        return fail("Return Bill not found.", 404)
    return jsonify({"success": True, "return": _return_json(ret)})


@api.get("/returns/<int:rid>/history")
@login_required_api
@tab_required_api("Sales & Invoicing")
def settled_return_history_detail(rid):
    ret = SalesReturn.query.filter_by(id=rid, financial_status="settled").first()
    if not ret:
        return fail("Closed Return Bill not found.", 404)
    settlements = []
    for item in ret.items:
        settlement = ReturnSettlement.query.filter_by(return_item_id=item.id).first()
        if not settlement:
            continue
        replacements = ReturnReplacementItem.query.filter_by(settlement_id=settlement.id).order_by(ReturnReplacementItem.id).all()
        entries = ReturnFinancialEntry.query.filter_by(settlement_id=settlement.id).order_by(ReturnFinancialEntry.id).all()
        exchange_difference = Decimal(str(settlement.replacement_value or 0)) - Decimal(str(settlement.return_value or 0))
        settlements.append({
            "returnItemId": item.id,
            "type": settlement.settlement_type,
            "returnValue": str(settlement.return_value),
            "replacementValue": str(settlement.replacement_value),
            "replacementSubtotal": str(settlement.replacement_value),
            "netAmountPayable": str(max(exchange_difference, Decimal("0.00"))),
            "refundDue": str(max(-exchange_difference, Decimal("0.00"))),
            "cashRefund": str(settlement.cash_refund),
            "amountCollected": str(settlement.amount_collected),
            "differenceRefund": str(settlement.difference_refund),
            "settledAt": settlement.created_at.isoformat(),
            "replacements": [{
                "product": row.product_name,
                "category": row.category,
                "variety": row.variety,
                "quantity": str(row.quantity),
                "unit": row.unit,
                "packageSize": row.package_size_kg,
                "pricePerKg": str(row.price_per_kg),
                "lineTotal": str(row.line_total),
            } for row in replacements],
            "financialEntries": [{
                "type": row.entry_type,
                "amount": str(row.amount),
                "method": row.method,
                "reference": row.reference,
            } for row in entries],
        })
    sale = db.session.get(Sale, ret.sale_id)
    final_document = CombinedTransactionDocument.query.filter_by(
        final_return_id=ret.id,
        is_final=True,
    ).first()
    detail = _return_json(ret)
    detail.update({
        "financialStatus": "settled",
        "status": "Closed",
        "inventoryStatus": _return_inventory_status(ret),
        "returnValue": str(ret.settlement_amount or 0),
        "settlements": settlements,
        "settlementType": "exchange" if settlements and all(row["type"] == "exchange" for row in settlements) else "refund",
        "originalInvoice": sale.invoice_no if sale else None,
        "documentNo": final_document.document_no if final_document else None,
        "receiptUrl": f"/return-receipt/{final_document.document_no}" if final_document else None,
    })
    return jsonify({"success": True, "return": detail})


@api.get("/returns/settlement-queue")
@login_required_api
@tab_required_api("Sales & Invoicing")
def return_settlement_queue():
    query = ReturnItem.query.join(SalesReturn).filter(
        SalesReturn.approval_status == "approved",
        ReturnItem.settlement_status != "settled",
    )
    raw_return_id = request.args.get("returnId")
    if raw_return_id:
        try:
            return_id = int(raw_return_id)
        except (TypeError, ValueError):
            return fail("A valid Return Bill ID is required.")
        query = query.filter(SalesReturn.id == return_id)
    rows = (query.order_by(SalesReturn.return_date, ReturnItem.id).all())
    result = []
    for item in rows:
        ret = db.session.get(SalesReturn, item.return_id)
        sale = db.session.get(Sale, ret.sale_id)
        si = db.session.get(SaleItem, item.sale_item_id)
        value = (Decimal(str(item.total_kg)) * Decimal(str(si.price_per_kg))).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        result.append({
            "returnId": ret.id,
            "returnNo": ret.return_no,
            "invoiceNo": sale.invoice_no,
            "customerId": sale.customer_id,
            **_sale_customer_fields(sale),
            "settlementType": "exchange" if ret.settlement_type == "exchange" else "refund",
            "itemId": item.id,
            "product": si.product_name,
            "variety": si.variety,
            "quantity": as_float(item.quantity),
            "unit": si.unit,
            "packageSize": si.package_size_kg,
            "returnValue": str(value),
        })
    return jsonify({"success": True, "items": result})


@api.get("/returns/approvals/pending")
@login_required_api
@admin_required_api
def return_approval_queue():
    rows=SalesReturn.query.filter(or_(
        SalesReturn.approval_status == "pending",
        SalesReturn.approval_status.is_(None),
    )).order_by(SalesReturn.return_date, SalesReturn.id).all()
    return jsonify({"success": True, "returns": [_return_json(r) for r in rows]})


@api.post("/returns/<int:rid>/approval")
@limiter.limit("20 per minute")
@login_required_api
@admin_required_api
def return_approval_decision(rid):
    d = request.get_json(silent=True) or {}
    action = d.get("action")
    notes = d.get("notes")
    if not isinstance(action, str) or action not in {"approve", "reject", "cancel"}:
        return fail("Decision must be approve, reject or cancel.")
    if notes is not None and (not isinstance(notes, str) or len(notes) > 2000):
        return fail("Decision notes are invalid.")
    if action == "reject" and (not isinstance(notes, str) or not notes.strip()):
        return fail("A rejection reason is required.")
    ret = SalesReturn.query.filter_by(id=rid).with_for_update().first()
    if not ret:
        return fail("Return not found.", 404)
    if action == "cancel":
        if ret.approval_status not in {None, "pending", "approved"}:
            return fail("This Return Bill cannot be cancelled in its current state.", 409)
        has_final_inspection = any(item.decision in {"approved", "rejected"} for item in ret.items)
        has_stock = StockMovement.query.join(
            ReturnItem,
            StockMovement.return_item_id == ReturnItem.id,
        ).filter(ReturnItem.return_id == ret.id).first()
        has_settlement = ReturnSettlement.query.join(ReturnItem).filter(ReturnItem.return_id == ret.id).first()
        if has_final_inspection or has_stock or has_settlement:
            return fail(
                "This Return Bill has inspection or settlement activity and requires an audited correction workflow.",
                409,
            )
    elif ret.approval_status not in {None, "pending"}:
        return fail("This return request is no longer awaiting an Admin decision.", 409)
    now = datetime.utcnow()
    user = current_user()
    ret.approval_status = {
        "approve": "approved",
        "reject": "rejected",
        "cancel": "cancelled",
    }[action]
    ret.approved_by = user.id
    ret.approved_at = now
    ret.approval_notes = notes
    if action == "reject":
        ret.financial_status = "rejected"
    elif action == "cancel":
        ret.financial_status = "cancelled"
    db.session.add(ReturnApprovalEvent(
        return_id=ret.id,
        admin_id=user.id,
        action=action,
        notes=notes,
        created_at=now,
    ))
    if ret.created_by:
        notification = ReturnApprovalNotification.query.filter_by(return_id=ret.id).with_for_update().first()
        if not notification:
            notification = ReturnApprovalNotification(
                user_id=ret.created_by,
                return_id=ret.id,
                created_at=now,
            )
            db.session.add(notification)
        else:
            notification.is_read = False
            notification.read_at = None
            notification.created_at = now
    audit(action, "sales_return", ret.id, {"approval_status": ret.approval_status, "notes": notes})
    try:
        db.session.commit()
        return jsonify({"success": True, "return": _return_json(ret)})
    except SQLAlchemyError:
        db.session.rollback()
        return fail("The Admin decision could not be saved. Please try again.", 500)


@api.get("/inventory/returns/pending")
@login_required_api
@inventory_permission_required("inspectReturnedItems")
def return_inspection_queue():
    rows = SalesReturn.query.filter(SalesReturn.approval_status == "approved").join(ReturnItem).filter(ReturnItem.decision == "pending").order_by(
        SalesReturn.return_date,
        ReturnItem.id,
    ).all()
    return jsonify({"success": True, "returns": [_return_json(r) for r in rows]})


@api.get("/inventory/returns/history")
@login_required_api
@inventory_permission_required("inspectReturnedItems")
def return_inspection_history():
    rows = SalesReturn.query.filter(SalesReturn.approval_status == "approved").join(ReturnItem).filter(ReturnItem.decision.in_(("approved", "rejected"))).order_by(
        SalesReturn.return_date.desc(),
        ReturnItem.id.desc(),
    ).all()
    unique = {r.id: r for r in rows}
    result = []
    for ret in unique.values():
        data = _return_json(ret)
        data["items"]=[item for item in data["items"] if item["decision"] in {
            "approved",
            "rejected",
        }]
        result.append(data)
    return jsonify({"success": True, "returns": result})


@api.post("/returns/<int:rid>/inspect")
@limiter.limit("30 per minute")
@login_required_api
@inventory_permission_required("inspectReturnedItems")
def inspect_return(rid):
    d = request.get_json(silent=True) or {}
    decisions = d.get("items")
    if not isinstance(decisions, list) or not decisions:
        return fail("Select at least one return item to inspect.")
    ret = SalesReturn.query.filter_by(id=rid).with_for_update().first()
    if not ret:
        return fail("Return not found.", 404)
    if ret.approval_status in {
        "rejected",
        "cancelled",
    } or ret.financial_status in {"rejected", "cancelled"}:
        return fail(
            "Rejected or cancelled Return Bills cannot enter physical stock inspection.",
            409,
        )
    if ret.approval_status != "approved":
        return fail("Admin approval is required before physical stock inspection.", 409)
    try:
        rows = []
        seen = set()
        for entry in decisions:
            if not isinstance(entry, dict):
                raise ValueError("Each inspection decision must be an object.")
            item_id = int(entry.get("id") or 0)
            action = entry.get("decision")
            if item_id in seen:
                raise ValueError("An item may appear only once per inspection request.")
            seen.add(item_id)
            if not isinstance(action, str) or action not in {"pending", "approved", "rejected"}:
                raise ValueError("Decision must be Pending, Approved or Rejected.")
            item = ReturnItem.query.filter_by(
                id=item_id,
                return_id=ret.id,
            ).with_for_update().first()
            if not item:
                raise ValueError("An inspection item does not belong to this Return Bill.")
            if item.decision in {"approved", "rejected"}:
                raise ValueError("This return item already has a final inspection decision.")
            note = entry.get("notes")
            if note is not None and (not isinstance(note, str) or len(note) > 2000):
                raise ValueError("Inspection notes are invalid.")
            rows.append((item, action, note))
        now = datetime.utcnow()
        user = current_user()
        for item, action, note in rows:
            item.decision = action
            item.inspection_notes = note
            item.inspected_by = user.id
            item.inspected_at = now
            db.session.add(ReturnInspectionEvent(
                return_item_id=item.id,
                inspector_id=user.id,
                decision=action,
                notes=note,
                created_at=now,
            ))
            if action == "approved":
                si = SaleItem.query.filter_by(
                    id=item.sale_item_id,
                    sale_id=ret.sale_id,
                ).with_for_update().first()
                stock_type = si.stock_type or {
                    "bulk": "rice",
                    "packed": "packed",
                    "bran": "bran",
                    "other": "other",
                }.get(si.category)
                if not stock_type:
                    raise ValueError("This returned product has an unsupported stock category.")
                exists = StockMovement.query.filter_by(return_item_id=item.id).first()
                if exists:
                    raise ValueError("Stock was already restored for this return item.")
                move_stock(
                    si.product_name,
                    stock_type,
                    si.variety or "",
                    item.total_kg,
                    item.quantity if si.category == "packed" else 0,
                    si.package_size_kg,
                    "return_approved",
                    "return_item",
                    item.id,
                    notes=f"Approved inspection for {ret.return_no}",
                    return_item_id=item.id,
                )
            audit("inspect_item", "return_item", item.id, {"decision": action, "return_id": ret.id})
        if any(i.decision == "pending" for i in ret.items):
            ret.status = "pending_inspection"
        elif all(i.decision == "rejected" for i in ret.items):
            ret.status = "rejected"
        else:
            ret.status = "inspected"
        inventory_status = _return_inventory_status(ret)
        for document in CombinedTransactionDocument.query.filter_by(return_id=ret.id).all():
            snapshot = dict(document.snapshot or {})
            return_data = dict(snapshot.get("returnBill") or {})
            return_data["inventoryStatus"] = inventory_status
            snapshot["returnBill"] = return_data
            document.snapshot = snapshot
        audit(
            "inspect",
            "sales_return",
            ret.id,
            {"items": [{"id": i.id, "decision": i.decision} for i, _, _ in rows]},
        )
        db.session.commit()
        return jsonify({"success": True, "return": _return_json(ret)})
    except (ValueError, TypeError) as e:
        db.session.rollback()
        return fail(str(e))
    except IntegrityError:
        db.session.rollback()
        return fail("This item has already been inspected or restored to stock.", 409)


def _combined_snapshot(ret, settlement):
    sale = db.session.get(Sale, ret.sale_id)
    customer = db.session.get(Customer, sale.customer_id) if sale.customer_id else None
    customer_balance = Decimal(str(customer_outstanding(customer.id) or 0)) if customer else Decimal("0")
    payment_rows= (db.session.query(Payment, PaymentAllocation).join(
        PaymentAllocation,
        PaymentAllocation.payment_id == Payment.id,
    ).filter(PaymentAllocation.sale_id == sale.id).order_by(Payment.payment_date, Payment.id).all())
    settlements = ReturnSettlement.query.join(ReturnItem).filter(ReturnItem.return_id == ret.id).order_by(ReturnSettlement.id).all()
    approved_return_value = sum(
        (Decimal(str(i.total_kg)) * Decimal(str(db.session.get(
            SaleItem,
            i.sale_item_id,
        ).price_per_kg)) for i in ret.items),
        Decimal("0"),
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "documentType": "Combined Sale and Return Settlement",
        "documentNo": None,
        "originalInvoice": {
            "invoiceNo": sale.invoice_no,
            "date": sale.sale_date.isoformat(),
            "customerId": sale.customer_id,
            **_sale_customer_fields(sale, {customer.id: customer} if customer else {}),
            "total": str(sale.total_amount),
            "amountPaid": str(sale.amount_paid),
            "amountDue": str(sale.amount_due),
            "paymentType": sale.payment_type,
            "paymentMethod": sale.payment_method,
            "items": [{"product": x.product_name, "category": x.category, "variety": x.variety, "quantity": str(x.quantity), "unit": x.unit, "packageSizeKg": x.package_size_kg, "pricePerKg": str(x.price_per_kg), "lineTotal": str(x.line_total)} for x in sale.items],
            "payments": [{"receiptNo": p.receipt_no, "date": p.payment_date.isoformat(), "method": p.method, "amount": str(a.amount), "reference": p.bank_reference} for p, a in payment_rows],
        },
        "returnBill": {
            "returnNo": ret.return_no,
            "date": ret.return_date.isoformat(),
            "financialStatus": ret.financial_status,
            "inventoryStatus": _return_inventory_status(ret),
            "approvalStatus": ret.approval_status,
            "approvedAt": ret.approved_at.isoformat() if ret.approved_at else None,
            "approvedBy": ((db.session.get(User, ret.approved_by).first_name + " " + (db.session.get(User, ret.approved_by).last_name or "")).strip() if ret.approved_by and db.session.get(User, ret.approved_by) else None),
            "approvedReturnValue": str(approved_return_value),
            "inspectionItems": [{"product": db.session.get(SaleItem, i.sale_item_id).product_name, "variety": db.session.get(SaleItem, i.sale_item_id).variety, "quantity": str(i.quantity), "unit": db.session.get(SaleItem, i.sale_item_id).unit, "packageSizeKg": db.session.get(SaleItem, i.sale_item_id).package_size_kg, "originalPricePerKg": str(db.session.get(SaleItem, i.sale_item_id).price_per_kg), "returnValue": str((Decimal(str(i.total_kg)) * Decimal(str(db.session.get(SaleItem, i.sale_item_id).price_per_kg))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)), "decision": i.decision, "notes": i.inspection_notes, "inspectedAt": i.inspected_at.isoformat() if i.inspected_at else None} for i in ret.items],
        },
        "settlements": [{
            "returnItemId": s.return_item_id,
            "type": s.settlement_type,
            "exchangeRef": s.exchange_ref,
            "settledAt": s.created_at.isoformat() if s.created_at else None,
            "settledBy": ((db.session.get(User, s.created_by).first_name + " " + (db.session.get(User, s.created_by).last_name or "")).strip() if db.session.get(User, s.created_by) else ""),
            "refundOption": s.refund_option,
            "returnValue": str(s.return_value),
            "replacementValue": str(s.replacement_value),
            "balanceApplied": str(s.balance_applied),
            "cashRefund": str(s.cash_refund),
            "amountCollected": str(s.amount_collected),
            "differenceRefund": str(s.difference_refund),
            "replacements": [{"product": x.product_name, "category": x.category, "variety": x.variety, "quantity": str(x.quantity), "unit": x.unit, "packageSizeKg": x.package_size_kg, "pricePerKg": str(x.price_per_kg), "lineTotal": str(x.line_total), "manualPriceReason": x.manual_price_reason} for x in ReturnReplacementItem.query.filter_by(settlement_id=s.id).all()],
            "financialEntries": [{"type": x.entry_type, "amount": str(x.amount), "method": x.method, "reference": x.reference, "saleId": x.sale_id} for x in ReturnFinancialEntry.query.filter_by(settlement_id=s.id).all()],
        } for s in settlements],
        "chosenRefundOption": settlement.refund_option if settlement else None,
        "outstandingBalanceAdjustment": str(settlement.balance_applied if settlement else Decimal("0")),
        "customerBalanceAfter": str(customer_balance),
        "finalCustomerBalance": str(customer_balance),
    }


def _make_combined_document(ret, user, settlement=None, final=False):
    snapshot = _combined_snapshot(ret, settlement)
    if final and settlement:
        # Older inspection flows could leave a final-marked, settlement-free
        # snapshot for this Return. Reuse that exact row instead of colliding
        # with uq(final_return_id) or creating a second customer document.
        prior = CombinedTransactionDocument.query.filter_by(
            final_return_id=ret.id,
            is_final=True,
        ).with_for_update().first()
        if prior:
            prior_snapshot = prior.snapshot if isinstance(prior.snapshot, dict) else {}
            if (prior.return_id != ret.id or prior.settlement_id is not None or prior_snapshot.get("settlements")):
                raise ValueError("A final transaction document already exists for this Return Bill; manual reconciliation is required.")
            snapshot["documentNo"] = prior.document_no
            snapshot["final"] = True
            prior.snapshot = snapshot
            prior.settlement_id = settlement.id
            return prior
    doc_no = (f"REF-{ret.return_no}" if final and settlement and settlement.settlement_type == "refund" else f"COMB-{datetime.now(COLOMBO_TIMEZONE):%Y%m%d}-{uuid4().hex[:10].upper()}")
    snapshot["documentNo"] = doc_no
    snapshot["final"] = final
    document = CombinedTransactionDocument(
        document_no=doc_no,
        return_id=ret.id,
        settlement_id=settlement.id if settlement else None,
        final_return_id=ret.id if final else None,
        snapshot=snapshot,
        is_final=final,
        created_by=user.id,
        created_at=datetime.utcnow(),
    )
    db.session.add(document)
    return document


@api.get("/returns/<int:rid>/refund-preview")
@login_required_api
@tab_required_api("Sales & Invoicing")
def return_refund_preview(rid):
    ret = SalesReturn.query.filter_by(id=rid).first()
    if not ret:
        return fail("Return Bill not found.", 404)
    if ret.approval_status != "approved":
        return fail("The Return Bill must be Admin approved before refund.", 409)
    settlements= (ReturnSettlement.query.join(
        ReturnItem,
        ReturnItem.id == ReturnSettlement.return_item_id,
    ).filter(ReturnItem.return_id == rid).all())
    if ret.financial_status == "settled":
        if not settlements or any(row.settlement_type != "refund" for row in settlements):
            return fail("This Return Bill has already been completed as an Item Exchange.", 409)
        data = _return_refund_preview(ret, include_settled=True)
        data["refundReference"] = f"REF-{ret.return_no}"
        final_document = CombinedTransactionDocument.query.filter_by(
            final_return_id=rid,
            is_final=True,
        ).first()
        if final_document:
            data["refundReference"] = final_document.document_no
            data["date"] = (final_document.created_at or datetime.utcnow()).isoformat()
        return jsonify({
            "success": True,
            "completed": True,
            "refund": data,
            "customerReceiptUrl": f"/return-receipt/{data['refundReference']}" if final_document else None,
        })
    if ret.financial_status not in {"pending_settlement", "partially_settled"}:
        return fail("This Return Bill is not open for Cash Refund.", 409)
    try:
        data = _return_refund_preview(ret)
        if not data["items"] or Decimal(data["totalRefund"]) <= 0:
            return fail("No unsettled return value remains to refund.", 409)
    except (ValueError, TypeError) as exc:
        return fail(str(exc), 409)
    return jsonify({"success": True, "completed": False, "refund": data})


@api.post("/returns/items/<int:item_id>/settle")
@limiter.limit("15 per minute")
@login_required_api
@tab_required_api("Sales & Invoicing")
def return_item_settle(item_id):
    d = request.get_json(silent=True) or {}
    if not isinstance(d, dict):
        return fail("A settlement request object is required.")
    try:
        item = ReturnItem.query.filter_by(id=item_id).with_for_update().first()
        if not item:
            return fail("Return item not found.", 404)
        ret = SalesReturn.query.filter_by(id=item.return_id).with_for_update().first()
        if ret.approval_status != "approved":
            return fail("The Return Bill must be approved before settlement.", 409)
        if (
            item.settlement_status == "settled"
            or ReturnSettlement.query.filter_by(return_item_id=item.id).first()
        ):
            return fail("This return item has already been settled.", 409)
        sale = Sale.query.filter_by(id=ret.sale_id).with_for_update().first()
        sale_item = SaleItem.query.filter_by(
            id=item.sale_item_id,
            sale_id=sale.id,
        ).with_for_update().first()
        return_value = (Decimal(str(item.total_kg)) * Decimal(str(sale_item.price_per_kg))).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        requested_kind = d.get("settlementType")
        if requested_kind not in (None, "refund", "exchange"):
            return fail("Choose Cash Refund or Item Exchange.")
        kind = requested_kind or ("exchange" if d.get("heldExchangeId") else "refund")
        existing_types={row[0] for row in db.session.query(ReturnSettlement.settlement_type).join(
            ReturnItem,
            ReturnItem.id == ReturnSettlement.return_item_id,
        ).filter(ReturnItem.return_id == ret.id).all()}
        if not existing_types:
            return fail(
                "New Return Bills must be settled as a whole through the Return Bill settlement workflow.",
                409,
            )
        if existing_types and existing_types != {kind}:
            return fail(
                "A Return Bill must use the same settlement choice for all returned items.",
                409,
            )
        if kind == "refund" and (d.get("replacements") not in (
            None,
            [],
        ) or d.get("heldExchangeId")):
            return fail("Replacement products require an approved Item Exchange selection.")
        user = current_user()
        now = datetime.utcnow()
        replacement_rows = []
        replacement_value = Decimal("0")
        held_exchange = None
        raw_held_id = d.get("heldExchangeId")
        if raw_held_id not in (None, ""):
            try:
                held_id = int(raw_held_id)
            except (TypeError, ValueError):
                raise ValueError("The held Return Exchange reference is invalid.")
            held_exchange = HeldSale.query.filter_by(
                id=held_id,
                draft_type="return_exchange",
                return_item_id=item.id,
                created_by=user.id,
            ).with_for_update().first()
            if not held_exchange:
                return fail("Held Return Exchange draft not found.", 404)
        elif kind == "exchange":
            held_exchange = HeldSale.query.filter_by(
                return_item_id=item.id,
                draft_type="return_exchange",
            ).with_for_update().first()
        if held_exchange and held_exchange.created_by != user.id and user.role != "admin":
            return fail("This return item has an active exchange draft owned by another user.", 409)
        if kind != "exchange" and not held_exchange:
            held_exchange = HeldSale.query.filter_by(
                return_item_id=item.id,
                draft_type="return_exchange",
            ).with_for_update().first()
            if held_exchange and held_exchange.created_by != user.id and user.role != "admin":
                return fail(
                    "This return item has an active exchange draft owned by another user.",
                    409,
                )
        if kind == "exchange":
            replacement_payload = d.get("replacements")
            if not isinstance(replacement_payload, list) and held_exchange:
                replacement_payload = (held_exchange.payload or {}).get("replacements", [])
            if not isinstance(replacement_payload, list) or not replacement_payload:
                raise ValueError("Select at least one replacement product.")
            prepared, _ = _prepare_sale_items(replacement_payload)
            for raw, part in zip(replacement_payload, prepared):
                price_key = part["variety"] if part["category"] in {
                    "bulk",
                    "packed",
                } else part["product"]
                setting = PriceSetting.query.filter_by(
                    item_name=price_key,
                    price_type="selling",
                    is_active=True,
                ).with_for_update().first()
                reason = raw.get("manualPriceReason")
                if not setting or setting.default_price is None:
                    if user.role != "admin":
                        raise ValueError(f"A configured current selling price is required for {price_key}; ask an Admin to set it.")
                    if raw.get("confirmManualPrice") is not True or not isinstance(
                        reason,
                        str,
                    ) or not reason.strip() or len(reason) > 2000:
                        raise ValueError(f"A configured current selling price is required for {price_key}, unless an Admin confirms a manual price and provides its reason.")
                else:
                    reason = None
                    current_price = sale_money(
                        setting.default_price,
                        "current replacement selling price",
                        Decimal("0.01"),
                    )
                    part["price"] = current_price
                    part["line_total"] = sale_money(
                        part["total_kg"] * current_price,
                        "replacement line total",
                        Decimal("0.01"),
                    )
                replacement_value += Decimal(str(part["line_total"]))
                replacement_rows.append((part, reason))
        refund_option = None
        balance_applied = Decimal("0")
        cash_refund = Decimal("0")
        collected = Decimal("0")
        difference_refund = Decimal("0")
        financial = []
        if kind == "refund":
            refund_option = d.get("refundOption", "direct_refund")
            if refund_option not in {None, "direct_refund"}:
                raise ValueError("Return refunds must be paid directly to the customer.")
            refund_option = "direct_refund"
            cash_refund = return_value
            if cash_refund:
                financial.append(("cash_refund", cash_refund, "Cash", None, None))
        else:
            difference = replacement_value - return_value
            if difference > 0:
                method = d.get("paymentMethod", "Cash")
                reference = d.get("paymentReference")
                if method not in {"Cash", "Bank Transfer"}:
                    raise ValueError("Exchange differences must be collected by Cash or Bank Transfer.")
                if method == "Bank Transfer" and (not isinstance(
                    reference,
                    str,
                ) or not reference.strip() or len(reference) > 100):
                    raise ValueError("A valid bank transfer reference is required.")
                collected = difference
                financial.append((
                    "exchange_difference_collection",
                    collected,
                    method,
                    reference,
                    None,
                ))
            elif difference < 0:
                difference_refund = -difference
                financial.append((
                    "exchange_difference_refund",
                    difference_refund,
                    "Cash",
                    None,
                    None,
                ))
        settlement = ReturnSettlement(
            return_item_id=item.id,
            settlement_type=kind,
            refund_option=refund_option,
            return_value=return_value,
            replacement_value=replacement_value,
            balance_applied=balance_applied,
            cash_refund=cash_refund,
            amount_collected=collected,
            difference_refund=difference_refund,
            exchange_ref=held_exchange.hold_ref if held_exchange and kind == "exchange" else None,
            created_by=user.id,
            created_at=now,
        )
        db.session.add(settlement)
        db.session.flush()
        total_settled= (db.session.query(func.coalesce(
            func.sum(ReturnSettlement.return_value),
            0,
        )).join(ReturnItem).filter(ReturnItem.return_id == ret.id).scalar() or Decimal("0"))
        ret.settlement_amount = Decimal(str(total_settled))
        for part, reason in replacement_rows:
            db.session.add(ReturnReplacementItem(
                settlement_id=settlement.id,
                category=part["category"],
                product_name=part["product"],
                variety=part["variety"] or None,
                quantity=part["quantity"],
                unit=part["unit"],
                package_size_kg=part["package_size"],
                total_kg=part["total_kg"],
                price_per_kg=part["price"],
                line_total=part["line_total"],
                manual_price_reason=reason,
            ))
            move_stock(
                part["stock_item"],
                part["stock_type"],
                part["variety"],
                -part["total_kg"],
                -part["quantity"] if part["category"] == "packed" else 0,
                part["package_size"],
                "return_exchange_out",
                "return_settlement",
                settlement.id,
                notes=f"Replacement for {ret.return_no}",
            )
        for entry_type, amount, method, reference, sale_id in financial:
            db.session.add(ReturnFinancialEntry(
                settlement_id=settlement.id,
                entry_type=entry_type,
                amount=amount,
                method=method,
                reference=reference,
                sale_id=sale_id,
                created_by=user.id,
                created_at=now,
            ))
        item.settlement_status = "settled"
        final = all(i.settlement_status == "settled" for i in ret.items)
        ret.financial_status = "settled" if final else "partially_settled"
        document = _make_combined_document(ret, user, settlement, final)
        doc_no = document.document_no
        audit(
            "settle",
            "return_item",
            item.id,
            {
                "settlement_id": settlement.id,
                "type": kind,
                "return_value": str(return_value),
                "replacement_value": str(replacement_value),
                "exchange_ref": settlement.exchange_ref,
                "document_no": doc_no,
            },
        )
        if held_exchange:
            held_id = held_exchange.id
            db.session.delete(held_exchange)
            db.session.add(AuditLog(
                user_id=user.id,
                action="complete",
                entity="held_return_exchange",
                entity_id=held_id,
                details={"reference": settlement.exchange_ref, "document_no": doc_no},
            ))
        db.session.commit()
        return jsonify({
            "success": True,
            "settlementId": settlement.id,
            "documentNo": doc_no,
            "exchangeReference": settlement.exchange_ref,
            "documentUrl": f"/combined-transaction/{doc_no}",
            "customerReceiptUrl": f"/return-receipt/{doc_no}" if final else None,
            "isFinal": final,
            "returnValue": str(return_value),
            "replacementValue": str(replacement_value),
            "balanceApplied": str(balance_applied),
            "cashRefund": str(cash_refund),
            "amountCollected": str(collected),
            "differenceRefund": str(difference_refund),
        }), 201
    except (ValueError, TypeError, KeyError) as e:
        db.session.rollback()
        return fail(str(e))
    except IntegrityError:
        db.session.rollback()
        return fail(
            "This return item has already been settled or the request conflicts with existing data.",
            409,
        )
    except SQLAlchemyError as exc:
        db.session.rollback()
        current_app.logger.error("Return settlement failed; exception_type=%s", type(exc).__name__)
        return fail("The return settlement could not be completed. No changes were saved.", 500)


@api.post("/returns/<int:rid>/settle", strict_slashes=False)
@limiter.limit("15 per minute")
@login_required_api
@tab_required_api("Sales & Invoicing")
def return_bill_settle(rid):
    """Settle one approved Return Bill as a whole, independently of inspection."""
    d = request.get_json(silent=True) or {}
    if not isinstance(d, dict):
        return fail("A Return Bill settlement request is required.")
    raw_key = request.headers.get("Idempotency-Key") or d.get("idempotencyKey") or ""
    if not isinstance(raw_key, str) or not 16 <= len(raw_key) <= 200:
        return fail("A stable settlement request key is required.")
    request_hash=hashlib.sha256(json.dumps(
        d,
        sort_keys=True,
        separators= (",", ":"),
        ensure_ascii=False,
    ).encode()).hexdigest()
    try:
        ret = SalesReturn.query.filter_by(id=rid).with_for_update().first()
        if not ret:
            return fail("Return Bill not found.", 404)
        if ret.approval_status != "approved":
            return fail("The Return Bill must be Admin approved before settlement.", 409)
        user = current_user()
        existing_settlements = (ReturnSettlement.query.join(ReturnItem).filter(ReturnItem.return_id == rid).order_by(ReturnSettlement.id).all())
        existing = existing_settlements[0] if existing_settlements else None
        if ret.financial_status == "settled" and existing:
            requested_kind = d.get("settlementType")
            saved_types = {row.settlement_type for row in existing_settlements}
            if requested_kind == "exchange" and saved_types == {"exchange"}:
                doc = CombinedTransactionDocument.query.filter_by(
                    final_return_id=rid,
                    is_final=True,
                ).with_for_update().first()
                linked_settlement = next(
                    (row for row in existing_settlements if doc and row.id == doc.settlement_id and row.settlement_type == "exchange"),
                    None,
                )
                if doc and doc.return_id == rid and linked_settlement:
                    return jsonify({
                        "success": True,
                        "replayed": True,
                        "alreadyCompleted": True,
                        "settlementId": linked_settlement.id,
                        "documentNo": doc.document_no,
                        "documentUrl": f"/combined-transaction/{doc.document_no}",
                        "salesBillUrl": f"/sales-bill?returnExchangeDocument={doc.document_no}",
                        "exchangeReference": linked_settlement.exchange_ref,
                        "isFinal": True,
                    }), 200
            if requested_kind == "refund" and saved_types == {"refund"}:
                doc = CombinedTransactionDocument.query.filter_by(
                    final_return_id=rid,
                    is_final=True,
                ).first()
                if doc:
                    return jsonify({
                        "success": True,
                        "replayed": True,
                        "alreadyCompleted": True,
                        "documentNo": doc.document_no,
                        "documentUrl": f"/combined-transaction/{doc.document_no}",
                        "customerReceiptUrl": f"/return-receipt/{doc.document_no}",
                        "isFinal": True,
                    }), 200
            audit_row = AuditLog.query.filter_by(
                entity="sales_return",
                entity_id=rid,
                action="settle_return_bill",
            ).order_by(AuditLog.id.desc()).first()
            if audit_row and (audit_row.details or {}).get("request_hash") == request_hash:
                doc = CombinedTransactionDocument.query.filter_by(
                    final_return_id=rid,
                    is_final=True,
                ).first()
                if doc:
                    return jsonify({
                        "success": True,
                        "replayed": True,
                        "documentNo": doc.document_no,
                        "documentUrl": f"/combined-transaction/{doc.document_no}",
                        "salesBillUrl": f"/sales-bill?returnExchangeDocument={doc.document_no}" if requested_kind == "exchange" else None,
                        "customerReceiptUrl": f"/return-receipt/{doc.document_no}",
                        "isFinal": True,
                    }), 200
            return fail("This Return Bill has already been fully settled.", 409)
        items = ReturnItem.query.filter_by(return_id=rid).order_by(ReturnItem.id).with_for_update().all()
        if not items:
            return fail("The Return Bill has no returned items.", 409)
        item_settlement_ids = {settlement.return_item_id for settlement in existing_settlements}
        if any(item.settlement_status == "settled" and item.id not in item_settlement_ids for item in items):
            return fail(
                "Return settlement history is inconsistent; an audited correction is required.",
                409,
            )
        remaining_items = [item for item in items if item.id not in item_settlement_ids and item.settlement_status != "settled"]
        if not remaining_items:
            return fail("No unsettled Return Bill items remain.", 409)
        sale = Sale.query.filter_by(id=ret.sale_id).with_for_update().first()
        if not sale:
            return fail("The original invoice could not be found.", 409)
        return_value = Decimal("0.00")
        for item in remaining_items:
            source = SaleItem.query.filter_by(
                id=item.sale_item_id,
                sale_id=sale.id,
            ).with_for_update().first()
            if not source:
                return fail("A returned item no longer matches the original invoice.", 409)
            return_value += (Decimal(str(item.total_kg)) * Decimal(str(source.price_per_kg))).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
        kind = d.get("settlementType")
        if kind not in {"refund", "exchange"}:
            return fail("Choose Cash Refund or Item Exchange after approval.")
        active_drafts= (HeldSale.query.join(ReturnItem, HeldSale.return_item_id == ReturnItem.id).filter(
            ReturnItem.return_id == rid,
            HeldSale.draft_type == "return_exchange",
        ).with_for_update().all())
        if any(draft.created_by != user.id and user.role != "admin" for draft in active_drafts):
            return fail("An active Return Exchange draft belongs to another user.", 409)
        held = None
        if d.get("heldExchangeId") not in (None, ""):
            try:
                held_id = int(d["heldExchangeId"])
            except (ValueError, TypeError):
                return fail("The held exchange reference is invalid.")
            held = next((draft for draft in active_drafts if draft.id == held_id), None)
            if not held:
                return fail("Held Return Exchange draft not found.", 404)
        replacement_payload = d.get("items") if isinstance(
            d.get("items"),
            list,
        ) else d.get("replacements")
        if held and not replacement_payload:
            replacement_payload = (held.payload or {}).get("replacements", [])
        prepared = []
        replacement_value = Decimal("0.00")
        if kind == "exchange":
            prepared, _ = _prepare_sale_items(replacement_payload)
            for raw, part in zip(replacement_payload, prepared):
                price_key = part["variety"] if part["category"] in {
                    "bulk",
                    "packed",
                } else part["product"]
                setting = PriceSetting.query.filter_by(
                    item_name=price_key,
                    price_type="selling",
                    is_active=True,
                ).with_for_update().first()
                reason = raw.get("manualPriceReason")
                if not setting or setting.default_price is None:
                    if user.role != "admin":
                        raise ValueError(f"A configured current selling price is required for {price_key}; ask an Admin to set it.")
                    if raw.get("confirmManualPrice") is not True or not isinstance(
                        reason,
                        str,
                    ) or not reason.strip() or len(reason) > 2000:
                        raise ValueError(f"A configured current selling price is required for {price_key}, unless an Admin confirms a manual price and provides its reason.")
                    reason = reason.strip()
                else:
                    configured = sale_money(
                        setting.default_price,
                        "current replacement selling price",
                        Decimal("0.01"),
                    )
                    if setting.selling_locked and part["price"] != configured:
                        raise ValueError(f"Configured selling price for {price_key} has changed. Refresh the exchange and try again.")
                    # Settlement value is authoritative from the current DB price,
                    # even when the UI allows editing prices on ordinary sales.
                    part["price"] = configured
                    part["line_total"] = sale_money(
                        part["total_kg"] * configured,
                        "replacement line total",
                        Decimal("0.01"),
                    )
                    reason = None
                part["manual_price_reason"] = reason
                stock = get_stock(
                    part["stock_item"],
                    part["stock_type"],
                    part["variety"],
                    part["package_size"],
                )
                if (
                    Decimal(str(stock.quantity_kg)) < part["total_kg"]
                    or (part["stock_type"] == "packed" and Decimal(str(stock.quantity_bags)) < part["quantity"])
                ):
                    raise ValueError(f"Insufficient stock for {part['product']}.")
                replacement_value += Decimal(str(part["line_total"]))
        elif replacement_payload:
            raise ValueError("Replacement products are available only for Item Exchange.")
        if kind == "exchange" and d.get("expectedReplacementTotal") not in (None, ""):
            expected_replacement_value = sale_money(
                d["expectedReplacementTotal"],
                "displayed replacement total",
                Decimal("0.00"),
            )
            if expected_replacement_value != replacement_value:
                raise ValueError("Configured replacement prices changed. Refresh the exchange and confirm the updated total.")
        delta = replacement_value - return_value
        held_payload = (held.payload or {}) if held else {}
        method = d.get("paymentMethod", held_payload.get("paymentMethod", "Cash"))
        reference = d.get("paymentReference", held_payload.get("paymentReference"))
        refund_method = d.get("refundMethod", held_payload.get("refundMethod", "Cash"))
        refund_reference = d.get("refundReference", held_payload.get("refundReference"))
        if delta > 0:
            if not isinstance(method, str):
                raise ValueError("Collect the exchange difference by Cash or Bank Transfer.")
            method_key = method.strip().lower().replace("_", " ")
            method_map = {"cash": "Cash", "bank": "Bank Transfer", "bank transfer": "Bank Transfer"}
            if method_key not in method_map:
                raise ValueError("Collect the exchange difference by Cash or Bank Transfer.")
            method = method_map[method_key]
            if method == "Bank Transfer" and (not isinstance(
                reference,
                str,
            ) or not reference.strip() or len(reference) > 100):
                raise ValueError("A valid bank transfer reference is required.")
        if kind == "exchange" and delta < 0:
            if not isinstance(refund_method, str):
                raise ValueError("Refund the exchange difference by Cash or Bank Transfer.")
            refund_key = refund_method.strip().lower().replace("_", " ")
            refund_map = {"cash": "Cash", "bank": "Bank Transfer", "bank transfer": "Bank Transfer"}
            if refund_key not in refund_map:
                raise ValueError("Refund the exchange difference by Cash or Bank Transfer.")
            refund_method = refund_map[refund_key]
            if refund_method == "Bank Transfer" and (not isinstance(
                refund_reference,
                str,
            ) or not refund_reference.strip() or len(refund_reference) > 100):
                raise ValueError("A valid refund bank transfer reference is required.")
        cash_received = Decimal("0.00")
        change_returned = Decimal("0.00")
        if kind == "exchange" and delta > 0 and method == "Cash":
            cash_received = sale_money(
                d.get("cashReceived", held_payload.get("cashReceived", 0)),
                "cash received",
                Decimal("0.00"),
            )
            if cash_received < delta:
                raise ValueError(f"Collect at least {delta:.2f} before confirming the exchange.")
            change_returned = (cash_received - delta).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
        anchor = remaining_items[0]
        now = datetime.utcnow()
        settlement = ReturnSettlement(
            return_item_id=anchor.id,
            settlement_type=kind,
            refund_option="direct_refund" if kind == "refund" else None,
            return_value=return_value,
            replacement_value=replacement_value,
            cash_refund=return_value if kind == "refund" else Decimal("0.00"),
            amount_collected=delta if delta > 0 else Decimal("0.00"),
            difference_refund=abs(delta) if kind == "exchange" and delta < 0 else Decimal("0.00"),
            exchange_ref=held.hold_ref if held else None,
            created_by=user.id,
            created_at=now,
        )
        db.session.add(settlement)
        db.session.flush()
        for part in prepared:
            db.session.add(ReturnReplacementItem(
                settlement_id=settlement.id,
                category=part["category"],
                product_name=part["product"],
                variety=part["variety"] or None,
                quantity=part["quantity"],
                unit=part["unit"],
                package_size_kg=part["package_size"],
                total_kg=part["total_kg"],
                price_per_kg=part["price"],
                line_total=part["line_total"],
                manual_price_reason=part.get("manual_price_reason"),
            ))
            move_stock(
                part["stock_item"],
                part["stock_type"],
                part["variety"],
                -part["total_kg"],
                -part["quantity"] if part["stock_type"] == "packed" else 0,
                part["package_size"],
                "return_exchange_out",
                "return_settlement",
                settlement.id,
                notes=f"Replacement for {ret.return_no}",
            )
        if kind == "refund":
            db.session.add(ReturnFinancialEntry(
                settlement_id=settlement.id,
                entry_type="cash_refund",
                amount=return_value,
                method="Cash",
                sale_id=ret.sale_id,
                created_by=user.id,
                created_at=now,
            ))
        elif delta > 0:
            db.session.add(ReturnFinancialEntry(
                settlement_id=settlement.id,
                entry_type="exchange_difference_collection",
                amount=delta,
                method=method,
                reference=reference,
                sale_id=ret.sale_id,
                created_by=user.id,
                created_at=now,
            ))
        elif delta < 0:
            db.session.add(ReturnFinancialEntry(
                settlement_id=settlement.id,
                entry_type="exchange_difference_refund",
                amount=abs(delta),
                method=refund_method,
                reference=refund_reference if refund_method == "Bank Transfer" else None,
                sale_id=ret.sale_id,
                created_by=user.id,
                created_at=now,
            ))
        for item in remaining_items:
            item.settlement_status = "settled"
        settled_return_value = sum(
            (Decimal(str(row.return_value)) for row in existing_settlements),
            Decimal("0.00"),
        ) + return_value
        ret.settlement_amount = settled_return_value
        ret.financial_status = "settled" if all(item.settlement_status == "settled" for item in items) else "partially_settled"
        final = ret.financial_status == "settled"
        document = _make_combined_document(ret, user, settlement, final)
        if kind == "exchange":
            snapshot = dict(document.snapshot or {})
            snapshot_settlements = list(snapshot.get("settlements") or [])
            for row in snapshot_settlements:
                if int(row.get("returnItemId") or 0) == anchor.id:
                    row.update({
                        "cashReceived": str(cash_received),
                        "changeReturned": str(change_returned),
                        "netAmountPayable": str(max(delta, Decimal("0.00"))),
                        "refundDue": str(max(-delta, Decimal("0.00"))),
                    })
                    break
            snapshot["settlements"] = snapshot_settlements
            document.snapshot = snapshot
        doc_no = document.document_no
        for draft in active_drafts:
            db.session.delete(draft)
            db.session.add(AuditLog(
                user_id=user.id,
                action="complete",
                entity="held_return_exchange",
                entity_id=draft.id,
                details={"reference": draft.hold_ref, "document_no": doc_no},
            ))
        db.session.add(AuditLog(
            user_id=user.id,
            action="settle_return_bill",
            entity="sales_return",
            entity_id=rid,
            details={
                "request_hash": request_hash,
                "type": kind,
                "return_value": str(return_value),
                "replacement_value": str(replacement_value),
                "document_no": doc_no,
            },
        ))
        # Settlement, replacement stock, financial entries, final Return status,
        # and the customer-facing document share this single commit boundary.
        db.session.commit()
        return jsonify({
            "success": True,
            "settlementId": settlement.id,
            "documentNo": doc_no,
            "documentUrl": f"/combined-transaction/{doc_no}",
            "customerReceiptUrl": f"/return-receipt/{doc_no}",
            "salesBillUrl": f"/sales-bill?returnExchangeDocument={doc_no}" if kind == "exchange" else None,
            "exchangeReference": settlement.exchange_ref,
            "isFinal": final,
            "returnValue": str(return_value),
            "replacementValue": str(replacement_value),
            "amountCollected": str(max(delta, Decimal("0.00"))),
            "differenceRefund": str(max(-delta, Decimal("0.00"))),
        }), 201
    except (ValueError, TypeError, KeyError) as exc:
        db.session.rollback()
        return fail(str(exc))
    except IntegrityError:
        db.session.rollback()
        return fail(
            "This Return Bill was already settled or the request conflicts with existing data.",
            409,
        )
    except SQLAlchemyError as exc:
        db.session.rollback()
        current_app.logger.error(
            "Whole Return settlement failed; exception_type=%s",
            type(exc).__name__,
        )
        return fail("The Return Bill could not be settled. No changes were saved.", 500)
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "Whole Return settlement aborted; exception_type=%s",
            type(exc).__name__,
        )
        return fail("The Return Bill could not be settled. No changes were saved.", 500)


@api.get("/returns/documents/<document_no>")
@login_required_api
@tab_required_api("Sales & Invoicing")
def combined_document_data(document_no):
    document = CombinedTransactionDocument.query.filter_by(document_no=document_no).first()
    if not document:
        return fail("Combined transaction document not found.", 404)
    customer = resolve_snapshot_customer(document.snapshot)
    return jsonify({
        "success": True,
        "document": normalize_transaction_snapshot(document.snapshot, customer),
    })


@api.get("/returns/documents/<document_no>/sales-bill")
@login_required_api
@tab_required_api("Sales & Invoicing")
def return_exchange_sales_bill_data(document_no):
    document = CombinedTransactionDocument.query.filter_by(document_no=document_no).first()
    if not document:
        return fail("Return Exchange bill not found.", 404)
    settlement = db.session.get(
        ReturnSettlement,
        document.settlement_id,
    ) if document.settlement_id else None
    if not settlement or settlement.settlement_type != "exchange":
        return fail("This document is not a Return Exchange Sales Bill.", 409)
    ret = db.session.get(SalesReturn, document.return_id)
    item = db.session.get(ReturnItem, settlement.return_item_id)
    if not ret or not item or item.return_id != ret.id or ret.approval_status != "approved":
        return fail("The approved Return Bill linked to this exchange could not be verified.", 409)
    sale = db.session.get(Sale, ret.sale_id)
    if not sale:
        return fail("The original invoice linked to this exchange could not be found.", 409)
    replacements = (ReturnReplacementItem.query.filter_by(settlement_id=settlement.id).order_by(ReturnReplacementItem.id).all())
    replacement_subtotal = sum(
        (Decimal(str(row.line_total)) for row in replacements),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    approved_return_value = Decimal(str(settlement.return_value)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    if replacement_subtotal != Decimal(str(settlement.replacement_value)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    ):
        return fail("Saved replacement lines do not match the exchange settlement total.", 409)
    difference = replacement_subtotal - approved_return_value
    exchange_snapshot = next(
        (row for row in (document.snapshot or {}).get(
            "settlements",
            [],
        ) if int(row.get("returnItemId") or 0) == item.id),
        {},
    )
    entries = ReturnFinancialEntry.query.filter_by(settlement_id=settlement.id).order_by(ReturnFinancialEntry.id).all()
    collection_entry = next(
        (row for row in entries if row.entry_type == "exchange_difference_collection"),
        None,
    )
    refund_entry = next(
        (row for row in entries if row.entry_type == "exchange_difference_refund"),
        None,
    )
    collection_method = collection_entry.method if collection_entry else "No additional payment"
    payment_method = collection_method if collection_entry else(f"Refund: {refund_entry.method or 'Cash'}" if refund_entry else "No additional payment")
    net_payable = max(difference, Decimal("0.00")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    refund_due = max(-difference, Decimal("0.00")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    cash_received = Decimal(str(exchange_snapshot.get("cashReceived", settlement.amount_collected or 0))).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    change_returned = Decimal(str(exchange_snapshot.get("changeReturned", 0))).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    amount_received = (cash_received if collection_entry and collection_entry.method == "Cash" else Decimal(str(collection_entry.amount)) if collection_entry else Decimal("0.00"))
    bill = {
        "invoiceNumber": document.document_no,
        "invoiceDate": (settlement.created_at or document.created_at).isoformat(),
        "customerName": _sale_customer_fields(sale).get("customerName") or sale.customer_name or "Retail Customer",
        "paymentTypeLabel": "Return Exchange",
        "paymentMethodLabel": payment_method,
        "items": [{
            "category": row.category,
            "product": row.product_name,
            "variety": row.variety or "",
            "package": row.package_size_kg or "",
            "quantity": str(row.quantity),
            "unit": row.unit,
            "price": str(row.price_per_kg),
            "lineTotal": str(row.line_total),
        } for row in replacements],
        "returnExchange": {
            "returnNumber": ret.return_no,
            "originalInvoice": sale.invoice_no,
            "approvedReturnValue": str(approved_return_value),
            "amountDeducted": str(approved_return_value),
            "replacementSubtotal": str(replacement_subtotal),
            "netAmountPayable": str(net_payable),
            "refundDue": str(refund_due),
            "cashReceived": str(cash_received),
            "amountReceived": str(amount_received),
            "changeReturned": str(change_returned),
            "paymentMethod": collection_method,
            "refundMethod": refund_entry.method if refund_entry else None,
        },
        "total": str(net_payable),
        "received": str(amount_received),
        "changeReturned": str(change_returned),
        "customerBalance": "0.00",
        "due": "0.00",
    }
    return jsonify({"success": True, "bill": bill})


@api.get("/payments/customers/<int:cid>/invoices")
@login_required_api
@tab_required_api("Payments & Receivables")
def customer_invoices(cid):
    customer = Customer.query.filter_by(id=cid, is_active=True).first()
    if not customer:
        return fail("Active Registered Customer not found.", 404)
    rows= (Sale.query.filter_by(customer_id=cid).filter(Sale.amount_due > 0).order_by(
        Sale.sale_date.asc(),
        Sale.id.asc(),
    ).all())
    invoice_balance = Decimal(str(customer_outstanding(cid) or 0))
    account_balance = customer_account_balance(cid)
    return jsonify({
        "success": True,
        "invoiceOutstanding": str(invoice_balance),
        "customerAccountBalance": str(account_balance),
        "invoices": [{
            "id": s.id,
            "invoiceNo": s.invoice_no,
            "customerId": cid,
            **_sale_customer_fields(s, {cid: customer}),
            "date": s.sale_date.date().isoformat(),
            "amount": str(s.total_amount),
            "paid": str(s.amount_paid),
            "due": str(s.amount_due),
        } for s in rows],
    })


@api.get("/payments/completed-outstanding")
@login_required_api
@tab_required_api("Payments & Receivables")
def payments_completed_outstanding():
    try:
        page = int(request.args.get("page", "1"))
        per_page = int(request.args.get("perPage", "50"))
    except (TypeError, ValueError):
        return fail("Page and perPage must be whole numbers.")
    if page < 1 or not 1 <= per_page <= 100:
        return fail("Page must be positive and perPage must be between 1 and 100.")
    term = request.args.get("q", "")
    if not isinstance(term, str) or len(term) > 100:
        return fail("Search must be at most 100 characters.")
    query= (db.session.query(Sale, Customer).join(Customer, Customer.id == Sale.customer_id).filter(
        Sale.status == "confirmed",
        Sale.payment_type.in_(("credit", "partial")),
        Sale.amount_due == 0,
    ))
    term = term.strip()
    if term:
        predicates = [
            Sale.invoice_no.ilike(f"%{term}%"),
            Customer.customer_code.ilike(f"%{term}%"),
            Customer.first_name.ilike(f"%{term}%"),
            Customer.last_name.ilike(f"%{term}%"),
        ]
        query = query.filter(or_(*predicates))
    candidates = query.order_by(Sale.sale_date.desc(), Sale.id.desc()).all()
    timelines = _payment_invoice_timeline([sale.id for sale, _ in candidates])
    completed = []
    for sale, customer in candidates:
        timeline = timelines.get(sale.id, {})
        if timeline.get("paid") != Decimal(str(sale.total_amount)) or not timeline.get("settledAt"):
            continue
        completed.append({
            "id": sale.id,
            "invoiceNo": sale.invoice_no,
            "customerId": customer.id,
            "customerCode": customer.customer_code,
            **_sale_customer_fields(sale, {customer.id: customer}),
            "date": sale.sale_date.date().isoformat(),
            "amount": str(sale.total_amount),
            "paid": str(sale.amount_paid),
            "completedAt": _payment_local_datetime(timeline["settledAt"]),
        })
    total = len(completed)
    start = (page - 1) * per_page
    return jsonify({
        "success": True,
        "invoices": completed[start:start + per_page],
        "pagination": {
            "page": page,
            "perPage": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page,
        },
    })


@api.get("/payments/invoices/<int:invoice_id>")
@login_required_api
@tab_required_api("Payments & Receivables")
def payment_invoice_detail(invoice_id):
    sale = db.session.get(Sale, invoice_id)
    if not sale:
        return fail("Invoice not found.", 404)
    return jsonify({"success": True, "sale": sale_json(sale)})


@api.get("/payments/customers")
@limiter.limit("60 per minute")
@login_required_api
@tab_required_api("Payments & Receivables")
def payment_customer_lookup():
    term = request.args.get("q", "")
    if not isinstance(term, str) or len(term) > 100:
        return fail("Customer search must be at most 100 characters.")
    adjustment_totals= (db.session.query(
        CustomerBalanceAdjustment.customer_id.label("customer_id"),
        func.coalesce(func.sum(CustomerBalanceAdjustment.amount), 0).label("adjustment_total"),
    ).group_by(CustomerBalanceAdjustment.customer_id).subquery())
    query= (db.session.query(
        Customer,
        func.coalesce(func.sum(Sale.amount_due), 0),
        func.coalesce(adjustment_totals.c.adjustment_total, 0),
    ).outerjoin(Sale, and_(Sale.customer_id == Customer.id, Sale.amount_due > 0)).outerjoin(adjustment_totals, adjustment_totals.c.customer_id == Customer.id).filter(Customer.is_active.is_(True)))
    term = term.strip()
    if term:
        predicates = [
            Customer.customer_code.ilike(f"%{term}%"),
            Customer.first_name.ilike(f"%{term}%"),
            Customer.last_name.ilike(f"%{term}%"),
            Customer.phone.ilike(f"%{term}%"),
        ]
        parts = term.split(maxsplit=1)
        if len(parts) == 2:
            predicates.append(and_(
                Customer.first_name.ilike(f"%{parts[0]}%"),
                Customer.last_name.ilike(f"%{parts[1]}%"),
            ))
        query = query.filter(or_(*predicates))
    rows = query.group_by(Customer.id, adjustment_totals.c.adjustment_total).order_by(
        Customer.first_name,
        Customer.last_name,
        Customer.id,
    ).all()
    return jsonify({
        "success": True,
        "customers": [{
            "id": customer.id,
            "customerCode": customer.customer_code,
            "name": customer.full_name,
            **customer_fields(customer.full_name, "registered", registered_customer=True),
            "phone": customer.phone,
            "creditLimit": str(customer.credit_limit),
            "invoiceOutstanding": str(Decimal(str(due or 0)).quantize(Decimal("0.01"))),
            "outstanding": str((Decimal(str(due or 0)) + Decimal(str(adjustments or 0))).quantize(Decimal("0.01"))),
        } for customer, due, adjustments in rows],
    })


@api.get("/payments/outstanding")
@login_required_api
@tab_required_api("Payments & Receivables")
def payments_outstanding():
    try:
        page = int(request.args.get("page", "1"))
        per_page = int(request.args.get("perPage", "50"))
    except (TypeError, ValueError):
        return fail("Page and perPage must be whole numbers.")
    if page < 1 or not 1 <= per_page <= 100:
        return fail("Page must be positive and perPage must be between 1 and 100.")
    term = request.args.get("q", "")
    if not isinstance(term, str) or len(term) > 100:
        return fail("Search must be at most 100 characters.")
    query= (db.session.query(Sale, Customer).join(
        Customer,
        Customer.id == Sale.customer_id,
    ).filter(Sale.amount_due > 0))
    term = term.strip()
    if term:
        predicates = [
            Sale.invoice_no.ilike(f"%{term}%"),
            Customer.customer_code.ilike(f"%{term}%"),
            Customer.first_name.ilike(f"%{term}%"),
            Customer.last_name.ilike(f"%{term}%"),
        ]
        query = query.filter(or_(*predicates))
    total = query.count()
    rows = query.order_by(
        Sale.sale_date.asc(),
        Sale.id.asc(),
    ).offset((page - 1) * per_page).limit(per_page).all()
    return jsonify({
        "success": True,
        "invoices": [{
            "id": sale.id,
            "invoiceNo": sale.invoice_no,
            "customerId": customer.id,
            "customerCode": customer.customer_code,
            **_sale_customer_fields(sale, {customer.id: customer}),
            "date": sale.sale_date.date().isoformat(),
            "amount": str(sale.total_amount),
            "paid": str(sale.amount_paid),
            "due": str(sale.amount_due),
        } for sale, customer in rows],
        "pagination": {
            "page": page,
            "perPage": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page,
        },
    })


@api.get("/payments/overview")
@login_required_api
@tab_required_api("Payments & Receivables")
def payments_overview():
    invoice_outstanding=Decimal(str(Sale.query.with_entities(func.coalesce(
        func.sum(Sale.amount_due),
        0,
    )).scalar() or 0))
    adjustment_balance=Decimal(str(CustomerBalanceAdjustment.query.with_entities(func.coalesce(
        func.sum(CustomerBalanceAdjustment.amount),
        0,
    )).scalar() or 0))
    outstanding = invoice_outstanding + adjustment_balance
    today_collections=Payment.query.with_entities(func.coalesce(
        func.sum(Payment.amount),
        0,
    )).filter(Payment.payment_date == colombo_date()).scalar() or Decimal("0")
    customers= (db.session.query(func.count(func.distinct(Sale.customer_id))).filter(
        Sale.customer_id.isnot(None),
        Sale.amount_due > 0,
    ).scalar() or 0)
    partials = Sale.query.filter(Sale.amount_paid > 0, Sale.amount_due > 0).count()
    return jsonify({
        "success": True,
        "summary": {
            "totalOutstanding": str(outstanding),
            "todayCollections": str(today_collections),
            "outstandingCustomers": customers,
            "partialInvoices": partials,
        },
    })


@api.post("/payments")
@limiter.limit("10 per minute")
@login_required_api
@tab_required_api("Payments & Receivables")
def payment_create():
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return fail("A JSON payment request is required.")
    try:
        user = current_user()
        key = _payment_idempotency_key(d)
        fingerprint = _payment_request_fingerprint(d)
        replay = _payment_replay(key, fingerprint, user.id)
        if replay is not None:
            return replay
        allowed = {
            "customerId",
            "amount",
            "method",
            "bankReference",
            "allocationMethod",
            "allocations",
            "date",
            "note",
            "idempotencyKey",
        }
        if set(d) - allowed:
            raise ValueError("Unexpected payment request field.")
        allocation_method = d.get("allocationMethod", "oldest")
        if not isinstance(allocation_method, str):
            raise ValueError("Allocation method must be oldest or manual.")
        allocation_method = allocation_method.strip().lower()
        if allocation_method not in {"oldest", "manual"}:
            raise ValueError("Allocation method must be oldest or manual.")
        method = d.get("method", "Cash")
        if not isinstance(method, str):
            raise ValueError("Payment method must be Cash or Bank Transfer.")
        method_key = method.strip().lower()
        method_map = {"cash": "Cash", "bank transfer": "Bank Transfer"}
        if method_key not in method_map:
            raise ValueError("Payment method must be Cash or Bank Transfer.")
        method = method_map[method_key]
        amount = _payment_money(d.get("amount"), "payment amount", positive=True)
        bank_reference = d.get("bankReference")
        if bank_reference is not None:
            if not isinstance(bank_reference, str) or len(bank_reference.strip()) > 100:
                raise ValueError("Bank reference must be text of at most 100 characters.")
            bank_reference = bank_reference.strip() or None
        if method == "Bank Transfer" and not bank_reference:
            raise ValueError("Bank reference is required for Bank Transfer.")
        note = d.get("note")
        if note is not None:
            if not isinstance(note, str) or len(note.strip()) > 2000:
                raise ValueError("Payment note must be text of at most 2000 characters.")
            note = note.strip() or None
        raw_date = d.get("date")
        if raw_date in (None, ""):
            payment_date = colombo_date()
        else:
            if not isinstance(raw_date, str) or len(raw_date) > 10:
                raise ValueError("Payment date must be a valid ISO date.")
            payment_date = date.fromisoformat(raw_date)
            if payment_date.isoformat() != raw_date:
                raise ValueError("Payment date must be a valid ISO date.")
        customer_id = d.get("customerId")
        if isinstance(customer_id, bool) or customer_id in (None, ""):
            raise ValueError("Select a registered customer.")
        try:
            customer_id = int(customer_id)
        except (TypeError, ValueError):
            raise ValueError("Customer ID must be a whole number.")
        if str(d.get("customerId")).strip() not in {str(customer_id), f"+{customer_id}"}:
            raise ValueError("Customer ID must be a whole number.")
        allocations = d.get("allocations", [])
        if not isinstance(allocations, list):
            raise ValueError("Allocations must be a list.")
        if allocation_method == "manual" and not allocations:
            raise ValueError("Manual allocation requires at least one invoice allocation.")

        # This customer lock serializes collections and registered sales for the
        # same account. The locked idempotency read below sees concurrent commits.
        c = Customer.query.filter_by(id=customer_id, is_active=True).with_for_update().first()
        if not c:
            return fail("Active Registered Customer not found.", 404)
        replay = _payment_replay(key, fingerprint, user.id, lock=True)
        if replay is not None:
            db.session.rollback()
            return replay
        invoices= (Sale.query.filter_by(customer_id=c.id).filter(Sale.amount_due > 0).order_by(
            Sale.sale_date.asc(),
            Sale.id.asc(),
        ).with_for_update().all())
        outstanding_by_id = {invoice.id: Decimal(str(invoice.amount_due)) for invoice in invoices}
        if amount > sum(outstanding_by_id.values(), Decimal("0.00")):
            raise ValueError("Payment exceeds the customer's total outstanding balance.")
        planned = []
        if allocation_method == "manual":
            aggregated = {}
            for index, allocation in enumerate(allocations, 1):
                if not isinstance(allocation, dict) or set(allocation) - {
                    "invoiceId",
                    "invoiceNo",
                    "amount",
                }:
                    raise ValueError(f"Allocation {index} must contain a valid invoiceId and amount.")
                raw_id = allocation.get("invoiceId")
                if isinstance(raw_id, bool) or raw_id in (None, ""):
                    raise ValueError(f"Allocation {index} must contain a valid invoiceId.")
                try:
                    invoice_id = int(raw_id)
                except (TypeError, ValueError):
                    raise ValueError(f"Allocation {index} must contain a valid invoiceId.")
                if str(raw_id).strip() not in {str(invoice_id), f"+{invoice_id}"}:
                    raise ValueError(f"Allocation {index} must contain a whole-number invoiceId.")
                part = _payment_money(
                    allocation.get("amount"),
                    f"allocation {index} amount",
                    positive=True,
                )
                aggregated[invoice_id] = aggregated.get(invoice_id, Decimal("0.00")) + part
            invoice_map = {invoice.id: invoice for invoice in invoices}
            if sum(aggregated.values(), Decimal("0.00")) != amount:
                raise ValueError("Manual allocations must equal the payment amount.")
            for invoice_id, part in sorted(aggregated.items()):
                invoice = invoice_map.get(invoice_id)
                if invoice is None or part > outstanding_by_id[invoice_id]:
                    raise ValueError("Manual allocation exceeds an invoice's current outstanding balance or invoice is unavailable.")
                planned.append((invoice, part))
        else:
            remaining = amount
            for invoice in invoices:  # Query order is FIFO with a stable id tie-breaker.
                if remaining <= 0:
                    break
                part = min(remaining, outstanding_by_id[invoice.id])
                if part > 0:
                    planned.append((invoice, part))
                    remaining -= part
            if remaining > 0:
                raise ValueError("Payment exceeds the customer's total outstanding balance.")

        p = Payment(
            receipt_no=f"PAY-{uuid4().hex.upper()}",
            customer_id=c.id,
            amount=amount,
            method=method,
            payment_date=payment_date,
            bank_reference=bank_reference,
            allocation_method=allocation_method,
            note=note,
            created_by=user.id,
            idempotency_key=key,
            request_fingerprint=fingerprint,
        )
        db.session.add(p)
        db.session.flush()
        for invoice, part in planned:
            invoice.amount_paid = Decimal(str(invoice.amount_paid)) + part
            invoice.amount_due = Decimal(str(invoice.amount_due)) - part
            db.session.add(PaymentAllocation(payment_id=p.id, sale_id=invoice.id, amount=part))
        audit("create", "payment", p.id, {"request_fingerprint": fingerprint})
        db.session.commit()
        return jsonify({"success": True, "payment": payment_receipt_json(p)}), 201
    except (ValueError, InvalidOperation) as error:
        db.session.rollback()
        return fail(str(error))
    except IntegrityError:
        db.session.rollback()
        try:
            if 'key' in locals() and 'fingerprint' in locals():
                replay = _payment_replay(key, fingerprint, current_user().id, lock=True)
                if replay is not None:
                    db.session.rollback()
                    return replay
        except SQLAlchemyError:
            db.session.rollback()
        return fail(
            "The request conflicts with existing data. Retry with the same idempotency key or review the submitted details.",
            409,
        )
    except SQLAlchemyError as error:
        db.session.rollback()
        current_app.logger.error(
            "Payment transaction failed; exception_type=%s",
            type(error).__name__,
        )
        return fail("The payment could not be saved. No balances were changed. Please retry.", 500)
    except Exception as error:
        db.session.rollback()
        current_app.logger.error(
            "Unexpected payment failure; exception_type=%s",
            type(error).__name__,
        )
        return fail("The payment could not be saved. No balances were changed. Please retry.", 500)


def _payment_money(value, label, positive=False):
    amount = dec(value, label, Decimal("0.01") if positive else 0)
    if amount > Decimal("999999999999.99"):
        raise ValueError(f"{label} exceeds the supported amount range.")
    try:
        if amount.quantize(Decimal("0.01")) != amount:
            raise ValueError(f"{label} may have at most two decimal places.")
    except InvalidOperation:
        raise ValueError(f"Invalid {label}.")
    return amount


def _payment_idempotency_key(data):
    header = (request.headers.get("Idempotency-Key") or "").strip()
    body = data.get("idempotencyKey", "")
    if not isinstance(body, str):
        raise ValueError("Idempotency key must be text.")
    body = body.strip()
    if header and body and header != body:
        raise ValueError("Header and body idempotency keys do not match.")
    key = header or body
    if not 16 <= len(key) <= 100:
        raise ValueError("A stable idempotency key of 16 to 100 characters is required.")
    return key


def _payment_request_fingerprint(data):
    canonical = json.dumps(
        {key: value for key, value in data.items() if key != "idempotencyKey"},
        sort_keys=True,
        separators= (",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _payment_replay(key, fingerprint, user_id, lock=False):
    query = Payment.query.filter_by(idempotency_key=key)
    if lock:
        query = query.with_for_update()
    existing = query.first()
    if not existing:
        return None
    if existing.created_by != user_id or existing.request_fingerprint != fingerprint:
        return fail("This idempotency key was already used with different payment details.", 409)
    return jsonify({
        "success": True,
        "payment": payment_receipt_json(existing),
        "replayed": True,
    }), 200


def _payment_local_datetime(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(COLOMBO_TIMEZONE).isoformat(timespec="seconds")


def _payment_invoice_timeline(sale_ids):
    ids = list(set(sale_ids))
    if not ids:
        return {}
    sale_totals={sale_id: Decimal(str(total)) for sale_id, total in db.session.query(
        Sale.id,
        Sale.total_amount,
    ).filter(Sale.id.in_(ids)).all()}
    events_by_sale = {sale_id: {} for sale_id in ids}
    payments= (db.session.query(
        PaymentAllocation.sale_id,
        Payment.id,
        Payment.created_at,
        PaymentAllocation.amount,
    ).join(Payment, Payment.id == PaymentAllocation.payment_id).filter(PaymentAllocation.sale_id.in_(ids)).all())
    for sale_id, payment_id, created_at, amount in payments:
        event_key = ("payment", payment_id)
        event = events_by_sale[sale_id].setdefault(
            event_key,
            {
                "at": created_at,
                "kind": 0,
                "id": payment_id,
                "amount": Decimal("0.00"),
                "paymentId": payment_id,
            },
        )
        event["amount"] += Decimal(str(amount))
    reversals= (db.session.query(
        PaymentReversalAllocation.sale_id,
        PaymentReversal.id,
        PaymentReversal.created_at,
        PaymentReversalAllocation.amount,
    ).join(PaymentReversal, PaymentReversal.id == PaymentReversalAllocation.reversal_id).filter(PaymentReversalAllocation.sale_id.in_(ids)).all())
    for sale_id, reversal_id, created_at, amount in reversals:
        event_key = ("reversal", reversal_id)
        event = events_by_sale[sale_id].setdefault(
            event_key,
            {
                "at": created_at,
                "kind": 1,
                "id": reversal_id,
                "amount": Decimal("0.00"),
                "paymentId": None,
            },
        )
        event["amount"] += Decimal(str(amount))
    timelines = {}
    for sale_id, grouped in events_by_sale.items():
        events = sorted(
            grouped.values(),
            key=lambda row: (row["at"] or datetime.min, row["kind"], row["id"]),
        )
        paid = Decimal("0.00")
        settled_at = None
        after_payment = {}
        for event in events:
            paid += event["amount"] if event["kind"] == 0 else -event["amount"]
            if event["paymentId"] is not None:
                after_payment[event["paymentId"]] = paid
            total = sale_totals.get(sale_id, Decimal("0.00"))
            settled_at = event["at"] if paid >= total and total > 0 else None
        timelines[sale_id] = {"paid": paid, "settledAt": settled_at, "afterPayment": after_payment}
    return timelines


def payment_receipt_json(payment):
    customer = db.session.get(Customer, payment.customer_id)
    allocations= (db.session.query(PaymentAllocation, Sale).join(Sale, Sale.id == PaymentAllocation.sale_id).filter(PaymentAllocation.payment_id == payment.id).order_by(
        Sale.sale_date.asc(),
        Sale.id.asc(),
        PaymentAllocation.id.asc(),
    ).all())
    timelines = _payment_invoice_timeline([sale.id for _, sale in allocations])
    customer_display_fields = resolve_transaction_customer(
        customer,
        "Retail Customer",
        "registered",
    )
    return {
        "id": payment.id,
        "receiptNo": payment.receipt_no,
        "customerId": payment.customer_id,
        **customer_display_fields,
        "amount": str(payment.amount),
        "method": payment.method,
        "date": payment.payment_date.isoformat(),
        "createdAt": _payment_local_datetime(payment.created_at),
        "bankReference": payment.bank_reference,
        "allocationMethod": payment.allocation_method,
        "note": payment.note,
        "allocations": [{
            "invoiceId": sale.id,
            "invoiceNo": sale.invoice_no,
            "date": sale.sale_date.date().isoformat(),
            "amount": str(allocation.amount),
            "total": str(sale.total_amount),
            "paidAfterPayment": str(timelines.get(sale.id, {}).get("afterPayment", {}).get(payment.id, Decimal(str(sale.amount_paid)))),
            "dueAfterPayment": str(max(Decimal("0.00"), Decimal(str(sale.total_amount)) - timelines.get(sale.id, {}).get("afterPayment", {}).get(payment.id, Decimal(str(sale.amount_paid))))),
            "paid": str(sale.amount_paid),
            "due": str(sale.amount_due),
            "settlementStatus": "Fully Paid" if timelines.get(sale.id, {}).get("afterPayment", {}).get(payment.id, Decimal(str(sale.amount_paid))) >= Decimal(str(sale.total_amount)) else "Partial",
        } for allocation, sale in allocations],
    }


def _correction_key(data):
    body = data.get("idempotencyKey", "")
    header = (request.headers.get("Idempotency-Key") or "").strip()
    if not isinstance(body, str):
        raise ValueError("Idempotency key must be text.")
    body = body.strip()
    if body and header and body != header:
        raise ValueError("Header and body idempotency keys do not match.")
    key = header or body
    if not 16 <= len(key) <= 100:
        raise ValueError("A stable idempotency key of 16 to 100 characters is required.")
    return key


def _correction_fingerprint(data):
    raw = json.dumps(data, sort_keys=True, separators= (",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _correction_amount(value):
    amount = dec(value, "adjustment amount")
    if amount == 0:
        raise ValueError("Adjustment amount must not be zero.")
    if abs(amount) > Decimal("999999999999.99"):
        raise ValueError("Adjustment amount exceeds the supported amount range.")
    try:
        if amount.quantize(Decimal("0.01")) != amount:
            raise ValueError("Adjustment amount may have at most two decimal places.")
    except InvalidOperation:
        raise ValueError("Invalid adjustment amount.")
    return amount


def payment_reversal_json(reversal):
    original = db.session.get(Payment, reversal.original_payment_id)
    customer = db.session.get(Customer, original.customer_id) if original else None
    rows= (db.session.query(PaymentReversalAllocation, PaymentAllocation, Sale).join(
        PaymentAllocation,
        PaymentAllocation.id == PaymentReversalAllocation.original_allocation_id,
    ).join(Sale, Sale.id == PaymentReversalAllocation.sale_id).filter(PaymentReversalAllocation.reversal_id == reversal.id).order_by(PaymentReversalAllocation.id).all())
    customer_display_fields = resolve_transaction_customer(
        customer,
        "Retail Customer",
        "registered",
    )
    return {
        "id": reversal.id,
        "reversalNo": reversal.reversal_no,
        "originalPaymentId": reversal.original_payment_id,
        "originalReceiptNo": original.receipt_no if original else None,
        "customerId": original.customer_id if original else None,
        **customer_display_fields,
        "amount": str(reversal.amount),
        "reason": reversal.reason,
        "createdAt": reversal.created_at.isoformat(),
        "allocations": [{
            "originalAllocationId": allocation.id,
            "invoiceId": sale.id,
            "invoiceNo": sale.invoice_no,
            "amount": str(reversed_allocation.amount),
        } for reversed_allocation, allocation, sale in rows],
    }


def customer_adjustment_json(adjustment):
    customer = db.session.get(Customer, adjustment.customer_id)
    customer_display_fields = resolve_transaction_customer(
        customer,
        "Retail Customer",
        "registered",
    )
    return {
        "id": adjustment.id,
        "customerId": adjustment.customer_id,
        **customer_display_fields,
        "amount": str(adjustment.amount),
        "reason": adjustment.reason,
        "reference": f"ADJ-{adjustment.id}",
        "createdAt": adjustment.created_at.isoformat(),
        "createdBy": adjustment.created_by,
    }


def _reversal_replay(key, fingerprint, user_id):
    row = PaymentReversal.query.filter_by(idempotency_key=key).first()
    if not row:
        return None
    if row.created_by != user_id or row.request_fingerprint != fingerprint:
        return fail("This idempotency key was already used with different correction details.", 409)
    return jsonify({"success": True, "reversal": payment_reversal_json(row), "replayed": True}), 200


@api.post("/payments/<int:payment_id>/reversals")
@admin_required_api
def payment_reverse(payment_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return fail("A JSON payment reversal request is required.")
    try:
        if set(data) - {"reason", "idempotencyKey"}:
            raise ValueError("Unexpected payment reversal request field.")
        reason = data.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
            raise ValueError("A reversal reason of 1 to 500 characters is required.")
        reason = reason.strip()
        key = _correction_key(data)
        fingerprint = _correction_fingerprint({"paymentId": payment_id, "reason": reason})
        user = current_user()
        replay = _reversal_replay(key, fingerprint, user.id)
        if replay is not None:
            return replay
        payment = Payment.query.filter_by(id=payment_id).first()
        if not payment:
            return fail("Payment not found.", 404)
        customer = Customer.query.filter_by(id=payment.customer_id).with_for_update().first()
        if not customer:
            return fail("Payment customer was not found.", 409)
        payment = Payment.query.filter_by(id=payment_id).with_for_update().first()
        replay = _reversal_replay(key, fingerprint, user.id)
        if replay is not None:
            db.session.rollback()
            return replay
        if PaymentReversal.query.filter_by(original_payment_id=payment.id).with_for_update().first():
            db.session.rollback()
            return fail("This payment has already been fully reversed.", 409)
        allocations = (PaymentAllocation.query.filter_by(payment_id=payment.id).order_by(PaymentAllocation.id).with_for_update().all())
        if not allocations:
            db.session.rollback()
            return fail(
                "Payment has no persisted invoice allocations and cannot be reversed safely.",
                409,
            )
        allocation_total = sum((Decimal(str(row.amount)) for row in allocations), Decimal("0.00"))
        if allocation_total != Decimal(str(payment.amount)):
            db.session.rollback()
            return fail("Payment allocations do not reconcile to the payment amount.", 409)
        sale_ids = sorted({row.sale_id for row in allocations})
        sales = (Sale.query.filter(Sale.id.in_(sale_ids)).order_by(Sale.id).with_for_update().all())
        sale_map = {sale.id: sale for sale in sales}
        if len(sale_map) != len(sale_ids) or any(sale.customer_id != payment.customer_id for sale in sales):
            db.session.rollback()
            return fail("Payment allocations reference unavailable invoices.", 409)
        for allocation in allocations:
            sale = sale_map[allocation.sale_id]
            amount = Decimal(str(allocation.amount))
            paid = Decimal(str(sale.amount_paid or 0))
            due = Decimal(str(sale.amount_due or 0))
            total = Decimal(str(sale.total_amount or 0))
            if amount <= 0 or paid < amount or due + amount > total:
                db.session.rollback()
                return fail("Invoice balances do not permit a safe full payment reversal.", 409)
        reversal = PaymentReversal(
            reversal_no=f"REV-{uuid4().hex.upper()}",
            original_payment_id=payment.id,
            amount=payment.amount,
            reason=reason,
            idempotency_key=key,
            request_fingerprint=fingerprint,
            created_by=user.id,
        )
        db.session.add(reversal)
        db.session.flush()
        for allocation in allocations:
            sale = sale_map[allocation.sale_id]
            amount = Decimal(str(allocation.amount))
            sale.amount_paid = Decimal(str(sale.amount_paid)) - amount
            sale.amount_due = Decimal(str(sale.amount_due)) + amount
            db.session.add(PaymentReversalAllocation(
                reversal_id=reversal.id,
                original_allocation_id=allocation.id,
                sale_id=sale.id,
                amount=amount,
            ))
        audit(
            "reverse",
            "payment",
            payment.id,
            {
                "reversalId": reversal.id,
                "reversalNo": reversal.reversal_no,
                "amount": str(reversal.amount),
                "reason": reason,
            },
        )
        db.session.commit()
        return jsonify({"success": True, "reversal": payment_reversal_json(reversal)}), 201
    except ValueError as error:
        db.session.rollback()
        return fail(str(error))
    except IntegrityError:
        db.session.rollback()
        if "key" in locals():
            replay = _reversal_replay(key, fingerprint, current_user().id)
            if replay is not None:
                return replay
        return fail("A reversal already exists or the request conflicts with existing data.", 409)
    except SQLAlchemyError as error:
        db.session.rollback()
        current_app.logger.error("Payment reversal failed; exception_type=%s", type(error).__name__)
        return fail("The payment reversal could not be saved. No balances were changed.", 500)
    except Exception as error:
        db.session.rollback()
        current_app.logger.error(
            "Unexpected payment reversal failure; exception_type=%s",
            type(error).__name__,
        )
        return fail("The payment reversal could not be saved. No balances were changed.", 500)


@api.post("/customers/<int:cid>/balance-adjustments")
@admin_required_api
def customer_balance_adjustment_create(cid):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return fail("A JSON customer balance adjustment request is required.")
    try:
        if set(data) - {"amount", "reason", "idempotencyKey"}:
            raise ValueError("Unexpected customer balance adjustment field.")
        amount = _correction_amount(data.get("amount"))
        reason = data.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
            raise ValueError("An adjustment reason of 1 to 500 characters is required.")
        reason = reason.strip()
        key = _correction_key(data)
        fingerprint=_correction_fingerprint({
            "customerId": cid,
            "amount": str(amount),
            "reason": reason,
        })
        user = current_user()
        existing = CustomerBalanceAdjustment.query.filter_by(idempotency_key=key).first()
        if existing:
            if existing.created_by != user.id or existing.request_fingerprint != fingerprint:
                return fail(
                    "This idempotency key was already used with different correction details.",
                    409,
                )
            return jsonify({
                "success": True,
                "adjustment": customer_adjustment_json(existing),
                "replayed": True,
            }), 200
        customer = Customer.query.filter_by(id=cid).with_for_update().first()
        if not customer:
            return fail("Customer not found.", 404)
        existing = CustomerBalanceAdjustment.query.filter_by(idempotency_key=key).with_for_update().first()
        if existing:
            db.session.rollback()
            if existing.created_by != user.id or existing.request_fingerprint != fingerprint:
                return fail(
                    "This idempotency key was already used with different correction details.",
                    409,
                )
            return jsonify({
                "success": True,
                "adjustment": customer_adjustment_json(existing),
                "replayed": True,
            }), 200
        adjustment = CustomerBalanceAdjustment(
            customer_id=cid,
            amount=amount,
            reason=reason,
            idempotency_key=key,
            request_fingerprint=fingerprint,
            created_by=user.id,
        )
        db.session.add(adjustment)
        db.session.flush()
        audit(
            "create",
            "customer_balance_adjustment",
            adjustment.id,
            {"customerId": cid, "amount": str(amount), "reason": reason},
        )
        db.session.commit()
        return jsonify({
            "success": True,
            "adjustment": customer_adjustment_json(adjustment),
            "customerAccountBalance": str(customer_account_balance(cid)),
        }), 201
    except ValueError as error:
        db.session.rollback()
        return fail(str(error))
    except IntegrityError:
        db.session.rollback()
        existing = CustomerBalanceAdjustment.query.filter_by(idempotency_key=key).first() if "key" in locals() else None
        if (
            existing
            and existing.created_by == current_user().id
            and existing.request_fingerprint == fingerprint
        ):
            return jsonify({
                "success": True,
                "adjustment": customer_adjustment_json(existing),
                "replayed": True,
            }), 200
        return fail("The idempotency key conflicts with an existing adjustment.", 409)
    except SQLAlchemyError as error:
        db.session.rollback()
        current_app.logger.error(
            "Customer balance adjustment failed; exception_type=%s",
            type(error).__name__,
        )
        return fail("The customer balance adjustment could not be saved.", 500)
    except Exception as error:
        db.session.rollback()
        current_app.logger.error(
            "Unexpected customer adjustment failure; exception_type=%s",
            type(error).__name__,
        )
        return fail("The customer balance adjustment could not be saved.", 500)


@api.get("/payments")
@login_required_api
@tab_required_api("Payments & Receivables")
def payments_list():
    rows = Payment.query.order_by(Payment.id.desc()).all()
    return jsonify({"success": True, "payments": [payment_receipt_json(p) for p in rows]})


@api.get("/masters")
@login_required_api
def masters_list():
    rows = MasterItem.query.order_by(MasterItem.group_name, MasterItem.name).all()
    prices = PriceSetting.query.order_by(PriceSetting.price_type, PriceSetting.item_name).all()
    purchase_prices = PurchasePriceSetting.query.join(
        MasterItem,
        PurchasePriceSetting.variety_id == MasterItem.id,
    ).order_by(MasterItem.name, PurchasePriceSetting.paddy_type).all()
    categories = MainCategory.query.filter_by(is_active=True).order_by(MainCategory.id).all()
    return jsonify({
        "success": True,
        "categories": [{
            "id": c.id,
            "code": c.code,
            "name": c.name,
            "inventorySection": c.inventory_section,
            "masterCategoryId": c.master_category_id,
        } for c in categories],
        "items": [{
            "id": m.id,
            "group": m.group_name,
            "name": m.name,
            "unit": m.unit,
            "active": m.is_active,
            "categoryIds": [c.id for c in m.main_categories if c.is_active],
            "categories": [c.code for c in m.main_categories if c.is_active],
        } for m in rows],
        "prices": [{
            "item": p.item_name,
            "type": p.price_type,
            "price": as_float(p.default_price) if p.default_price is not None else None,
            "locked": bool(p.is_locked() and p.default_price is not None),
            "active": p.is_active,
        } for p in prices],
        "purchasePrices": [{
            "varietyId": p.variety_id,
            "variety": p.variety.name,
            "paddyType": p.paddy_type,
            "price": as_float(p.default_price) if p.default_price is not None else None,
            "locked": bool(p.is_locked and p.default_price is not None),
            "active": p.is_active,
        } for p in purchase_prices],
        "legacyPurchasePrices": [{
            "item": p.item_name,
            "price": as_float(p.default_price),
            "locked": p.purchase_locked,
        } for p in prices if p.price_type == "purchase" and p.default_price is not None],
    })


def _resolve_main_category_assignment(group, requested_ids=None):
    if group not in {"rice_variety", "product"}:
        if requested_ids is not None:
            raise ValueError("Main categories are only configurable for rice varieties and sellable products.")
        return []
    allowed_codes = {"bulk", "packed"} if group == "rice_variety" else {"other"}
    if requested_ids is None:
        requested_ids=[row.id for row in MainCategory.query.filter(
            MainCategory.code.in_(allowed_codes),
            MainCategory.is_active.is_(True),
        ).all()]
    if not isinstance(
        requested_ids,
        list,
    ) or not requested_ids or any(type(value) is not int or value <= 0 for value in requested_ids):
        raise ValueError("Select at least one valid main category.")
    if len(set(requested_ids)) != len(requested_ids):
        raise ValueError("A main category cannot be selected more than once.")
    categories = MainCategory.query.filter(
        MainCategory.id.in_(requested_ids),
        MainCategory.is_active.is_(True),
    ).all()
    if len(categories) != len(requested_ids) or any(row.code not in allowed_codes for row in categories):
        raise ValueError("One or more selected main categories are not valid for this product.")
    if group == "product" and {row.code for row in categories} != {"other"}:
        raise ValueError("Sellable products must belong to Other Sellable Products.")
    return categories


@api.post("/masters")
@admin_required_api
def master_create():
    d = request.get_json(silent=True) or {}
    group = d.get("group")
    name = (d.get("name") or "").strip()
    if group not in {
        "rice_variety",
        "paddy_type",
        "category",
        "product",
        "package_size",
    } or not name:
        return fail("Valid group and name are required.")
    try:
        categories = _resolve_main_category_assignment(group, d.get("categoryIds"))
    except ValueError as error:
        return fail(str(error))
    m = MasterItem(group_name=group, name=name, unit=d.get("unit", "kg"))
    db.session.add(m)
    try:
        db.session.flush()
        m.main_categories = categories
        audit("create", "master_item", m.id)
        db.session.commit()
        return jsonify({"success": True, "id": m.id}), 201
    except IntegrityError:
        db.session.rollback()
        return fail("This master item already exists.")


@api.put("/masters/<int:mid>")
@admin_required_api
def master_update(mid):
    m = db.session.get(MasterItem, mid)
    if not m:
        return fail("Configuration item not found.", 404)
    d = request.get_json(silent=True) or {}
    if (
        m.group_name == "category"
        and MainCategory.query.filter_by(master_category_id=m.id).first()
        and ("name" in d or "active" in d)
    ):
        return fail(
            "Canonical sellable categories are fixed. Manage their products from the matching Administration module.",
            409,
        )
    if "active" in d:
        m.is_active = bool(d["active"])
    if "categoryIds" in d:
        try:
            categories = _resolve_main_category_assignment(m.group_name, d.get("categoryIds"))
        except ValueError as error:
            db.session.rollback()
            return fail(str(error))
        if m.group_name == "rice_variety":
            assigned = {category.code for category in categories}
            used_sections={"bulk" if stock_type == "rice" else "packed" for stock_type in db.session.query(Stock.stock_type).filter_by(product_id=m.id).distinct().all() if stock_type[0] in {
                "rice",
                "packed",
            }}
            if not used_sections.issubset(assigned):
                db.session.rollback()
                return fail(
                    "Existing stock requires its current main category to remain assigned.",
                    409,
                )
        m.main_categories = categories
    if "name" in d:
        name = (d.get("name") or "").strip()
        if not name:
            return fail("Item name is required.")
        duplicate = MasterItem.query.filter(
            MasterItem.group_name == m.group_name,
            func.lower(MasterItem.name) == name.lower(),
            MasterItem.id != m.id,
        ).first()
        if duplicate:
            return fail("This item already exists.")
        old_name = m.name
        m.name = name
        if old_name != name and m.group_name in {"rice_variety", "paddy_type", "product"}:
            for stock in Stock.query.filter_by(product_id=m.id).with_for_update().all():
                if m.group_name == "product":
                    stock.item_name = name
                elif m.group_name == "paddy_type":
                    stock.item_name = name
                    stock.variety = name
                elif stock.stock_type in {"rice", "packed"}:
                    stock.item_name = f"{name} Rice"
                    stock.variety = name
                else:
                    stock.variety = name
        if m.group_name in {"rice_variety", "product"} and old_name != name:
            for price in PriceSetting.query.filter_by(item_name=old_name).all():
                if not PriceSetting.query.filter(
                    PriceSetting.item_name == name,
                    PriceSetting.price_type == price.price_type,
                    PriceSetting.id != price.id,
                ).first():
                    price.item_name = name
    try:
        audit("update", "master_item", m.id)
        db.session.commit()
        return jsonify({
            "success": True,
            "item": {
                "id": m.id,
                "group": m.group_name,
                "name": m.name,
                "unit": m.unit,
                "active": m.is_active,
                "categoryIds": [c.id for c in m.main_categories],
                "categories": [c.code for c in m.main_categories],
            },
        })
    except IntegrityError:
        db.session.rollback()
        return fail("This configuration item already exists.")


@api.delete("/masters/<int:mid>")
@admin_required_api
def master_delete(mid):
    m = db.session.get(MasterItem, mid)
    if not m:
        return fail("Configuration item not found.", 404)
    if m.group_name == "category" and MainCategory.query.filter_by(master_category_id=m.id).first():
        return fail("Canonical sellable categories cannot be deleted.", 409)
    # Preserve master values referenced by historical or current operational records.
    used = False
    if m.group_name == "rice_variety":
        used=any([
            Purchase.query.filter_by(variety=m.name).first(),
            ProductionRecord.query.filter_by(variety=m.name).first(),
            Stock.query.filter(or_(Stock.variety == m.name, Stock.product_id == m.id)).first(),
            SaleItem.query.filter(or_(
                SaleItem.variety == m.name,
                SaleItem.product_id == m.id,
            )).first(),
        ])
    elif m.group_name == "product":
        used=any([
            Stock.query.filter(or_(Stock.item_name == m.name, Stock.product_id == m.id)).first(),
            SaleItem.query.filter(or_(
                SaleItem.product_name == m.name,
                SaleItem.product_id == m.id,
            )).first(),
        ])
    if used:
        return fail("This item is already used in records. Deactivate it instead.", 409)
    for price in PriceSetting.query.filter_by(item_name=m.name).all():
        db.session.delete(price)
    db.session.delete(m)
    audit("delete", "master_item", mid)
    db.session.commit()
    return jsonify({"success": True})


@api.put("/prices")
@admin_required_api
def price_update():
    d = request.get_json(silent=True) or {}
    item = d.get("item")
    ptype = d.get("type")
    if not isinstance(
        item,
        str,
    ) or not item.strip() or len(item.strip()) > 120 or ptype not in {"purchase", "selling"}:
        return fail("A valid item and price type are required.")
    item = item.strip()
    if "price" not in d:
        return fail("Price is required; use null to allow manual pricing.")
    if "locked" in d and not isinstance(d["locked"], bool):
        return fail("Price lock setting must be true or false.")
    try:
        new_price = dec(
            d["price"],
            "price",
            Decimal("0.01"),
        ) if d["price"] not in (None, "") else None
    except ValueError as error:
        return fail(str(error))
    p = PriceSetting.query.filter_by(item_name=item, price_type=ptype).first()
    if not p:
        p = PriceSetting(item_name=item, price_type=ptype)
        db.session.add(p)
    p.default_price = new_price
    p.is_active = True
    if p.default_price is None:
        p.set_locked(False)
    elif "locked" in d:
        p.set_locked(d["locked"])
    audit(
        "update",
        "price_setting",
        p.id,
        {
            "item": item,
            "type": ptype,
            "locked": p.is_locked(),
            "has_price": p.default_price is not None,
        },
    )
    db.session.commit()
    return jsonify({"success": True})


@api.put("/purchase-prices")
@admin_required_api
def purchase_price_update():
    d = request.get_json(silent=True) or {}
    variety_id = d.get("varietyId")
    paddy_type = d.get("paddyType")
    if type(variety_id) is not int or variety_id <= 0 or paddy_type not in {
        "Wet Paddy",
        "Dry Paddy",
    }:
        return fail("Select a valid rice variety and Wet or Dry Paddy price type.")
    if "price" not in d:
        return fail("Price is required; use null to allow manual pricing.")
    if "locked" in d and not isinstance(d["locked"], bool):
        return fail("Price lock setting must be true or false.")
    try:
        price = dec(d["price"], "price", Decimal("0.01")) if d["price"] not in (None, "") else None
    except ValueError as error:
        return fail(str(error))
    variety = MasterItem.query.filter_by(
        id=variety_id,
        group_name="rice_variety",
        is_active=True,
    ).with_for_update().first()
    if not variety:
        return fail("The selected rice variety is inactive or unavailable.", 404)
    setting = PurchasePriceSetting.query.filter_by(
        variety_id=variety.id,
        paddy_type=paddy_type,
    ).with_for_update().first()
    created = setting is None
    if setting is None:
        setting = PurchasePriceSetting(variety_id=variety.id, paddy_type=paddy_type)
        db.session.add(setting)
    setting.default_price = price
    setting.is_active = True
    setting.is_locked = bool(d.get("locked", False)) if price is not None else False
    try:
        db.session.flush()
        audit(
            "create" if created else "update",
            "purchase_price_setting",
            setting.id,
            {
                "variety_id": variety.id,
                "paddy_type": paddy_type,
                "locked": setting.is_locked,
                "has_price": setting.default_price is not None,
            },
        )
        db.session.commit()
        return jsonify({
            "success": True,
            "price": {
                "varietyId": variety.id,
                "variety": variety.name,
                "paddyType": paddy_type,
                "price": as_float(setting.default_price) if setting.default_price is not None else None,
                "locked": bool(setting.is_locked and setting.default_price is not None),
            },
        })
    except IntegrityError:
        db.session.rollback()
        return fail(
            "The paddy price setting conflicts with another saved update. Reload and try again.",
            409,
        )


@api.get("/dashboard/summary")
@login_required_api
@tab_required_api("Dashboard")
def dashboard_summary():
    today, start_utc, end_utc = _dashboard_colombo_day_window()
    totals = {kind: Decimal("0") for kind in ("wet_paddy", "dry_paddy", "rice", "packed")}
    for row in Stock.query.all():
        if row.stock_type in totals:
            if row.stock_type == "packed":
                totals["packed"] += dec(row.quantity_bags)
            else:
                totals[row.stock_type] += dec(row.quantity_kg)
    sales_today = db.session.query(func.coalesce(func.sum(Sale.total_amount), 0)).filter(
        Sale.sale_date >= start_utc,
        Sale.sale_date < end_utc,
    ).scalar()
    collections_today=db.session.query(func.coalesce(
        func.sum(Payment.amount),
        0,
    )).filter(Payment.payment_date == today).scalar()
    invoice_outstanding=Decimal(str(db.session.query(func.coalesce(
        func.sum(Sale.amount_due),
        0,
    )).scalar() or 0))
    adjustment_balance=Decimal(str(db.session.query(func.coalesce(
        func.sum(CustomerBalanceAdjustment.amount),
        0,
    )).scalar() or 0))
    customer_outstanding = invoice_outstanding + adjustment_balance
    pending= (ReturnItem.query.join(SalesReturn).filter(
        SalesReturn.approval_status == "approved",
        ReturnItem.decision == "pending",
    ).count())
    return jsonify({
        "success": True,
        "wetPaddyKg": as_float(totals["wet_paddy"]),
        "dryPaddyKg": as_float(totals["dry_paddy"]),
        "bulkRiceKg": as_float(totals["rice"]),
        "packedBags": as_float(totals["packed"]),
        "salesToday": as_float(sales_today),
        "collectionsToday": as_float(collections_today),
        "customerOutstanding": as_float(customer_outstanding),
        "pendingInspection": pending,
    })


def _dashboard_colombo_day_window(now=None):
    """Return Colombo's calendar day and its UTC-naive storage boundaries."""
    local_now = now.astimezone(COLOMBO_TIMEZONE) if now is not None else datetime.now(COLOMBO_TIMEZONE)
    today = local_now.date()
    start_local = datetime.combine(today, datetime.min.time(), tzinfo=COLOMBO_TIMEZONE)
    end_local = datetime.combine(
        today + timedelta(days=1),
        datetime.min.time(),
        tzinfo=COLOMBO_TIMEZONE,
    )
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return today, start_utc, end_utc


@api.get("/reports/summary")
@admin_required_api
def report_summary():
    invoice_outstanding=Decimal(str(db.session.query(func.coalesce(
        func.sum(Sale.amount_due),
        0,
    )).scalar() or 0))
    adjustment_balance=Decimal(str(db.session.query(func.coalesce(
        func.sum(CustomerBalanceAdjustment.amount),
        0,
    )).scalar() or 0))
    purchase_total=Decimal(str(db.session.query(func.coalesce(
        func.sum(Purchase.total_amount),
        0,
    )).scalar() or 0))
    corrected_purchases = (Purchase.query.filter(Purchase.effective_correction_id.isnot(None)).options(joinedload(Purchase.effective_correction)).all())
    for purchase in corrected_purchases:
        purchase_total += purchase_effective_values(purchase)["total_amount"] - Decimal(str(purchase.total_amount))
    return jsonify({
        "success": True,
        "salesTotal": as_float(db.session.query(func.coalesce(
            func.sum(Sale.total_amount),
            0,
        )).scalar()),
        "purchaseTotal": as_float(purchase_total),
        "customerOutstanding": as_float(invoice_outstanding),
        "customerAccountBalance": str(invoice_outstanding + adjustment_balance),
        "customerAdjustmentsBalance": str(adjustment_balance),
        "customers": Customer.query.filter_by(is_active=True).count(),
        "pendingReturns": SalesReturn.query.filter_by(status="pending_inspection").count(),
        "stocks": [{
            "item": s.item_name,
            "type": s.stock_type,
            "kg": as_float(s.quantity_kg),
            "bags": as_float(s.quantity_bags),
        } for s in Stock.query.all()],
    })


def _financial_report_params():
    raw_from = request.args.get("dateFrom")
    raw_to = request.args.get("dateTo")
    try:
        date_from = date.fromisoformat(raw_from) if raw_from else None
        date_to = date.fromisoformat(raw_to) if raw_to else None
        if date_from and date_from.isoformat() != raw_from:
            raise ValueError
        if date_to and date_to.isoformat() != raw_to:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("Report dates must use YYYY-MM-DD format.")
    if date_from and date_to and date_from > date_to:
        raise ValueError("Start date cannot be after end date.")

    all_param= (request.args.get("all") in {
        "true",
        "1",
        "all",
    } or request.args.get("allRecords") in {"true", "1"} or request.args.get("perPage") in {"all", "0"})
    if all_param:
        return date_from, date_to, 1, None

    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("perPage", 50))
    except (TypeError, ValueError):
        raise ValueError("Page and perPage must be whole numbers.")
    if page < 1 or not 1 <= per_page <= 100:
        raise ValueError("Page must be positive and perPage must be between 1 and 100.")
    return date_from, date_to, page, per_page


def _date_window(query, column, date_from, date_to):
    if date_from:
        query = query.filter(func.date(column) >= date_from.isoformat())
    if date_to:
        query = query.filter(func.date(column) <= date_to.isoformat())
    return query


def _paginate_report(records, page, per_page):
    total = len(records)
    if per_page is None:
        return records, {"page": 1, "perPage": total, "total": total, "pages": 1}
    return records[(page - 1) * per_page:page * per_page], {
        "page": page,
        "perPage": per_page,
        "total": total,
        "pages": (total + per_page - 1) // per_page,
    }


def _financial_maps():
    allocations = db.session.query(
        PaymentAllocation.sale_id,
        func.coalesce(func.sum(PaymentAllocation.amount), 0),
    ).group_by(PaymentAllocation.sale_id).all()
    allocated = {sale_id: Decimal(str(amount or 0)) for sale_id, amount in allocations}
    reversals = db.session.query(
        PaymentReversalAllocation.sale_id,
        func.coalesce(func.sum(PaymentReversalAllocation.amount), 0),
    ).group_by(PaymentReversalAllocation.sale_id).all()
    reversed_allocated = {sale_id: Decimal(str(amount or 0)) for sale_id, amount in reversals}
    return_adjustments = {}
    entries= (db.session.query(ReturnFinancialEntry, SalesReturn).join(
        ReturnSettlement,
        ReturnSettlement.id == ReturnFinancialEntry.settlement_id,
    ).join(ReturnItem, ReturnItem.id == ReturnSettlement.return_item_id).join(SalesReturn, SalesReturn.id == ReturnItem.return_id).filter(ReturnFinancialEntry.entry_type == "outstanding_adjustment").all())
    for entry, return_record in entries:
        sale_id = entry.sale_id or return_record.sale_id
        if sale_id is not None:
            return_adjustments[sale_id] = return_adjustments.get(
                sale_id,
                Decimal("0.00"),
            ) + Decimal(str(entry.amount))
    return allocated, reversed_allocated, return_adjustments


def _invoice_reconciliation_rows(date_from=None, date_to=None):
    allocated, reversed_allocated, return_adjustments = _financial_maps()
    query = Sale.query.filter(Sale.customer_id.isnot(None))
    query = _date_window(query, Sale.sale_date, date_from, date_to)
    rows = []
    for sale in query.order_by(Sale.sale_date, Sale.id).all():
        net_paid = allocated.get(
            sale.id,
            Decimal("0.00"),
        ) - reversed_allocated.get(sale.id, Decimal("0.00"))
        return_credit = return_adjustments.get(sale.id, Decimal("0.00"))
        expected_due = Decimal(str(sale.total_amount)) - net_paid - return_credit
        paid_difference = Decimal(str(sale.amount_paid)) - net_paid
        due_difference = Decimal(str(sale.amount_due)) - expected_due
        nonnegative = (Decimal(str(sale.total_amount)) >= 0 and Decimal(str(sale.amount_paid)) >= 0 and Decimal(str(sale.amount_due)) >= 0)
        rows.append({
            "invoiceId": sale.id,
            "invoiceNo": sale.invoice_no,
            "customerId": sale.customer_id,
            "date": sale.sale_date.date().isoformat(),
            "total": str(sale.total_amount),
            "netAllocatedPayments": str(net_paid),
            "returnOutstandingAdjustments": str(return_credit),
            "expectedPaid": str(net_paid),
            "storedPaid": str(sale.amount_paid),
            "paidDifference": str(paid_difference),
            "expectedDue": str(expected_due),
            "storedDue": str(sale.amount_due),
            "dueDifference": str(due_difference),
            "nonnegativeBalances": nonnegative,
            "reconciled": paid_difference == 0 and due_difference == 0 and nonnegative,
        })
    return rows


def _payment_reconciliation_rows(date_from=None, date_to=None):
    query = _date_window(Payment.query, Payment.payment_date, date_from, date_to)
    rows = []
    for payment in query.order_by(Payment.payment_date, Payment.id).all():
        allocated= (db.session.query(func.coalesce(
            func.sum(PaymentAllocation.amount),
            0,
        )).filter_by(payment_id=payment.id).scalar())
        allocated = Decimal(str(allocated or 0))
        amount = Decimal(str(payment.amount))
        allocation_rows = PaymentAllocation.query.filter_by(payment_id=payment.id).all()
        invalid_amount_allocations = [row.id for row in allocation_rows if Decimal(str(row.amount)) <= 0]
        pairs= (db.session.query(
            PaymentAllocation.sale_id,
            func.count(PaymentAllocation.id),
            Sale.customer_id,
        ).join(Sale, Sale.id == PaymentAllocation.sale_id).filter(PaymentAllocation.payment_id == payment.id).group_by(PaymentAllocation.sale_id, Sale.customer_id).all())
        duplicate_invoices = [sale_id for sale_id, count, _customer_id in pairs if count > 1]
        mismatched_customers = [sale_id for sale_id, _count, customer_id in pairs if customer_id is None or customer_id != payment.customer_id]
        positive_amounts = amount > 0 and not invalid_amount_allocations
        reconciled = (amount == allocated and positive_amounts and not duplicate_invoices and not mismatched_customers)
        rows.append({
            "paymentId": payment.id,
            "receiptNo": payment.receipt_no,
            "customerId": payment.customer_id,
            "date": payment.payment_date.isoformat(),
            "method": payment.method,
            "paymentAmount": str(amount),
            "allocatedAmount": str(allocated),
            "difference": str(amount - allocated),
            "duplicateInvoiceAllocations": duplicate_invoices,
            "customerMismatchInvoiceIds": mismatched_customers,
            "invalidAllocationIds": invalid_amount_allocations,
            "positiveAmounts": positive_amounts,
            "reconciled": reconciled,
        })
    return rows


def _reversal_reconciliation_rows(date_from=None, date_to=None):
    query = _date_window(PaymentReversal.query, PaymentReversal.created_at, date_from, date_to)
    rows = []
    for reversal in query.order_by(PaymentReversal.created_at, PaymentReversal.id).all():
        payment = db.session.get(Payment, reversal.original_payment_id)
        parts = PaymentReversalAllocation.query.filter_by(reversal_id=reversal.id).all()
        reversed_total = sum((Decimal(str(part.amount)) for part in parts), Decimal("0.00"))
        original_parts = PaymentAllocation.query.filter_by(payment_id=reversal.original_payment_id).all()
        original_by_id={part.id: (
            part.sale_id,
            Decimal(str(part.amount)),
        ) for part in original_parts}
        reversal_by_id={part.original_allocation_id: (
            part.sale_id,
            Decimal(str(part.amount)),
        ) for part in parts}
        allocations_match = original_by_id == reversal_by_id
        original_amount = Decimal(str(payment.amount)) if payment else Decimal("0.00")
        positive_amounts = Decimal(str(reversal.amount)) > 0 and all(Decimal(str(part.amount)) > 0 for part in parts)
        amount_match = Decimal(str(reversal.amount)) == original_amount == reversed_total and positive_amounts
        rows.append({
            "reversalId": reversal.id,
            "reversalNo": reversal.reversal_no,
            "originalPaymentId": reversal.original_payment_id,
            "originalReceiptNo": payment.receipt_no if payment else None,
            "customerId": payment.customer_id if payment else None,
            "date": reversal.created_at.date().isoformat(),
            "reversalAmount": str(reversal.amount),
            "originalPaymentAmount": str(original_amount),
            "reversalAllocationsTotal": str(reversed_total),
            "positiveAmounts": positive_amounts,
            "allocationsMatch": allocations_match,
            "reconciled": amount_match and allocations_match,
        })
    return rows


def _return_settlement_rows(date_from=None, date_to=None):
    query= (db.session.query(ReturnSettlement, ReturnItem, SalesReturn).join(
        ReturnItem,
        ReturnItem.id == ReturnSettlement.return_item_id,
    ).join(SalesReturn, SalesReturn.id == ReturnItem.return_id))
    query = _date_window(query, ReturnSettlement.created_at, date_from, date_to)
    rows = []
    for settlement, item, return_record in query.order_by(
        ReturnSettlement.created_at,
        ReturnSettlement.id,
    ).all():
        entries = ReturnFinancialEntry.query.filter_by(settlement_id=settlement.id).all()
        by_type = {}
        counts_by_type = {}
        for entry in entries:
            by_type[entry.entry_type] = by_type.get(
                entry.entry_type,
                Decimal("0.00"),
            ) + Decimal(str(entry.amount))
            counts_by_type[entry.entry_type] = counts_by_type.get(entry.entry_type, 0) + 1
        balance_entry = by_type.get("outstanding_adjustment", Decimal("0.00"))
        cash_entry = by_type.get("cash_refund", Decimal("0.00"))
        exchange_collected = by_type.get("exchange_difference_collection", Decimal("0.00"))
        exchange_refunded = by_type.get("exchange_difference_refund", Decimal("0.00"))
        kind = settlement.settlement_type
        if kind == "refund":
            values = [
                (
                    "refund_components",
                    Decimal(str(settlement.return_value)),
                    Decimal(str(settlement.balance_applied)) + Decimal(str(settlement.cash_refund)),
                ),
                (
                    "outstanding_adjustment_entries",
                    Decimal(str(settlement.balance_applied)),
                    balance_entry,
                ),
                ("cash_refund_entries", Decimal(str(settlement.cash_refund)), cash_entry),
                (
                    "unexpected_replacement_value",
                    Decimal("0.00"),
                    Decimal(str(settlement.replacement_value)),
                ),
                (
                    "unexpected_amount_collected",
                    Decimal("0.00"),
                    Decimal(str(settlement.amount_collected)),
                ),
                (
                    "unexpected_difference_refund",
                    Decimal("0.00"),
                    Decimal(str(settlement.difference_refund)),
                ),
                ("unexpected_exchange_collection_entries", Decimal("0.00"), exchange_collected),
                ("unexpected_exchange_refund_entries", Decimal("0.00"), exchange_refunded),
            ]
            if Decimal(str(settlement.return_value)) <= 0:
                values.append((
                    "positive_return_value",
                    Decimal("0.01"),
                    Decimal(str(settlement.return_value)),
                ))
            for name, value in (
                ("balance_applied", settlement.balance_applied),
                ("cash_refund", settlement.cash_refund),
            ):
                if Decimal(str(value)) < 0:
                    values.append((f"nonnegative_{name}", Decimal("0.00"), Decimal(str(value))))
        elif kind == "exchange":
            difference = Decimal(str(settlement.replacement_value)) - Decimal(str(settlement.return_value))
            expected_collected = max(difference, Decimal("0.00"))
            expected_refund = max(-difference, Decimal("0.00"))
            values = [
                (
                    "exchange_collection_amount",
                    expected_collected,
                    Decimal(str(settlement.amount_collected)),
                ),
                (
                    "exchange_refund_amount",
                    expected_refund,
                    Decimal(str(settlement.difference_refund)),
                ),
                (
                    "exchange_collection_entries",
                    Decimal(str(settlement.amount_collected)),
                    exchange_collected,
                ),
                (
                    "exchange_refund_entries",
                    Decimal(str(settlement.difference_refund)),
                    exchange_refunded,
                ),
                ("unexpected_outstanding_adjustment", Decimal("0.00"), balance_entry),
                ("unexpected_cash_refund", Decimal("0.00"), cash_entry),
                (
                    "unexpected_balance_applied",
                    Decimal("0.00"),
                    Decimal(str(settlement.balance_applied)),
                ),
                (
                    "unexpected_cash_refund_total",
                    Decimal("0.00"),
                    Decimal(str(settlement.cash_refund)),
                ),
            ]
            if Decimal(str(settlement.return_value)) <= 0:
                values.append((
                    "positive_return_value",
                    Decimal("0.01"),
                    Decimal(str(settlement.return_value)),
                ))
            if Decimal(str(settlement.replacement_value)) < 0:
                values.append((
                    "nonnegative_replacement_value",
                    Decimal("0.00"),
                    Decimal(str(settlement.replacement_value)),
                ))
        else:
            values = [("settlement_type", Decimal("0.00"), Decimal("1.00"))]
        for entry_type, count in counts_by_type.items():
            if count > 1:
                values.append((f"duplicate_{entry_type}_events", Decimal("1.00"), Decimal(count)))
        for entry in entries:
            if Decimal(str(entry.amount)) <= 0:
                values.append((
                    f"nonpositive_{entry.entry_type}_entry",
                    Decimal("0.01"),
                    Decimal(str(entry.amount)),
                ))
        discrepancies=[{
            "check": name,
            "expected": str(expected),
            "actual": str(actual),
            "difference": str(expected - actual),
        } for name, expected, actual in values if expected != actual]
        arithmetic_ok=not any(row["check"] in {
            "refund_components",
            "exchange_collection_amount",
            "exchange_refund_amount",
            "unexpected_replacement_value",
            "unexpected_amount_collected",
            "unexpected_difference_refund",
            "unexpected_balance_applied",
            "unexpected_cash_refund_total",
            "positive_return_value",
            "nonnegative_balance_applied",
            "nonnegative_cash_refund",
            "nonnegative_replacement_value",
        } for row in discrepancies)
        entries_ok=not any(row["check"] not in {
            "refund_components",
            "exchange_collection_amount",
            "exchange_refund_amount",
            "unexpected_replacement_value",
            "unexpected_amount_collected",
            "unexpected_difference_refund",
            "unexpected_balance_applied",
            "unexpected_cash_refund_total",
            "positive_return_value",
            "nonnegative_balance_applied",
            "nonnegative_cash_refund",
            "nonnegative_replacement_value",
        } for row in discrepancies)
        rows.append({
            "settlementId": settlement.id,
            "returnNo": return_record.return_no,
            "returnItemId": item.id,
            "saleId": return_record.sale_id,
            "customerId": return_record.customer_id,
            "date": settlement.created_at.date().isoformat(),
            "settlementType": kind,
            "returnValue": str(settlement.return_value),
            "replacementValue": str(settlement.replacement_value),
            "balanceApplied": str(settlement.balance_applied),
            "cashRefund": str(settlement.cash_refund),
            "amountCollected": str(settlement.amount_collected),
            "differenceRefund": str(settlement.difference_refund),
            "financialEntries": [{
                "type": entry.entry_type,
                "amount": str(entry.amount),
                "method": entry.method,
            } for entry in entries],
            "arithmeticReconciled": arithmetic_ok,
            "entriesReconciled": entries_ok,
            "reconciled": not discrepancies,
            "discrepancies": discrepancies,
        })
    return rows


def _customer_outstanding_rows():
    allocated, reversed_allocated, return_adjustments = _financial_maps()
    invoice_rows = Sale.query.filter(Sale.customer_id.isnot(None)).all()
    by_customer = {}
    for sale in invoice_rows:
        group = by_customer.setdefault(
            sale.customer_id,
            {
                "invoiceOutstanding": Decimal("0.00"),
                "totalInvoiced": Decimal("0.00"),
                "calculatedBalance": Decimal("0.00"),
            },
        )
        group["invoiceOutstanding"] += Decimal(str(sale.amount_due))
        group["totalInvoiced"] += Decimal(str(sale.total_amount))
        net_paid = allocated.get(
            sale.id,
            Decimal("0.00"),
        ) - reversed_allocated.get(sale.id, Decimal("0.00"))
        group["calculatedBalance"] += Decimal(str(sale.total_amount)) - net_paid - return_adjustments.get(
            sale.id,
            Decimal("0.00"),
        )
    adjustments= (db.session.query(
        CustomerBalanceAdjustment.customer_id,
        func.coalesce(func.sum(CustomerBalanceAdjustment.amount), 0),
    ).group_by(CustomerBalanceAdjustment.customer_id).all())
    adjustment_by_customer = {cid: Decimal(str(amount or 0)) for cid, amount in adjustments}
    customers = Customer.query.order_by(Customer.id).all()
    rows = []
    for customer in customers:
        values = by_customer.get(
            customer.id,
            {
                "invoiceOutstanding": Decimal("0.00"),
                "totalInvoiced": Decimal("0.00"),
                "calculatedBalance": Decimal("0.00"),
            },
        )
        adjustment = adjustment_by_customer.get(customer.id, Decimal("0.00"))
        stored_balance = values["invoiceOutstanding"] + adjustment
        calculated = values["calculatedBalance"] + adjustment
        rows.append({
            "customerId": customer.id,
            "customerCode": customer.customer_code,
            **customer_fields(customer.full_name, "registered"),
            "active": customer.is_active,
            "totalInvoiced": str(values["totalInvoiced"]),
            "invoiceOutstanding": str(values["invoiceOutstanding"]),
            "adjustmentsBalance": str(adjustment),
            "customerAccountBalance": str(stored_balance),
            "calculatedAccountBalance": str(calculated),
            "difference": str(stored_balance - calculated),
            "reconciled": stored_balance == calculated,
        })
    return rows


def _financial_discrepancies(date_from=None, date_to=None):
    findings = []
    for row in _invoice_reconciliation_rows(date_from, date_to):
        if not row["reconciled"]:
            for field in ("paidDifference", "dueDifference"):
                if Decimal(row[field]) != 0:
                    findings.append({
                        "type": "invoice_balance",
                        "reference": row["invoiceNo"],
                        "date": row["date"],
                        "customerId": row["customerId"],
                        "field": field,
                        "difference": row[field],
                    })
            if not row["nonnegativeBalances"]:
                findings.append({
                    "type": "invoice_balance",
                    "reference": row["invoiceNo"],
                    "date": row["date"],
                    "customerId": row["customerId"],
                    "field": "negative_stored_balance",
                    "difference": "0.00",
                })
    for row in _payment_reconciliation_rows(date_from, date_to):
        if not row["reconciled"]:
            findings.append({
                "type": "payment_allocation",
                "reference": row["receiptNo"],
                "date": row["date"],
                "customerId": row["customerId"],
                "difference": row["difference"],
                "duplicateInvoiceAllocations": row["duplicateInvoiceAllocations"],
                "customerMismatchInvoiceIds": row["customerMismatchInvoiceIds"],
                "invalidAllocationIds": row["invalidAllocationIds"],
                "positiveAmounts": row["positiveAmounts"],
            })
    for row in _reversal_reconciliation_rows(date_from, date_to):
        if not row["reconciled"]:
            findings.append({
                "type": "payment_reversal",
                "reference": row["reversalNo"],
                "date": row["date"],
                "customerId": row["customerId"],
                "difference": str(Decimal(row["originalPaymentAmount"]) - Decimal(row["reversalAllocationsTotal"])),
                "allocationsMatch": row["allocationsMatch"],
                "positiveAmounts": row["positiveAmounts"],
            })
    for row in _return_settlement_rows(date_from, date_to):
        if not row["reconciled"]:
            for discrepancy in row["discrepancies"]:
                findings.append({
                    "type": "return_settlement",
                    "reference": row["returnNo"],
                    "date": row["date"],
                    "customerId": row["customerId"],
                    "field": discrepancy["check"],
                    "difference": discrepancy["difference"],
                })
    for row in _customer_outstanding_rows():
        if date_from or date_to:
            continue  # A current balance is a point-in-time figure, not a date-window flow.
        if not row["reconciled"]:
            findings.append({
                "type": "customer_account_balance",
                "reference": row["customerCode"],
                "date": colombo_date().isoformat(),
                "customerId": row["customerId"],
                "difference": row["difference"],
            })
    return findings


def _financial_report_response(records, report_type):
    try:
        date_from, date_to, page, per_page = _financial_report_params()
        page_records, pagination = _paginate_report(records(date_from, date_to), page, per_page)
        return jsonify({
            "success": True,
            "report": report_type,
            "dateFrom": date_from.isoformat() if date_from else None,
            "dateTo": date_to.isoformat() if date_to else None,
            "records": page_records,
            "pagination": pagination,
        })
    except ValueError as error:
        return fail(str(error))


@api.get("/reports/financial/invoice-reconciliation")
@admin_required_api
def financial_invoice_reconciliation_report():
    return _financial_report_response(_invoice_reconciliation_rows, "invoice-reconciliation")


@api.get("/reports/sales")
@admin_required_api
def sales_report():
    """Return persisted sales invoices and their current paid/due balances."""
    try:
        date_from, date_to, page, per_page = _financial_report_params()
    except ValueError as error:
        return fail(str(error))
    query = _date_window(
        Sale.query,
        Sale.sale_date,
        date_from,
        date_to,
    ).outerjoin(Customer, Customer.id == Sale.customer_id)
    search = request.args.get("q", "").strip()
    if len(search) > 120:
        return fail("Search text must be 120 characters or fewer.")
    if search:
        pattern = f"%{search}%"
        name_parts = search.split(maxsplit=1)
        customer_name_match = or_(
            Customer.first_name.ilike(pattern),
            Customer.last_name.ilike(pattern),
            and_(
                Customer.first_name.ilike(f"%{name_parts[0]}%"),
                Customer.last_name.ilike(f"%{name_parts[1]}%"),
            ) if len(name_parts) == 2 else literal(False),
        )
        query=query.filter(or_(
            Sale.invoice_no.ilike(pattern),
            Sale.customer_name.ilike(pattern),
            customer_name_match,
            Sale.payment_type.ilike(pattern),
            Sale.payment_method.ilike(pattern),
            Sale.items.any(or_(
                SaleItem.product_name.ilike(pattern),
                SaleItem.variety.ilike(pattern),
                SaleItem.category.ilike(pattern),
            )),
        ))

    total = query.count()
    summary_row = query.with_entities(
        func.coalesce(func.sum(Sale.total_amount), 0),
        func.coalesce(func.sum(Sale.amount_paid), 0),
        func.coalesce(func.sum(Sale.amount_due), 0),
    ).one()
    query_ordered= (query.options(selectinload(Sale.items)).order_by(
        Sale.sale_date.desc(),
        Sale.id.desc(),
    ))
    if per_page is None:
        rows = query_ordered.all()
        pagination = {"page": 1, "perPage": total, "total": total, "pages": 1}
    else:
        rows = query_ordered.offset((page - 1) * per_page).limit(per_page).all()
        pagination = {
            "page": page,
            "perPage": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page,
        }
    customers = _sale_customer_map(rows)
    records = []
    for sale in rows:
        records.append({
            "id": sale.id,
            "invoiceNo": sale.invoice_no,
            "date": sale.sale_date.isoformat(),
            "customerId": sale.customer_id,
            **_sale_customer_fields(sale, customers),
            "customer": _sale_customer_fields(sale, customers)["customerDisplay"],
            "items": [{
                "category": item.category,
                "product": item.product_name,
                "variety": item.variety,
                "quantity": str(item.quantity),
                "unit": item.unit,
                "packageSize": item.package_size_kg,
                "totalKg": str(item.total_kg),
            } for item in sale.items],
            "paymentType": sale.payment_type,
            "paymentMethod": sale.payment_method,
            "total": str(sale.total_amount),
            "paid": str(sale.amount_paid),
            "due": str(sale.amount_due),
        })
    return jsonify({
        "success": True,
        "report": "sales",
        "dateFrom": date_from.isoformat() if date_from else None,
        "dateTo": date_to.isoformat() if date_to else None,
        "records": records,
        "summary": {
            "totalSales": str(Decimal(str(summary_row[0] or 0))),
            "totalCollected": str(Decimal(str(summary_row[1] or 0))),
            "totalOutstanding": str(Decimal(str(summary_row[2] or 0))),
        },
        "pagination": pagination,
    })


@api.get("/reports/purchases")
@admin_required_api
def purchase_report():
    """Return purchase rows and totals using the latest approved correction version."""
    try:
        date_from, date_to, page, per_page = _financial_report_params()
    except ValueError as error:
        return fail(str(error))
    query = Purchase.query.outerjoin(
        PurchaseCorrection,
        Purchase.effective_correction_id == PurchaseCorrection.id,
    )

    def effective_text(key, original):
        return func.coalesce(PurchaseCorrection.proposed_values[key].as_string(), original)

    def effective_number(key, original, scale):
        return func.coalesce(
            cast(PurchaseCorrection.proposed_values[key].as_string(), db.Numeric(14, scale)),
            original,
        )

    effective_date = func.coalesce(
        func.str_to_date(effective_text("purchaseDate", None), "%Y-%m-%d"),
        Purchase.purchase_date,
    )
    effective_quantity = effective_number("quantityKg", Purchase.quantity_kg, 3)
    effective_total = effective_number("totalAmount", Purchase.total_amount, 2)
    if date_from:
        query = query.filter(effective_date >= date_from)
    if date_to:
        query = query.filter(effective_date <= date_to)
    search = request.args.get("q", "").strip()
    if len(search) > 120:
        return fail("Search text must be 120 characters or fewer.")
    if search:
        pattern = f"%{search}%"
        query=query.filter(or_(
            Purchase.purchase_no.ilike(pattern),
            cast(Purchase.id, String).ilike(pattern),
            effective_text("sellerName", Purchase.seller_name).ilike(pattern),
            effective_text("purchaseType", Purchase.purchase_type).ilike(pattern),
            effective_text("variety", Purchase.variety).ilike(pattern),
        ))
    total = query.count()
    summary_row = query.with_entities(
        func.coalesce(func.sum(effective_quantity), 0),
        func.coalesce(func.sum(effective_total), 0),
    ).one()
    query_ordered= (query.options(joinedload(Purchase.effective_correction)).order_by(
        effective_date.desc(),
        Purchase.id.desc(),
    ))
    if per_page is None:
        rows = query_ordered.all()
        pagination = {"page": 1, "perPage": total, "total": total, "pages": 1}
    else:
        rows = query_ordered.offset((page - 1) * per_page).limit(per_page).all()
        pagination = {
            "page": page,
            "perPage": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page,
        }
    records = []
    for purchase in rows:
        values = purchase_effective_values(purchase)
        records.append({
            "id": purchase.id,
            "purchaseNo": purchase.purchase_no,
            "date": values["purchase_date"].isoformat(),
            "seller": values["seller_name"],
            "type": values["purchase_type"],
            "variety": values["variety"],
            "quantity": str(values["quantity_kg"]),
            "rate": str(values["rate_per_kg"]),
            "total": str(values["total_amount"]),
        })
    return jsonify({
        "success": True,
        "report": "purchases",
        "dateFrom": date_from.isoformat() if date_from else None,
        "dateTo": date_to.isoformat() if date_to else None,
        "records": records,
        "summary": {
            "totalQuantityKg": str(Decimal(str(summary_row[0] or 0))),
            "totalPurchaseValue": str(Decimal(str(summary_row[1] or 0))),
        },
        "pagination": pagination,
    })


@api.get("/reports/profit-loss")
@admin_required_api
def profit_loss_report():
    """Show persisted revenue and purchase spend without implying unsupported profit."""
    try:
        date_from, date_to, page, per_page = _financial_report_params()
    except ValueError as error:
        return fail(str(error))
    search = request.args.get("q", "").strip()
    if len(search) > 120:
        return fail("Search text must be 120 characters or fewer.")

    retail_generic_names = (
        "retail",
        "retail customer",
        "retail_customer",
        "walk_in",
        "walkin",
        "walk-in",
        "walk in",
        "walk-in customer",
        "walk in customer",
        "walkin customer",
        "walk_in_customer",
        "walkin_customer",
        "unregistered",
        "unregistered customer",
        "unregistered_customer",
        "registered_customer",
        "registered customer",
    )
    registered_report_name = func.trim(Customer.first_name + literal(" ") + Customer.last_name)
    report_customer_name = case (
        (Customer.id.isnot(None), registered_report_name),
        (
            func.lower(func.trim(Sale.customer_name)).in_(retail_generic_names),
            literal("Retail Customer"),
        ),
        else_=func.coalesce(
            func.nullif(func.trim(Sale.customer_name), ""),
            literal("Retail Customer"),
        ),
    )
    report_customer_type = case (
        (Customer.id.isnot(None), literal("Registered Customer")),
        else_=literal("Retail Customer"),
    )
    sales= (db.session.query(
        func.date(Sale.sale_date).label("report_date"),
        literal("Sales revenue").label("entry_type"),
        Sale.invoice_no.label("reference"),
        report_customer_name.label("description"),
        report_customer_type.label("customer_type_label"),
        Sale.total_amount.label("revenue"),
        cast(literal(0), db.Numeric(14, 2)).label("purchase_cost"),
    ).outerjoin(Customer, Customer.id == Sale.customer_id).filter(Sale.status == "confirmed"))
    purchase_from = Purchase.__table__.outerjoin(
        PurchaseCorrection.__table__,
        Purchase.effective_correction_id == PurchaseCorrection.id,
    )

    def effective_text(key, original):
        return func.coalesce(PurchaseCorrection.proposed_values[key].as_string(), original)

    effective_date = func.coalesce(
        func.str_to_date(effective_text("purchaseDate", None), "%Y-%m-%d"),
        Purchase.purchase_date,
    )
    effective_total = func.coalesce(
        cast(PurchaseCorrection.proposed_values["totalAmount"].as_string(), db.Numeric(14, 2)),
        Purchase.total_amount,
    )
    purchase_rows = db.session.query(
        effective_date.label("report_date"),
        literal("Purchase cost").label("entry_type"),
        Purchase.purchase_no.label("reference"),
        (effective_text(
            "sellerName",
            Purchase.seller_name,
        ) + literal(" Â· ") + effective_text("purchaseType", Purchase.purchase_type) + literal(" Â· ") + effective_text("variety", Purchase.variety)).label("description"),
        cast(literal(None), String).label("customer_type_label"),
        cast(literal(0), db.Numeric(14, 2)).label("revenue"),
        effective_total.label("purchase_cost"),
    ).select_from(purchase_from)

    if date_from:
        sales = sales.filter(func.date(Sale.sale_date) >= date_from.isoformat())
        purchase_rows = purchase_rows.filter(effective_date >= date_from)
    if date_to:
        sales = sales.filter(func.date(Sale.sale_date) <= date_to.isoformat())
        purchase_rows = purchase_rows.filter(effective_date <= date_to)
    if search:
        pattern = f"%{search}%"
        name_parts = search.split(maxsplit=1)
        customer_name_match = or_(
            Customer.first_name.ilike(pattern),
            Customer.last_name.ilike(pattern),
            and_(
                Customer.first_name.ilike(f"%{name_parts[0]}%"),
                Customer.last_name.ilike(f"%{name_parts[1]}%"),
            ) if len(name_parts) == 2 else literal(False),
        )
        sales=sales.filter(or_(
            Sale.invoice_no.ilike(pattern),
            Sale.customer_name.ilike(pattern),
            customer_name_match,
        ))
        purchase_rows=purchase_rows.filter(or_(
            Purchase.purchase_no.ilike(pattern),
            cast(Purchase.id, String).ilike(pattern),
            effective_text("sellerName", Purchase.seller_name).ilike(pattern),
            effective_text("purchaseType", Purchase.purchase_type).ilike(pattern),
            effective_text("variety", Purchase.variety).ilike(pattern),
        ))

    combined = sales.union_all(purchase_rows).subquery()
    total = db.session.query(func.count()).select_from(combined).scalar() or 0
    totals = db.session.query(
        func.coalesce(func.sum(combined.c.revenue), 0),
        func.coalesce(func.sum(combined.c.purchase_cost), 0),
    ).select_from(combined).one()
    query_ordered = db.session.query(combined).order_by(
        combined.c.report_date.desc(),
        combined.c.entry_type,
        combined.c.reference.desc(),
    )
    if per_page is None:
        entries = query_ordered.all()
        pagination = {"page": 1, "perPage": total, "total": total, "pages": 1}
    else:
        entries = query_ordered.offset((page - 1) * per_page).limit(per_page).all()
        pagination = {
            "page": page,
            "perPage": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page,
        }
    records=[{
        "date": row.report_date.isoformat() if hasattr(
            row.report_date,
            "isoformat",
        ) else str(row.report_date),
        "entryType": row.entry_type,
        "reference": row.reference,
        "description": row.description,
        "customerName": row.description if row.entry_type == "Sales revenue" else None,
        "customerTypeLabel": row.customer_type_label,
        "revenue": str(Decimal(str(row.revenue or 0))),
        "purchaseCost": str(Decimal(str(row.purchase_cost or 0))),
        "grossProfit": None,
        "netProfit": None,
    } for row in entries]
    return jsonify({
        "success": True,
        "report": "profit-loss",
        "dateFrom": date_from.isoformat() if date_from else None,
        "dateTo": date_to.isoformat() if date_to else None,
        "records": records,
        "summary": {
            "salesRevenue": str(Decimal(str(totals[0] or 0))),
            "purchaseCosts": str(Decimal(str(totals[1] or 0))),
            "grossProfit": None,
            "netProfit": None,
            "grossProfitUnavailable": "Recorded cost of goods sold is unavailable.",
            "netProfitUnavailable": "Operating and production expense amounts are not recorded.",
        },
        "pagination": pagination,
    })


@api.get("/reports/inventory")
@admin_required_api
def inventory_report():
    """Report current stock snapshots and recorded movements without reconstruction."""
    try:
        date_from, date_to, page, per_page = _financial_report_params()
    except ValueError as error:
        return fail(str(error))
    search = request.args.get("q", "").strip()
    if len(search) > 120:
        return fail("Search text must be 120 characters or fewer.")
    stock_query = Stock.query
    if search:
        pattern = f"%{search}%"
        stock_query=stock_query.filter(or_(
            Stock.item_name.ilike(pattern),
            Stock.stock_type.ilike(pattern),
            func.coalesce(Stock.variety, "").ilike(pattern),
            cast(Stock.package_size_kg, String).ilike(pattern),
        ))

    total = stock_query.count()
    current_row = stock_query.with_entities(
        func.coalesce(
            func.sum(case ((Stock.stock_type == "packed", 0), else_=Stock.quantity_kg)),
            0,
        ),
        func.coalesce(
            func.sum(case ((Stock.stock_type == "packed", Stock.quantity_bags), else_=0)),
            0,
        ),
    ).one()

    # Movement timestamps are stored as naive UTC. Convert date-only filters
    # using the same Sri Lanka calendar-day boundary as /api/inventory/movements.
    local_zone = timezone(timedelta(hours=5, minutes=30))
    movement_quantity = case (
        (Stock.stock_type == "packed", StockMovement.quantity_bags),
        else_=StockMovement.quantity_kg,
    )
    opening_movement = StockMovement.movement_type == "opening_stock"
    opening_positive = and_(opening_movement, movement_quantity > 0)
    received_movement = and_(~opening_movement, movement_quantity > 0)
    issued_movement = and_(~opening_movement, movement_quantity < 0)
    movement_query= (db.session.query(
        StockMovement.stock_id,
        Stock.stock_type,
        func.sum(case ((opening_positive, movement_quantity), else_=0)),
        func.sum(case ((opening_positive, 1), else_=0)),
        func.sum(case ((received_movement, movement_quantity), else_=0)),
        func.sum(case ((issued_movement, -movement_quantity), else_=0)),
    ).join(Stock, Stock.id == StockMovement.stock_id).filter(Stock.id.in_(stock_query.with_entities(Stock.id))))
    if date_from:
        start_utc = datetime.combine(
            date_from,
            datetime.min.time(),
            tzinfo=local_zone,
        ).astimezone(timezone.utc).replace(tzinfo=None)
        movement_query = movement_query.filter(StockMovement.created_at >= start_utc)
    if date_to:
        end_utc = datetime.combine(
            date_to + timedelta(days=1),
            datetime.min.time(),
            tzinfo=local_zone,
        ).astimezone(timezone.utc).replace(tzinfo=None)
        movement_query = movement_query.filter(StockMovement.created_at < end_utc)
    aggregate_rows = movement_query.group_by(StockMovement.stock_id, Stock.stock_type).all()
    movement_totals = {}
    summary = {
        "openingKg": Decimal("0"),
        "openingBags": Decimal("0"),
        "openingKgRows": 0,
        "openingBagRows": 0,
        "receivedKg": Decimal("0"),
        "receivedBags": Decimal("0"),
        "issuedKg": Decimal("0"),
        "issuedBags": Decimal("0"),
    }
    for stock_id, stock_type, opening, opening_count, received, issued in aggregate_rows:
        amounts = (
            Decimal(str(opening or 0)),
            int(opening_count or 0),
            Decimal(str(received or 0)),
            Decimal(str(issued or 0)),
        )
        movement_totals[stock_id] = amounts
        suffix = "Bags" if stock_type == "packed" else "Kg"
        summary[f"opening{suffix}"] += amounts[0]
        summary[f"opening{'Bag' if stock_type=='packed' else 'Kg'}Rows"] += 1 if amounts[1] else 0
        summary[f"received{suffix}"] += amounts[2]
        summary[f"issued{suffix}"] += amounts[3]

    query_ordered = stock_query.order_by(
        Stock.stock_type,
        Stock.item_name,
        Stock.variety,
        Stock.package_size_kg,
    )
    if per_page is None:
        rows = query_ordered.all()
        pagination = {"page": 1, "perPage": total, "total": total, "pages": 1}
    else:
        rows = query_ordered.offset((page - 1) * per_page).limit(per_page).all()
        pagination = {
            "page": page,
            "perPage": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page,
        }
    records = []
    for stock in rows:
        unit = "bags" if stock.stock_type == "packed" else "kg"
        current = Decimal(str(stock.quantity_bags if unit == "bags" else stock.quantity_kg))
        opening, opening_count, received, issued = movement_totals.get(
            stock.id,
            (Decimal("0"), 0, Decimal("0"), Decimal("0")),
        )
        records.append({
            "id": stock.id,
            "item": stock.item_name,
            "category": stock.stock_type,
            "variety": stock.variety or "",
            "packageSize": stock.package_size_kg,
            "unit": unit,
            "currentStock": format(current.quantize(Decimal("0.001")), ".3f"),
            "availableBalance": format(current.quantize(Decimal("0.001")), ".3f"),
            "openingStock": str(opening) if opening_count else None,
            "stockReceived": format(received.quantize(Decimal("0.001")), ".3f"),
            "stockIssued": format(issued.quantize(Decimal("0.001")), ".3f"),
        })

    def quantity_text(value):
        return format(Decimal(str(value or 0)).quantize(Decimal("0.001")), ".3f")
    return jsonify({
        "success": True,
        "report": "inventory",
        "dateFrom": date_from.isoformat() if date_from else None,
        "dateTo": date_to.isoformat() if date_to else None,
        "records": records,
        "summary": {
            "currentKg": quantity_text(current_row[0]),
            "currentBags": quantity_text(current_row[1]),
            ** {key: quantity_text(value) if not key.endswith("Rows") else value for key, value in summary.items()},
        },
        "pagination": pagination,
    })


@api.get("/reports/financial/payment-reconciliation")
@admin_required_api
def financial_payment_reconciliation_report():
    return _financial_report_response(_payment_reconciliation_rows, "payment-reconciliation")


@api.get("/reports/financial/reversal-reconciliation")
@admin_required_api
def financial_reversal_reconciliation_report():
    return _financial_report_response(_reversal_reconciliation_rows, "reversal-reconciliation")


@api.get("/reports/financial/return-settlements")
@admin_required_api
def financial_return_settlement_report():
    return _financial_report_response(_return_settlement_rows, "return-settlements")


@api.get("/reports/financial/customer-outstanding")
@admin_required_api
def financial_customer_outstanding_report():
    try:
        date_from, date_to, page, per_page = _financial_report_params()
        if date_from or date_to:
            return fail("Customer outstanding is a current snapshot and does not accept date filters.")
        rows = _customer_outstanding_rows()
        page_rows, pagination = _paginate_report(rows, page, per_page)
        return jsonify({
            "success": True,
            "report": "customer-outstanding",
            "asOf": colombo_date().isoformat(),
            "dateFrom": None,
            "dateTo": None,
            "records": page_rows,
            "pagination": pagination,
        })
    except ValueError as error:
        return fail(str(error))


@api.get("/reports/financial/payment-collections")
@admin_required_api
def financial_payment_collection_report():
    try:
        date_from, date_to, page, per_page = _financial_report_params()
        query = _date_window(Payment.query, Payment.payment_date, date_from, date_to)
        payments = query.order_by(Payment.payment_date, Payment.id).all()
        totals = {
            "Cash": Decimal("0.00"),
            "Bank Transfer": Decimal("0.00"),
            "other": Decimal("0.00"),
        }
        records = []
        for payment in payments:
            amount = Decimal(str(payment.amount))
            method = payment.method
            totals[method if method in {"Cash", "Bank Transfer"} else "other"] += amount
            customer = db.session.get(Customer, payment.customer_id)
            records.append({
                "paymentId": payment.id,
                "receiptNo": payment.receipt_no,
                "date": payment.payment_date.isoformat(),
                "customerId": payment.customer_id,
                **resolve_transaction_customer(customer, "Retail Customer", "registered"),
                "method": method,
                "amount": str(amount),
                "bankReference": payment.bank_reference,
            })
        page_rows, pagination = _paginate_report(records, page, per_page)
        return jsonify({
            "success": True,
            "report": "payment-collections",
            "dateFrom": date_from.isoformat() if date_from else None,
            "dateTo": date_to.isoformat() if date_to else None,
            "totalsByMethod": {key: str(value) for key, value in totals.items()},
            "grossCollections": str(sum(totals.values(), Decimal("0.00"))),
            "records": page_rows,
            "pagination": pagination,
        })
    except ValueError as error:
        return fail(str(error))


@api.get("/reports/financial/payment-reversals")
@admin_required_api
def financial_payment_reversal_report():
    return _financial_report_response(_reversal_reconciliation_rows, "payment-reversals")


@api.get("/reports/financial/discrepancies")
@admin_required_api
def financial_discrepancy_report():
    try:
        date_from, date_to, page, per_page = _financial_report_params()
        findings = _financial_discrepancies(date_from, date_to)
        page_rows, pagination = _paginate_report(findings, page, per_page)
        return jsonify({
            "success": True,
            "report": "financial-discrepancies",
            "dateFrom": date_from.isoformat() if date_from else None,
            "dateTo": date_to.isoformat() if date_to else None,
            "discrepancyCount": len(findings),
            "records": page_rows,
            "pagination": pagination,
        })
    except ValueError as error:
        return fail(str(error))


@api.put("/account/profile")
@limiter.limit("10 per hour")
@account_settings_required_api
def account_profile_update():
    u = current_user()
    d = request.get_json(silent=True) or {}
    first = (d.get("firstName") or "").strip()
    last = (d.get("lastName") or "").strip()
    ok_f, err_f, clean_first = validate_person_name(first, "First Name", required=True)
    if not ok_f:
        return fail(err_f)
    ok_l, err_l, clean_last = validate_person_name(last, "Last Name", required=True)
    if not ok_l:
        return fail(err_l)

    ok_e, err_e, clean_email = validate_and_normalize_email(d.get("email"), required=False)
    if not ok_e:
        return fail(err_e)
    if clean_email and User.query.filter(User.email == clean_email, User.id != u.id).first():
        return fail("This email address is already used.", 409)

    ok_p, err_p, clean_phone = validate_and_normalize_phone(d.get("contactNumber"), required=False)
    if not ok_p:
        return fail(err_p)

    u.first_name = clean_first
    u.last_name = clean_last
    u.email = clean_email
    u.contact_number = clean_phone
    try:
        audit("update", "profile", u.id)
        db.session.commit()
        session["first_name"] = u.first_name
        session["last_name"] = u.last_name
        return jsonify({"success": True})
    except IntegrityError:
        db.session.rollback()
        return fail("This email address is already used.", 409)


@api.post("/account/change-password")
@limiter.limit("5 per hour")
@account_settings_required_api
def account_password_update():
    u = current_user()
    d = request.get_json(silent=True) or {}
    current = d.get("currentPassword", "")
    new = d.get("newPassword", "")
    confirm = d.get("confirmPassword", "")
    if not check_password_hash(u.password_hash, current):
        return fail("Current password is incorrect.")
    ok_p, err_p = validate_password_complexity(new)
    if not ok_p:
        return fail(err_p)
    if new != confirm:
        return fail("New passwords do not match.")
    u.password_hash = generate_password_hash(new)
    u.auth_version = (u.auth_version or 0) + 1
    session["auth_version"] = u.auth_version
    audit("change_password", "user", u.id)
    db.session.commit()
    return jsonify({"success": True})


@api.get("/admin/users")
@admin_required_api
def users_list():
    return jsonify({
        "success": True,
        "users": [{
            "id": u.id,
            "firstName": u.first_name,
            "lastName": u.last_name,
            "username": u.username,
            "email": u.email,
            "contactNumber": u.contact_number,
            "role": u.role,
            "active": u.is_active,
            "permissions": u.permissions_json,
        } for u in User.query.order_by(User.id).all()],
    })


def _normalize_employee_permissions(value):
    if not isinstance(value, dict):
        raise ValueError("Permissions must be an object.")
    normalized = dict(value)
    inventory = value.get("inventory", {})
    if not isinstance(inventory, dict):
        raise ValueError("Inventory permissions must be an object.")
    normalized["inventory"] = {key: inventory.get(key) is True for key in INVENTORY_SUBPERMISSION_KEYS}
    account_settings = value.get("accountSettings")
    if account_settings is None:
        account_settings = value.get("account_settings")
    normalized["account_settings"] = account_settings is True or account_settings == "allow"
    return normalized


@api.post("/admin/users")
@limiter.limit("10 per hour")
@admin_required_api
def user_create():
    d = request.get_json(silent=True) or {}
    if not isinstance(d, dict):
        return fail("A JSON user request is required.")
    first = d.get("firstName", "")
    last = d.get("lastName", "")
    username = d.get("username", "")
    password = d.get("password", "")
    if not first or not username or not password:
        return fail("First name, username and password are required.")
    ok_f, err_f, clean_first = validate_person_name(first, "First Name", required=True)
    if not ok_f:
        return fail(err_f)
    ok_l, err_l, clean_last = validate_person_name(last, "Last Name", required=False)
    if not ok_l:
        return fail(err_l)
    ok_u, err_u, clean_username = validate_username(username, required=True)
    if not ok_u:
        return fail(err_u)
    if User.query.filter_by(username=clean_username).first():
        return fail("This username is already in use.", 409)

    ok_e, err_e, clean_email = validate_and_normalize_email(d.get("email"), required=False)
    if not ok_e:
        return fail(err_e)
    if clean_email and User.query.filter_by(email=clean_email).first():
        return fail("This email address is already in use.", 409)

    ok_p, err_p, clean_phone = validate_and_normalize_phone(d.get("contactNumber"), required=False)
    if not ok_p:
        return fail(err_p)

    ok_pwd, err_pwd = validate_password_complexity(password)
    if not ok_pwd:
        return fail(err_pwd)

    role = d.get("role", "employee")
    if role not in {"admin", "employee"}:
        return fail("Invalid role.")
    try:
        permissions = _normalize_employee_permissions(d.get("permissions", {}))
    except ValueError as error:
        return fail(str(error))
    u = User(
        username=clean_username,
        password_hash=generate_password_hash(password),
        first_name=clean_first,
        last_name=clean_last,
        email=clean_email,
        contact_number=clean_phone,
        role=role,
        permissions_json=permissions,
    )
    db.session.add(u)
    try:
        db.session.flush()
        audit("create", "user", u.id)
        db.session.commit()
        return jsonify({"success": True, "id": u.id}), 201
    except IntegrityError:
        db.session.rollback()
        return fail("Username or email already exists.", 409)


@api.put("/admin/users/<int:uid>")
@limiter.limit("30 per hour")
@admin_required_api
def user_update(uid):
    u = db.session.get(User, uid)
    if not u:
        return fail("User not found.", 404)
    d = request.get_json(silent=True) or {}
    try:
        permissions = _normalize_employee_permissions(d["permissions"]) if "permissions" in d else None
    except ValueError as error:
        return fail(str(error))
    if uid == current_user().id and d.get("active") is False:
        return fail("You cannot deactivate your own administrator account.")
    if "firstName" in d:
        ok_f, err_f, clean_first = validate_person_name(d["firstName"], "First Name", required=True)
        if not ok_f:
            return fail(err_f)
        u.first_name = clean_first
    if "lastName" in d:
        ok_l, err_l, clean_last = validate_person_name(d["lastName"], "Last Name", required=False)
        if not ok_l:
            return fail(err_l)
        u.last_name = clean_last
    if "username" in d:
        ok_u, err_u, clean_username = validate_username(d["username"], required=True)
        if not ok_u:
            return fail(err_u)
        if User.query.filter(User.username == clean_username, User.id != uid).first():
            return fail("This username is already in use.", 409)
        u.username = clean_username
    if "active" in d:
        u.is_active = bool(d["active"])
    if "email" in d:
        ok_e, err_e, clean_email = validate_and_normalize_email(d["email"], required=False)
        if not ok_e:
            return fail(err_e)
        if clean_email and User.query.filter(User.email == clean_email, User.id != uid).first():
            return fail("This email address is already in use.", 409)
        u.email = clean_email
    if "contactNumber" in d:
        ok_p, err_p, clean_phone = validate_and_normalize_phone(d["contactNumber"], required=False)
        if not ok_p:
            return fail(err_p)
        u.contact_number = clean_phone
    permissions_changed = "permissions" in d and u.permissions_json != permissions
    if "permissions" in d:
        old_perms = u.permissions_json or {}
        new_perms = permissions or {}
        major_changed = (old_perms.get("tabs") != new_perms.get("tabs") or old_perms.get("inventory") != new_perms.get("inventory") or old_perms.get("administration") != new_perms.get("administration"))
        u.permissions_json = permissions
        if major_changed:
            u.auth_version = (u.auth_version or 0) + 1
        if uid == current_user().id:
            session["auth_version"] = u.auth_version
            session["permissions"] = permissions
    if "password" in d and d["password"]:
        ok_pwd, err_pwd = validate_password_complexity(d["password"])
        if not ok_pwd:
            return fail(err_pwd)
        u.password_hash = generate_password_hash(d["password"])
        u.auth_version = (u.auth_version or 0) + 1
        if uid == current_user().id:
            session["auth_version"] = u.auth_version
        audit("reset_password", "user", uid)
    try:
        audit("update", "user", uid)
        db.session.commit()
        return jsonify({"success": True})
    except IntegrityError:
        db.session.rollback()
        return fail("Username or email already used by another account.", 409)


@api.post("/admin/users/<int:uid>/reset-password")
@limiter.limit("5 per hour")
@admin_required_api
def user_reset_password(uid):
    u = db.session.get(User, uid)
    d = request.get_json(silent=True) or {}
    if not u:
        return fail("User not found.", 404)
    pwd = d.get("password", "")
    ok_pwd, err_pwd = validate_password_complexity(pwd)
    if not ok_pwd:
        return fail(err_pwd)
    u.password_hash = generate_password_hash(pwd)
    u.auth_version = (u.auth_version or 0) + 1
    if uid == current_user().id:
        session["auth_version"] = u.auth_version
    audit("reset_password", "user", uid)
    db.session.commit()
    return jsonify({"success": True})
