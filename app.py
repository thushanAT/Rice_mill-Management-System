import os
import secrets
from pathlib import Path
from decimal import Decimal
from datetime import timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from dotenv import load_dotenv
from flask_wtf.csrf import CSRFError
from models import (
    db,
    User,
    Setting,
    MasterItem,
    Customer,
    CombinedTransactionDocument,
    AuditLog,
    Purchase,
    SalesReturn,
    Sale,
    SaleItem,
    purchase_effective_values,
)
from types import SimpleNamespace
from api import api, resolve_snapshot_customer
from config.extensions import csrf, limiter, migrate
from config.rate_limit import resolve_app_environment, resolve_rate_limit_storage
from config.validation import (
    validate_person_name,
    validate_username,
    validate_and_normalize_email,
    validate_and_normalize_phone,
    validate_password_complexity,
    check_login_lockout,
    record_failed_login,
    clear_failed_login,
)
from customer_types import normalize_transaction_snapshot

load_dotenv()


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please login first.", "error")
            return redirect(url_for("login"))
        user = db.session.get(User, session["user_id"])
        if not user or not user.is_active or session.get(
            "auth_version",
            0,
        ) != (user.auth_version or 0):
            session.clear()
            flash("Your account is inactive. Contact the administrator.", "error")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped


def page_access_required(module_name):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            user = db.session.get(User, session.get("user_id"))
            if not user or not user.is_active or session.get(
                "auth_version",
                0,
            ) != (user.auth_version or 0):
                session.clear()
                return redirect(url_for("login"))
            if user.role == "admin":
                return fn(*args, **kwargs)
            allowed = (user.permissions_json or {}).get("tabs", [])
            if module_name not in allowed:
                abort(403)
            return fn(*args, **kwargs)
        return wrapped
    return decorator


def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please login first.", "error")
            return redirect(url_for("login"))
        user = db.session.get(User, session["user_id"])
        if not user or not user.is_active or session.get(
            "auth_version",
            0,
        ) != (user.auth_version or 0):
            session.clear()
            flash("Your account is inactive. Contact the administrator.", "error")
            return redirect(url_for("login"))
        if user.role != "admin":
            flash("Administrator permission required.", "error")
            return redirect(url_for("profile"))
        return fn(*args, **kwargs)
    return wrapped


def account_settings_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please login first.", "error")
            return redirect(url_for("login"))
        user = db.session.get(User, session["user_id"])
        if not user or not user.is_active or session.get(
            "auth_version",
            0,
        ) != (user.auth_version or 0):
            session.clear()
            flash("Your account is inactive. Contact the administrator.", "error")
            return redirect(url_for("login"))
        if user.role == "admin":
            return fn(*args, **kwargs)
        perms = user.permissions_json if isinstance(user.permissions_json, dict) else {}
        if perms.get("account_settings") is True or perms.get("accountSettings") is True:
            return fn(*args, **kwargs)
        flash("You do not have permission to access Account Settings.", "error")
        return redirect(url_for("profile"))
    return wrapped


def profile_data():
    u = db.session.get(User, session.get("user_id"))
    mill = Setting.query.filter_by(key="mill_name").first()
    return {
        "first_name": u.first_name,
        "last_name": u.last_name,
        "username": u.username,
        "email": u.email or "",
        "contact_number": u.contact_number or "",
        "role": "Admin / Mill Owner" if u.role == "admin" else "Employee",
        "mill_name": mill.value if mill else "Not configured",
        "created_date": u.created_at.strftime("%Y-%m-%d"),
        "active": u.is_active,
    }


def resolve_secret_key(app_environment: str) -> str:
    """Resolve a secure, stable SECRET_KEY that persists across server restarts.

    1. Uses os.getenv("SECRET_KEY") if provided in the environment / .env.
    2. If not provided and running in production: raises RuntimeError.
    3. If not provided and running in development/testing:
       Persists and reuses a generated secret in an instance keyfile (.secret_key)
       so it remains 100% stable across server restarts, rather than generating a
       new ephemeral in-memory key on every startup.
    """
    env_secret = os.getenv("SECRET_KEY")
    if env_secret and env_secret.strip():
        return env_secret.strip()

    if app_environment == "production":
        raise RuntimeError("SECRET_KEY must be configured in production.")

    instance_dir = Path(__file__).resolve().parent / "instance"
    secret_file = instance_dir / ".secret_key"
    try:
        instance_dir.mkdir(parents=True, exist_ok=True)
        if secret_file.is_file():
            stored = secret_file.read_text(encoding="utf-8").strip()
            if stored:
                return stored
        new_secret = secrets.token_urlsafe(48)
        secret_file.write_text(new_secret, encoding="utf-8")
        return new_secret
    except Exception:
        return "dev-stable-secret-key-rice-mill-default-2026"


