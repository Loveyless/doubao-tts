# TTS 服务化计划书（执行版）

## 1. 文档目标

- 目标：将当前仓库从“核心库 + CLI”演进为“核心库 + HTTP 服务适配层”。
- 成功标准：文档能直接显示哪些已经完成、哪些未完成、下一步该做什么，以及做完后如何验收。
- 状态标记：
  - `[x]` 已完成：仓库中已有对应代码、测试或文档，且满足当前阶段验收标准。
  - `[-]` 部分完成：已有基础，但还未达到当前阶段验收标准。
  - `[ ]` 未完成：仓库中尚未落地。
- 标记原则：状态只基于仓库事实，不基于口头计划。

## 2. 当前仓库事实

### 2.1 核心边界

- 核心库位于 `doubao_tts.py`，公开 `TTSConfig`、`TTSResult`、`DoubaoTTS`。
- 合成主入口为 `DoubaoTTS.synthesize()` 和 `DoubaoTTS.synthesize_sync()`。
- `TTSResult` 直接返回 `audio_data` 字节数据，已经具备被 HTTP 层直接复用的基础。
- `SPEAKERS` 已维护简称到完整 speaker ID 的映射。

### 2.2 现有调用链

- 命令行入口位于 `doubao_tts_cli.py`。
- CLI 负责参数解析、构造 `TTSConfig`、调用 `DoubaoTTS.synthesize()`、将 `result.audio_data` 写入文件。
- 当前 CLI 是适配层，不是协议核心。

### 2.3 当前验证基础

- 底层测试位于 `tests/test_doubao_tts.py`。
- 当前已覆盖 Cookie 规范化、成功完成态、超时、不完整音频、block 重试、同步入口误用等关键路径。
- 协议观察脚本位于 `scripts/observe_session.py`。
- 协议观察文档位于 `docs/protocol_observation.md`。

### 2.4 当前限制

- 上游仍依赖豆包 WebSocket 协议和 Cookie，这个外部依赖不稳定，HTTP 服务只是隔离了影响面，没有消灭风险。
- 服务进程可以在未配置 `TTS_COOKIE` 时启动，但此时 `GET /healthz` 会返回 `503`，不能把“进程活着”误判成“服务可用”。
- 当前配置治理仍以环境变量为主，尚无配置中心、热更新或租户级隔离。
- 当前鉴权、指标、并发限制和容器化只做到单服务基线：能部署，但还不是大规模生产治理方案。

## 3. 当前状态总览

- [x] 核心库已具备文本转音频的基本能力。
- [x] CLI 适配层已独立存在。
- [x] 底层协议回归测试已存在。
- [x] 协议观察脚本与观察文档已存在。
- [x] HTTP 服务层与接口测试已落地。
- [x] 服务模式已改为显式 Cookie 注入和环境变量配置治理。
- [x] README、本地启动说明、服务器部署说明已落地。
- [x] 流式接口、并发限制、服务级超时、简单鉴权、指标接口已落地。
- [-] `Dockerfile`、`.dockerignore` 和 Docker 文档已落地，但当前环境缺少 `docker` 命令，镜像构建未验证。

## 4. 服务化目标

### 4.1 目标

- 提供本地和服务器均可部署的 HTTP TTS 服务。
- 调用方通过稳定 HTTP 接口发送文本和可选参数后获得音频二进制响应。
- 上层应用通过 HTTP 调用复用能力，不直接依赖 `doubao_tts.py` 的内部细节。

### 4.2 成功标准

- 服务可在 `127.0.0.1:8080` 启动；配置 `TTS_COOKIE` 后 `/healthz` 返回 `200 OK`。
- 调用方可通过 `POST /v1/tts` 获取完整音频，或通过 `POST /v1/tts/stream` 获取流式响应。
- 服务可在服务器环境运行，不依赖交互式 CLI，也不依赖仓库内本地 Cookie 文件。
- 服务保留当前库已有的失败判定与 block 重试行为。
- 服务层具备健康检查、参数校验、错误映射、基础日志、简单鉴权和指标能力。
## 5. 非目标

