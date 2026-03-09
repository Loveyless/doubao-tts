from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from doubao_tts import normalize_cookie, parse_cookie_string


DEFAULT_SQLITE_PATH = Path.home() / ".doubao-tts" / "tts_service.db"
VALID_SQLITE_JOURNAL_MODES = {"DELETE", "TRUNCATE", "PERSIST", "MEMORY", "WAL", "OFF"}
DEFAULT_ENV_ACCOUNT_NAME = "seed-from-env"
ACCOUNT_STATUS_HEALTHY = "healthy"
ACCOUNT_STATUS_COOLDOWN = "cooldown"
ACCOUNT_STATUS_DISABLED = "disabled"
ACCOUNT_STATUS_INVALID = "invalid"


class DatabaseError(RuntimeError):
    """Raised when SQLite operations fail."""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_sqlite_path() -> str:
    configured = os.getenv("TTS_SQLITE_PATH", "").strip()
    if not configured:
        return str(DEFAULT_SQLITE_PATH)
    return configured


def _ensure_parent_directory(path: str) -> None:
    if path == ":memory:":
        return
    Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def get_sqlite_journal_mode() -> str:
    mode = os.getenv("TTS_SQLITE_JOURNAL_MODE", "WAL").strip().upper() or "WAL"
    if mode not in VALID_SQLITE_JOURNAL_MODES:
        raise DatabaseError(f"Unsupported SQLite journal mode: {mode}")
    return mode


def _normalize_service_settings_payload(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "default_speaker": settings["default_speaker"],
        "default_format": settings["default_format"],
        "retry_on_block": int(bool(settings["retry_on_block"])),
        "retry_max_retries": int(settings["retry_max_retries"]),
        "retry_backoff_seconds": float(settings["retry_backoff_seconds"]),
        "retry_backoff_multiplier": float(settings["retry_backoff_multiplier"]),
        "retry_backoff_jitter_ratio": float(settings["retry_backoff_jitter_ratio"]),
        "request_timeout_seconds": float(settings["request_timeout_seconds"]),
        "max_concurrency": int(settings["max_concurrency"]),
        "enable_streaming": int(bool(settings.get("enable_streaming", True))),
        "allow_request_override": int(bool(settings.get("allow_request_override", True))),
        "report_retention_days": int(settings.get("report_retention_days", 30)),
    }


def _normalize_account_payload(
    name: str,
    sessionid: str,
    sid_guard: str,
    uid_tt: str,
) -> dict[str, str]:
    normalized_name = name.strip()
    if not normalized_name:
        raise DatabaseError("Account name must not be blank")

    normalized_cookie, missing_fields = normalize_cookie(
        f"sessionid={sessionid}; sid_guard={sid_guard}; uid_tt={uid_tt}"
    )
    if missing_fields:
        raise DatabaseError(f"Cookie 缺少必需字段: {', '.join(missing_fields)}")

    cookie_items = parse_cookie_string(normalized_cookie)
    return {
        "name": normalized_name,
        "sessionid": cookie_items["sessionid"],
        "sid_guard": cookie_items["sid_guard"],
        "uid_tt": cookie_items["uid_tt"],
    }


