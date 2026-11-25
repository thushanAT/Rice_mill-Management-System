"""Environment-aware Flask-Limiter storage selection."""

from urllib.parse import urlsplit


DEVELOPMENT_ENVS = {"development", "dev", "local"}
TEST_ENVS = {"test", "testing"}
PRODUCTION_ENVS = {"production", "prod"}
SHARED_STORAGE_SCHEMES = {"redis", "rediss", "memcached", "mongodb", "mongodb+srv"}


def resolve_app_environment(configured_environment, debug_enabled, running_as_main):
    """Resolve app mode, keeping imported/WSGI startup production-safe by default."""
    if configured_environment:
        environment = configured_environment.strip().lower()
    elif running_as_main or debug_enabled:
        environment = "development"
    else:
        environment = "production"

    if environment in DEVELOPMENT_ENVS:
        return "development"
    if environment in TEST_ENVS:
        return "testing"
    if environment in PRODUCTION_ENVS:
        return "production"
    raise RuntimeError("APP_ENV must be development, testing, or production.")


def resolve_rate_limit_storage(environment, configured_uri):
    """Return an active limiter backend or reject unsafe production config."""
    uri = (configured_uri or "").strip()
    if environment in {"development", "testing"}:
        # Memory storage is development/test-only; route-specific limits remain on.
        return uri or "memory://"

    scheme = urlsplit(uri).scheme.lower() if uri else ""
    if scheme not in SHARED_STORAGE_SCHEMES:
        raise RuntimeError(
            "Production requires RATELIMIT_STORAGE_URI to use a shared store "
            "(Redis, Memcached, or MongoDB); in-memory storage is development-only."
        )
    return uri