- 当前范围不实现多 Provider 抽象。
- 当前范围不实现账号池、多租户、鉴权平台、管理后台。
- 当前范围不实现配置中心、持久化队列或任务编排。
- 当前范围不把“服务端发声播放”作为通用能力。
## 6. 推荐架构与接口边界

### 6.1 总体策略

- 保持 `doubao_tts.py` 作为协议与合成核心。
- 新增 HTTP 服务层，负责请求校验、配置注入、结果映射、HTTP 响应输出。
- 避免在服务层直接复用 CLI 逻辑；CLI 和 HTTP 都应依赖核心库，而不是互相调用。

### 6.2 目标目录结构

```text
service/
  app.py                # HTTP 应用入口
  models.py             # 请求/响应模型
  config.py             # 环境变量与服务配置
  errors.py             # 领域错误到 HTTP 状态码映射
  dependencies.py       # TTS 客户端创建与注入
tests/
  test_service_api.py   # HTTP API 回归测试
```

### 6.3 技术选型

- HTTP 框架：FastAPI。
- 运行器：uvicorn。
- 测试方式：优先使用框架测试客户端做接口回归。

### 6.4 HTTP 接口草案

#### `POST /v1/tts`

请求 JSON：

```json
{
  "text": "你好，欢迎使用 TTS 服务。",
  "speaker": "taozi",
  "format": "aac",
  "speed": 0,
  "pitch": 0
}
```

字段约束：

- `text`：必填，非空字符串。
- `speaker`：可选，支持 `SPEAKERS` 中的简称，也允许透传完整 speaker ID。
- `format`：第一阶段仅允许 `aac`、`mp3`。
- `speed`：沿用当前库的 `-1.0 ~ 1.0`。
- `pitch`：沿用当前库的 `-1.0 ~ 1.0`。

成功响应：

- `200 OK`
- `Content-Type: audio/aac` 或 `audio/mpeg`
- 响应体为音频二进制

建议响应头：

- `X-TTS-Speaker`
- `X-TTS-Format`
- `X-TTS-Attempt-Count`

失败响应：

- `400 Bad Request`：请求参数不合法
- `502 Bad Gateway`：上游 WebSocket 握手失败、Cookie 失效、协议异常
- `504 Gateway Timeout`：合成超时或结果不完整
- `500 Internal Server Error`：服务内部错误

#### `GET /healthz`

响应：

- 已配置 `TTS_COOKIE` 时返回 `200 OK` 和 `{"status": "ok"}`。
- 未配置 `TTS_COOKIE` 或配置非法时返回 `503 Service Unavailable`。

#### `GET /v1/speakers`

- 用途：返回当前支持的 speaker 映射，避免调用方硬编码。

#### `POST /v1/tts/stream`

- 请求体与 `POST /v1/tts` 相同。
- 成功时返回 `200 OK`，`Content-Type` 为 `audio/aac` 或 `audio/mpeg`，响应体为音频字节流。
- 若上游没有产生分片但最终合成成功，服务会退回一次性二进制响应；不把“假流式”硬装成实时流式。
- 失败状态码沿用 `400` / `401` / `502` / `504` / `500`。

#### `GET /metrics`

- 返回 `text/plain` 指标文本。
- 当配置 `TTS_AUTH_TOKEN` 时，同样要求 `Authorization: Bearer <token>`。
- 当 `TTS_ENABLE_METRICS=false` 时返回 `503 Service Unavailable`。

## 7. 配置方案

### 7.1 原则

- 服务模式不依赖本地 `.cookie.json` 自动发现。
- 服务配置优先使用环境变量，而不是在运行时写入仓库文件。
- 文件配置模式只保留给 CLI 和本地开发场景。

### 7.2 建议配置项