@contextmanager
def get_db_connection(path: str | None = None) -> Iterator[sqlite3.Connection]:
    resolved_path = path or get_sqlite_path()
    try:
        _ensure_parent_directory(resolved_path)
        connection = sqlite3.connect(resolved_path)
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to open SQLite database: {exc}") from exc

    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def initialize_database(path: str | None = None, seed_service_settings: dict[str, Any] | None = None) -> str:
    resolved_path = path or get_sqlite_path()
    now = utcnow_iso()
    schema = """
    CREATE TABLE IF NOT EXISTS admin_settings (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        password_hash TEXT,
        setup_completed INTEGER NOT NULL DEFAULT 0,
        password_updated_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS service_settings (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        default_speaker TEXT NOT NULL,
        default_format TEXT NOT NULL,
        retry_on_block INTEGER NOT NULL DEFAULT 0,
        retry_max_retries INTEGER NOT NULL DEFAULT 0,
        retry_backoff_seconds REAL NOT NULL DEFAULT 1.0,
        retry_backoff_multiplier REAL NOT NULL DEFAULT 2.0,
        retry_backoff_jitter_ratio REAL NOT NULL DEFAULT 0.0,
        request_timeout_seconds REAL NOT NULL DEFAULT 35.0,
        max_concurrency INTEGER NOT NULL DEFAULT 4,
        enable_streaming INTEGER NOT NULL DEFAULT 1,
        allow_request_override INTEGER NOT NULL DEFAULT 1,
        report_retention_days INTEGER NOT NULL DEFAULT 30,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS doubao_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        sessionid TEXT NOT NULL,
        sid_guard TEXT NOT NULL,
        uid_tt TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'healthy',
        cooldown_until TEXT,
        last_error TEXT,
        last_used_at TEXT,
        success_count INTEGER NOT NULL DEFAULT 0,
        failure_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS api_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        key_prefix TEXT NOT NULL,
        key_hash TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        last_used_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS request_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id TEXT NOT NULL,
        api_key_id INTEGER,
        doubao_account_id INTEGER,
        endpoint TEXT NOT NULL,
        speaker TEXT,
        format TEXT,
        text_chars INTEGER NOT NULL DEFAULT 0,
        status_code INTEGER NOT NULL,
        success INTEGER NOT NULL DEFAULT 0,
        latency_ms INTEGER NOT NULL DEFAULT 0,
        error_type TEXT,
        error_detail TEXT,
        created_at TEXT NOT NULL
    );
    """

    try:
        with get_db_connection(resolved_path) as connection:
            connection.execute(f"PRAGMA journal_mode={get_sqlite_journal_mode()}")
            connection.executescript(schema)
            connection.execute(
                """
                INSERT OR IGNORE INTO admin_settings (id, setup_completed, created_at, updated_at)
                VALUES (1, 0, ?, ?)
                """,
                (now, now),
            )
            if seed_service_settings is not None:
                payload = _normalize_service_settings_payload(seed_service_settings)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO service_settings (
                        id,
                        default_speaker,
                        default_format,
                        retry_on_block,
                        retry_max_retries,
                        retry_backoff_seconds,
                        retry_backoff_multiplier,
                        retry_backoff_jitter_ratio,
                        request_timeout_seconds,
                        max_concurrency,
                        enable_streaming,
                        allow_request_override,
                        report_retention_days,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        payload["default_speaker"],
                        payload["default_format"],
                        payload["retry_on_block"],
                        payload["retry_max_retries"],
                        payload["retry_backoff_seconds"],
                        payload["retry_backoff_multiplier"],
                        payload["retry_backoff_jitter_ratio"],
                        payload["request_timeout_seconds"],
                        payload["max_concurrency"],
                        payload["enable_streaming"],
                        payload["allow_request_override"],
                        payload["report_retention_days"],
                        now,
                        now,
                    ),
                )
            connection.commit()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to initialize SQLite database: {exc}") from exc

    return resolved_path


def fetch_admin_settings(path: str | None = None) -> dict[str, Any]:
    initialize_database(path)
    try:
        with get_db_connection(path) as connection:
            row = connection.execute("SELECT * FROM admin_settings WHERE id = 1").fetchone()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to read admin settings: {exc}") from exc

    return dict(row) if row is not None else {}


def update_admin_password_hash(password_hash: str, path: str | None = None) -> None:
    initialize_database(path)
    now = utcnow_iso()
    try:
        with get_db_connection(path) as connection:
            connection.execute(
                """
                UPDATE admin_settings
                SET password_hash = ?, setup_completed = 1, password_updated_at = ?, updated_at = ?
                WHERE id = 1
                """,
                (password_hash, now, now),
            )
            connection.commit()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to update admin password: {exc}") from exc


