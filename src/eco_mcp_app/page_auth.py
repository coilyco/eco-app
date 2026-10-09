"""Page-password gate for the URL-only surfaces (/replay, /social).

A deliberately lightweight gate (eco-app#73): the SPA prompts for a password
before it shows /replay and /social, and posts the answer to `POST /page-auth`
here. This is **not** a security boundary — the underlying JSON APIs
(/replay/api) stay public — just a "don't wander in,
don't index" speed bump for two surfaces that are otherwise URL-only.

The password is a throwaway random string. Prefer the `ECO_PAGE_PASSWORD` env
var (local dev, and the k3s deploy via ExternalSecret). Fall back to SSM
`/coilyco/eco-app/page-password` in `us-east-1` via boto3, then the `aws` CLI —
mirroring the admin-key lookup in `species.py`. When nothing is configured
anywhere the gate stays open, so a bare local checkout is never locked out.
"""

from __future__ import annotations

import hmac
import os

_ENV_VAR = "ECO_PAGE_PASSWORD"
_SSM_PARAM_NAME = "/coilyco/eco-app/page-password"
_SSM_REGION = "us-east-1"

# Cached for the life of the process — SSM round-trips are slow and the
# password doesn't rotate mid-request. `None` means "not looked up yet or
# nothing available"; the env var is re-read every call so tests and hot
# reload see changes without a restart.
_PASSWORD_CACHE: str | None = None
_LOOKED_UP = False


def get_page_password() -> str | None:
    """The configured page password, or None when none is set anywhere.

    Prefers `ECO_PAGE_PASSWORD`; falls back to a one-time SSM lookup.
    """
    env = os.environ.get(_ENV_VAR)
    if env:
        return env
    global _PASSWORD_CACHE, _LOOKED_UP
    if _LOOKED_UP:
        return _PASSWORD_CACHE
    _LOOKED_UP = True
    _PASSWORD_CACHE = _fetch_from_ssm()
    return _PASSWORD_CACHE


def password_required() -> bool:
    """True when a password is configured, so the gate should engage."""
    return bool(get_page_password())


def verify_password(candidate: str) -> bool:
    """Constant-time compare `candidate` against the configured password.

    False when no password is configured (nothing to match against) or on
    mismatch. `hmac.compare_digest` keeps the check timing-safe.
    """
    secret = get_page_password()
    if not secret:
        return False
    return hmac.compare_digest(candidate, secret)


def _fetch_from_ssm() -> str | None:
    # boto3 path — only taken if boto3 is installed (it isn't in the slim prod
    # image; the env var path covers k3s). Mirrors species._fetch_admin_key.
    try:
        import boto3  # type: ignore[import-not-found]

        client = boto3.client("ssm", region_name=_SSM_REGION)
        resp = client.get_parameter(Name=_SSM_PARAM_NAME, WithDecryption=True)
        return str(resp["Parameter"]["Value"])
    except ImportError:
        pass
    except Exception:
        return None
    # AWS CLI fallback — zero runtime deps, uses whatever creds the caller has.
    # Primary path for local dev on this repo.
    import shutil
    import subprocess

    if not shutil.which("aws"):
        return None
    try:
        result = subprocess.run(
            [
                "aws",
                "ssm",
                "get-parameter",
                "--name",
                _SSM_PARAM_NAME,
                "--with-decryption",
                "--region",
                _SSM_REGION,
                "--query",
                "Parameter.Value",
                "--output",
                "text",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None
