"""Reusable Gmail SMTP delivery service with safe local diagnostics."""

from email.message import EmailMessage
from email.utils import parseaddr
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import re
import smtplib
import ssl
import sys


SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
DIAGNOSTIC_LOG_PATH = Path(__file__).resolve().parent / "logs" / "smtp-diagnostics.log"
_DIAGNOSTIC_STAGES = {
    "logger_initialization",
    "request",
    "service_invocation",
    "configuration",
    "message_preparation",
    "connection",
    "starttls",
    "authentication",
    "message_submission",
    "delivery",
}
_diagnostic_logger = logging.getLogger("email_service.diagnostics")
_diagnostic_logger.setLevel(logging.INFO)
_diagnostic_logger.propagate = False


def _safe_exception_class(exception_class):
    if not exception_class:
        return "None"
    value = exception_class if isinstance(exception_class, str) else getattr(
        exception_class,
        "__name__",
        "",
    )
    return value if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) else "UnknownException"


def _fallback_write(record):
    """Write a sanitized record to stderr if the local file cannot be used."""
    try:
        timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        stage = record.stage if record.stage in _DIAGNOSTIC_STAGES else "unknown"
        exception_class = _safe_exception_class(record.exception_class)
        sys.stderr.write(f"{timestamp} stage={stage} exception_class={exception_class}\n")
        sys.stderr.flush()
    except Exception:
        # A broken stderr must not leak an exception or interrupt email handling.
        pass


class _SafeFileHandler(logging.FileHandler):
    def handleError(self, record):
        _fallback_write(record)


