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

- `requirements.txt` 当前仅包含 `websockets`，尚未引入 HTTP 服务框架。
- `DoubaoTTS` 在未显式传入 Cookie 时会默认读取本地 `.cookie.json` / `.cookie`，存在文件系统耦合。
- 库层仍有直接 `print` 输出与 Cookie 保存副作用，不适合作为服务模式下的默认行为。
- `TTSConfig` 注释提到 `wav`，但现有 CLI 仅实际暴露 `aac`、`mp3`；未验证能力不应直接对外承诺。

## 3. 当前状态总览

- [x] 核心库已具备文本转音频的基本能力。
- [x] CLI 适配层已独立存在。
- [x] 底层协议回归测试已存在。
- [x] 协议观察脚本与观察文档已存在。
- [ ] HTTP 服务层尚未实现。
- [ ] HTTP 接口测试尚未实现。
- [ ] 服务配置层尚未从核心默认文件读取策略中解耦。
- [ ] 服务部署文档尚未实现。

## 4. 服务化目标

### 4.1 目标

- 提供本地和服务器均可部署的 HTTP TTS 服务。
- 调用方通过稳定 HTTP 接口发送文本和可选参数后获得音频二进制响应。
- 上层应用通过 HTTP 调用复用能力，不直接依赖 `doubao_tts.py` 的内部细节。

### 4.2 成功标准

- 服务可在 `127.0.0.1:8080` 启动。
- 调用方可通过 `POST /v1/tts` 获得 `audio/aac` 或 `audio/mpeg` 响应。
- 服务可在服务器环境运行，不依赖交互式 CLI。
- 服务保留当前库已有的失败判定与 block 重试行为。
- 服务层具备最小健康检查、参数校验、错误映射和基础日志能力。

## 5. 非目标

- 第一阶段不实现多 Provider 抽象。
- 第一阶段不实现账号池、多租户、鉴权平台、管理后台。
- 第一阶段不承诺流式音频输出。
- 第一阶段不把“服务端发声播放”作为通用能力。

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

```json
{
  "status": "ok"
}
```

#### `GET /v1/speakers`

- 用途：返回当前支持的 speaker 映射，避免调用方硬编码。

## 7. 配置方案

### 7.1 原则

- 服务模式不依赖本地 `.cookie.json` 自动发现。
- 服务配置优先使用环境变量，而不是在运行时写入仓库文件。
- 文件配置模式只保留给 CLI 和本地开发场景。

### 7.2 建议配置项

- `TTS_COOKIE`
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

### 7.3 配置边界

- 若环境变量中未提供 Cookie，服务应启动失败或健康检查失败，而不是静默回退到本地文件。
- 若未来仍需兼容本地开发文件配置，应由服务配置层显式控制，不能由底层库默认决定。

## 8. 当前推进范围

- 当前默认只推进 `M1：最小可用 HTTP 服务`。
- `M2`、`M3`、`M4` 不得提前插队，除非 `M1` 验收失败且必须调整基础设计。
- 当前迭代的完成定义：
  - `service/app.py` 已存在。
  - `service/models.py` 已存在。
  - `service/errors.py` 已存在。
  - `service/dependencies.py` 已存在。
  - `tests/test_service_api.py` 已存在。
  - `POST /v1/tts`、`GET /healthz`、`GET /v1/speakers` 可执行。
  - HTTP 最小回归测试可执行。

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

- [ ] 在 `requirements.txt` 增加 `fastapi`。
- [ ] 在 `requirements.txt` 增加 `uvicorn`。
- [ ] 新建 `service/app.py`。
- [ ] 新建 `service/models.py`。
- [ ] 新建 `service/errors.py`。
- [ ] 新建 `service/dependencies.py`。

验收标准：

- [ ] 服务模块可被 Python 导入。
- [ ] `python -m uvicorn service.app:app --host 127.0.0.1 --port 8080` 可启动。

#### M1-2 请求模型与参数校验

- [ ] 定义 TTS 请求模型。
- [ ] 校验 `text` 为非空字符串。
- [ ] 校验 `format` 仅允许 `aac`、`mp3`。
- [ ] 校验 `speed` 范围为 `-1.0 ~ 1.0`。
- [ ] 校验 `pitch` 范围为 `-1.0 ~ 1.0`。
- [ ] 支持 speaker 简称映射与完整 speaker ID 透传。

验收标准：

- [ ] 空文本返回 `400`。
- [ ] 非法格式返回 `400`。
- [ ] 非法范围参数返回 `400`。

