from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from doubao_tts import build_cookie_string
from service.config import ServiceConfig
from service.db import (
    ACCOUNT_STATUS_COOLDOWN,
    ACCOUNT_STATUS_HEALTHY,
    fetch_doubao_account_by_id,
    list_available_doubao_accounts,
    mark_doubao_account_failure,
    mark_doubao_account_success,
    seed_initial_doubao_account,
)
from service.errors import BadRequestError, ServiceHTTPError, ServiceUnavailableError, UpstreamBadGatewayError

ACCOUNT_COOLDOWN_SECONDS = 300


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _to_iso(value: datetime) -> str:
    return value.isoformat()


def ensure_seed_account(cookie: str) -> int | None:
    return seed_initial_doubao_account(cookie)


def build_account_cookie(account: dict[str, Any]) -> str:
    return build_cookie_string(
        {
            "sessionid": str(account["sessionid"]).strip(),
            "sid_guard": str(account["sid_guard"]).strip(),
            "uid_tt": str(account["uid_tt"]).strip(),
        }
    )


def config_with_account_cookie(config: ServiceConfig, account: dict[str, Any]) -> ServiceConfig:
    return replace(config, cookie=build_account_cookie(account))


def get_account_or_raise(account_id: int) -> dict[str, Any]:
    account = fetch_doubao_account_by_id(account_id)
    if account is None:
        raise BadRequestError(f"Doubao account {account_id} does not exist")
    return account


def select_account(*, exclude_ids: list[int] | tuple[int, ...] | None = None) -> dict[str, Any]:
    accounts = list_available_doubao_accounts(exclude_ids=exclude_ids, limit=1)
    if not accounts:
        raise ServiceUnavailableError("No healthy Doubao accounts configured")
    return accounts[0]


def is_retryable_account_error(error: ServiceHTTPError) -> bool:
    return isinstance(error, UpstreamBadGatewayError)


def mark_account_attempt_success(account_id: int) -> None:
    mark_doubao_account_success(account_id)


def mark_account_attempt_failure(account_id: int, error: ServiceHTTPError) -> None:
    if is_retryable_account_error(error):
        cooldown_until = _to_iso(_utcnow() + timedelta(seconds=ACCOUNT_COOLDOWN_SECONDS))
        mark_doubao_account_failure(
            account_id,
            error.detail,
            cooldown_until=cooldown_until,
            status=ACCOUNT_STATUS_COOLDOWN,
        )
        return

    mark_doubao_account_failure(
        account_id,
        error.detail,
        cooldown_until=None,
        status=ACCOUNT_STATUS_HEALTHY,
    )
