"""Application timezone helpers. Database DateTime columns remain UTC-naive."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    COLOMBO = ZoneInfo("Asia/Colombo")
except ZoneInfoNotFoundError:  # Windows Python installs may omit the tzdata package.
    COLOMBO = timezone(timedelta(hours=5, minutes=30), name="Asia/Colombo")

UTC = timezone.utc


def utc_now_naive():
    """UTC timestamp suitable for existing MySQL DATETIME columns."""
    return datetime.now(UTC).replace(tzinfo=None)


def colombo_now():
    return datetime.now(COLOMBO)


def colombo_date():
    return colombo_now().date()


def as_colombo(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(COLOMBO)


def colombo_iso(value):
    local = as_colombo(value)
    return local.isoformat(timespec="seconds") if local else None
