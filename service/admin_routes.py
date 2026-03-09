from __future__ import annotations

import html
import secrets
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from service.auth import (
    clear_admin_session,
    generate_api_key,
    generate_csrf_token,
    get_bootstrap_password,
    get_or_create_csrf_token,
    hash_api_key,
    hash_password,
    require_admin_session,
    set_admin_session_cookie,
    set_csrf_cookie,
    should_use_secure_cookies,
    validate_csrf,
    validate_new_password,
    verify_password,
)
from service.config import clear_service_config_cache, get_service_config
from service.credential_pool import (
    build_account_cookie,
    get_account_or_raise,
    mark_account_attempt_failure,
    mark_account_attempt_success,
    select_account,
)
from service.db import (
    count_healthy_doubao_accounts,
    create_api_key_record,
    create_doubao_account_record,
    fetch_api_keys,
    fetch_admin_settings,
    fetch_doubao_accounts,
    fetch_service_settings,
    get_sqlite_path,
    save_service_settings,
    set_api_key_enabled,
    set_doubao_account_enabled,
    clear_doubao_account_cooldown,
    update_admin_password_hash,
    update_doubao_account_record,
)
from service.errors import BadRequestError, ForbiddenError, ServiceHTTPError, ServiceUnavailableError, UnauthorizedError
from service.models import (
    AdminAccountTestRequest,
    AdminAccountWriteRequest,
    AdminActionResponse,
    AdminApiKeyCreateRequest,
    AdminApiKeyCreateResponse,
    AdminApiKeyStatusResponse,
    AdminLoginRequest,
    AdminServiceSettingsRequest,
    AdminSetupRequest,
    AdminTestTTSRequest,
    AdminTestTTSResponse,
)
from service.reporting import fetch_report_snapshot
from service.tts_runtime import synthesize_once


admin_router = APIRouter()


