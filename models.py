from datetime import datetime, date
from timezone_utils import colombo_date
from decimal import Decimal
import re
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, Computed, Index, UniqueConstraint, event

db = SQLAlchemy()

master_item_main_categories = db.Table(
    "master_item_main_categories",
    db.Column(
        "master_item_id",
        db.Integer,
        db.ForeignKey("master_items.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "main_category_id",
        db.Integer,
        db.ForeignKey("main_categories.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    mysql_engine="InnoDB",
)


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class User(db.Model, TimestampMixin):
    __table_args__ = {"mysql_engine": "InnoDB"}
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False, default="")
    email = db.Column(db.String(160), unique=True)
    contact_number = db.Column(db.String(30))
    role = db.Column(db.String(20), nullable=False, default="employee")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    permissions_json = db.Column(db.JSON, nullable=False, default=dict)
    auth_version = db.Column(db.Integer, nullable=False, default=0, server_default="0")


class PasswordRecoveryToken(db.Model):
    """Append-only recovery attempts; OTPs and reset tokens are stored as hashes."""
    __tablename__ = "password_recovery_tokens"
    __table_args__ = (
        UniqueConstraint("reset_token_hash", name="uq_password_recovery_reset_hash"),
        Index("ix_password_recovery_user_purpose_created", "user_id", "purpose", "created_at"),
        CheckConstraint("attempt_count >= 0", name="ck_password_recovery_attempts_nonnegative"),
        {"mysql_engine": "InnoDB"},
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", name="fk_password_recovery_tokens_user_id_users"),
        nullable=False,
    )
    purpose = db.Column(db.String(32), nullable=False, default="password_reset")
    status = db.Column(db.String(16), nullable=False, default="pending")
    otp_hash = db.Column(db.String(64))
    otp_expires_at = db.Column(db.DateTime)
    attempt_count = db.Column(db.Integer, nullable=False, default=0)
    reset_token_hash = db.Column(db.String(64))
    reset_expires_at = db.Column(db.DateTime)
    requested_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    sent_at = db.Column(db.DateTime)
    consumed_at = db.Column(db.DateTime)
    revoked_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class Setting(db.Model, TimestampMixin):
    __table_args__ = {"mysql_engine": "InnoDB"}
    __tablename__ = "settings"
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text)


class MasterItem(db.Model, TimestampMixin):
    __tablename__ = "master_items"
    id = db.Column(db.Integer, primary_key=True)
    group_name = db.Column(db.String(40), nullable=False)  # rice_variety, paddy_type, category, product, package_size
    name = db.Column(db.String(120), nullable=False)
    unit = db.Column(db.String(20), default="kg")
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    main_categories = db.relationship(
        "MainCategory",
        secondary=master_item_main_categories,
        back_populates="items",
        lazy="selectin",
    )
    __table_args__ = (
        UniqueConstraint("group_name", "name", name="uq_master_group_name"),
        {"mysql_engine": "InnoDB"},
    )


class MainCategory(db.Model, TimestampMixin):
    __tablename__ = "main_categories"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), nullable=False, unique=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    inventory_section = db.Column(db.String(32), nullable=False, unique=True)
    master_category_id = db.Column(db.Integer, db.ForeignKey("master_items.id"), unique=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    category_master = db.relationship("MasterItem", lazy="joined")
    items = db.relationship(
        "MasterItem",
        secondary=master_item_main_categories,
        back_populates="main_categories",
        lazy="selectin",
    )
    __table_args__ = ({"mysql_engine": "InnoDB"},)


class PriceSetting(db.Model, TimestampMixin):
    __tablename__ = "price_settings"
    id = db.Column(db.Integer, primary_key=True)
    item_name = db.Column(db.String(120), nullable=False)
    price_type = db.Column(db.String(20), nullable=False)  # purchase / selling
    default_price = db.Column(db.Numeric(14, 2))
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    purchase_locked = db.Column(db.Boolean, default=False, nullable=False)
    selling_locked = db.Column(db.Boolean, default=False, nullable=False)

    def is_locked(self):
        return bool(self.purchase_locked if self.price_type == "purchase" else self.selling_locked)

    def set_locked(self, locked):
        if self.price_type == "purchase":
            self.purchase_locked = bool(locked)
        else:
            self.selling_locked = bool(locked)
    __table_args__ = (
        UniqueConstraint("item_name", "price_type", name="uq_price_item_type"),
        {"mysql_engine": "InnoDB"},
    )


class Supplier(db.Model, TimestampMixin):
    __table_args__ = {"mysql_engine": "InnoDB"}
    __tablename__ = "suppliers"
    id = db.Column(db.Integer, primary_key=True)
    supplier_code = db.Column(db.String(30), unique=True, nullable=False)
    name = db.Column(db.String(160), nullable=False)
    phone = db.Column(db.String(30))
    address = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True, nullable=False)