#### M1-3 基础只读接口

- [ ] 实现 `GET /healthz`。
- [ ] 实现 `GET /v1/speakers`。

验收标准：

- [ ] `/healthz` 返回 `200`。
- [ ] `/healthz` 响应体包含 `{"status": "ok"}`。
- [ ] `/v1/speakers` 返回当前 speaker 映射。

#### M1-4 `POST /v1/tts` 主路径

- [ ] HTTP 层调用 `DoubaoTTS`。
- [ ] 请求参数正确映射到 `TTSConfig`。
- [ ] 返回 `TTSResult.audio_data`。
- [ ] 根据输出格式返回正确 `Content-Type`。
- [ ] 返回 `X-TTS-Speaker`、`X-TTS-Format`、`X-TTS-Attempt-Count`。

验收标准：

- [ ] 正常请求返回 `200`。
- [ ] 成功响应体为非空音频二进制。
- [ ] `Content-Type` 与请求格式一致。

#### M1-5 HTTP 错误映射与接口测试

- [ ] 请求参数错误映射为 `400`。
- [ ] 上游握手失败、Cookie 失效、协议异常映射为 `502`。
- [ ] 上游超时或结果不完整映射为 `504`。
- [ ] 未分类异常映射为 `500`。
- [ ] 新建 `tests/test_service_api.py`。
- [ ] 覆盖空文本 `400`。
- [ ] 覆盖非法格式 `400`。
- [ ] 覆盖上游错误 `502`。
- [ ] 覆盖上游超时或不完整结果 `504`。
- [ ] 覆盖正常请求返回音频二进制。
- [ ] 覆盖 `/healthz`。
- [ ] 覆盖 `/v1/speakers`。

验收标准：

- [ ] HTTP 接口测试可执行。
- [ ] 现有底层测试仍可执行。

### M2：服务模式解耦与错误治理

目标：把“能跑”变成“可部署”，重点处理 Cookie 来源、日志出口和错误分类。

- [ ] 新建 `service/config.py`。
- [ ] 服务模式下仅接受显式 Cookie 配置。
- [ ] 服务路径不再依赖仓库目录下写入 Cookie 文件。
- [ ] 明确错误映射到 `400` / `502` / `504` / `500`。
- [ ] 收敛库层直接 `print` 输出，确保服务响应不被污染。

验收标准：

- [ ] 未配置 Cookie 时，服务行为明确失败。
- [ ] 上游 Cookie 错误或协议错误返回 `502`。
- [ ] 上游超时或结果不完整返回 `504`。
- [ ] 服务日志可预测，不污染 HTTP 响应。

### M3：文档与部署

目标：把“开发可用”变成“第三方可接入”。

- [ ] README 增加服务模式用法。
- [ ] 增加本地启动说明。
- [ ] 增加服务器部署说明。
- [ ] 增加请求示例和环境变量说明。

验收标准：

- [ ] 第三方调用方可按文档完成接入。
- [ ] 核心测试和 HTTP 接口测试均可执行。

### M4：可选增强

- [ ] 流式输出接口。
- [ ] 调用并发限制。
- [ ] 请求超时控制。
- [ ] 简单鉴权。
- [ ] 容器化部署。
- [ ] 观测指标。

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

- 当前核心没有服务级并发治理和限流能力。

对策：

- 第一阶段限制为轻量服务使用。
- 如需服务器长期运行，再补充并发限制、超时和熔断策略。

## 11. 最小验收命令

### 11.1 测试命令

- 底层测试：`python -m unittest tests.test_doubao_tts`
- HTTP 接口测试：`python -m unittest tests.test_service_api`

### 11.2 启动命令

- 本地启动：`python -m uvicorn service.app:app --host 127.0.0.1 --port 8080`

### 11.3 手工验证命令

- 健康检查：`curl http://127.0.0.1:8080/healthz`
- speaker 列表：`curl http://127.0.0.1:8080/v1/speakers`
- TTS 请求：`curl -X POST http://127.0.0.1:8080/v1/tts -H "Content-Type: application/json" -d "{\"text\":\"你好\",\"speaker\":\"taozi\",\"format\":\"aac\",\"speed\":0,\"pitch\":0}" --output output.aac`

说明：

- 以上命令是计划内验收路径，不代表当前已经验证通过。
- 只有实际执行通过后，相关任务才能从 `[ ]` 改为 `[x]`。

## 12. 状态维护规则

- 只有代码、测试、文档已经落地，且满足对应验收标准，任务才能标记为 `[x]`。
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