def fetch_service_settings(path: str | None = None) -> dict[str, Any]:
    initialize_database(path)
    try:
        with get_db_connection(path) as connection:
            row = connection.execute("SELECT * FROM service_settings WHERE id = 1").fetchone()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to read service settings: {exc}") from exc

    return dict(row) if row is not None else {}


def save_initial_service_settings(settings: dict[str, Any], path: str | None = None) -> None:
    initialize_database(path, seed_service_settings=settings)
    now = utcnow_iso()
    payload = _normalize_service_settings_payload(settings)
    try:
        with get_db_connection(path) as connection:
            connection.execute(
                """
                UPDATE service_settings
                SET default_speaker = ?,
                    default_format = ?,
                    retry_on_block = ?,
                    retry_max_retries = ?,
                    retry_backoff_seconds = ?,
                    retry_backoff_multiplier = ?,
                    retry_backoff_jitter_ratio = ?,
                    request_timeout_seconds = ?,
                    max_concurrency = ?,
                    enable_streaming = ?,
                    allow_request_override = ?,
                    report_retention_days = ?,
                    updated_at = ?
                WHERE id = 1
                """,
                (
                    payload["default_speaker"],
                    payload["default_format"],
                    payload["retry_on_block"],
                    payload["retry_max_retries"],
                    payload["retry_backoff_seconds"],
                    payload["retry_backoff_multiplier"],
                    payload["retry_backoff_jitter_ratio"],
                    payload["request_timeout_seconds"],
                    payload["max_concurrency"],
                    payload["enable_streaming"],
                    payload["allow_request_override"],
                    payload["report_retention_days"],
                    now,
                ),
            )
            connection.commit()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to save service settings: {exc}") from exc


def save_service_settings(settings: dict[str, Any], path: str | None = None) -> None:
    save_initial_service_settings(settings, path)


def count_doubao_accounts(path: str | None = None) -> int:
    initialize_database(path)
    try:
        with get_db_connection(path) as connection:
            row = connection.execute("SELECT COUNT(*) AS total FROM doubao_accounts").fetchone()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to count Doubao accounts: {exc}") from exc

    return int(row["total"]) if row is not None else 0


def count_healthy_doubao_accounts(path: str | None = None, now_iso: str | None = None) -> int:
    initialize_database(path)
    now_value = now_iso or utcnow_iso()
    try:
        with get_db_connection(path) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM doubao_accounts
                WHERE enabled = 1
                  AND status != ?
                  AND (cooldown_until IS NULL OR cooldown_until <= ?)
                """,
                (ACCOUNT_STATUS_INVALID, now_value),
            ).fetchone()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to count healthy Doubao accounts: {exc}") from exc

    return int(row["total"]) if row is not None else 0


def fetch_doubao_accounts(path: str | None = None) -> list[dict[str, Any]]:
    initialize_database(path)
    try:
        with get_db_connection(path) as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    name,
                    sessionid,
                    sid_guard,
                    uid_tt,
                    enabled,
                    status,
                    cooldown_until,
                    last_error,
                    last_used_at,
                    success_count,
                    failure_count,
                    created_at,
                    updated_at
                FROM doubao_accounts
                ORDER BY id ASC
                """
            ).fetchall()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to read Doubao accounts: {exc}") from exc

    return [dict(row) for row in rows]


