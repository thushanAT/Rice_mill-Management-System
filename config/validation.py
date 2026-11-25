"""Reusable input validation and security utilities for Rice Mill Management System.

Enforces consistent validation across frontend and backend:
- Person names (English alphabetic characters, spaces, hyphens only).
- Usernames (minimum 4 chars, alphanumeric and underscore).
- Emails (standard format, normalized to lowercase).
- Contact numbers (Sri Lankan formats: 07XXXXXXXX, +947XXXXXXXX, and landlines).
- Passwords (minimum 8 chars, uppercase, lowercase, number, special character).
- Prices and quantities (positive numbers, valid decimals, business rules).
"""
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Tuple, Optional, Any

# Regex definitions
NAME_PATTERN = re.compile(r"^[A-Za-z]+(?:[ -][A-Za-z]+)*$")
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{4,50}$")
EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

# Sri Lankan phone number pattern
# Matches:
# - Local mobile: 07[0-9]{8} (10 digits)
# - International mobile: +947[0-9]{8} (12 chars) or 947[0-9]{8} (11 digits)
# - Local landlines: 0[1-9][0-9]{8} (10 digits) or +94[1-9][0-9]{8} (12 chars)
SL_PHONE_LOCAL_MOBILE = re.compile(r"^07[0-9]{8}$")
SL_PHONE_INTL_MOBILE = re.compile(r"^\+947[0-9]{8}$")
SL_PHONE_LOCAL_GENERAL = re.compile(r"^0[1-9][0-9]{8}$")
SL_PHONE_INTL_GENERAL = re.compile(r"^\+94[1-9][0-9]{8}$")