class Customer(db.Model, TimestampMixin):
    __table_args__ = (
        UniqueConstraint("active_phone", name="uq_customers_active_phone"),
        {"mysql_engine": "InnoDB"},
    )
    __tablename__ = "customers"
    id = db.Column(db.Integer, primary_key=True)
    customer_code = db.Column(db.String(30), unique=True, nullable=False)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False, default="")
    phone = db.Column(db.String(30), nullable=False)
    phone_normalized = db.Column(db.String(30), nullable=False)
    active_phone = db.Column(
        db.String(30),
        Computed("CASE WHEN `is_active` = 1 THEN `phone_normalized` ELSE NULL END", persisted=True),
    )
    shop_name = db.Column(db.String(160))
    credit_limit = db.Column(db.Numeric(14, 2), default=0, nullable=False)
    address1 = db.Column(db.String(200))
    address2 = db.Column(db.String(200))
    city = db.Column(db.String(100))
    registered_on = db.Column(db.Date, default=colombo_date)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    @property
    def full_name(self):
        return (self.first_name + " " + (self.last_name or "")).strip()


@event.listens_for(Customer, "before_insert")
@event.listens_for(Customer, "before_update")
def _normalize_customer_phone_key(_mapper, _connection, customer):
    """Keep ORM-created customer records inside the same phone-key invariant."""
    digits = re.sub(r"\D", "", customer.phone or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("94") and len(digits) == 11:
        digits = "+" + digits
    elif digits.startswith("0") and len(digits) == 10:
        digits = "+94" + digits[1:]
    customer.phone_normalized = digits


class Purchase(db.Model, TimestampMixin):
    __table_args__ = {"mysql_engine": "InnoDB"}
    __tablename__ = "purchases"
    id = db.Column(db.Integer, primary_key=True)
    purchase_no = db.Column(db.String(40), unique=True, nullable=False)
    idempotency_key = db.Column(db.String(100), unique=True)
    request_fingerprint = db.Column(db.String(64))
    purchase_type = db.Column(db.String(30), nullable=False)
    variety_id = db.Column(
        db.Integer,
        db.ForeignKey("master_items.id", name="fk_purchases_variety_id_master_items"),
    )
    variety = db.Column(db.String(120), nullable=False)
    seller_name = db.Column(db.String(160), nullable=False)
    quantity_kg = db.Column(db.Numeric(14, 3), nullable=False)
    rate_per_kg = db.Column(db.Numeric(14, 2), nullable=False)
    total_amount = db.Column(db.Numeric(14, 2), nullable=False)
    purchase_date = db.Column(db.Date, nullable=False, default=colombo_date)
    payment_method = db.Column(db.String(30), default="Cash")
    notes = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    effective_correction_id = db.Column(
        db.Integer,
        db.ForeignKey("purchase_corrections.id", name="fk_purchases_effective_correction_id"),
    )
    effective_correction = db.relationship(
        "PurchaseCorrection",
        foreign_keys=[effective_correction_id],
        uselist=False,
    )


class PurchasePriceSetting(db.Model, TimestampMixin):
    __tablename__ = "purchase_price_settings"
    __table_args__ = (
        UniqueConstraint("variety_id", "paddy_type", name="uq_purchase_price_variety_type"),
        {"mysql_engine": "InnoDB"},
    )
    id = db.Column(db.Integer, primary_key=True)
    variety_id = db.Column(
        db.Integer,
        db.ForeignKey("master_items.id", name="fk_purchase_price_variety_id_master_items"),
        nullable=False,
    )
    paddy_type = db.Column(db.String(30), nullable=False)
    default_price = db.Column(db.Numeric(14, 2))
    is_locked = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    variety = db.relationship("MasterItem", lazy="joined")


class PurchaseCorrection(db.Model):
    """Append-only proposed purchase versions with an Admin decision state."""
    __tablename__ = "purchase_corrections"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_purchase_corrections_idempotency_key"),
        Index("ix_purchase_corrections_purchase_status", "purchase_id", "status", "id"),
        {"mysql_engine": "InnoDB"},
    )
    id = db.Column(db.Integer, primary_key=True)
    purchase_id = db.Column(db.Integer, db.ForeignKey("purchases.id"), nullable=False)
    idempotency_key = db.Column(db.String(100), nullable=False)
    request_fingerprint = db.Column(db.String(64), nullable=False)
    reason = db.Column(db.String(500), nullable=False)
    before_values = db.Column(db.JSON, nullable=False)
    proposed_values = db.Column(db.JSON, nullable=False)
    status = db.Column(db.String(16), nullable=False, default="pending", server_default="pending")
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_at = db.Column(db.DateTime)
    decision_notes = db.Column(db.String(2000))
    purchase = db.relationship("Purchase", foreign_keys=[purchase_id])
    requester = db.relationship("User", foreign_keys=[created_by])
    reviewer = db.relationship("User", foreign_keys=[reviewed_by])