def fetch_doubao_account_by_id(account_id: int, path: str | None = None) -> dict[str, Any] | None:
    initialize_database(path)
    try:
        with get_db_connection(path) as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    name,
                    sessionid,
                    sid_guard,
                    uid_tt,
                    enabled,
                    status,
                    cooldown_until,
                    last_error,
                    last_used_at,
                    success_count,
                    failure_count,
                    created_at,
                    updated_at
                FROM doubao_accounts
                WHERE id = ?
                LIMIT 1
                """,
                (account_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to read Doubao account {account_id}: {exc}") from exc

    return dict(row) if row is not None else None


def create_doubao_account_record(
    name: str,
    sessionid: str,
    sid_guard: str,
    uid_tt: str,
    *,
    enabled: bool = True,
    path: str | None = None,
) -> int:
    initialize_database(path)
    now = utcnow_iso()
    payload = _normalize_account_payload(name, sessionid, sid_guard, uid_tt)
    status = ACCOUNT_STATUS_HEALTHY if enabled else ACCOUNT_STATUS_DISABLED
    try:
        with get_db_connection(path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO doubao_accounts (
                    name,
                    sessionid,
                    sid_guard,
                    uid_tt,
                    enabled,
                    status,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["name"],
                    payload["sessionid"],
                    payload["sid_guard"],
                    payload["uid_tt"],
                    1 if enabled else 0,
                    status,
                    now,
                    now,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to create Doubao account: {exc}") from exc


def update_doubao_account_record(
    account_id: int,
    name: str,
    sessionid: str,
    sid_guard: str,
    uid_tt: str,
    *,
    path: str | None = None,
) -> None:
    initialize_database(path)
    now = utcnow_iso()
    payload = _normalize_account_payload(name, sessionid, sid_guard, uid_tt)
    try:
        with get_db_connection(path) as connection:
            cursor = connection.execute(
                """
                UPDATE doubao_accounts
                SET name = ?,
                    sessionid = ?,
                    sid_guard = ?,
                    uid_tt = ?,
                    status = CASE
                        WHEN enabled = 0 THEN ?
                        WHEN cooldown_until IS NOT NULL AND cooldown_until > ? THEN ?
                        ELSE ?
                    END,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    payload["name"],
                    payload["sessionid"],
                    payload["sid_guard"],
                    payload["uid_tt"],
                    ACCOUNT_STATUS_DISABLED,
                    now,
                    ACCOUNT_STATUS_COOLDOWN,
                    ACCOUNT_STATUS_HEALTHY,
                    now,
                    account_id,
                ),
            )
            if cursor.rowcount == 0:
                raise DatabaseError(f"Doubao account {account_id} does not exist")
            connection.commit()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to update Doubao account: {exc}") from exc


def set_doubao_account_enabled(account_id: int, enabled: bool, path: str | None = None) -> None:
    initialize_database(path)
    now = utcnow_iso()
    try:
        with get_db_connection(path) as connection:
            row = connection.execute(
                "SELECT cooldown_until FROM doubao_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            if row is None:
                raise DatabaseError(f"Doubao account {account_id} does not exist")
            cooldown_until = row["cooldown_until"]
            status = (
                ACCOUNT_STATUS_DISABLED
                if not enabled
                else ACCOUNT_STATUS_COOLDOWN
                if cooldown_until and str(cooldown_until) > now
                else ACCOUNT_STATUS_HEALTHY
            )
            connection.execute(
                """
                UPDATE doubao_accounts
                SET enabled = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (1 if enabled else 0, status, now, account_id),
            )
            connection.commit()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to update Doubao account status: {exc}") from exc


def clear_doubao_account_cooldown(account_id: int, path: str | None = None) -> None:
    initialize_database(path)
    now = utcnow_iso()
    try:
        with get_db_connection(path) as connection:
            cursor = connection.execute(
                """
                UPDATE doubao_accounts
                SET cooldown_until = NULL,
                    status = CASE WHEN enabled = 1 THEN ? ELSE ? END,
                    updated_at = ?
                WHERE id = ?
                """,
                (ACCOUNT_STATUS_HEALTHY, ACCOUNT_STATUS_DISABLED, now, account_id),
            )
            if cursor.rowcount == 0:
                raise DatabaseError(f"Doubao account {account_id} does not exist")
            connection.commit()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to clear Doubao account cooldown: {exc}") from exc


def mark_doubao_account_success(account_id: int, path: str | None = None) -> None:
    initialize_database(path)
    now = utcnow_iso()
    try:
        with get_db_connection(path) as connection:
            cursor = connection.execute(
                """
                UPDATE doubao_accounts
                SET success_count = success_count + 1,
                    last_used_at = ?,
                    last_error = NULL,
                    cooldown_until = NULL,
                    status = CASE WHEN enabled = 1 THEN ? ELSE ? END,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, ACCOUNT_STATUS_HEALTHY, ACCOUNT_STATUS_DISABLED, now, account_id),
            )
            if cursor.rowcount == 0:
                raise DatabaseError(f"Doubao account {account_id} does not exist")
            connection.commit()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to update Doubao account success state: {exc}") from exc