- `TTS_COOKIE`：服务 ready 的前提；未配置时 `/healthz` 返回 `503`。
- `TTS_DEFAULT_SPEAKER`
- `TTS_DEFAULT_FORMAT`
- `TTS_HOST`
- `TTS_PORT`
- `TTS_LOG_LEVEL`
- `TTS_RETRY_ON_BLOCK`
- `TTS_RETRY_MAX_RETRIES`
- `TTS_RETRY_BACKOFF_SECONDS`
- `TTS_RETRY_BACKOFF_MULTIPLIER`
- `TTS_RETRY_BACKOFF_JITTER_RATIO`
- `TTS_REQUEST_TIMEOUT_SECONDS`
- `TTS_MAX_CONCURRENCY`
- `TTS_AUTH_TOKEN`
- `TTS_ENABLE_METRICS`
### 7.3 配置边界

- 若环境变量中未提供 Cookie，服务应启动失败或健康检查失败，而不是静默回退到本地文件。
- 若未来仍需兼容本地开发文件配置，应由服务配置层显式控制，不能由底层库默认决定。

## 8. 当前推进范围

- 当前结果：`M0` ~ `M4` 的代码与文档均已落地，计划书进入维护态，不再是待启动草稿。
- 当前迭代的完成定义：
  - `service/app.py`、`service/models.py`、`service/errors.py`、`service/dependencies.py`、`service/config.py`、`service/__main__.py` 已存在。
  - `tests/test_service_api.py` 已存在并覆盖健康检查、错误映射、配置解析、流式接口、鉴权、指标。
  - `POST /v1/tts`、`POST /v1/tts/stream`、`GET /healthz`、`GET /v1/speakers`、`GET /metrics` 可执行。
  - `README.md`、`Dockerfile`、`.dockerignore` 已落地。
  - 已验证：`python -m unittest tests.test_doubao_tts tests.test_service_api` 通过；带最小 `TTS_COOKIE` 的 `python -m service` 可启动并让 `/healthz` 返回 `200`。
  - 未验证：当前环境缺少 `docker` 命令，无法实测 `docker build`。
## 9. 里程碑与执行看板

### M0：现有基线整理

- [x] 核心库与 CLI 分层已存在。
- [x] `SPEAKERS` 映射已存在。
- [x] 底层协议回归测试已存在。
- [x] 协议观察脚本已存在。
- [x] 协议观察文档已存在。

验收标准：

- [x] 服务化输入基线已经明确。

### M1：最小可用 HTTP 服务

目标：先把 HTTP 边界立起来，同时补最小接口回归；不允许“先写接口，后补测试”。

#### M1-1 依赖与服务骨架

- [x] Add `fastapi` to `requirements.txt`.
- [x] Add `uvicorn` to `requirements.txt`.
- [x] Create `service/app.py`.
- [x] Create `service/models.py`.
- [x] Create `service/errors.py`.
- [x] Create `service/dependencies.py`.

验收标准：

- [x] Service modules are importable by Python.
- [x] `python -m service` 可启动服务。

#### M1-2 请求模型与参数校验

- [x] Define the TTS request model.
- [x] Validate `text` as a non-empty string.
- [x] Restrict `format` to `aac` and `mp3`.
- [x] Validate `speed` in `-1.0 ~ 1.0`.
- [x] Validate `pitch` in `-1.0 ~ 1.0`.
- [x] Support speaker alias mapping and full speaker ID passthrough.

验收标准：

- [x] Blank text returns `400`.
- [x] Invalid format returns `400`.
- [x] Out-of-range parameters return `400`.

#### M1-3 基础只读接口

- [x] Implement `GET /healthz`.
- [x] Implement `GET /v1/speakers`.

验收标准：

- [x] 配置 `TTS_COOKIE` 时 `/healthz` 返回 `200`。
- [x] 配置 `TTS_COOKIE` 时 `/healthz` 返回 `{"status": "ok"}`。
- [x] 未配置 `TTS_COOKIE` 时 `/healthz` 返回 `503`。
- [x] `/v1/speakers` 返回当前 speaker 映射。