def purchase_effective_values(purchase):
    """Return the latest approved snapshot, falling back to immutable original fields."""
    correction = purchase.effective_correction
    values = correction.proposed_values if correction and correction.status == "approved" else None
    if values:
        return {
            "purchase_type": values["purchaseType"],
            "variety": values["variety"],
            "seller_name": values["sellerName"],
            "quantity_kg": Decimal(str(values["quantityKg"])),
            "rate_per_kg": Decimal(str(values["ratePerKg"])),
            "total_amount": Decimal(str(values["totalAmount"])),
            "purchase_date": date.fromisoformat(values["purchaseDate"]),
            "notes": values.get("notes"),
        }
    return {
        "purchase_type": purchase.purchase_type,
        "variety": purchase.variety,
        "seller_name": purchase.seller_name,
        "quantity_kg": Decimal(str(purchase.quantity_kg)),
        "rate_per_kg": Decimal(str(purchase.rate_per_kg)),
        "total_amount": Decimal(str(purchase.total_amount)),
        "purchase_date": purchase.purchase_date,
        "notes": purchase.notes,
    }


class Stock(db.Model, TimestampMixin):
    __tablename__ = "stocks"
    id = db.Column(db.Integer, primary_key=True)
    item_name = db.Column(db.String(80), nullable=False)
    stock_type = db.Column(db.String(30), nullable=False)  # wet_paddy/dry_paddy/rice/bran/packed/return_quarantine
    variety = db.Column(db.String(80), default="")
    quantity_kg = db.Column(db.Numeric(16, 3), nullable=False, default=0)
    quantity_bags = db.Column(db.Numeric(16, 3), nullable=False, default=0)
    package_size_kg = db.Column(db.Integer)
    product_id = db.Column(db.Integer, db.ForeignKey("master_items.id"), index=True)
    product = db.relationship("MasterItem", lazy="joined")
    variety_key = db.Column(db.String(80), Computed("COALESCE(`variety`, '')", persisted=True))
    package_size_key = db.Column(
        db.Integer,
        Computed("COALESCE(`package_size_kg`, 0)", persisted=True),
    )
    __table_args__ = (
        UniqueConstraint(
            "item_name",
            "stock_type",
            "variety",
            "package_size_kg",
            name="uq_stock_item_type_variety_pack",
        ),
        UniqueConstraint(
            "item_name",
            "stock_type",
            "variety_key",
            "package_size_key",
            name="uq_stocks_logical_key",
        ),
        {"mysql_engine": "InnoDB"},
    )