def mark_doubao_account_failure(
    account_id: int,
    error_detail: str,
    *,
    cooldown_until: str | None = None,
    status: str | None = None,
    path: str | None = None,
) -> None:
    initialize_database(path)
    now = utcnow_iso()
    resolved_status = status or (ACCOUNT_STATUS_COOLDOWN if cooldown_until else ACCOUNT_STATUS_HEALTHY)
    try:
        with get_db_connection(path) as connection:
            cursor = connection.execute(
                """
                UPDATE doubao_accounts
                SET failure_count = failure_count + 1,
                    last_error = ?,
                    cooldown_until = ?,
                    status = CASE
                        WHEN enabled = 0 THEN ?
                        ELSE ?
                    END,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    error_detail,
                    cooldown_until,
                    ACCOUNT_STATUS_DISABLED,
                    resolved_status,
                    now,
                    account_id,
                ),
            )
            if cursor.rowcount == 0:
                raise DatabaseError(f"Doubao account {account_id} does not exist")
            connection.commit()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to update Doubao account failure state: {exc}") from exc


def list_available_doubao_accounts(
    *,
    exclude_ids: list[int] | tuple[int, ...] | None = None,
    limit: int | None = None,
    path: str | None = None,
    now_iso: str | None = None,
) -> list[dict[str, Any]]:
    initialize_database(path)
    excluded = tuple(int(item) for item in (exclude_ids or ()))
    now_value = now_iso or utcnow_iso()
    limit_clause = f" LIMIT {int(limit)}" if limit is not None and int(limit) > 0 else ""
    where_parts = [
        "enabled = 1",
        "status != ?",
        "(cooldown_until IS NULL OR cooldown_until <= ?)",
    ]
    params: list[Any] = [ACCOUNT_STATUS_INVALID, now_value]
    if excluded:
        placeholders = ", ".join("?" for _ in excluded)
        where_parts.append(f"id NOT IN ({placeholders})")
        params.extend(excluded)

    query = f"""
        SELECT
            id,
            name,
            sessionid,
            sid_guard,
            uid_tt,
            enabled,
            status,
            cooldown_until,
            last_error,
            last_used_at,
            success_count,
            failure_count,
            created_at,
            updated_at
        FROM doubao_accounts
        WHERE {' AND '.join(where_parts)}
        ORDER BY
            CASE WHEN last_used_at IS NULL THEN 0 ELSE 1 END ASC,
            last_used_at ASC,
            id ASC
        {limit_clause}
    """
    try:
        with get_db_connection(path) as connection:
            rows = connection.execute(query, params).fetchall()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to select available Doubao accounts: {exc}") from exc

    return [dict(row) for row in rows]


def seed_initial_doubao_account(cookie: str, path: str | None = None) -> int | None:
    initialize_database(path)
    if not cookie.strip() or count_doubao_accounts(path) > 0:
        return None

    normalized_cookie, missing_fields = normalize_cookie(cookie)
    if missing_fields:
        return None

    cookie_items = parse_cookie_string(normalized_cookie)
    return create_doubao_account_record(
        DEFAULT_ENV_ACCOUNT_NAME,
        cookie_items["sessionid"],
        cookie_items["sid_guard"],
        cookie_items["uid_tt"],
        path=path,
    )


def fetch_api_keys(path: str | None = None) -> list[dict[str, Any]]:
    initialize_database(path)
    try:
        with get_db_connection(path) as connection:
            rows = connection.execute(
                """
                SELECT id, name, key_prefix, enabled, last_used_at, created_at, updated_at
                FROM api_keys
                ORDER BY id ASC
                """
            ).fetchall()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to read API keys: {exc}") from exc

    return [dict(row) for row in rows]


def count_enabled_api_keys(path: str | None = None) -> int:
    initialize_database(path)
    try:
        with get_db_connection(path) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM api_keys WHERE enabled = 1"
            ).fetchone()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to count API keys: {exc}") from exc

    return int(row["total"]) if row is not None else 0


def create_api_key_record(name: str, key_prefix: str, key_hash: str, path: str | None = None) -> int:
    initialize_database(path)
    now = utcnow_iso()
    try:
        with get_db_connection(path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO api_keys (
                    name,
                    key_prefix,
                    key_hash,
                    enabled,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, 1, ?, ?)
                """,
                (name, key_prefix, key_hash, now, now),
            )
            connection.commit()
            return int(cursor.lastrowid)
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to create API key: {exc}") from exc