#### M1-4 `POST /v1/tts` 主路径

- [x] HTTP layer calls `DoubaoTTS`.
- [x] Request parameters are mapped to `TTSConfig` correctly.
- [x] Return `TTSResult.audio_data`.
- [x] Return the correct `Content-Type` for the chosen format.
- [x] Return `X-TTS-Speaker`, `X-TTS-Format`, and `X-TTS-Attempt-Count`.

验收标准：

- [x] Normal requests return `200`.
- [x] Successful responses contain non-empty audio bytes.
- [x] `Content-Type` matches the requested format.

#### M1-5 HTTP 错误映射与接口测试

- [x] Request parameter errors map to `400`.
- [x] Handshake / Cookie / protocol errors map to `502`.
- [x] Timeout or incomplete audio results map to `504`.
- [x] Unclassified failures map to `500`.
- [x] Create `tests/test_service_api.py`.
- [x] Cover blank-text `400`.
- [x] Cover invalid-format `400`.
- [x] Cover upstream `502`.
- [x] Cover upstream timeout / incomplete-result `504`.
- [x] Cover successful binary audio responses.
- [x] Cover `/healthz`.
- [x] Cover `/v1/speakers`.

验收标准：

- [x] HTTP API tests are executable.
- [x] Existing core tests still pass.

### M2：服务模式解耦与错误治理

目标：把“能跑”变成“可部署”，重点处理 Cookie 来源、日志出口和错误分类。

- [x] 新建 `service/config.py`。
- [x] 服务模式只接受显式 Cookie 配置。
- [x] 服务请求不再依赖仓库本地 Cookie 文件。
- [x] `400` / `502` / `504` / `500` 错误映射已显式化。
- [x] 服务路径下库层不再直接 `print`，日志通过 `logging` 收口。

验收标准：

- [x] 缺失 Cookie 时显式失败。
- [x] 上游 Cookie / 协议错误返回 `502`。
- [x] 超时或不完整音频返回 `504`。
- [x] 服务请求日志不再污染默认输出；CLI 保留自身交互输出。

### M3：文档与部署

目标：把“开发可用”变成“第三方可接入”。

- [x] README 增加服务模式用法。
- [x] 增加本地启动说明。
- [x] 增加服务器部署说明。
- [x] 增加请求示例和环境变量说明。
- [x] 增加 Docker 部署说明。

验收标准：

- [x] 仓库文档已覆盖启动、调用、部署和环境变量信息。
- [x] 核心测试和 HTTP 接口测试均可执行。

### M4：可选增强

- [x] 流式输出接口。
- [x] 调用并发限制。
- [x] 请求超时控制。
- [x] 简单鉴权。
- [-] 容器化部署（`Dockerfile` / `.dockerignore` / README 已落地，当前环境未验证 `docker build`）。
- [x] 观测指标。

验收标准：

- [x] 流式接口、鉴权、指标、超时、配置解析已有测试覆盖。
- [x] 服务级并发限制与请求级超时已在应用层显式实现。
- [-] 容器镜像构建未在当前环境验证，因为本机缺少 `docker` 命令。

说明：

- [x] 以上增强项必须在 M1、M2 稳定后再做。
## 10. 风险与对策

### 10.1 上游协议风险

风险：

- 当前实现依赖豆包 WebSocket 协议和 Cookie，属于外部不稳定依赖。

对策：

- 保持 HTTP 层与协议层分离。
- 保留现有协议观察脚本和底层回归测试。
- 错误响应中明确区分“服务错误”和“上游错误”。

### 10.2 配置风险

风险：

- 当前默认文件读写模式不适合服务部署和容器环境。

对策：

- 服务模式仅接受显式配置。
- 文件模式只保留给 CLI 和本地开发。

### 10.3 一致性风险

风险：

- 若服务接口暴露了当前未验证的格式或参数，会造成文档与行为不一致。