def initialize_diagnostic_logger(log_path=DIAGNOSTIC_LOG_PATH):
    """Create the absolute-path log destination, falling back safely to stderr."""
    absolute_path = Path(log_path).resolve()
    for handler in list(_diagnostic_logger.handlers):
        _diagnostic_logger.removeHandler(handler)
        try:
            handler.close()
        except OSError as error:
            record = logging.LogRecord(
                _diagnostic_logger.name,
                logging.WARNING,
                "",
                0,
                "",
                (),
                None,
            )
            record.stage = "logger_initialization"
            record.exception_class = type(error).__name__
            _fallback_write(record)

    try:
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        handler = _SafeFileHandler(absolute_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s stage=%(stage)s exception_class=%(exception_class)s"))
        handler._smtp_diagnostic_path = str(absolute_path)
        _diagnostic_logger.addHandler(handler)
        return True
    except OSError as error:
        fallback = logging.StreamHandler(sys.stderr)
        fallback.setFormatter(logging.Formatter("%(asctime)s stage=%(stage)s exception_class=%(exception_class)s"))
        _diagnostic_logger.addHandler(fallback)
        _log_diagnostic("logger_initialization", type(error).__name__)
        return False


def _log_diagnostic(stage, exception_class=None):
    safe_stage = stage if stage in _DIAGNOSTIC_STAGES else "unknown"
    safe_class = _safe_exception_class(exception_class)
    try:
        _diagnostic_logger.info(
            "SMTP diagnostic",
            extra={"stage": safe_stage, "exception_class": safe_class},
        )
    except Exception:
        record = logging.LogRecord(_diagnostic_logger.name, logging.WARNING, "", 0, "", (), None)
        record.stage = safe_stage
        record.exception_class = safe_class
        _fallback_write(record)


def _smtp_stage(stage, operation):
    try:
        result = operation()
    except Exception as error:
        _log_diagnostic(stage, type(error).__name__)
        raise EmailDeliveryError("Email could not be delivered. Please try again later.") from None
    _log_diagnostic(stage)
    return result


class EmailServiceError(Exception):
    """Base error for safe, caller-facing email service failures."""


class EmailConfigurationError(EmailServiceError):
    """Raised when required SMTP configuration is absent or invalid."""


class EmailDeliveryError(EmailServiceError):
    """Raised when Gmail SMTP cannot accept a message."""


def _required_env(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise EmailConfigurationError(f"Email configuration is missing {name}.")
    return value


def _validated_address(value, label):
    if not isinstance(value, str) or not value or "\r" in value or "\n" in value:
        raise EmailConfigurationError(f"{label} must be a valid email address.")
    _, address = parseaddr(value)
    if address != value or "@" not in address:
        raise EmailConfigurationError(f"{label} must be a valid email address.")
    return address


class GmailSMTPEmailService:
    """Send plain-text or multipart email over Gmail STARTTLS on port 587."""

    def __init__(self, timeout=None):
        self.timeout = timeout

    def send_email(self, to_email, subject, text_body, html_body=None):
        _log_diagnostic("service_invocation")
        try:
            username = _validated_address(
                _required_env("GMAIL_SMTP_USERNAME"),
                "GMAIL_SMTP_USERNAME",
            )
            app_password = _required_env("GMAIL_SMTP_APP_PASSWORD")
            from_email = _validated_address(
                os.getenv("GMAIL_SMTP_FROM_EMAIL", "").strip() or username,
                "GMAIL_SMTP_FROM_EMAIL",
            )
            from_name = os.getenv("GMAIL_SMTP_FROM_NAME", "Rice Mill Management System").strip()
            if "\r" in from_name or "\n" in from_name:
                raise EmailConfigurationError("GMAIL_SMTP_FROM_NAME is invalid.")
        except EmailConfigurationError as error:
            _log_diagnostic("configuration", type(error).__name__)
            raise

        try:
            recipient = _validated_address(to_email, "Recipient")
            if not isinstance(
                subject,
                str,
            ) or not subject.strip() or "\r" in subject or "\n" in subject:
                raise ValueError("Email subject must be non-empty single-line text.")
            if not isinstance(text_body, str) or not text_body:
                raise ValueError("Email text body must be non-empty text.")
            if html_body is not None and not isinstance(html_body, str):
                raise ValueError("Email HTML body must be text.")

            message = EmailMessage()
            message["Subject"] = subject.strip()
            message["From"] = f"{from_name} <{from_email}>" if from_name else from_email
            message["To"] = recipient
            message.set_content(text_body)
            if html_body is not None:
                message.add_alternative(html_body, subtype="html")

            timeout = self.timeout
            if timeout is None:
                try:
                    timeout = float(os.getenv("GMAIL_SMTP_TIMEOUT_SECONDS", "10"))
                except ValueError:
                    raise EmailConfigurationError("GMAIL_SMTP_TIMEOUT_SECONDS must be a positive number.") from None
            if timeout <= 0:
                raise EmailConfigurationError("GMAIL_SMTP_TIMEOUT_SECONDS must be a positive number.")
        except Exception as error:
            _log_diagnostic("message_preparation", type(error).__name__)
            raise

        smtp = None
        try:
            def connect():
                connection = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=timeout)
                connection.ehlo()
                return connection

            def starttls():
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()

            smtp = _smtp_stage("connection", connect)
            with smtp:
                _smtp_stage("starttls", starttls)
                _smtp_stage("authentication", lambda: smtp.login(username, app_password))
                refused = _smtp_stage("message_submission", lambda: smtp.send_message(message))
                if refused:
                    _log_diagnostic("message_submission", "SMTPRecipientsRefused")
                    raise EmailDeliveryError("Gmail SMTP did not accept the recipient.")
        except EmailDeliveryError as error:
            _log_diagnostic("delivery", type(error).__name__)
            raise
        except (smtplib.SMTPException, OSError) as error:
            _log_diagnostic("delivery", type(error).__name__)
            raise EmailDeliveryError("Email could not be delivered. Please try again later.") from None
        _log_diagnostic("delivery")


def send_email(to_email, subject, text_body, html_body=None):
    """Convenience function for application code that does not need an instance."""
    return GmailSMTPEmailService().send_email(to_email, subject, text_body, html_body)

# Initialize eagerly so the absolute log file exists before any mail request.
initialize_diagnostic_logger()

# Used by the recovery route for a privacy-safe entry before the SMTP call.
log_diagnostic = _log_diagnostic