def set_api_key_enabled(api_key_id: int, enabled: bool, path: str | None = None) -> None:
    initialize_database(path)
    now = utcnow_iso()
    try:
        with get_db_connection(path) as connection:
            cursor = connection.execute(
                """
                UPDATE api_keys
                SET enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (1 if enabled else 0, now, api_key_id),
            )
            if cursor.rowcount == 0:
                raise DatabaseError(f"API key {api_key_id} does not exist")
            connection.commit()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to update API key status: {exc}") from exc


def find_enabled_api_key_by_hash(key_hash: str, path: str | None = None) -> dict[str, Any] | None:
    initialize_database(path)
    try:
        with get_db_connection(path) as connection:
            row = connection.execute(
                """
                SELECT id, name, key_prefix, enabled, last_used_at, created_at, updated_at
                FROM api_keys
                WHERE key_hash = ? AND enabled = 1
                LIMIT 1
                """,
                (key_hash,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to look up API key: {exc}") from exc

    return dict(row) if row is not None else None


def touch_api_key_last_used(api_key_id: int, path: str | None = None) -> None:
    initialize_database(path)
    now = utcnow_iso()
    try:
        with get_db_connection(path) as connection:
            connection.execute(
                """
                UPDATE api_keys
                SET last_used_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, api_key_id),
            )
            connection.commit()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to update API key last_used_at: {exc}") from exc


def create_request_log(record: dict[str, Any], path: str | None = None) -> int:
    initialize_database(path)
    try:
        with get_db_connection(path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO request_logs (
                    request_id,
                    api_key_id,
                    doubao_account_id,
                    endpoint,
                    speaker,
                    format,
                    text_chars,
                    status_code,
                    success,
                    latency_ms,
                    error_type,
                    error_detail,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["request_id"],
                    record.get("api_key_id"),
                    record.get("doubao_account_id"),
                    record["endpoint"],
                    record.get("speaker"),
                    record.get("format"),
                    int(record.get("text_chars", 0)),
                    int(record["status_code"]),
                    1 if record.get("success") else 0,
                    int(record.get("latency_ms", 0)),
                    record.get("error_type"),
                    record.get("error_detail"),
                    record.get("created_at") or utcnow_iso(),
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to create request log: {exc}") from exc


def prune_request_logs_before(cutoff_iso: str, path: str | None = None) -> int:
    initialize_database(path)
    try:
        with get_db_connection(path) as connection:
            cursor = connection.execute(
                "DELETE FROM request_logs WHERE created_at < ?",
                (cutoff_iso,),
            )
            connection.commit()
            return int(cursor.rowcount or 0)
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to prune request logs: {exc}") from exc