def _model_dump(model):
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _mask_secret(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _admin_layout(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4efe7;
      --panel: #fff9f1;
      --ink: #1f1a17;
      --accent: #a43b22;
      --muted: #6f6259;
      --line: #d5c6b7;
      --ok: #1c6b2f;
      --warn: #9a6700;
      --danger: #b42318;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(164,59,34,0.12), transparent 28%),
        linear-gradient(160deg, var(--bg), #efe4d6 65%, #ead9ca);
      color: var(--ink);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }}
    main {{
      width: min(1100px, 100%);
      background: rgba(255, 249, 241, 0.94);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 28px;
      box-shadow: 0 18px 60px rgba(44, 28, 14, 0.12);
      backdrop-filter: blur(8px);
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: 30px;
      line-height: 1.1;
    }}
    p {{
      color: var(--muted);
      line-height: 1.6;
    }}
    form {{
      display: grid;
      gap: 12px;
      margin-top: 18px;
    }}
    label {{
      display: grid;
      gap: 6px;
      font-weight: 600;
    }}
    input, button, textarea, select {{
      font: inherit;
      border-radius: 12px;
      border: 1px solid var(--line);
      padding: 12px 14px;
    }}
    input, textarea, select {{
      background: #fff;
      color: var(--ink);
    }}
    textarea {{
      min-height: 110px;
      resize: vertical;
    }}
    button {{
      background: var(--accent);
      color: #fff;
      border: none;
      cursor: pointer;
      font-weight: 700;
    }}
    button.secondary {{
      background: transparent;
      color: var(--accent);
      border: 1px solid var(--accent);
    }}
    button.warn {{
      background: #f7f1e6;
      color: var(--warn);
      border: 1px solid #e9d5b2;
    }}
    button.danger {{
      background: #fff2f0;
      color: var(--danger);
      border: 1px solid #f5c2c0;
    }}
    .grid {{
      display: grid;
      gap: 14px;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      margin-top: 22px;
    }}
    .card {{
      background: rgba(255,255,255,0.68);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 16px;
    }}
    .card strong {{
      display: block;
      font-size: 13px;
      color: var(--muted);
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .status {{
      min-height: 24px;
      font-weight: 700;
      white-space: pre-wrap;
    }}
    .status.error {{ color: var(--danger); }}
    .status.ok {{ color: var(--ok); }}
    code {{
      background: rgba(31, 26, 23, 0.06);
      padding: 2px 6px;
      border-radius: 6px;
      word-break: break-all;
    }}
    nav {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 18px;
    }}
    .toolbar {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 14px;
    }}
    .split {{
      display: grid;
      gap: 18px;
      grid-template-columns: minmax(300px, 360px) 1fr;
      margin-top: 22px;
    }}
    .table-wrap {{
      overflow-x: auto;
      margin-top: 18px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: rgba(255,255,255,0.72);
      border-radius: 14px;
      overflow: hidden;
    }}
    th, td {{
      padding: 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      font-size: 13px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 12px;
      font-weight: 700;
      background: #efe5d6;
      color: var(--ink);
    }}
    .pill.ok {{ background: #e8f5ea; color: var(--ok); }}
    .pill.warn {{ background: #fff3d6; color: var(--warn); }}
    .pill.error {{ background: #fff1ef; color: var(--danger); }}
    @media (max-width: 860px) {{
      .split {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <main>{body}</main>
</body>
</html>"""


def _render_page(request: Request, title: str, body: str, status_code: int = 200) -> HTMLResponse:
    response = HTMLResponse(_admin_layout(title, body), status_code=status_code)
    set_csrf_cookie(response, get_or_create_csrf_token(request), secure=should_use_secure_cookies(request))
    return response


def _render_error_page(request: Request, title: str, detail: str, status_code: int) -> HTMLResponse:
    body = f"""
    <h1>{html.escape(title)}</h1>
    <p>{html.escape(detail)}</p>
    <nav>
      <a href="/admin/setup">初始化</a>
      <a href="/admin/login">登录</a>
    </nav>
    """
    return _render_page(request, title, body, status_code=status_code)


def _redirect(request: Request, target: str) -> RedirectResponse:
    response = RedirectResponse(target, status_code=303)
    set_csrf_cookie(response, get_or_create_csrf_token(request), secure=should_use_secure_cookies(request))
    return response


def _is_setup_completed() -> bool:
    return bool(fetch_admin_settings().get("setup_completed"))


def _is_authenticated(request: Request) -> bool:
    try:
        require_admin_session(request)
    except UnauthorizedError:
        return False
    return True


def _ensure_runtime_settings_seeded() -> None:
    get_service_config()


def _guard_admin_page(request: Request) -> RedirectResponse | None:
    _ensure_runtime_settings_seeded()
    if not _is_setup_completed():
        return _redirect(request, "/admin/setup")
    try:
        require_admin_session(request)
    except UnauthorizedError:
        return _redirect(request, "/admin/login")
    return None


def _admin_nav() -> str:
    return """
    <nav>
      <a href="/admin">总览</a>
      <a href="/admin/settings">服务设置</a>
      <a href="/admin/accounts">豆包凭据池</a>
      <a href="/admin/api-keys">API Key</a>
      <a href="/admin/reports">调用报表</a>
      <a href="/admin/test-tts">测试合成</a>
    </nav>
    """


def _setup_page_body() -> str:
    return """
    <h1>初始化后台</h1>
    <p>先用环境变量里的 bootstrap 密码完成一次受控初始化，再设置正式管理密码。匿名首访直接占后台，这种坑不留。</p>
    <form id="setup-form">
      <label>Bootstrap 密码
        <input id="bootstrap-password" type="password" autocomplete="current-password" required>
      </label>
      <label>正式管理密码
        <input id="new-password" type="password" autocomplete="new-password" minlength="8" required>
      </label>
      <button type="submit">完成初始化</button>
      <div id="setup-status" class="status"></div>
    </form>
    <script>
      function getCookie(name) {
        const prefix = name + "=";
        return document.cookie.split("; ").find(item => item.startsWith(prefix))?.slice(prefix.length) || "";
      }
      document.getElementById("setup-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const status = document.getElementById("setup-status");
        status.className = "status";
        status.textContent = "提交中...";
        const response = await fetch("/admin/setup", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": getCookie("tts_admin_csrf")
          },
          body: JSON.stringify({
            bootstrap_password: document.getElementById("bootstrap-password").value,
            new_password: document.getElementById("new-password").value
          })
        });
        const payload = await response.json();
        if (!response.ok) {
          status.className = "status error";
          status.textContent = payload.detail || "初始化失败";
          return;
        }
        status.className = "status ok";
        status.textContent = payload.detail;
        window.location.href = payload.redirect_to || "/admin";
      });
    </script>
    """


def _login_page_body() -> str:
    return """
    <h1>管理后台登录</h1>
    <p>这里只认正式管理密码，不认 API Key。管理员会话和公开调用凭据必须分开。</p>
    <form id="login-form">
      <label>管理密码
        <input id="password" type="password" autocomplete="current-password" required>
      </label>
      <button type="submit">登录</button>
      <div id="login-status" class="status"></div>
    </form>
    <script>
      function getCookie(name) {
        const prefix = name + "=";
        return document.cookie.split("; ").find(item => item.startsWith(prefix))?.slice(prefix.length) || "";
      }
      document.getElementById("login-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const status = document.getElementById("login-status");
        status.className = "status";
        status.textContent = "提交中...";
        const response = await fetch("/admin/login", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": getCookie("tts_admin_csrf")
          },
          body: JSON.stringify({
            password: document.getElementById("password").value
          })
        });
        const payload = await response.json();
        if (!response.ok) {
          status.className = "status error";
          status.textContent = payload.detail || "登录失败";
          return;
        }
        status.className = "status ok";
        status.textContent = payload.detail;
        window.location.href = payload.redirect_to || "/admin";
      });
    </script>
    """


def _dashboard_body(
    service_settings: dict[str, object],
    api_keys: list[dict[str, object]],
    accounts: list[dict[str, object]],
    report_snapshot: dict[str, Any],
) -> str:
    sqlite_path = html.escape(get_sqlite_path())
    speaker = html.escape(str(service_settings.get("default_speaker", "taozi")))
    audio_format = html.escape(str(service_settings.get("default_format", "aac")))
    timeout = html.escape(str(service_settings.get("request_timeout_seconds", 35.0)))
    concurrency = html.escape(str(service_settings.get("max_concurrency", 4)))
    enabled_api_key_count = sum(1 for item in api_keys if int(item.get("enabled", 0)) == 1)
    healthy_accounts = count_healthy_doubao_accounts()
    total_accounts = len(accounts)
    totals = report_snapshot["totals"]
    return f"""
    <h1>管理后台</h1>
    <p>现在才算开始像个能运维的服务了。配置、凭据池、API Key、报表都在同一个后台里收口，不再靠手改环境变量硬顶。</p>
    {_admin_nav()}
    <nav>
      <button id="logout-button" class="secondary" type="button">退出登录</button>
    </nav>
    <div class="grid">
      <section class="card"><strong>SQLite</strong><div><code>{sqlite_path}</code></div></section>
      <section class="card"><strong>默认 Speaker</strong><div>{speaker}</div></section>
      <section class="card"><strong>默认格式</strong><div>{audio_format}</div></section>
      <section class="card"><strong>请求超时</strong><div>{timeout} 秒</div></section>
      <section class="card"><strong>最大并发</strong><div>{concurrency}</div></section>
      <section class="card"><strong>启用中的 API Key</strong><div>{enabled_api_key_count}</div></section>
      <section class="card"><strong>健康凭据数</strong><div>{healthy_accounts} / {total_accounts}</div></section>
      <section class="card"><strong>最近 7 天总请求</strong><div>{totals["total_requests"]}</div></section>
      <section class="card"><strong>最近 7 天成功率</strong><div>{totals["success_rate"]}%</div></section>
      <section class="card"><strong>最近 7 天平均耗时</strong><div>{totals["avg_latency_ms"]} ms</div></section>
    </div>
    <div id="logout-status" class="status"></div>
    <script>
      function getCookie(name) {{
        const prefix = name + "=";
        return document.cookie.split("; ").find(item => item.startsWith(prefix))?.slice(prefix.length) || "";
      }}
      document.getElementById("logout-button").addEventListener("click", async () => {{
        const status = document.getElementById("logout-status");
        const response = await fetch("/admin/logout", {{
          method: "POST",
          headers: {{
            "X-CSRF-Token": getCookie("tts_admin_csrf")
          }}
        }});
        const payload = await response.json();
        if (!response.ok) {{
          status.className = "status error";
          status.textContent = payload.detail || "退出失败";
          return;
        }}
        window.location.href = payload.redirect_to || "/admin/login";
      }});
    </script>
    """


def _settings_page_body(service_settings: dict[str, object]) -> str:
    default_speaker = html.escape(str(service_settings.get("default_speaker", "taozi")))
    default_format = html.escape(str(service_settings.get("default_format", "aac")))
    request_timeout_seconds = html.escape(str(service_settings.get("request_timeout_seconds", 35.0)))
    max_concurrency = html.escape(str(service_settings.get("max_concurrency", 4)))
    retry_on_block = "checked" if int(service_settings.get("retry_on_block", 0)) else ""
    retry_max_retries = html.escape(str(service_settings.get("retry_max_retries", 0)))
    retry_backoff_seconds = html.escape(str(service_settings.get("retry_backoff_seconds", 1.0)))
    retry_backoff_multiplier = html.escape(str(service_settings.get("retry_backoff_multiplier", 2.0)))
    retry_backoff_jitter_ratio = html.escape(str(service_settings.get("retry_backoff_jitter_ratio", 0.0)))
    enable_streaming = "checked" if int(service_settings.get("enable_streaming", 1)) else ""
    allow_request_override = "checked" if int(service_settings.get("allow_request_override", 1)) else ""
    report_retention_days = html.escape(str(service_settings.get("report_retention_days", 30)))
    return f"""
    <h1>服务设置</h1>
    <p>这里改的是 SQLite 里的当前生效配置，不再依赖你去改环境变量。环境变量只负责第一次种子和部署期秘密。</p>
    {_admin_nav()}
    <form id="settings-form">
      <label>默认 Speaker
        <input id="default-speaker" type="text" value="{default_speaker}" required>
      </label>
      <label>默认格式
        <select id="default-format">
          <option value="aac" {"selected" if default_format == "aac" else ""}>aac</option>
          <option value="mp3" {"selected" if default_format == "mp3" else ""}>mp3</option>
        </select>
      </label>
      <label>请求超时（秒）
        <input id="request-timeout-seconds" type="number" min="0.1" step="0.1" value="{request_timeout_seconds}" required>
      </label>
      <label>最大并发
        <input id="max-concurrency" type="number" min="1" step="1" value="{max_concurrency}" required>
      </label>
      <label><input id="retry-on-block" type="checkbox" {retry_on_block}> 启用 block 重试</label>
      <label>最大额外重试次数
        <input id="retry-max-retries" type="number" min="0" step="1" value="{retry_max_retries}" required>
      </label>
      <label>首轮退避秒数
        <input id="retry-backoff-seconds" type="number" min="0.1" step="0.1" value="{retry_backoff_seconds}" required>
      </label>
      <label>退避倍率
        <input id="retry-backoff-multiplier" type="number" min="1" step="0.1" value="{retry_backoff_multiplier}" required>
      </label>
      <label>退避抖动比例
        <input id="retry-backoff-jitter-ratio" type="number" min="0" max="1" step="0.01" value="{retry_backoff_jitter_ratio}" required>
      </label>
      <label><input id="enable-streaming" type="checkbox" {enable_streaming}> 允许流式接口</label>
      <label><input id="allow-request-override" type="checkbox" {allow_request_override}> 允许调用方覆盖默认参数</label>
      <label>报表保留天数
        <input id="report-retention-days" type="number" min="1" max="3650" step="1" value="{report_retention_days}" required>
      </label>
      <button type="submit">保存设置</button>
      <div id="settings-status" class="status"></div>
    </form>
    <script>
      function getCookie(name) {{
        const prefix = name + "=";
        return document.cookie.split("; ").find(item => item.startsWith(prefix))?.slice(prefix.length) || "";
      }}
      document.getElementById("settings-form").addEventListener("submit", async (event) => {{
        event.preventDefault();
        const status = document.getElementById("settings-status");
        status.className = "status";
        status.textContent = "保存中...";
        const response = await fetch("/admin/settings", {{
          method: "POST",
          headers: {{
            "Content-Type": "application/json",
            "X-CSRF-Token": getCookie("tts_admin_csrf")
          }},
          body: JSON.stringify({{
            default_speaker: document.getElementById("default-speaker").value,
            default_format: document.getElementById("default-format").value,
            request_timeout_seconds: Number(document.getElementById("request-timeout-seconds").value),
            max_concurrency: Number(document.getElementById("max-concurrency").value),
            retry_on_block: document.getElementById("retry-on-block").checked,
            retry_max_retries: Number(document.getElementById("retry-max-retries").value),
            retry_backoff_seconds: Number(document.getElementById("retry-backoff-seconds").value),
            retry_backoff_multiplier: Number(document.getElementById("retry-backoff-multiplier").value),
            retry_backoff_jitter_ratio: Number(document.getElementById("retry-backoff-jitter-ratio").value),
            enable_streaming: document.getElementById("enable-streaming").checked,
            allow_request_override: document.getElementById("allow-request-override").checked,
            report_retention_days: Number(document.getElementById("report-retention-days").value)
          }})
        }});
        const payload = await response.json();
        if (!response.ok) {{
          status.className = "status error";
          status.textContent = payload.detail || "保存失败";
          return;
        }}
        status.className = "status ok";
        status.textContent = payload.detail;
      }});
    </script>
    """


def _api_keys_page_body(api_keys: list[dict[str, object]]) -> str:
    rows: list[str] = []
    for item in api_keys:
        key_id = int(item["id"])
        name = html.escape(str(item["name"]))
        key_prefix = html.escape(str(item["key_prefix"]))
        enabled = int(item["enabled"]) == 1
        last_used = html.escape(str(item.get("last_used_at") or "未使用"))
        created_at = html.escape(str(item.get("created_at") or ""))
        toggle_action = "disable" if enabled else "enable"
        toggle_label = "停用" if enabled else "启用"
        toggle_class = "danger" if enabled else "secondary"
        rows.append(
            f"""
            <section class="card">
              <strong>{name}</strong>
              <div>前缀：<code>{key_prefix}</code></div>
              <div>状态：{"启用" if enabled else "停用"}</div>
              <div>最后使用：{last_used}</div>
              <div>创建时间：{created_at}</div>
              <div class="toolbar">
                <button class="{toggle_class}" type="button" onclick="toggleKey({key_id}, '{toggle_action}')">{toggle_label}</button>
              </div>
            </section>
            """
        )
    cards = "\n".join(rows) if rows else "<p>还没有 API Key。没有这个东西，公开接口就不该对外开放。</p>"
    return f"""
    <h1>API Key 管理</h1>
    <p>公开 TTS 接口只认 API Key，不认后台密码。创建后的原始 key 只展示一次，别指望后台后面再帮你找回明文。</p>
    {_admin_nav()}
    <form id="api-key-form">
      <label>Key 名称
        <input id="api-key-name" type="text" maxlength="120" required>
      </label>
      <button type="submit">创建 API Key</button>
      <div id="api-key-create-status" class="status"></div>
      <div id="api-key-create-result" class="status"></div>
    </form>
    <div class="grid">{cards}</div>
    <div id="api-key-toggle-status" class="status"></div>
    <script>
      function getCookie(name) {{
        const prefix = name + "=";
        return document.cookie.split("; ").find(item => item.startsWith(prefix))?.slice(prefix.length) || "";
      }}
      document.getElementById("api-key-form").addEventListener("submit", async (event) => {{
        event.preventDefault();
        const status = document.getElementById("api-key-create-status");
        const result = document.getElementById("api-key-create-result");
        status.className = "status";
        result.className = "status";
        result.textContent = "";
        status.textContent = "创建中...";
        const response = await fetch("/admin/api-keys", {{
          method: "POST",
          headers: {{
            "Content-Type": "application/json",
            "X-CSRF-Token": getCookie("tts_admin_csrf")
          }},
          body: JSON.stringify({{
            name: document.getElementById("api-key-name").value
          }})
        }});
        const payload = await response.json();
        if (!response.ok) {{
          status.className = "status error";
          status.textContent = payload.detail || "创建失败";
          return;
        }}
        status.className = "status ok";
        status.textContent = payload.detail;
        result.className = "status ok";
        result.textContent = "原始 Key（只展示一次）：" + payload.raw_key;
        window.setTimeout(() => window.location.reload(), 600);
      }});
      async function toggleKey(keyId, action) {{
        const status = document.getElementById("api-key-toggle-status");
        status.className = "status";
        status.textContent = "提交中...";
        const response = await fetch(`/admin/api-keys/${{keyId}}/${{action}}`, {{
          method: "POST",
          headers: {{
            "X-CSRF-Token": getCookie("tts_admin_csrf")
          }}
        }});
        const payload = await response.json();
        if (!response.ok) {{
          status.className = "status error";
          status.textContent = payload.detail || "操作失败";
          return;
        }}
        status.className = "status ok";
        status.textContent = payload.detail;
        window.setTimeout(() => window.location.reload(), 400);
      }}
    </script>
    """


def _accounts_page_body(accounts: list[dict[str, object]]) -> str:
    cards: list[str] = []
    for item in accounts:
        account_id = int(item["id"])
        enabled = int(item["enabled"]) == 1
        status = str(item.get("status") or "healthy")
        cooldown_until = html.escape(str(item.get("cooldown_until") or "无"))
        last_error = html.escape(str(item.get("last_error") or "无"))
        last_used = html.escape(str(item.get("last_used_at") or "未使用"))
        success_count = html.escape(str(item.get("success_count") or 0))
        failure_count = html.escape(str(item.get("failure_count") or 0))
        button_class = "danger" if enabled else "secondary"
        button_label = "停用" if enabled else "启用"
        status_class = "ok" if status == "healthy" else "warn" if status == "cooldown" else "error"
        cards.append(
            f"""
            <section class="card">
              <strong>{html.escape(str(item["name"]))}</strong>
              <div><span class="pill {status_class}">{html.escape(status)}</span></div>
              <div>sessionid：<code>{html.escape(_mask_secret(str(item["sessionid"])))}</code></div>
              <div>sid_guard：<code>{html.escape(_mask_secret(str(item["sid_guard"])))}</code></div>
              <div>uid_tt：<code>{html.escape(_mask_secret(str(item["uid_tt"])))}</code></div>
              <div>最后使用：{last_used}</div>
              <div>冷却到：{cooldown_until}</div>
              <div>成功 / 失败：{success_count} / {failure_count}</div>
              <div>最近错误：{last_error}</div>
              <div class="toolbar">
                <button
                  type="button"
                  class="secondary"
                  data-account-id="{account_id}"
                  data-name="{html.escape(str(item['name']), quote=True)}"
                  data-sessionid="{html.escape(str(item['sessionid']), quote=True)}"
                  data-sid-guard="{html.escape(str(item['sid_guard']), quote=True)}"
                  data-uid-tt="{html.escape(str(item['uid_tt']), quote=True)}"
                  onclick="fillAccountForm(this)"
                >编辑</button>
                <button type="button" class="secondary" onclick="testAccount({account_id})">测试</button>
                <button type="button" class="{button_class}" onclick="toggleAccount({account_id}, '{'disable' if enabled else 'enable'}')">{button_label}</button>
                <button type="button" class="warn" onclick="resetCooldown({account_id})">解除冷却</button>
              </div>
            </section>
            """
        )
    card_html = "\n".join(cards) if cards else "<p>还没有豆包凭据。当前服务就算配置别的都对，也不算 ready。</p>"
    return f"""
    <h1>豆包凭据池</h1>
    <p>这里管的是一组一组完整 Cookie。少一个字段都不算可用，纯轮询也不允许，必须让失效账号退出调度。</p>
    {_admin_nav()}
    <div class="split">
      <section class="card">
        <strong>新增 / 编辑凭据</strong>
        <form id="account-form">
          <input id="account-id" type="hidden">
          <label>名称
            <input id="account-name" type="text" maxlength="120" required>
          </label>
          <label>sessionid
            <textarea id="account-sessionid" required></textarea>
          </label>
          <label>sid_guard
            <textarea id="account-sid-guard" required></textarea>
          </label>
          <label>uid_tt
            <textarea id="account-uid-tt" required></textarea>
          </label>
          <div class="toolbar">
            <button type="submit">保存凭据</button>
            <button id="account-reset-button" class="secondary" type="button">清空表单</button>
          </div>
          <div id="account-form-status" class="status"></div>
        </form>
      </section>
      <section>
        <div class="grid">{card_html}</div>
      </section>
    </div>
    <div id="account-action-status" class="status"></div>
    <script>
      function getCookie(name) {{
        const prefix = name + "=";
        return document.cookie.split("; ").find(item => item.startsWith(prefix))?.slice(prefix.length) || "";
      }}
      function resetAccountForm() {{
        document.getElementById("account-id").value = "";
        document.getElementById("account-name").value = "";
        document.getElementById("account-sessionid").value = "";
        document.getElementById("account-sid-guard").value = "";
        document.getElementById("account-uid-tt").value = "";
      }}
      function fillAccountForm(button) {{
        document.getElementById("account-id").value = button.dataset.accountId;
        document.getElementById("account-name").value = button.dataset.name;
        document.getElementById("account-sessionid").value = button.dataset.sessionid;
        document.getElementById("account-sid-guard").value = button.dataset.sidGuard;
        document.getElementById("account-uid-tt").value = button.dataset.uidTt;
        window.scrollTo({{ top: 0, behavior: "smooth" }});
      }}
      document.getElementById("account-reset-button").addEventListener("click", resetAccountForm);
      document.getElementById("account-form").addEventListener("submit", async (event) => {{
        event.preventDefault();
        const accountId = document.getElementById("account-id").value;
        const path = accountId ? `/admin/accounts/${{accountId}}` : "/admin/accounts";
        const status = document.getElementById("account-form-status");
        status.className = "status";
        status.textContent = "保存中...";
        const response = await fetch(path, {{
          method: "POST",
          headers: {{
            "Content-Type": "application/json",
            "X-CSRF-Token": getCookie("tts_admin_csrf")
          }},
          body: JSON.stringify({{
            name: document.getElementById("account-name").value,
            sessionid: document.getElementById("account-sessionid").value,
            sid_guard: document.getElementById("account-sid-guard").value,
            uid_tt: document.getElementById("account-uid-tt").value
          }})
        }});
        const payload = await response.json();
        if (!response.ok) {{
          status.className = "status error";
          status.textContent = payload.detail || "保存失败";
          return;
        }}
        status.className = "status ok";
        status.textContent = payload.detail;
        resetAccountForm();
        window.setTimeout(() => window.location.reload(), 500);
      }});
      async function toggleAccount(accountId, action) {{
        await postAccountAction(`/admin/accounts/${{accountId}}/${{action}}`);
      }}
      async function resetCooldown(accountId) {{
        await postAccountAction(`/admin/accounts/${{accountId}}/reset-cooldown`);
      }}
      async function testAccount(accountId) {{
        await postAccountAction(`/admin/accounts/${{accountId}}/test`, {{
          text: "后台凭据测试"
        }});
      }}
      async function postAccountAction(path, body) {{
        const status = document.getElementById("account-action-status");
        status.className = "status";
        status.textContent = "处理中...";
        const response = await fetch(path, {{
          method: "POST",
          headers: {{
            "Content-Type": "application/json",
            "X-CSRF-Token": getCookie("tts_admin_csrf")
          }},
          body: body ? JSON.stringify(body) : undefined
        }});
        const payload = await response.json();
        if (!response.ok) {{
          status.className = "status error";
          status.textContent = payload.detail || "操作失败";
          return;
        }}
        status.className = "status ok";
        status.textContent = payload.detail;
        window.setTimeout(() => window.location.reload(), 500);
      }}
    </script>
    """


def _render_stats_table(title: str, rows: list[dict[str, Any]], first_key: str, first_label: str) -> str:
    if not rows:
        return f"<section class='card'><strong>{html.escape(title)}</strong><p>当前筛选范围内还没有数据。</p></section>"
    body_rows = []
    for row in rows:
        body_rows.append(
            f"""
            <tr>
              <td>{html.escape(str(row.get(first_key) or '未知'))}</td>
              <td>{html.escape(str(row.get('total_requests', 0)))}</td>
              <td>{html.escape(str(row.get('success_requests', 0)))}</td>
              <td>{html.escape(str(round(float(row.get('avg_latency_ms', 0) or 0), 2)))}</td>
            </tr>
            """
        )
    return f"""
    <section class="card">
      <strong>{html.escape(title)}</strong>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{html.escape(first_label)}</th>
              <th>总请求</th>
              <th>成功数</th>
              <th>平均耗时 ms</th>
            </tr>
          </thead>
          <tbody>{''.join(body_rows)}</tbody>
        </table>
      </div>
    </section>
    """


def _reports_page_body(
    snapshot: dict[str, Any],
    api_keys: list[dict[str, object]],
    accounts: list[dict[str, object]],
) -> str:
    filters = snapshot["filters"]
    totals = snapshot["totals"]
    selected_days = int(filters["days"])
    selected_result = str(filters["result"])
    selected_api_key = filters["api_key_id"]
    selected_account = filters["account_id"]
    api_key_options = ['<option value="">全部 API Key</option>']
    for item in api_keys:
        key_id = int(item["id"])
        selected = "selected" if selected_api_key == key_id else ""
        api_key_options.append(
            f'<option value="{key_id}" {selected}>{html.escape(str(item["name"]))}</option>'
        )
    account_options = ['<option value="">全部凭据</option>']
    for item in accounts:
        account_id = int(item["id"])
        selected = "selected" if selected_account == account_id else ""
        account_options.append(
            f'<option value="{account_id}" {selected}>{html.escape(str(item["name"]))}</option>'
        )
    failures_rows = snapshot["recent_failures"]
    failure_table = "<p>当前筛选范围内没有失败请求。</p>"
    if failures_rows:
        failure_lines = []
        for row in failures_rows:
            failure_lines.append(
                f"""
                <tr>
                  <td>{html.escape(str(row['created_at']))}</td>
                  <td>{html.escape(str(row['endpoint']))}</td>
                  <td>{html.escape(str(row['api_key_name']))}</td>
                  <td>{html.escape(str(row['account_name']))}</td>
                  <td>{html.escape(str(row['status_code']))}</td>
                  <td>{html.escape(str(row.get('error_type') or ''))}</td>
                  <td>{html.escape(str(row.get('error_detail') or ''))}</td>
                </tr>
                """
            )
        failure_table = f"""
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>时间</th>
                <th>接口</th>
                <th>API Key</th>
                <th>凭据</th>
                <th>状态码</th>
                <th>错误类型</th>
                <th>错误详情</th>
              </tr>
            </thead>
            <tbody>{''.join(failure_lines)}</tbody>
          </table>
        </div>
        """
    return f"""
    <h1>调用报表</h1>
    <p>这里看的是 SQLite 里的真实请求记录，不是进程内计数器幻觉。重启服务后，历史还在。</p>
    {_admin_nav()}
    <form method="get" action="/admin/reports">
      <div class="grid">
        <label>时间范围
          <select name="days">
            <option value="1" {"selected" if selected_days == 1 else ""}>最近 1 天</option>
            <option value="7" {"selected" if selected_days == 7 else ""}>最近 7 天</option>
            <option value="30" {"selected" if selected_days == 30 else ""}>最近 30 天</option>
            <option value="90" {"selected" if selected_days == 90 else ""}>最近 90 天</option>
          </select>
        </label>
        <label>结果
          <select name="result">
            <option value="all" {"selected" if selected_result == "all" else ""}>全部</option>
            <option value="success" {"selected" if selected_result == "success" else ""}>成功</option>
            <option value="failure" {"selected" if selected_result == "failure" else ""}>失败</option>
          </select>
        </label>
        <label>API Key
          <select name="api_key_id">{''.join(api_key_options)}</select>
        </label>
        <label>凭据
          <select name="account_id">{''.join(account_options)}</select>
        </label>
      </div>
      <div class="toolbar">
        <button type="submit">应用过滤</button>
      </div>
    </form>
    <div class="grid">
      <section class="card"><strong>总请求</strong><div>{totals["total_requests"]}</div></section>
      <section class="card"><strong>成功数</strong><div>{totals["success_requests"]}</div></section>
      <section class="card"><strong>失败数</strong><div>{totals["failed_requests"]}</div></section>
      <section class="card"><strong>成功率</strong><div>{totals["success_rate"]}%</div></section>
      <section class="card"><strong>平均耗时</strong><div>{totals["avg_latency_ms"]} ms</div></section>
    </div>
    {_render_stats_table("按接口统计", snapshot["by_endpoint"], "endpoint", "接口")}
    {_render_stats_table("按 API Key 统计", snapshot["by_api_key"], "api_key_name", "API Key")}
    {_render_stats_table("按凭据统计", snapshot["by_account"], "account_name", "凭据")}
    <section class="card">
      <strong>最近失败请求</strong>
      {failure_table}
    </section>
    """


def _test_tts_page_body(accounts: list[dict[str, object]]) -> str:
    options = ['<option value="">自动选择健康凭据</option>']
    for item in accounts:
        options.append(
            f'<option value="{int(item["id"])}">{html.escape(str(item["name"]))}</option>'
        )
    return f"""
    <h1>测试合成</h1>
    <p>这里走的是后台受控测试，不需要 API Key。它的意义是验证当前默认配置和凭据池到底能不能工作，不是给你开后门。</p>
    {_admin_nav()}
    <form id="test-tts-form">
      <label>测试文本
        <textarea id="test-text">后台测试一下当前配置</textarea>
      </label>
      <label>指定凭据（可选）
        <select id="test-account-id">{''.join(options)}</select>
      </label>
      <label>Speaker（可选）
        <input id="test-speaker" type="text" placeholder="留空则使用默认">
      </label>
      <label>格式
        <select id="test-format">
          <option value="">默认</option>
          <option value="aac">aac</option>
          <option value="mp3">mp3</option>
        </select>
      </label>
      <button type="submit">开始测试</button>
      <div id="test-tts-status" class="status"></div>
      <div id="test-tts-result" class="status"></div>
    </form>
    <script>
      function getCookie(name) {{
        const prefix = name + "=";
        return document.cookie.split("; ").find(item => item.startsWith(prefix))?.slice(prefix.length) || "";
      }}
      document.getElementById("test-tts-form").addEventListener("submit", async (event) => {{
        event.preventDefault();
        const status = document.getElementById("test-tts-status");
        const result = document.getElementById("test-tts-result");
        status.className = "status";
        result.className = "status";
        result.textContent = "";
        status.textContent = "测试中...";
        const accountIdValue = document.getElementById("test-account-id").value;
        const speakerValue = document.getElementById("test-speaker").value.trim();
        const formatValue = document.getElementById("test-format").value;
        const response = await fetch("/admin/test-tts", {{
          method: "POST",
          headers: {{
            "Content-Type": "application/json",
            "X-CSRF-Token": getCookie("tts_admin_csrf")
          }},
          body: JSON.stringify({{
            text: document.getElementById("test-text").value,
            account_id: accountIdValue ? Number(accountIdValue) : null,
            speaker: speakerValue || null,
            format: formatValue || null
          }})
        }});
        const payload = await response.json();
        if (!response.ok) {{
          status.className = "status error";
          status.textContent = payload.detail || "测试失败";
          return;
        }}
        status.className = "status ok";
        status.textContent = payload.detail;
        result.className = "status ok";
        result.textContent = `凭据：${{payload.account_name || "自动"}}\\nSpeaker：${{payload.speaker}}\\n格式：${{payload.format}}\\n音频大小：${{payload.audio_bytes}} bytes\\n尝试次数：${{payload.attempt_count}}`;
      }});
    </script>
    """


def _build_test_request(
    text: str,
    speaker: str | None = None,
    audio_format: str | None = None,
    speed: float | None = None,
    pitch: float | None = None,
):
    from service.models import TTSRequest

    return TTSRequest(
        text=text,
        speaker=speaker,
        format=audio_format,
        speed=speed,
        pitch=pitch,
    )


def _coerce_optional_int(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    return int(value)


@admin_router.get("/admin/setup")
async def admin_setup_page(request: Request):
    try:
        _ensure_runtime_settings_seeded()
        get_bootstrap_password()
    except ServiceUnavailableError as exc:
        return _render_error_page(request, "后台初始化不可用", exc.detail, exc.status_code)

    if _is_setup_completed():
        return _redirect(request, "/admin" if _is_authenticated(request) else "/admin/login")
    return _render_page(request, "初始化后台", _setup_page_body())


@admin_router.post("/admin/setup", response_model=AdminActionResponse)
async def admin_setup(payload: AdminSetupRequest, request: Request):
    validate_csrf(request)
    admin_settings = fetch_admin_settings()
    if admin_settings.get("setup_completed"):
        raise ForbiddenError("Admin setup has already been completed")

    expected_password = get_bootstrap_password()
    if not secrets.compare_digest(payload.bootstrap_password, expected_password):
        raise UnauthorizedError("Invalid bootstrap password")

    validate_new_password(payload.new_password)
    update_admin_password_hash(hash_password(payload.new_password))

    secure = should_use_secure_cookies(request)
    response = JSONResponse(
        _model_dump(AdminActionResponse(status="ok", detail="Admin setup completed", redirect_to="/admin"))
    )
    set_admin_session_cookie(response, secure=secure)
    set_csrf_cookie(response, generate_csrf_token(), secure=secure)
    return response


@admin_router.get("/admin/login")
async def admin_login_page(request: Request):
    _ensure_runtime_settings_seeded()
    if not _is_setup_completed():
        return _redirect(request, "/admin/setup")
    if _is_authenticated(request):
        return _redirect(request, "/admin")
    return _render_page(request, "后台登录", _login_page_body())


@admin_router.post("/admin/login", response_model=AdminActionResponse)
async def admin_login(payload: AdminLoginRequest, request: Request):
    validate_csrf(request)
    admin_settings = fetch_admin_settings()
    if not admin_settings.get("setup_completed") or not admin_settings.get("password_hash"):
        raise UnauthorizedError("Admin setup has not been completed")

    if not verify_password(payload.password, str(admin_settings["password_hash"])):
        raise UnauthorizedError("Invalid admin password")

    secure = should_use_secure_cookies(request)
    response = JSONResponse(
        _model_dump(AdminActionResponse(status="ok", detail="Login successful", redirect_to="/admin"))
    )
    set_admin_session_cookie(response, secure=secure)
    set_csrf_cookie(response, generate_csrf_token(), secure=secure)
    return response


@admin_router.post("/admin/logout", response_model=AdminActionResponse)
async def admin_logout(request: Request):
    validate_csrf(request)
    require_admin_session(request)

    secure = should_use_secure_cookies(request)
    response = JSONResponse(
        _model_dump(AdminActionResponse(status="ok", detail="Logout successful", redirect_to="/admin/login"))
    )
    clear_admin_session(response)
    set_csrf_cookie(response, generate_csrf_token(), secure=secure)
    return response


@admin_router.get("/admin")
async def admin_dashboard(request: Request):
    redirect = _guard_admin_page(request)
    if redirect is not None:
        return redirect
    return _render_page(
        request,
        "管理后台",
        _dashboard_body(
            fetch_service_settings(),
            fetch_api_keys(),
            fetch_doubao_accounts(),
            fetch_report_snapshot(days=7),
        ),
    )


@admin_router.get("/admin/settings")
async def admin_settings_page(request: Request):
    redirect = _guard_admin_page(request)
    if redirect is not None:
        return redirect
    return _render_page(request, "服务设置", _settings_page_body(fetch_service_settings()))


@admin_router.post("/admin/settings", response_model=AdminActionResponse)
async def admin_settings_update(payload: AdminServiceSettingsRequest, request: Request):
    validate_csrf(request)
    require_admin_session(request)
    settings_payload = _model_dump(payload)
    settings_payload["default_speaker"] = str(settings_payload["default_speaker"]).strip()
    if not settings_payload["default_speaker"]:
        raise BadRequestError("default_speaker must not be blank")
    save_service_settings(settings_payload)

    clear_service_config_cache()
    return JSONResponse(
        _model_dump(AdminActionResponse(status="ok", detail="Service settings saved"))
    )


@admin_router.get("/admin/accounts")
async def admin_accounts_page(request: Request):
    redirect = _guard_admin_page(request)
    if redirect is not None:
        return redirect
    return _render_page(request, "豆包凭据池", _accounts_page_body(fetch_doubao_accounts()))


@admin_router.post("/admin/accounts")
async def admin_accounts_create(payload: AdminAccountWriteRequest, request: Request):
    validate_csrf(request)
    require_admin_session(request)
    account_id = create_doubao_account_record(
        payload.name,
        payload.sessionid,
        payload.sid_guard,
        payload.uid_tt,
    )
    return JSONResponse(
        {
            "status": "ok",
            "detail": "Doubao account created",
            "account_id": account_id,
        }
    )


@admin_router.post("/admin/accounts/{account_id}")
async def admin_accounts_update(account_id: int, payload: AdminAccountWriteRequest, request: Request):
    validate_csrf(request)
    require_admin_session(request)
    update_doubao_account_record(
        account_id,
        payload.name,
        payload.sessionid,
        payload.sid_guard,
        payload.uid_tt,
    )
    return JSONResponse(
        {
            "status": "ok",
            "detail": "Doubao account updated",
            "account_id": account_id,
        }
    )


@admin_router.post("/admin/accounts/{account_id}/enable", response_model=AdminActionResponse)
async def admin_account_enable(account_id: int, request: Request):
    validate_csrf(request)
    require_admin_session(request)
    set_doubao_account_enabled(account_id, True)
    return JSONResponse(_model_dump(AdminActionResponse(status="ok", detail="Doubao account enabled")))


@admin_router.post("/admin/accounts/{account_id}/disable", response_model=AdminActionResponse)
async def admin_account_disable(account_id: int, request: Request):
    validate_csrf(request)
    require_admin_session(request)
    set_doubao_account_enabled(account_id, False)
    return JSONResponse(_model_dump(AdminActionResponse(status="ok", detail="Doubao account disabled")))


@admin_router.post("/admin/accounts/{account_id}/reset-cooldown", response_model=AdminActionResponse)
async def admin_account_reset_cooldown(account_id: int, request: Request):
    validate_csrf(request)
    require_admin_session(request)
    clear_doubao_account_cooldown(account_id)
    return JSONResponse(_model_dump(AdminActionResponse(status="ok", detail="Doubao account cooldown cleared")))


@admin_router.post("/admin/accounts/{account_id}/test", response_model=AdminTestTTSResponse)
async def admin_account_test(account_id: int, payload: AdminAccountTestRequest, request: Request):
    validate_csrf(request)
    require_admin_session(request)
    config = get_service_config()
    account = get_account_or_raise(account_id)
    tts_request = _build_test_request(
        text=payload.text,
        speaker=payload.speaker,
        audio_format=payload.format,
        speed=payload.speed,
        pitch=payload.pitch,
    )
    try:
        result, client = await synthesize_once(
            tts_request,
            config,
            cookie_override=build_account_cookie(account),
        )
    except ServiceHTTPError as error:
        mark_account_attempt_failure(account_id, error)
        raise error

    mark_account_attempt_success(account_id)
    return JSONResponse(
        _model_dump(
            AdminTestTTSResponse(
                status="ok",
                detail="Account test synthesis succeeded",
                account_id=account_id,
                account_name=str(account["name"]),
                speaker=str(client.config.speaker),
                format=str(client.config.format),
                audio_bytes=len(result.audio_data),
                attempt_count=int(result.attempt_count),
            )
        )
    )


@admin_router.get("/admin/api-keys")
async def admin_api_keys_page(request: Request):
    redirect = _guard_admin_page(request)
    if redirect is not None:
        return redirect
    return _render_page(request, "API Key 管理", _api_keys_page_body(fetch_api_keys()))


@admin_router.post("/admin/api-keys", response_model=AdminApiKeyCreateResponse)
async def admin_api_keys_create(payload: AdminApiKeyCreateRequest, request: Request):
    validate_csrf(request)
    require_admin_session(request)

    name = payload.name.strip()
    if not name:
        raise BadRequestError("API key name must not be blank")

    raw_key, key_prefix = generate_api_key()
    key_id = create_api_key_record(name, key_prefix, hash_api_key(raw_key))
    return JSONResponse(
        _model_dump(
            AdminApiKeyCreateResponse(
                status="ok",
                detail="API key created",
                key_id=key_id,
                name=name,
                raw_key=raw_key,
            )
        )
    )


@admin_router.post("/admin/api-keys/{api_key_id}/enable", response_model=AdminApiKeyStatusResponse)
async def admin_api_key_enable(api_key_id: int, request: Request):
    validate_csrf(request)
    require_admin_session(request)
    set_api_key_enabled(api_key_id, True)
    return JSONResponse(
        _model_dump(
            AdminApiKeyStatusResponse(
                status="ok",
                detail="API key enabled",
                key_id=api_key_id,
                enabled=True,
            )
        )
    )


@admin_router.post("/admin/api-keys/{api_key_id}/disable", response_model=AdminApiKeyStatusResponse)
async def admin_api_key_disable(api_key_id: int, request: Request):
    validate_csrf(request)
    require_admin_session(request)
    set_api_key_enabled(api_key_id, False)
    return JSONResponse(
        _model_dump(
            AdminApiKeyStatusResponse(
                status="ok",
                detail="API key disabled",
                key_id=api_key_id,
                enabled=False,
            )
        )
    )


@admin_router.get("/admin/reports")
async def admin_reports_page(request: Request):
    redirect = _guard_admin_page(request)
    if redirect is not None:
        return redirect

    days = _coerce_optional_int(request.query_params.get("days")) or 7
    if days <= 0:
        raise BadRequestError("days must be greater than 0")
    result = str(request.query_params.get("result") or "all")
    if result not in {"all", "success", "failure"}:
        raise BadRequestError("result must be one of: all, success, failure")
    api_key_id = _coerce_optional_int(request.query_params.get("api_key_id"))
    account_id = _coerce_optional_int(request.query_params.get("account_id"))
    return _render_page(
        request,
        "调用报表",
        _reports_page_body(
            fetch_report_snapshot(
                days=days,
                result=result,
                api_key_id=api_key_id,
                account_id=account_id,
            ),
            fetch_api_keys(),
            fetch_doubao_accounts(),
        ),
    )


@admin_router.get("/admin/test-tts")
async def admin_test_tts_page(request: Request):
    redirect = _guard_admin_page(request)
    if redirect is not None:
        return redirect
    return _render_page(request, "测试合成", _test_tts_page_body(fetch_doubao_accounts()))


@admin_router.post("/admin/test-tts", response_model=AdminTestTTSResponse)
async def admin_test_tts(payload: AdminTestTTSRequest, request: Request):
    validate_csrf(request)
    require_admin_session(request)
    config = get_service_config()
    tts_request = _build_test_request(
        text=payload.text,
        speaker=payload.speaker,
        audio_format=payload.format,
        speed=payload.speed,
        pitch=payload.pitch,
    )
    if payload.account_id is not None:
        account = get_account_or_raise(payload.account_id)
    else:
        account = select_account()

    account_id = int(account["id"])
    try:
        result, client = await synthesize_once(
            tts_request,
            config,
            cookie_override=build_account_cookie(account),
        )
    except ServiceHTTPError as error:
        mark_account_attempt_failure(account_id, error)
        raise error

    mark_account_attempt_success(account_id)
    return JSONResponse(
        _model_dump(
            AdminTestTTSResponse(
                status="ok",
                detail="Admin test synthesis succeeded",
                account_id=account_id,
                account_name=str(account["name"]),
                speaker=str(client.config.speaker),
                format=str(client.config.format),
                audio_bytes=len(result.audio_data),
                attempt_count=int(result.attempt_count),
            )
        )
    )