def create_app():
    app = Flask(__name__)
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() in {"1", "true", "yes", "on"}
    app_environment = resolve_app_environment(
        os.getenv("APP_ENV"),
        debug_mode,
        __name__ == "__main__",
    )
    if app_environment == "production" and debug_mode:
        raise RuntimeError("FLASK_DEBUG must be disabled in production.")

    def env_bool(name, default):
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    app.config["DEBUG"] = debug_mode
    app.config["APP_ENV"] = app_environment
    # Keep development/test templates in sync with files changed while Flask
    # remains running; production templates stay cached for normal performance.
    app.config["TEMPLATES_AUTO_RELOAD"] = app_environment in {"development", "testing"}
    app.config["SECRET_KEY"] = resolve_secret_key(app_environment)
    session_cookie_name = os.getenv("SESSION_COOKIE_NAME")
    if not session_cookie_name:
        session_cookie_name = "rice_mill_test_session" if app_environment == "testing" else "rice_mill_session"
    app.config["SESSION_COOKIE_NAME"] = session_cookie_name
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
        "DATABASE_URL",
        "mysql+pymysql://root:YOUR_PASSWORD@localhost:3306/rice_mill_db?charset=utf8mb4",
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 280}
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SECURE"] = env_bool(
        "SESSION_COOKIE_SECURE",
        app_environment == "production",
    )
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PERMANENT_SESSION_LIFETIME"]=timedelta(minutes=int(os.getenv(
        "SESSION_LIFETIME_MINUTES",
        "30",
    )))
    app.config["SESSION_REFRESH_EACH_REQUEST"] = True
    app.config["WTF_CSRF_TIME_LIMIT"] = 3600
    app.config["WTF_CSRF_SSL_STRICT"] = app_environment == "production"
    app.config["RATELIMIT_STORAGE_URI"] = resolve_rate_limit_storage(
        app_environment,
        os.getenv("RATELIMIT_STORAGE_URI"),
    )

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    limiter.init_app(app)
    app.register_blueprint(api)

    @app.get("/api/health")
    def health():
        try:
            db.session.execute(text("SELECT 1"))
        except Exception as exc:
            db.session.rollback()
            app.logger.error("Health check failed; exception_type=%s", type(exc).__name__)
            return {
                "success": False,
                "database": "disconnected",
                "message": "Database is unavailable.",
            }, 503
        return {"success": True, "database": "connected"}

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        if request.path.startswith("/api/") or request.is_json:
            return {
                "success": False,
                "message": "Security token is missing or expired. Refresh and try again.",
            }, 400
        session.pop("csrf_token", None)
        flash("Your session has expired. Please refresh the page and try again.", "warning")
        return redirect(url_for("login"))

    @app.before_request
    def refresh_session_state():
        # The database state is canonical. Refresh the session copy on every
        # authenticated request so permission or name changes are reflected across pages
        # in already signed-in sessions without requiring another login.
        if session.get("user_id"):
            u = db.session.get(User, session["user_id"])
            if u and u.is_active:
                session["permissions"] = u.permissions_json or {}
                session["role"] = u.role
                session["first_name"] = u.first_name
                session["last_name"] = u.last_name
            setting = Setting.query.filter_by(key="mill_name").first()
            session["mill_name"] = setting.value if setting else "Rice Mill Management System"

    @app.context_processor
    def inject_permissions():
        def can_access_account_settings():
            uid = session.get("user_id")
            if not uid:
                return False
            u = db.session.get(User, uid)
            if not u or not u.is_active:
                return False
            if u.role == "admin":
                return True
            perms = u.permissions_json if isinstance(u.permissions_json, dict) else {}
            return perms.get("account_settings") is True or perms.get("accountSettings") is True
        return {"can_access_account_settings": can_access_account_settings}

    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        if isinstance(error, HTTPException):
            if request.path.startswith("/api/"):
                return {
                    "success": False,
                    "message": error.description or "API request failed.",
                }, error.code
            return error
        if app.testing:
            raise error
        app.logger.error("Unhandled request failure; exception_type=%s", type(error).__name__)
        if request.path.startswith("/api/"):
            return {"success": False, "message": "An unexpected server error occurred."}, 500
        return "An unexpected server error occurred.", 500

    @app.route("/", methods=["GET", "POST"])
    @app.route("/login", methods=["GET", "POST"])
    @limiter.limit("10 per minute", methods=["POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            client_key = f"{request.remote_addr}:{username.lower()}"
            is_locked, remaining = check_login_lockout(client_key)
            if is_locked:
                mins = max(1, (remaining + 59) // 60)
                flash(
                    f"Too many failed login attempts. Please try again after {mins} minute{'s' if mins!=1 else ''}.",
                    "error",
                )
                return redirect(url_for("login"))
            user = User.query.filter_by(username=username).first()
            if user and user.is_active and check_password_hash(user.password_hash, password):
                clear_failed_login(client_key)
                session.clear()
                # Rotate the CSRF session seed together with the authenticated
                # session so the next rendered page has a token bound to it.
                session["csrf_token"] = secrets.token_hex(32)
                session.permanent = True
                session["user_id"] = user.id
                session["username"] = user.username
                session["first_name"] = user.first_name
                session["last_name"] = user.last_name
                session["role"] = user.role
                session["permissions"] = user.permissions_json or {}
                session["auth_version"] = user.auth_version or 0
                setting = Setting.query.filter_by(key="mill_name").first()
                session["mill_name"] = setting.value if setting else "Rice Mill Management System"
                flash("Login successful.", "success")
                if user.role == "admin":
                    return redirect(url_for("dashboard"))
                allowed = (user.permissions_json or {}).get("tabs", [])
                page_routes = [
                    ("Dashboard", "dashboard"),
                    ("Paddy Purchase", "paddy_purchase"),
                    ("Sales & Invoicing", "sales"),
                    ("Inventory Management", "inventory"),
                    ("Production Management", "production"),
                    ("Customer Management", "customer"),
                    ("Payments & Receivables", "payments"),
                ]
                for label, endpoint in page_routes:
                    if label in allowed:
                        return redirect(url_for(endpoint))
                flash(
                    "Your account has no module access yet. Please contact the administrator.",
                    "error",
                )
                return redirect(url_for("profile"))
            record_failed_login(client_key)
            flash("Invalid username or password!", "error")
            return redirect(url_for("login"))
        return render_template("login.html")

    @app.route("/register", methods=["GET", "POST"])
    @limiter.limit("5 per hour", methods=["POST"])
    def register():
        if request.method == "POST":
            # Initial owner registration only. Employee accounts are created in Administration.
            if User.query.filter_by(role="admin").first():
                flash(
                    "The rice mill is already registered. Please contact the administrator.",
                    "error",
                )
                return redirect(url_for("login"))
            d = request.form
            field_errors = {}
            mill_name = d.get("mill_name", "").strip()
            if not mill_name or len(mill_name) > 120:
                field_errors["mill_name"] = "Rice Mill Name is required and must be 120 characters or fewer."

            first_name = d.get("first_name", "").strip()
            ok_f, err_f, clean_first = validate_person_name(first_name, "First Name", required=True)
            if not ok_f:
                field_errors["first_name"] = err_f

            last_name = d.get("last_name", "").strip()
            ok_l, err_l, clean_last = validate_person_name(last_name, "Last Name", required=True)
            if not ok_l:
                field_errors["last_name"] = err_l

            username = d.get("username", "").strip()
            ok_u, err_u, clean_user = validate_username(username, required=True)
            if not ok_u:
                field_errors["username"] = err_u
            elif User.query.filter_by(username=clean_user).first():
                field_errors["username"] = "This username is already in use."

            email = d.get("email", "").strip()
            ok_e, err_e, clean_email = validate_and_normalize_email(email, required=True)
            if not ok_e:
                field_errors["email"] = err_e
            elif clean_email and User.query.filter_by(email=clean_email).first():
                field_errors["email"] = "This email address is already in use."

            password = d.get("password", "")
            confirm_password = d.get("confirm_password", "")
            ok_p, err_p = validate_password_complexity(password)
            if not ok_p:
                field_errors["password"] = err_p
            if password != confirm_password:
                field_errors["confirm_password"] = "Passwords do not match."

            if field_errors:
                flash("Please correct the validation errors below.", "error")
                return render_template("register.html", form_values=d, errors=field_errors)

            try:
                u = User(
                    username=clean_user,
                    password_hash=generate_password_hash(password),
                    first_name=clean_first,
                    last_name=clean_last,
                    email=clean_email,
                    role="admin",
                )
                db.session.add(u)
                db.session.flush()
                s = Setting.query.filter_by(key="mill_name").first()
                if not s:
                    db.session.add(Setting(key="mill_name", value=mill_name or "Rice Mill"))
                db.session.commit()
                flash("Rice mill registered. Please login.", "success")
                return redirect(url_for("login"))
            except Exception:
                db.session.rollback()
                flash("Registration failed. Username or email may already exist.", "error")
                return render_template("register.html", form_values=d, errors=field_errors)
        return render_template("register.html", form_values={}, errors={})

    @app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password():
        if request.method == "POST":
            flash("Enable JavaScript to complete secure password recovery.", "error")
            return redirect(url_for("forgot_password"))
        return render_template("forgot_password.html")

    @app.route("/verify-otp", methods=["GET", "POST"])
    def verify_otp():
        if request.method == "POST":
            flash("Enable JavaScript to verify the recovery code.", "error")
            return redirect(url_for("forgot_password"))
        return render_template("verify_otp.html")

    @app.route("/reset-password", methods=["GET", "POST"])
    def reset_password():
        if request.method == "POST":
            flash("Enable JavaScript to complete the password reset.", "error")
            return redirect(url_for("forgot_password"))
        return render_template("reset_password.html")

    @app.get("/dashboard")
    @login_required
    @page_access_required("Dashboard")
    def dashboard():
        return render_template("dashboard.html", active_page="dashboard")

    @app.get("/paddy-purchase")
    @login_required
    @page_access_required("Paddy Purchase")
    def paddy_purchase():
        return render_template("purchase.html", active_page="purchase")

    @app.get("/paddy-purchase/<int:purchase_id>/bill")
    @login_required
    @page_access_required("Paddy Purchase")
    def paddy_purchase_bill(purchase_id):
        purchase = db.session.get(Purchase, purchase_id)
        if not purchase:
            abort(404)
        bill_purchase = SimpleNamespace(
            **purchase_effective_values(purchase),
            purchase_no=purchase.purchase_no,
            payment_method=purchase.payment_method,
        )
        return render_template(
            "purchase_bill.html",
            purchase=bill_purchase,
            mill_name=session.get("mill_name", "RICE MILL NAME"),
            active_page="purchase",
        )

    @app.get("/sales")
    @login_required
    @page_access_required("Sales & Invoicing")
    def sales():
        return render_template("sales.html", active_page="sales")

    @app.get("/return-refund-preview/<int:return_id>")
    @login_required
    @page_access_required("Sales & Invoicing")
    def return_refund_preview_page(return_id):
        return render_template(
            "return_refund_preview.html",
            return_id=return_id,
            mill_name=session.get("mill_name", "Rice Mill"),
            active_page="sales",
        )

    @app.get("/sales-bill")
    @login_required
    @page_access_required("Sales & Invoicing")
    def sales_bill():
        return render_template("sales_bill.html")

    @app.get("/combined-transaction/<document_no>")
    @login_required
    @page_access_required("Sales & Invoicing")
    def combined_transaction_document(document_no):
        document = CombinedTransactionDocument.query.filter_by(document_no=document_no).first_or_404()
        customer = resolve_snapshot_customer(document.snapshot)
        return render_template(
            "combined_transaction.html",
            document=normalize_transaction_snapshot(document.snapshot, customer),
        )

    @app.get("/return-receipt/<document_no>")
    @login_required
    @page_access_required("Sales & Invoicing")
    def customer_return_receipt(document_no):
        document = CombinedTransactionDocument.query.filter_by(
            document_no=document_no,
            is_final=True,
        ).first_or_404()
        snapshot = document.snapshot or {}
        ret = db.session.get(SalesReturn, document.return_id)
        sale = db.session.get(Sale, ret.sale_id) if ret else None
        if not ret or not sale:
            abort(404)
        customer = db.session.get(Customer, sale.customer_id) if sale.customer_id else None
        items = []
        for returned in ret.items:
            original = db.session.get(SaleItem, returned.sale_item_id)
            if original:
                items.append({
                    "product": original.product_name,
                    "variety": original.variety,
                    "quantity": returned.quantity,
                    "unit": original.unit,
                    "packageSize": original.package_size_kg,
                    "price": original.price_per_kg,
                    "value": (Decimal(str(returned.total_kg)) * Decimal(str(original.price_per_kg))).quantize(Decimal("0.01")),
                })
        settlements = snapshot.get("settlements", [])
        settlement_type = "Item Exchange" if any(x.get("type") == "exchange" for x in settlements) else "Cash Refund"
        auto_print = (settlement_type == "Cash Refund" and request.args.get("autoPrint") == "1")
        return_after_print = (auto_print and request.args.get("returnToSales") == "1")
        return render_template(
            "customer_return_receipt.html",
            mill_name=session.get("mill_name", "RICE MILL"),
            customer_name= (customer.full_name if customer and hasattr(
                customer,
                "full_name",
            ) else sale.customer_name or "Retail Customer"),
            invoice_no=sale.invoice_no,
            return_no=ret.return_no,
            document_no=document.document_no,
            date=document.created_at,
            items=items,
            settlements=settlements,
            return_value=ret.settlement_amount or 0,
            settlement_type=settlement_type,
            auto_print=auto_print,
            return_after_print=return_after_print,
        )

    @app.get("/inventory")
    @login_required
    @page_access_required("Inventory Management")
    def inventory():
        return render_template("inventory.html", active_page="inventory")

    @app.get("/production")
    @login_required
    @page_access_required("Production Management")
    def production():
        return render_template("production.html", active_page="production")

    @app.get("/customer")
    @login_required
    @page_access_required("Customer Management")
    def customer():
        return render_template("customer.html", active_page="customer")

    @app.get("/payments")
    @login_required
    @page_access_required("Payments & Receivables")
    def payments():
        return render_template("payments.html", active_page="payments")

    @app.get("/reports")
    @admin_required
    def reports():
        return render_template("reports.html", active_page="reports")

    @app.get("/administration")
    @admin_required
    def administration():
        return render_template("administration.html", active_page="administration")

    @app.get("/profile")
    @login_required
    def profile():
        return render_template("profile.html", active_page="profile", profile=profile_data())

    @app.get("/account-settings")
    @account_settings_required
    def account_settings():
        return render_template(
            "account_settings.html",
            active_page="account-settings",
            profile=profile_data(),
            form_values={},
        )

    @app.post("/update-my-profile")
    @account_settings_required
    def update_my_profile():
        u = db.session.get(User, session["user_id"])
        first = request.form.get("first_name", "").strip()
        last = request.form.get("last_name", "").strip()
        mill_name = request.form.get("mill_name", "").strip()
        email = request.form.get("email", "").strip()
        contact_number = request.form.get("contact_number", "").strip()
        current_profile = profile_data()
        effective_mill_name = mill_name if u.role == "admin" else current_profile.get(
            "mill_name",
            "",
        )
        form_values = {
            "mill_name": effective_mill_name,
            "first_name": first,
            "last_name": last,
            "email": email,
            "contact_number": contact_number,
        }

        if u.role == "admin":
            if not mill_name or len(mill_name) > 120:
                flash("Rice Mill Name is required and must be 120 characters or fewer.", "error")
                return render_template(
                    "account_settings.html",
                    active_page="account-settings",
                    profile=current_profile,
                    form_values=form_values,
                )

        ok_f, err_f, clean_first = validate_person_name(first, "First Name", required=True)
        if not ok_f:
            flash(err_f, "error")
            return render_template(
                "account_settings.html",
                active_page="account-settings",
                profile=current_profile,
                form_values=form_values,
            )

        ok_l, err_l, clean_last = validate_person_name(last, "Last Name", required=True)
        if not ok_l:
            flash(err_l, "error")
            return render_template(
                "account_settings.html",
                active_page="account-settings",
                profile=current_profile,
                form_values=form_values,
            )

        ok_e, err_e, clean_email = validate_and_normalize_email(email, required=False)
        if not ok_e:
            flash(err_e, "error")
            return render_template(
                "account_settings.html",
                active_page="account-settings",
                profile=current_profile,
                form_values=form_values,
            )

        if clean_email and User.query.filter(User.email == clean_email, User.id != u.id).first():
            flash("This email address is already in use.", "error")
            return render_template(
                "account_settings.html",
                active_page="account-settings",
                profile=current_profile,
                form_values=form_values,
            )

        ok_p, err_p, clean_phone = validate_and_normalize_phone(contact_number, required=False)
        if not ok_p:
            flash(err_p, "error")
            return render_template(
                "account_settings.html",
                active_page="account-settings",
                profile=current_profile,
                form_values=form_values,
            )

        u.first_name = clean_first
        u.last_name = clean_last
        u.email = clean_email
        u.contact_number = clean_phone
        if u.role == "admin":
            setting = Setting.query.filter_by(key="mill_name").with_for_update().first()
            if setting is None:
                setting = Setting(key="mill_name", value=mill_name)
                db.session.add(setting)
                db.session.flush()
                mill_name_changed = True
            else:
                mill_name_changed = setting.value != mill_name
                setting.value = mill_name
            if mill_name_changed:
                db.session.add(AuditLog(
                    user_id=u.id,
                    action="update",
                    entity="setting",
                    entity_id=setting.id,
                    details={"key": "mill_name"},
                ))
            session["mill_name"] = mill_name
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("This email address is already in use.", "error")
            return render_template(
                "account_settings.html",
                active_page="account-settings",
                profile=profile_data(),
                form_values=form_values,
            )
        except Exception:
            db.session.rollback()
            flash(
                "Profile changes could not be saved. Please correct any issues and try again.",
                "error",
            )
            return render_template(
                "account_settings.html",
                active_page="account-settings",
                profile=profile_data(),
                form_values=form_values,
            )
        session["first_name"] = clean_first
        session["last_name"] = clean_last
        flash("Profile updated successfully.", "success")
        return redirect(url_for("account_settings"))

    @app.post("/change-my-password")
    @limiter.limit("5 per hour")
    @account_settings_required
    def change_my_password():
        u = db.session.get(User, session["user_id"])
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not check_password_hash(u.password_hash, current):
            flash("Current password is incorrect.", "error")
            return redirect(url_for("account_settings"))
        ok_p, err_p = validate_password_complexity(new)
        if not ok_p:
            flash(err_p, "error")
            return redirect(url_for("account_settings"))
        if new != confirm:
            flash("Passwords do not match.", "error")
            return redirect(url_for("account_settings"))
        u.password_hash = generate_password_hash(new)
        u.auth_version = (u.auth_version or 0) + 1
        session["auth_version"] = u.auth_version
        db.session.add(AuditLog(
            user_id=u.id,
            action="change_password",
            entity="user",
            entity_id=u.id,
            details={},
        ))
        db.session.commit()
        flash("Password changed successfully.", "success")
        return redirect(url_for("account_settings"))

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.cli.command("seed-initial-data")
    def seed_initial_data():
        """Explicitly seed an already-migrated, empty installation."""
        # Seed master data used by the initial UI. Admin can maintain these later.
        if MasterItem.query.count() == 0:
            defaults = {
                "rice_variety": ["Samba", "Keeri Samba", "Nadu", "Red Rice"],
                "paddy_type": ["Wet Paddy", "Dry Paddy"],
                "product": ["Red Rice Bran", "Other Bran"],
                "category": ["Bulk Rice", "Packed Rice", "Bran", "Other"],
                "package_size": ["10kg", "25kg", "50kg"],
            }
            for group, names in defaults.items():
                for name in names:
                    db.session.add(MasterItem(group_name=group, name=name))
            db.session.commit()

        # Seeding is explicit; normal application startup never creates tables or users.
        if not User.query.filter_by(role="admin").first():
            admin_user = os.getenv("ADMIN_USERNAME", "").strip()
            admin_password = os.getenv("ADMIN_PASSWORD", "")
            if not admin_user or len(admin_password) < 12 or admin_password == "ChangeMe123!":
                raise RuntimeError("Set ADMIN_USERNAME and a unique ADMIN_PASSWORD of at least 12 characters before seeding.")
            db.session.add(User(
                username=admin_user,
                password_hash=generate_password_hash(admin_password),
                first_name="Mill",
                last_name="Owner",
                role="admin",
            ))
            if not Setting.query.filter_by(key="mill_name").first():
                db.session.add(Setting(key="mill_name", value=os.getenv("MILL_NAME", "Rice Mill")))
            db.session.commit()
        else:
            db.session.commit()
        print("Initial master data and administrator seed completed where needed.")
    return app

app = create_app()

if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