# Password complexity regexes
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128
PASSWORD_UPPERCASE = re.compile(r"[A-Z]")
PASSWORD_LOWERCASE = re.compile(r"[a-z]")
PASSWORD_NUMBER = re.compile(r"[0-9]")
PASSWORD_SPECIAL = re.compile(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>\/?`~]")


def validate_person_name(
    value: Any,
    field_name: str = "Name",
    required: bool = True,
    max_length: int = 100,
) -> Tuple[bool, str, str]:
    """Validate a person's first or last name.

    English alphabetic characters only (A-Z, a-z).
    Allows single spaces and hyphens between name parts.
    Rejects numbers and unsupported special characters.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            return False, f"{field_name} is required.", ""
        return True, "", ""
    if not isinstance(value, str):
        return False, f"{field_name} must be text.", ""

    cleaned = value.strip()
    if len(cleaned) > max_length:
        return False, f"{field_name} must be {max_length} characters or fewer.", ""
    if not NAME_PATTERN.match (cleaned):
        return False, f"Only alphabetic characters, spaces, and hyphens are allowed in {field_name}.", ""
    return True, "", cleaned


def validate_username(value: Any, required: bool = True) -> Tuple[bool, str, str]:
    """Validate username.

    Minimum 4 characters.
    Letters, numbers and underscores only.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            return False, "Username is required.", ""
        return True, "", ""
    if not isinstance(value, str):
        return False, "Username must be text.", ""

    cleaned = value.strip()
    if len(cleaned) < 4:
        return False, "Username must contain at least 4 characters.", ""
    if len(cleaned) > 50:
        return False, "Username must be 50 characters or fewer.", ""
    if not USERNAME_PATTERN.match (cleaned):
        return False, "Username can only contain letters, numbers, and underscores.", ""
    return True, "", cleaned


def validate_and_normalize_email(
    value: Any,
    required: bool = False,
) -> Tuple[bool, str, Optional[str]]:
    """Validate standard email format and normalize consistently.

    Normalizes to lowercase trimmed string, or None if empty.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            return False, "Email address is required.", None
        return True, "", None
    if not isinstance(value, str):
        return False, "Email address must be text.", None

    cleaned = value.strip().lower()
    if len(cleaned) > 160:
        return False, "Email address must be 160 characters or fewer.", None
    if not EMAIL_PATTERN.match (cleaned):
        return False, "Please enter a valid email address.", None
    return True, "", cleaned


def validate_and_normalize_phone(
    value: Any,
    required: bool = False,
) -> Tuple[bool, str, Optional[str]]:
    """Validate Sri Lankan phone number format and normalize consistently.

    Supports valid 07XXXXXXXX and +947XXXXXXXX formats, plus Sri Lankan landlines.
    Normalizes consistently to +94XXXXXXXXX format.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            return False, "Contact number is required.", None
        return True, "", None
    if not isinstance(value, str):
        return False, "Contact number must be text.", None

    cleaned = re.sub(r"[\s\-\(\)]", "", value.strip())
    if not cleaned:
        if required:
            return False, "Contact number is required.", None
        return True, "", None

    if len(cleaned) > 30:
        return False, "Contact number must be 30 characters or fewer.", None

    # Handle leading double zeros or un-prefixed 94
    if cleaned.startswith("0094"):
        cleaned = "+94" + cleaned[4:]
    elif cleaned.startswith("94") and len(cleaned) == 11:
        cleaned = "+94" + cleaned[2:]

    # Local format 0XXXXXXXXX (exactly 10 digits) -> +94XXXXXXXXX
    if cleaned.startswith("0") and len(cleaned) == 10 and cleaned.isdigit():
        return True, "", "+94" + cleaned[1:]

    # International format +94XXXXXXXXX (exactly 12 chars: +94 + 9 digits)
    if cleaned.startswith("+94") and len(cleaned) == 12 and cleaned[1:].isdigit():
        return True, "", cleaned

    # International without plus: 94XXXXXXXXX (exactly 11 digits) -> +94XXXXXXXXX
    if cleaned.startswith("94") and len(cleaned) == 11 and cleaned.isdigit():
        return True, "", "+" + cleaned

    return False, "Please enter a valid Sri Lankan phone number (e.g., 07XXXXXXXX or +947XXXXXXXX).", None


def validate_password_complexity(password: Any) -> Tuple[bool, str]:
    """Validate password complexity.

    Requirements:
    - Minimum 8 characters.
    - At least one uppercase letter.
    - At least one lowercase letter.
    - At least one number.
    - At least one special character.
    """
    if not isinstance(password, str) or not password:
        return False, "Password is required."
    if len(password) < PASSWORD_MIN_LENGTH:
        return False, "Password must contain at least 8 characters."
    if len(password) > PASSWORD_MAX_LENGTH:
        return False, f"Password must be {PASSWORD_MAX_LENGTH} characters or fewer."
    if not PASSWORD_UPPERCASE.search(password):
        return False, "Password must include at least one uppercase letter (A-Z)."
    if not PASSWORD_LOWERCASE.search(password):
        return False, "Password must include at least one lowercase letter (a-z)."
    if not PASSWORD_NUMBER.search(password):
        return False, "Password must include at least one number (0-9)."
    if not PASSWORD_SPECIAL.search(password):
        return False, "Password must include at least one special character (!@#$%^&*...)."
    return True, ""


def calculate_password_strength(password: str) -> dict:
    """Calculate password strength score and feedback.

    Returns dict with: score (0-4), status ('weak', 'medium', 'strong'),
    hasUpper, hasLower, hasNumber, hasSpecial, hasLength.
    """
    if not isinstance(password, str):
        return {"score": 0, "status": "weak", "message": "Password is required."}

    has_len = len(password) >= 8
    has_upper = bool(PASSWORD_UPPERCASE.search(password))
    has_lower = bool(PASSWORD_LOWERCASE.search(password))
    has_number = bool(PASSWORD_NUMBER.search(password))
    has_special = bool(PASSWORD_SPECIAL.search(password))

    criteria_met = sum([has_upper, has_lower, has_number, has_special])

    if not has_len or criteria_met < 2:
        status = "weak"
        score = 1 if has_len or criteria_met > 0 else 0
        message = "Weak password"
    elif criteria_met < 4 or len(password) < 10:
        status = "medium"
        score = 2
        message = "Medium strength"
    else:
        status = "strong"
        score = 3
        message = "Strong password"

    return {
        "score": score,
        "status": status,
        "message": message,
        "hasLength": has_len,
        "hasUpper": has_upper,
        "hasLower": has_lower,
        "hasNumber": has_number,
        "hasSpecial": has_special,
    }


def validate_positive_number(
    value: Any,
    field_name: str = "Price",
    allow_zero: bool = False,
    max_decimals: int = 2,
) -> Tuple[bool, str, Optional[Decimal]]:
    """Validate positive integer or decimal numbers.

    Rejects negative values, invalid characters, and malformed decimals.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return False, f"{field_name} is required.", None

    try:
        n = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return False, f"{field_name} must be a valid number.", None

    if not n.is_finite():
        return False, f"{field_name} must be a valid finite number.", None

    if n < Decimal("0"):
        return False, f"{field_name} cannot be negative.", None

    if not allow_zero and n == Decimal("0"):
        return False, f"{field_name} must be greater than zero.", None

    # Check decimal places
    exponent = abs(n.as_tuple().exponent) if n.as_tuple().exponent < 0 else 0
    if exponent > max_decimals:
        return False, f"{field_name} may have at most {max_decimals} decimal places.", None

    return True, "", n

# In-memory store for failed login attempts (tracked by username/ip)
_FAILED_LOGINS = {}
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION_SECONDS = 600  # 10 minutes
ATTEMPT_WINDOW_SECONDS = 600  # 10 minutes


def check_login_lockout(key: str) -> Tuple[bool, int]:
    """Check if key (username or IP) is locked out due to repeated failed logins.

    Returns (is_locked, remaining_seconds).
    """
    if not key:
        return False, 0
    now = time.time()
    record = _FAILED_LOGINS.get(key)
    if not record:
        return False, 0
    locked_until = record.get("locked_until", 0)
    if locked_until > now:
        return True, int(locked_until - now)
    return False, 0


def record_failed_login(key: str) -> bool:
    """Record a failed login attempt.

    Returns True if this attempt triggered a lockout.
    """
    if not key:
        return False
    now = time.time()
    record = _FAILED_LOGINS.get(key)
    if not record or (now - record.get("first_failed", 0)) > ATTEMPT_WINDOW_SECONDS:
        _FAILED_LOGINS[key] = {"count": 1, "first_failed": now, "locked_until": 0}
        return False

    record["count"] += 1
    if record["count"] >= MAX_FAILED_ATTEMPTS:
        record["locked_until"] = now + LOCKOUT_DURATION_SECONDS
        return True
    return False


def clear_failed_login(key: str):
    """Reset failed login count upon successful authentication."""
    if key:
        _FAILED_LOGINS.pop(key, None)