class StockMovement(db.Model, TimestampMixin):
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_stock_movements_idempotency_key"),
        {"mysql_engine": "InnoDB"},
    )
    __tablename__ = "stock_movements"
    id = db.Column(db.Integer, primary_key=True)
    stock_id = db.Column(db.Integer, db.ForeignKey("stocks.id"), nullable=False)
    movement_type = db.Column(db.String(40), nullable=False)
    quantity_kg = db.Column(db.Numeric(16, 3), nullable=False)
    quantity_bags = db.Column(db.Numeric(16, 3), default=0, nullable=False)
    reference_type = db.Column(db.String(40))
    reference_id = db.Column(db.Integer)
    # Unique source item makes return stock restoration idempotent.
    return_item_id = db.Column(db.Integer, db.ForeignKey("return_items.id"), unique=True)
    production_record_id = db.Column(db.Integer, db.ForeignKey("production_records.id"), index=True)
    idempotency_key = db.Column(db.String(100))
    notes = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))


class ProductionRecord(db.Model, TimestampMixin):
    __table_args__ = {"mysql_engine": "InnoDB"}
    __tablename__ = "production_records"
    id = db.Column(db.Integer, primary_key=True)
    record_type = db.Column(db.String(20), nullable=False)  # drying/milling/packaging
    record_date = db.Column(db.Date, nullable=False, default=colombo_date)
    variety = db.Column(db.String(120), nullable=False)
    input_kg = db.Column(db.Numeric(16, 3), nullable=False)
    primary_output_kg = db.Column(db.Numeric(16, 3), nullable=False)
    secondary_output_kg = db.Column(db.Numeric(16, 3), default=0, nullable=False)
    waste_kg = db.Column(db.Numeric(16, 3), default=0, nullable=False)
    remaining_kg = db.Column(db.Numeric(16, 3), default=0, nullable=False)
    package_size_kg = db.Column(db.Integer)
    packet_count = db.Column(db.Integer, default=0)
    batch_no = db.Column(db.String(80))
    operator = db.Column(db.String(120))
    notes = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    idempotency_key = db.Column(db.String(100), unique=True)
    request_fingerprint = db.Column(db.String(64))
    movements = db.relationship("StockMovement", backref="production_record", lazy="selectin")


