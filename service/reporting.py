from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from service.db import create_request_log, get_db_connection, initialize_database, prune_request_logs_before


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


@dataclass
class RequestLogContext:
    request_id: str
    endpoint: str
    api_key_id: int | None
    text_chars: int
    created_at: str
    started_at: float


def start_request_log(endpoint: str, api_key_id: int | None, text: str) -> RequestLogContext:
    return RequestLogContext(
        request_id=uuid.uuid4().hex,
        endpoint=endpoint,
        api_key_id=api_key_id,
        text_chars=len(text),
        created_at=_utcnow().isoformat(),
        started_at=time.perf_counter(),
    )


def finish_request_log(
    context: RequestLogContext,
    *,
    account_id: int | None,
    speaker: str | None,
    audio_format: str | None,
    status_code: int,
    success: bool,
    error_type: str | None = None,
    error_detail: str | None = None,
    retention_days: int | None = None,
) -> None:
    latency_ms = max(0, int((time.perf_counter() - context.started_at) * 1000))
    create_request_log(
        {
            "request_id": context.request_id,
            "api_key_id": context.api_key_id,
            "doubao_account_id": account_id,
            "endpoint": context.endpoint,
            "speaker": speaker,
            "format": audio_format,
            "text_chars": context.text_chars,
            "status_code": status_code,
            "success": success,
            "latency_ms": latency_ms,
            "error_type": error_type,
            "error_detail": error_detail,
            "created_at": context.created_at,
        }
    )
    if retention_days and retention_days > 0:
        cutoff = (_utcnow() - timedelta(days=retention_days)).isoformat()
        prune_request_logs_before(cutoff)


def _build_filters(
    *,
    days: int,
    result: str,
    api_key_id: int | None,
    account_id: int | None,
) -> tuple[str, list[Any]]:
    where_parts = ["request_logs.created_at >= ?"]
    params: list[Any] = [(_utcnow() - timedelta(days=days)).isoformat()]
    if result == "success":
        where_parts.append("request_logs.success = 1")
    elif result == "failure":
        where_parts.append("request_logs.success = 0")
    if api_key_id is not None:
        where_parts.append("request_logs.api_key_id = ?")
        params.append(api_key_id)
    if account_id is not None:
        where_parts.append("request_logs.doubao_account_id = ?")
        params.append(account_id)
    return " WHERE " + " AND ".join(where_parts), params


def fetch_report_snapshot(
    *,
    days: int = 7,
    result: str = "all",
    api_key_id: int | None = None,
    account_id: int | None = None,
) -> dict[str, Any]:
    initialize_database()
    where_clause, params = _build_filters(days=days, result=result, api_key_id=api_key_id, account_id=account_id)
    try:
        with get_db_connection() as connection:
            totals_row = connection.execute(
                f"""
                SELECT
                    COUNT(*) AS total_requests,
                    COALESCE(SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END), 0) AS success_requests,
                    COALESCE(AVG(latency_ms), 0) AS avg_latency_ms
                FROM request_logs
                {where_clause}
                """,
                params,
            ).fetchone()
            by_endpoint_rows = connection.execute(
                f"""
                SELECT
                    endpoint,
                    COUNT(*) AS total_requests,
                    COALESCE(SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END), 0) AS success_requests,
                    COALESCE(AVG(latency_ms), 0) AS avg_latency_ms
                FROM request_logs
                {where_clause}
                GROUP BY endpoint
                ORDER BY total_requests DESC, endpoint ASC
                """,
                params,
            ).fetchall()
            by_api_key_rows = connection.execute(
                f"""
                SELECT
                    request_logs.api_key_id AS api_key_id,
                    COALESCE(api_keys.name, '未知 API Key') AS api_key_name,
                    COUNT(*) AS total_requests,
                    COALESCE(SUM(CASE WHEN request_logs.success = 1 THEN 1 ELSE 0 END), 0) AS success_requests,
                    COALESCE(AVG(request_logs.latency_ms), 0) AS avg_latency_ms
                FROM request_logs
                LEFT JOIN api_keys ON api_keys.id = request_logs.api_key_id
                {where_clause}
                GROUP BY request_logs.api_key_id, api_keys.name
                ORDER BY total_requests DESC, api_key_name ASC
                """,
                params,
            ).fetchall()
            by_account_rows = connection.execute(
                f"""
                SELECT
                    request_logs.doubao_account_id AS account_id,
                    COALESCE(doubao_accounts.name, '未知凭据') AS account_name,
                    COUNT(*) AS total_requests,
                    COALESCE(SUM(CASE WHEN request_logs.success = 1 THEN 1 ELSE 0 END), 0) AS success_requests,
                    COALESCE(AVG(request_logs.latency_ms), 0) AS avg_latency_ms
                FROM request_logs
                LEFT JOIN doubao_accounts ON doubao_accounts.id = request_logs.doubao_account_id
                {where_clause}
                GROUP BY request_logs.doubao_account_id, doubao_accounts.name
                ORDER BY total_requests DESC, account_name ASC
                """,
                params,
            ).fetchall()
            recent_failure_rows = connection.execute(
                f"""
                SELECT
                    request_logs.request_id,
                    request_logs.endpoint,
                    request_logs.status_code,
                    request_logs.error_type,
                    request_logs.error_detail,
                    request_logs.latency_ms,
                    request_logs.created_at,
                    request_logs.text_chars,
                    COALESCE(api_keys.name, '未知 API Key') AS api_key_name,
                    COALESCE(doubao_accounts.name, '未知凭据') AS account_name
                FROM request_logs
                LEFT JOIN api_keys ON api_keys.id = request_logs.api_key_id
                LEFT JOIN doubao_accounts ON doubao_accounts.id = request_logs.doubao_account_id
                {where_clause}
                  AND request_logs.success = 0
                ORDER BY request_logs.created_at DESC
                LIMIT 20
                """,
                params,
            ).fetchall()
    except Exception:
        raise

    totals = dict(totals_row) if totals_row is not None else {
        "total_requests": 0,
        "success_requests": 0,
        "avg_latency_ms": 0,
    }
    total_requests = int(totals.get("total_requests", 0) or 0)
    success_requests = int(totals.get("success_requests", 0) or 0)
    success_rate = round((success_requests / total_requests) * 100, 2) if total_requests else 0.0
    return {
        "filters": {
            "days": days,
            "result": result,
            "api_key_id": api_key_id,
            "account_id": account_id,
        },
        "totals": {
            "total_requests": total_requests,
            "success_requests": success_requests,
            "failed_requests": max(0, total_requests - success_requests),
            "success_rate": success_rate,
            "avg_latency_ms": round(float(totals.get("avg_latency_ms", 0) or 0), 2),
        },
        "by_endpoint": [dict(row) for row in by_endpoint_rows],
        "by_api_key": [dict(row) for row in by_api_key_rows],
        "by_account": [dict(row) for row in by_account_rows],
        "recent_failures": [dict(row) for row in recent_failure_rows],
    }