对策：

- 第一阶段仅暴露已验证的 `aac`、`mp3`。
- 接口参数范围严格沿用当前实现。

### 10.4 并发稳定性风险

风险：

- 当前实现虽然已经加入服务级并发限制和请求超时，但仍然只是单进程内治理，没有排队、熔断和多实例协调。

对策：

- 通过 `TTS_MAX_CONCURRENCY` 控制并发，通过 `TTS_REQUEST_TIMEOUT_SECONDS` 控制单次请求生命周期。
- 如需长期公开服务，再补反向代理限流、熔断、监控告警和多实例部署策略。

## 11. 最小验收命令

### 11.1 测试命令

- 底层测试：`python -m unittest tests.test_doubao_tts`
- HTTP 接口测试：`python -m unittest tests.test_service_api`
- 完整回归：`python -m unittest tests.test_doubao_tts tests.test_service_api`

### 11.2 启动命令

- 本地启动：`python -m service`
- 直接使用 `uvicorn`：`python -m uvicorn service.app:app --host 127.0.0.1 --port 8080`

### 11.3 手工验证命令

- 先配置最小环境：`TTS_COOKIE='sessionid=...; sid_guard=...; uid_tt=...'`
- 健康检查：`curl http://127.0.0.1:8080/healthz`
- speaker 列表：`curl http://127.0.0.1:8080/v1/speakers`
- TTS 请求：`curl -X POST http://127.0.0.1:8080/v1/tts -H "Content-Type: application/json" -d "{\"text\":\"你好\",\"speaker\":\"taozi\",\"format\":\"aac\",\"speed\":0,\"pitch\":0}" --output output.aac`
- 流式 TTS：`curl -X POST http://127.0.0.1:8080/v1/tts/stream -H "Content-Type: application/json" -d "{\"text\":\"你好\",\"speaker\":\"taozi\",\"format\":\"aac\"}" --output stream_output.aac`
- 指标查看：`curl http://127.0.0.1:8080/metrics`
- 开启鉴权后的指标查看：`curl http://127.0.0.1:8080/metrics -H "Authorization: Bearer <token>"`

说明：

- 已实际验证：`python -m unittest tests.test_doubao_tts tests.test_service_api` 通过。
- 已实际验证：带最小 `TTS_COOKIE` 的 `python -m service` 可启动，并且 `/healthz` 返回 `200` 与 `{"status":"ok"}`。
- 未验证：`docker build`，因为当前环境缺少 `docker` 命令。
## 12. 状态维护规则

- 只有代码、测试、文档已经落地，且满足对应验收标准，任务才能标记为 `[x]`。
- 已完成的任务必须及时从 `[ ]` 或 `[-]` 更新为 `[x]`，不要让计划书状态滞后于仓库事实。
- 只有“有基础但未达到验收标准”时，任务才允许标记为 `[-]`。
- 不允许因为“代码写了一半”就标记为 `[x]`。
- 如范围变化，新增任务，不覆盖旧状态。
- 更新状态时必须同步更新相关验收条目，避免任务状态和验收状态脱节。

## 13. 推荐执行顺序

1. 完成 `M1-1` 依赖与服务骨架。
2. 完成 `M1-2` 请求模型与参数校验。
3. 完成 `M1-3` 基础只读接口。
4. 完成 `M1-4` `POST /v1/tts` 主路径。
5. 完成 `M1-5` HTTP 错误映射与接口测试。
6. `M1` 验收通过后，再进入 `M2`。
7. `M2` 稳定后，再进入 `M3` 和 `M4`。

## 14. 最终建议

- 这次改造应被定义为“把现有豆包 TTS 客户端封装成独立 HTTP 服务”。
- 不要一上来就做多 Provider 平台，不要先做本机播放，不要先做复杂插件系统。
- 先把单一职责做对：稳定收文本，稳定吐音频，稳定可部署。这个边界立住后，再扩展才不会烂掉。