class Sale(db.Model, TimestampMixin):
    __table_args__ = {"mysql_engine": "InnoDB"}
    __tablename__ = "sales"
    id = db.Column(db.Integer, primary_key=True)
    invoice_no = db.Column(db.String(50), unique=True, nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"))
    customer_name = db.Column(db.String(160), nullable=False)
    customer_type = db.Column(db.String(20), nullable=False, default="walk_in")
    subtotal = db.Column(db.Numeric(14, 2), nullable=False)
    total_amount = db.Column(db.Numeric(14, 2), nullable=False)
    amount_paid = db.Column(db.Numeric(14, 2), default=0, nullable=False)
    amount_received = db.Column(
        db.Numeric(14, 2),
        default=lambda context: context.get_current_parameters().get("amount_paid", 0),
        nullable=False,
    )
    amount_due = db.Column(db.Numeric(14, 2), default=0, nullable=False)
    payment_type = db.Column(db.String(20), nullable=False)
    payment_method = db.Column(db.String(30))
    sale_date = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    status = db.Column(db.String(20), default="confirmed", nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    items = db.relationship("SaleItem", cascade="all, delete-orphan", lazy="selectin")


class SaleItem(db.Model):
    __table_args__ = {"mysql_engine": "InnoDB"}
    __tablename__ = "sale_items"
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id", ondelete="CASCADE"), nullable=False)
    category = db.Column(db.String(40), nullable=False)
    stock_type = db.Column(db.String(30))
    product_name = db.Column(db.String(140), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("master_items.id"), index=True)
    product = db.relationship("MasterItem", lazy="joined")
    variety = db.Column(db.String(120))
    quantity = db.Column(db.Numeric(14, 3), nullable=False)
    unit = db.Column(db.String(20), nullable=False)
    package_size_kg = db.Column(db.Integer)
    total_kg = db.Column(db.Numeric(14, 3), nullable=False)
    price_per_kg = db.Column(db.Numeric(14, 2), nullable=False)
    line_total = db.Column(db.Numeric(14, 2), nullable=False)


class HeldSale(db.Model, TimestampMixin):
    __table_args__ = (
        UniqueConstraint("return_item_id", name="uq_held_sales_return_item"),
        {"mysql_engine": "InnoDB"},
    )
    __tablename__ = "held_sales"
    id = db.Column(db.Integer, primary_key=True)
    hold_ref = db.Column(db.String(40), unique=True, nullable=False)
    payload = db.Column(db.JSON, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    draft_type = db.Column(
        db.String(24),
        nullable=False,
        default="sales_bill",
        server_default="sales_bill",
    )
    return_item_id = db.Column(db.Integer, db.ForeignKey("return_items.id"))


class SalesReturn(db.Model, TimestampMixin):
    __table_args__ = {"mysql_engine": "InnoDB"}
    __tablename__ = "sales_returns"
    id = db.Column(db.Integer, primary_key=True)
    return_no = db.Column(db.String(50), unique=True, nullable=False)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"))
    return_date = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    # New Return Bills do not choose a financial method until Admin approval.
    # Nullable preserves legacy historical values without assigning new requests a choice.
    settlement_type = db.Column(db.String(20), nullable=True)
    settlement_amount = db.Column(db.Numeric(14, 2), default=0, nullable=False)
    status = db.Column(db.String(30), default="pending_inspection", nullable=False)
    financial_status = db.Column(
        db.String(32),
        default="pending_settlement",
        server_default="pending_settlement",
        nullable=False,
    )
    approval_status = db.Column(db.String(32))
    approved_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)
    approval_notes = db.Column(db.Text)
    idempotency_key = db.Column(db.String(100), unique=True)
    notes = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    items = db.relationship("ReturnItem", cascade="all, delete-orphan", lazy="selectin")


class ReturnItem(db.Model):
    __table_args__ = {"mysql_engine": "InnoDB"}
    __tablename__ = "return_items"
    id = db.Column(db.Integer, primary_key=True)
    return_id = db.Column(
        db.Integer,
        db.ForeignKey("sales_returns.id", ondelete="CASCADE"),
        nullable=False,
    )
    sale_item_id = db.Column(db.Integer, db.ForeignKey("sale_items.id"), nullable=False)
    quantity = db.Column(db.Numeric(14, 3), nullable=False)
    total_kg = db.Column(db.Numeric(14, 3), nullable=False)
    decision = db.Column(db.String(20), default="pending", nullable=False)
    inspection_notes = db.Column(db.Text)
    inspected_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    inspected_at = db.Column(db.DateTime)
    settlement_status = db.Column(db.String(24), default="unsettled", nullable=False)


class ReturnApprovalEvent(db.Model):
    __tablename__ = "return_approval_events"
    __table_args__ = (
        Index("ix_return_approval_events_return_id", "return_id"),
        {"mysql_engine": "InnoDB"},
    )
    id = db.Column(db.Integer, primary_key=True)
    return_id = db.Column(db.Integer, db.ForeignKey("sales_returns.id"), nullable=False)
    admin_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    action = db.Column(db.String(20), nullable=False)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ReturnApprovalNotification(db.Model):
    """Persist one Admin decision notification for its original requester."""
    __tablename__ = "return_approval_notifications"
    __table_args__ = (
        UniqueConstraint("return_id", name="uq_return_approval_notification_return"),
        Index("ix_return_approval_notifications_user_read", "user_id", "is_read"),
        {"mysql_engine": "InnoDB"},
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    return_id = db.Column(db.Integer, db.ForeignKey("sales_returns.id"), nullable=False)
    is_read = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    read_at = db.Column(db.DateTime)


class ReturnInspectionEvent(db.Model):
    __tablename__ = "return_inspection_events"
    __table_args__ = (
        Index("ix_return_inspection_events_item_id", "return_item_id"),
        {"mysql_engine": "InnoDB"},
    )
    id = db.Column(db.Integer, primary_key=True)
    return_item_id = db.Column(db.Integer, db.ForeignKey("return_items.id"), nullable=False)
    inspector_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    decision = db.Column(db.String(20), nullable=False)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ReturnSettlement(db.Model):
    __tablename__ = "return_settlements"
    __table_args__ = {"mysql_engine": "InnoDB"}
    id = db.Column(db.Integer, primary_key=True)
    return_item_id = db.Column(
        db.Integer,
        db.ForeignKey("return_items.id"),
        nullable=False,
        unique=True,
    )
    settlement_type = db.Column(db.String(20), nullable=False)
    refund_option = db.Column(db.String(32))
    return_value = db.Column(db.Numeric(14, 2), nullable=False)
    replacement_value = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    balance_applied = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    cash_refund = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    amount_collected = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    difference_refund = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    exchange_ref = db.Column(db.String(40))
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ReturnReplacementItem(db.Model):
    __tablename__ = "return_replacement_items"
    __table_args__ = (
        Index("ix_return_replacement_items_settlement_id", "settlement_id"),
        {"mysql_engine": "InnoDB"},
    )
    id = db.Column(db.Integer, primary_key=True)
    settlement_id = db.Column(
        db.Integer,
        db.ForeignKey("return_settlements.id", ondelete="CASCADE"),
        nullable=False,
    )
    category = db.Column(db.String(40), nullable=False)
    product_name = db.Column(db.String(140), nullable=False)
    variety = db.Column(db.String(120))
    quantity = db.Column(db.Numeric(14, 3), nullable=False)
    unit = db.Column(db.String(20), nullable=False)
    package_size_kg = db.Column(db.Integer)
    total_kg = db.Column(db.Numeric(14, 3), nullable=False)
    price_per_kg = db.Column(db.Numeric(14, 2), nullable=False)
    line_total = db.Column(db.Numeric(14, 2), nullable=False)
    manual_price_reason = db.Column(db.Text)


class ReturnFinancialEntry(db.Model):
    __tablename__ = "return_financial_entries"
    __table_args__ = (
        Index("ix_return_financial_entries_settlement_id", "settlement_id"),
        {"mysql_engine": "InnoDB"},
    )
    id = db.Column(db.Integer, primary_key=True)
    settlement_id = db.Column(db.Integer, db.ForeignKey("return_settlements.id"), nullable=False)
    entry_type = db.Column(db.String(32), nullable=False)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    method = db.Column(db.String(30))
    reference = db.Column(db.String(100))
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"))
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class CombinedTransactionDocument(db.Model):
    __tablename__ = "combined_transaction_documents"
    __table_args__ = (
        Index("ix_combined_documents_return_id", "return_id"),
        {"mysql_engine": "InnoDB"},
    )
    id = db.Column(db.Integer, primary_key=True)
    document_no = db.Column(db.String(60), unique=True, nullable=False)
    return_id = db.Column(db.Integer, db.ForeignKey("sales_returns.id"), nullable=False)
    settlement_id = db.Column(db.Integer, db.ForeignKey("return_settlements.id"), unique=True)
    final_return_id = db.Column(db.Integer, db.ForeignKey("sales_returns.id"), unique=True)
    snapshot = db.Column(db.JSON, nullable=False)
    is_final = db.Column(db.Boolean, nullable=False, default=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Payment(db.Model, TimestampMixin):
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_payments_idempotency_key"),
        {"mysql_engine": "InnoDB"},
    )
    __tablename__ = "payments"
    id = db.Column(db.Integer, primary_key=True)
    receipt_no = db.Column(db.String(50), unique=True, nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    method = db.Column(db.String(30), nullable=False)
    payment_date = db.Column(db.Date, nullable=False, default=colombo_date)
    bank_reference = db.Column(db.String(100))
    allocation_method = db.Column(db.String(20), default="oldest_first")
    note = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    idempotency_key = db.Column(db.String(100))
    request_fingerprint = db.Column(db.String(64))


class PaymentAllocation(db.Model):
    __table_args__ = {"mysql_engine": "InnoDB"}
    __tablename__ = "payment_allocations"
    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(
        db.Integer,
        db.ForeignKey("payments.id", ondelete="CASCADE"),
        nullable=False,
    )
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=False)
    amount = db.Column(db.Numeric(14, 2), nullable=False)


class PaymentReversal(db.Model):
    __tablename__ = "payment_reversals"
    __table_args__ = (
        UniqueConstraint("original_payment_id", name="uq_payment_reversals_original_payment"),
        UniqueConstraint("reversal_no", name="uq_payment_reversals_reversal_no"),
        UniqueConstraint("idempotency_key", name="uq_payment_reversals_idempotency_key"),
        CheckConstraint("amount > 0", name="ck_payment_reversals_positive_amount"),
        {"mysql_engine": "InnoDB"},
    )
    id = db.Column(db.Integer, primary_key=True)
    reversal_no = db.Column(db.String(60), nullable=False)
    original_payment_id = db.Column(db.Integer, db.ForeignKey("payments.id"), nullable=False)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    reason = db.Column(db.String(500), nullable=False)
    idempotency_key = db.Column(db.String(100), nullable=False)
    request_fingerprint = db.Column(db.String(64), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class PaymentReversalAllocation(db.Model):
    __tablename__ = "payment_reversal_allocations"
    __table_args__ = (
        UniqueConstraint("original_allocation_id", name="uq_payment_reversal_allocations_original"),
        CheckConstraint("amount > 0", name="ck_payment_reversal_allocations_positive_amount"),
        {"mysql_engine": "InnoDB"},
    )
    id = db.Column(db.Integer, primary_key=True)
    reversal_id = db.Column(db.Integer, db.ForeignKey("payment_reversals.id"), nullable=False)
    original_allocation_id = db.Column(
        db.Integer,
        db.ForeignKey("payment_allocations.id"),
        nullable=False,
    )
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=False)
    amount = db.Column(db.Numeric(14, 2), nullable=False)


class CustomerBalanceAdjustment(db.Model):
    __tablename__ = "customer_balance_adjustments"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_customer_balance_adjustments_idempotency_key"),
        CheckConstraint("amount <> 0", name="ck_customer_balance_adjustments_nonzero_amount"),
        {"mysql_engine": "InnoDB"},
    )
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    reason = db.Column(db.String(500), nullable=False)
    idempotency_key = db.Column(db.String(100), nullable=False)
    request_fingerprint = db.Column(db.String(64), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class AuditLog(db.Model):
    __table_args__ = {"mysql_engine": "InnoDB"}
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(80), nullable=False)
    entity = db.Column(db.String(80), nullable=False)
    entity_id = db.Column(db.Integer)
    details = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

from timezone_utils import colombo_date
