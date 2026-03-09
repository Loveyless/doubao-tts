from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request, Response

from service.errors import BadRequestError, ForbiddenError, ServiceUnavailableError, UnauthorizedError


ADMIN_SESSION_COOKIE = "tts_admin_session"
ADMIN_CSRF_COOKIE = "tts_admin_csrf"
ADMIN_SESSION_TTL_SECONDS = 12 * 60 * 60
MIN_PASSWORD_LENGTH = 8
API_KEY_PREFIX_LENGTH = 12


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _get_session_secret() -> str:
    secret = os.getenv("TTS_SESSION_SECRET", "").strip()
    if not secret:
        raise ServiceUnavailableError("TTS_SESSION_SECRET is not configured")
    return secret


def get_bootstrap_password() -> str:
    password = os.getenv("TTS_ADMIN_BOOTSTRAP_PASSWORD", "").strip()
    if not password:
        raise ServiceUnavailableError("TTS_ADMIN_BOOTSTRAP_PASSWORD is not configured")
    return password


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key() -> tuple[str, str]:
    raw_key = f"tts_{secrets.token_urlsafe(32)}"
    return raw_key, raw_key[:API_KEY_PREFIX_LENGTH]


def should_use_secure_cookies(request: Request) -> bool:
    explicit = os.getenv("TTS_SECURE_COOKIES", "").strip().lower()
    if explicit in {"1", "true", "yes", "on"}:
        return True
    if explicit in {"0", "false", "no", "off"}:
        return False

    forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
    if forwarded_proto:
        return forwarded_proto.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def set_csrf_cookie(response: Response, token: str, secure: bool) -> None:
    response.set_cookie(
        ADMIN_CSRF_COOKIE,
        token,
        httponly=False,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_admin_session(response: Response) -> None:
    response.delete_cookie(ADMIN_SESSION_COOKIE, path="/")


def get_or_create_csrf_token(request: Request) -> str:
    return request.cookies.get(ADMIN_CSRF_COOKIE) or generate_csrf_token()


def validate_csrf(request: Request) -> None:
    cookie_token = request.cookies.get(ADMIN_CSRF_COOKIE, "")
    header_token = request.headers.get("X-CSRF-Token", "")
    if not cookie_token or not header_token or not secrets.compare_digest(cookie_token, header_token):
        raise ForbiddenError("Missing or invalid CSRF token")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return f"pbkdf2_sha256$310000${_b64encode(salt)}${_b64encode(derived)}"


def verify_password(password: str, encoded_password: str) -> bool:
    try:
        algorithm, rounds, salt_encoded, hash_encoded = encoded_password.split("$", 3)
    except ValueError:
        return False

    if algorithm != "pbkdf2_sha256":
        return False

    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        _b64decode(salt_encoded),
        int(rounds),
    )
    return secrets.compare_digest(_b64encode(derived), hash_encoded)


def _sign_payload(payload: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return _b64encode(digest)


def create_admin_session_token() -> str:
    payload = {
        "sub": "admin",
        "exp": int((_utcnow() + timedelta(seconds=ADMIN_SESSION_TTL_SECONDS)).timestamp()),
    }
    encoded_payload = _b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    secret = _get_session_secret()
    signature = _sign_payload(encoded_payload.encode("utf-8"), secret)
    return f"{encoded_payload}.{signature}"


def read_admin_session(token: str) -> dict[str, Any]:
    try:
        encoded_payload, provided_signature = token.split(".", 1)
    except ValueError as exc:
        raise UnauthorizedError("Invalid admin session") from exc

    secret = _get_session_secret()
    expected_signature = _sign_payload(encoded_payload.encode("utf-8"), secret)
    if not secrets.compare_digest(expected_signature, provided_signature):
        raise UnauthorizedError("Invalid admin session")

    try:
        payload = json.loads(_b64decode(encoded_payload))
    except (ValueError, json.JSONDecodeError) as exc:
        raise UnauthorizedError("Invalid admin session") from exc

    if payload.get("sub") != "admin":
        raise UnauthorizedError("Invalid admin session")

    if int(payload.get("exp", 0)) <= int(_utcnow().timestamp()):
        raise UnauthorizedError("Admin session expired")

    return payload


def set_admin_session_cookie(response: Response, secure: bool) -> None:
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        create_admin_session_token(),
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
        max_age=ADMIN_SESSION_TTL_SECONDS,
    )


def validate_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise BadRequestError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")


def require_admin_session(request: Request) -> dict[str, Any]:
    token = request.cookies.get(ADMIN_SESSION_COOKIE, "")
    if not token:
        raise UnauthorizedError("Admin login required")
    return read_admin_session(token)
