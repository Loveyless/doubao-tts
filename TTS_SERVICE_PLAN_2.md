# TTS 服务化计划书（二期执行版）

## 1. 文档目标

- 目标：在当前“核心库 + HTTP 服务”基础上，演进为“核心库 + HTTP 服务 + 管理后台 + SQLite 持久化”的可维护部署形态。
- 成功标准：文档能直接指导后续开发，明确当前仓库事实、二期目标、接口边界、数据模型、失败策略、验收方式。
- 状态标记：
  - `[x]` 已存在：当前仓库中已经落地。
  - `[-]` 部分存在：已有基础，但还不满足二期目标。
  - `[ ]` 未开始：当前仓库中尚未落地。

## 2. 当前仓库事实

### 2.1 当前服务边界

- [x] 当前 HTTP 服务入口位于 `service/app.py`，已提供 `POST /v1/tts`、`POST /v1/tts/stream`、`GET /healthz`、`GET /v1/speakers`、`GET /metrics`。
- [x] 当前请求模型位于 `service/models.py`，公开参数为 `text`、`speaker`、`format`、`speed`、`pitch`。
- [x] 当前服务配置位于 `service/config.py`；部署期秘密仍来自环境变量，默认服务参数已迁移到 SQLite。
- [x] 当前公开 TTS 接口已切换为 API Key 鉴权；`/metrics` 已收口到后台登录态，不再接受兼容 token。
- [x] 当前指标仍保留进程内计数器，定义在 `service/app.py` 的 `ServiceMetrics` 中；同时请求日志与报表已持久化到 SQLite。

### 2.2 当前豆包凭据边界

- [x] 底层库位于 `doubao_tts.py`。
- [x] 豆包 Cookie 必需字段固定为 `sessionid`、`sid_guard`、`uid_tt`。
- [x] 当前底层库已提供 `normalize_cookie()`，可用于规范化 Cookie 串并检查缺失字段。
- [x] 当前底层库已维护 `SPEAKERS` 映射与 `block` 错误码识别逻辑。

### 2.3 当前二期缺口

- [x] 当前已具备 Web 管理后台：初始化、登录、设置页、凭据池页、API Key 页、报表页、测试合成页。
- [x] 当前已具备多调用方 API Key 管理能力。
- [x] 当前调用日志已持久化，后台可按时间、结果、API Key、凭据组查看报表。
- [x] 当前已支持多组豆包凭据池、健康优先轮询、冷却与一次受控切换。
- [x] 当前服务配置已可在线修改，并可在后台查看当前生效默认参数。

## 3. 二期问题定义

当前服务已经能合成音频，但部署形态仍然停留在“靠环境变量和手工运维”的阶段。这个阶段的主要问题不是功能缺失，而是治理能力不足：

- 运维层问题：豆包 Cookie、默认参数、并发和超时等配置无法通过后台维护。
- 安全层问题：不能让匿名请求直接调用 TTS，也不能把 API 密钥放进 URL。
- 稳定性问题：若未来接入多组豆包 Cookie，纯轮询会把失效凭据和健康凭据混在一起轮着失败。
- 可观测性问题：当前 `/metrics` 只能看内存计数，不能满足报表、排查、按 API Key 统计的需要。

结论：二期不是“加个页面”，而是把当前服务升级成一个最小可治理系统。

## 4. 二期目标

### 4.1 业务目标

- 提供一个单管理员密码的 Web 管理后台。
- 管理后台可维护多组豆包 Cookie 凭据，并查看每组凭据的状态。
- 管理后台可创建、停用、查看多个 API Key，供外部系统调用 TTS。
- 管理后台可查看调用报表和基础聚合统计。
- 管理后台可维护默认 TTS 参数和服务级参数。

### 4.2 技术目标

- 运行期可变配置迁移到 SQLite，不再依赖修改环境变量才能调整业务配置。
- 公开 TTS 接口强制使用 API Key 鉴权。
- 豆包凭据池支持“健康优先轮询”，而不是无脑轮询。
- 调用日志持久化到 SQLite，支持按时间、接口、结果、API Key、凭据组查询。
- 保持 `doubao_tts.py` 为核心协议层，不把后台逻辑塞进底层库。

### 4.3 成功标准

- 首次部署后，管理员可通过受控初始化流程设置后台密码。
- 后台登录成功后，可新增、编辑、启停豆包凭据组。
- 每组凭据都要求完整的 `sessionid`、`sid_guard`、`uid_tt` 三字段，不允许半组数据保存。
- 后台可创建多个 API Key；原始 key 只在创建时展示一次。
- 外部调用 `POST /v1/tts`、`POST /v1/tts/stream` 时，未提供有效 API Key 返回 `401`。
- 后台可查看调用报表，且报表在服务重启后仍然存在。
- 当存在多组健康凭据时，请求可按健康优先轮询分配。
- 当选中的凭据命中 `block`、Cookie 失效或握手失败时，系统会记录失败并进入冷却，而不是继续无脑轮询。

## 5. 非目标

- 当前范围不实现多管理员、多用户、RBAC 权限系统。
- 当前范围不实现多 Provider 抽象。
- 当前范围不实现分布式队列、跨实例共享调度器、账号池中心化协调。
- 当前范围不把全文请求文本默认持久化到报表库中。
- 当前范围不允许通过 URL query string 传递 API 密钥。
- 当前范围不允许匿名首访直接抢占后台密码初始化。

## 6. 配置分层原则

二期必须把“可在线维护的业务配置”和“部署期不可变秘密”分开，否则迟早失控。

### 6.1 保留在环境变量中的配置

- `TTS_SQLITE_PATH`：SQLite 文件路径。
- `TTS_SESSION_SECRET`：后台登录 session 签名密钥。
- `TTS_ADMIN_BOOTSTRAP_PASSWORD`：首次初始化后台时使用的引导密码，不能长期当正式管理密码使用。
- `TTS_LOG_LEVEL`：日志级别。

### 6.2 迁移到 SQLite 的配置

- 豆包凭据组。
- API Key 列表。
- 默认 speaker / format。
- 服务级超时、并发、重试策略。
- 是否允许流式接口、是否允许调用方覆盖默认参数。
- 报表保留策略。

### 6.3 必须拒绝的坏方案

- 不允许要求运维通过 XFTP 或直接改环境变量来维护豆包 Cookie。
- 不允许让匿名用户在首次访问后台时直接设置正式管理密码。
- 不允许把 API Key 放在 URL、表单明文链接或日志容易泄漏的位置。

## 7. 推荐架构

### 7.1 总体策略

- 保持 `doubao_tts.py` 作为协议与合成核心，不引入后台逻辑。
- 在 `service/` 下增加后台、数据库、认证、凭据池、报表相关模块。
- 管理后台优先采用服务端渲染 HTML 模板，不先上单页前端框架。

推荐结论：这个项目当前体量不需要 React/Vue 后台。服务端渲染更简单、更稳、更易测。

### 7.2 推荐目录结构

```text
service/
  app.py                 # 应用装配入口
  public_routes.py       # /v1/*、/healthz、/v1/speakers
  admin_routes.py        # /admin/*
  auth.py                # 管理员登录、session、API Key 校验
  db.py                  # SQLite 初始化、连接管理、迁移入口
  repositories.py        # SQLite 读写封装
  credential_pool.py     # 豆包凭据选择、冷却、状态更新
  reporting.py           # 请求日志记录与报表聚合
  settings.py            # 二期配置读取与聚合
  templates/             # 后台 HTML 模板
  static/                # 极少量静态资源
tests/
  test_admin_auth.py
  test_api_keys.py
  test_credential_pool.py
  test_reporting.py
  test_admin_pages.py
```

### 7.3 运行时模型

- 公开调用面：
  - `POST /v1/tts`
  - `POST /v1/tts/stream`
  - `GET /v1/speakers`
  - `GET /healthz`
- 管理面：
  - `/admin/setup`
  - `/admin/login`
  - `/admin/logout`
  - `/admin/settings`
  - `/admin/accounts`
  - `/admin/api-keys`
  - `/admin/reports`
  - `/admin/test-tts`
- 内部支撑：
  - SQLite 持久化
  - 豆包凭据池选择器
  - API Key 校验器
  - 调用日志记录器

## 8. 认证与安全设计

### 8.1 管理后台认证

- 后台只需要一个管理员密码，不需要用户名。
- 密码必须存哈希，不能明文写入 SQLite。
- 管理后台登录后使用 session cookie 维持状态。
- 既然用 cookie 会话，涉及写操作的后台表单必须有 CSRF 防护。

### 8.2 首次初始化流程

- 当系统尚未完成初始化时，只开放 `/admin/setup`。
- `/admin/setup` 不能匿名直接设置正式密码。
- 初始化流程必须要求输入 `TTS_ADMIN_BOOTSTRAP_PASSWORD`，验证通过后才能设置正式管理密码。
- 初始化完成后，系统写入 `setup_completed=true`。

说明：

- 这样设计不是为了复杂，而是为了避免服务一上线就被第一个外部访问者抢走后台控制权。

### 8.3 API Key 设计

- API Key 用于外部调用公开 TTS 接口，不用于后台登录。
- API Key 支持多个，每个 key 至少包含：
  - `name`
  - `key_prefix`
  - `key_hash`
  - `enabled`
  - `created_at`
  - `last_used_at`
- 原始 key 只在创建成功时显示一次；后续后台只能看到前缀和状态。
- 公开接口必须使用请求头传递 key。

推荐格式：

- `Authorization: Bearer <api-key>`

不推荐格式：

- `GET /v1/tts?...&key=...`
- `POST /v1/tts?...&token=...`

### 8.4 指标接口权限

- `/metrics` 不应继续复用外部 API Key。
- 二期推荐将 `/metrics` 限制为：
  - 仅后台登录态可见；或
  - 仅反向代理内网访问。

## 9. 豆包凭据池设计

### 9.1 凭据组定义

一组豆包凭据必须包含以下三个字段：

- `sessionid`
- `sid_guard`
- `uid_tt`

每条记录代表一组完整凭据，不允许只填其中一个或两个字段。

### 9.2 凭据表字段建议

`doubao_accounts`

- `id`
- `name`
- `sessionid`
- `sid_guard`
- `uid_tt`
- `enabled`
- `status`
- `cooldown_until`
- `last_error`
- `last_used_at`
- `success_count`
- `failure_count`
- `created_at`
- `updated_at`

`status` 建议取值：

- `healthy`
- `cooldown`
- `disabled`
- `invalid`

### 9.3 选择策略

默认策略：健康优先轮询。

推荐规则：

- 只在 `enabled=true` 且 `cooldown_until` 未生效的凭据中参与选择。
- 在健康凭据集合内做轮询；若集合为空，则直接失败。
- 成功请求后更新 `last_used_at`、`success_count`。
- 若命中 `block`、Cookie 缺失/失效、握手失败、上游拒绝等错误，则更新 `failure_count`、`last_error`，并进入冷却。
- 冷却结束后可重新参与选择。

### 9.4 切换与重试边界

- 非流式接口：
  - 在尚未成功拿到有效结果前，允许最多切换 1 次备用凭据。
- 流式接口：
  - 如果在第一个音频 chunk 之前失败，允许最多切换 1 次备用凭据。
  - 如果已经开始向客户端回流音频，就不能再切换凭据重试，也不能再改 HTTP 状态码。

说明：

- 这是协议和 HTTP 流式响应机制决定的，不是实现喜好。
- 如果已经发出部分音频后还偷偷切换到另一个豆包账号继续生成，结果一致性和故障语义都会变脏。

### 9.5 后台管理动作

- 新增凭据组
- 编辑凭据组
- 启用 / 停用凭据组
- 立即测试当前凭据组
- 解除冷却
- 查看最近错误

## 10. 服务参数治理

### 10.1 应当可在后台维护的参数

- 默认 speaker
- 默认 format
- 请求超时
- 最大并发
- block 重试开关
- block 最大额外重试次数
- block 退避参数
- 是否启用流式接口
- 是否允许调用方覆盖默认 speaker / format / speed / pitch

### 10.2 参数治理原则

- 后台修改的是默认值和全局约束，不是悄悄篡改每个请求的原始输入。
- 若允许调用方覆盖，则以请求参数优先。
- 若不允许调用方覆盖，则服务应显式拒绝越权参数，而不是静默吞掉。

## 11. 报表与日志设计

### 11.1 最小记录字段

`request_logs`

- `id`
- `request_id`
- `api_key_id`
- `doubao_account_id`
- `endpoint`
- `speaker`
- `format`
- `text_chars`
- `status_code`
- `success`
- `latency_ms`
- `error_type`
- `error_detail`
- `created_at`

### 11.2 默认不记录的内容

- 不默认持久化全文文本。
- 不默认持久化完整音频。
- 不在后台页面回显完整 Cookie。
- 不在后台页面回显原始 API Key。

### 11.3 报表页面最小能力

- 总请求数
- 成功率
- 平均耗时
- 按接口统计
- 按 API Key 统计
- 按豆包凭据组统计
- 最近失败请求列表

### 11.4 保留策略

- 二期至少支持按天数清理历史报表。
- 清理策略应为显式配置，不要无限增长 SQLite 文件。

## 12. 后台页面范围

### 12.1 必须页面

- `初始化页面`
- `登录页面`
- `系统设置页面`
- `豆包凭据池页面`
- `API Key 管理页面`
- `调用报表页面`
- `测试合成页面`

### 12.2 页面职责

#### 初始化页面

- 输入 bootstrap 密码
- 设置正式管理密码

#### 登录页面

- 输入正式管理密码
- 建立后台会话

#### 系统设置页面

- 修改默认参数
- 修改超时、并发、重试策略
- 修改流式接口和参数覆盖策略

#### 豆包凭据池页面

- 查看凭据列表、状态、最近错误、成功/失败计数
- 新增、编辑、启停、测试、解除冷却

#### API Key 管理页面

- 创建 key
- 停用 / 启用 key
- 查看 `last_used_at`

#### 调用报表页面

- 按时间过滤
- 按结果过滤
- 按 API Key / 凭据组过滤
- 查看错误明细

#### 测试合成页面

- 使用后台登录态发起一次受控测试请求
- 用于验证当前默认参数和凭据池可用性

## 13. 二期接口草案

### 13.1 后台接口

#### `POST /admin/setup`

- 用途：首次初始化正式管理密码。
- 约束：只有 `setup_completed=false` 时可用。
- 必填字段：
  - `bootstrap_password`
  - `new_password`

#### `POST /admin/login`

- 用途：后台登录。
- 必填字段：
  - `password`

#### `POST /admin/logout`

- 用途：退出后台登录态。

#### `GET /admin/settings`

- 用途：读取当前后台可配置项。

#### `POST /admin/settings`

- 用途：更新服务级默认参数和治理参数。

#### `GET /admin/accounts`

- 用途：获取豆包凭据池列表。

#### `POST /admin/accounts`

- 用途：新增豆包凭据组。
- 必填字段：
  - `name`
  - `sessionid`
  - `sid_guard`
  - `uid_tt`

#### `POST /admin/accounts/{id}`

- 用途：编辑指定豆包凭据组。

#### `POST /admin/accounts/{id}/test`

- 用途：测试指定豆包凭据组。

#### `POST /admin/accounts/{id}/enable`

- 用途：启用凭据组。

#### `POST /admin/accounts/{id}/disable`

- 用途：停用凭据组。

#### `POST /admin/accounts/{id}/reset-cooldown`

- 用途：手动解除凭据组冷却。

#### `GET /admin/api-keys`

- 用途：获取 API Key 列表。

#### `POST /admin/api-keys`

- 用途：创建 API Key。
- 返回：
  - `key_id`
  - `name`
  - `raw_key`（仅此一次）

#### `POST /admin/api-keys/{id}/enable`

- 用途：启用 API Key。

#### `POST /admin/api-keys/{id}/disable`

- 用途：停用 API Key。

#### `GET /admin/reports`

- 用途：查看调用报表与筛选结果。

#### `GET /admin/test-tts`

- 用途：渲染后台测试合成页面。

#### `POST /admin/test-tts`

- 用途：使用后台登录态发起一次受控测试合成。

### 13.2 公开接口

#### `POST /v1/tts`

- 保持现有请求模型不变。
- 二期新增要求：必须携带有效 API Key。

#### `POST /v1/tts/stream`

- 保持现有请求模型不变。
- 二期新增要求：必须携带有效 API Key。

#### `GET /v1/speakers`

- 可保持无鉴权，或与公开接口一致要求 API Key。
- 推荐做法：若 speaker 列表不敏感，可保持公开；若想统一接入规则，可要求 API Key。

#### `GET /healthz`

- 保持公开。
- 响应应体现后台初始化状态、健康凭据数量、服务 ready 状态。
- 当后台未初始化、没有启用中的 API Key、或没有健康凭据时，应返回 `503` 与 `status=not_ready`。

#### `GET /metrics`

- 改为后台登录态可见，不再给普通 API Key 或兼容 token 使用。

## 14. SQLite 数据模型草案

### 14.1 `admin_settings`

- `id`
- `password_hash`
- `setup_completed`
- `password_updated_at`
- `created_at`
- `updated_at`

### 14.2 `service_settings`

- `id`
- `default_speaker`
- `default_format`
- `request_timeout_seconds`
- `max_concurrency`
- `retry_on_block`
- `retry_max_retries`
- `retry_backoff_seconds`
- `retry_backoff_multiplier`
- `retry_backoff_jitter_ratio`
- `enable_streaming`
- `allow_request_override`
- `report_retention_days`
- `created_at`
- `updated_at`

### 14.3 `doubao_accounts`

- `id`
- `name`
- `sessionid`
- `sid_guard`
- `uid_tt`
- `enabled`
- `status`
- `cooldown_until`
- `last_error`
- `last_used_at`
- `success_count`
- `failure_count`
- `created_at`
- `updated_at`

### 14.4 `api_keys`

- `id`
- `name`
- `key_prefix`
- `key_hash`
- `enabled`
- `last_used_at`
- `created_at`
- `updated_at`

### 14.5 `request_logs`

- `id`
- `request_id`
- `api_key_id`
- `doubao_account_id`
- `endpoint`
- `speaker`
- `format`
- `text_chars`
- `status_code`
- `success`
- `latency_ms`
- `error_type`
- `error_detail`
- `created_at`

## 15. 里程碑与执行看板

### M5：SQLite 与配置迁移基线

- [x] 新建 SQLite 初始化与迁移模块。
- [x] 增加 `admin_settings`、`service_settings`、`doubao_accounts`、`api_keys`、`request_logs` 表。
- [x] 将可变服务配置从环境变量迁移到 SQLite。
- [x] 保留部署期不可变秘密配置的环境变量读取。

验收标准：

- [x] 服务在无业务配置环境变量时仍可启动。
- [x] SQLite 文件可自动初始化。
- [x] 当前公开接口可从 SQLite 读取默认配置。

### M6：后台初始化与单密码登录

- [x] 实现 `/admin/setup`。
- [x] 实现 `/admin/login`、`/admin/logout`。
- [x] 后台密码改为哈希存储。
- [x] 后台写操作具备 CSRF 防护。

验收标准：

- [x] 未初始化时只能完成受控初始化，不能匿名直接占用后台。
- [x] 初始化后可使用正式管理密码登录。
- [x] 错误密码返回 `401` 或明确失败提示。

### M7：API Key 与公开接口鉴权升级

- [x] 增加 API Key 数据表与后台管理页面。
- [x] 公开 TTS 接口改为 API Key 鉴权。
- [x] 原始 API Key 仅创建时展示一次。
- [x] `/metrics` 权限与公开 API Key 解耦。

验收标准：

- [x] 无有效 API Key 时，`/v1/tts` 与 `/v1/tts/stream` 返回 `401`。
- [x] 禁用 API Key 后，相关请求立即失效。
- [x] 管理后台仍能正常查看运行指标；`/metrics` 仅后台登录态可见。

### M8：豆包凭据池与健康优先轮询

- [x] 增加豆包凭据池数据表与后台管理页面。
- [x] 实现凭据组新增、编辑、启停、测试、解除冷却。
- [x] 实现健康优先轮询。
- [x] 为上游失败增加冷却与状态更新逻辑。

验收标准：

- [x] 少于三字段的凭据组不能保存。
- [x] 多组健康凭据可轮流承接请求。
- [x] 命中 `block` 或 Cookie 失效后，凭据会进入冷却，不会继续被立即选中。
- [x] 流式接口在首个音频 chunk 前失败时，最多只切换一次备用凭据。

### M9：报表、后台页面与文档

- [x] 持久化记录请求日志。
- [x] 实现后台报表页和过滤能力。
- [x] 实现后台测试合成页。
- [x] 更新 README、部署文档、Docker 文档。

验收标准：

- [x] 服务重启后历史报表仍存在。
- [x] 报表可按 API Key、凭据组、时间、结果过滤。
- [x] 文档能覆盖初始化、登录、配置、API 调用、Docker 挂载 SQLite 文件等信息。

## 16. 最小测试与验收建议

### 16.1 自动化测试

- [x] `tests/test_service_api.py` 继续覆盖当前公开接口主路径。
- [x] 新增 `tests/test_admin_auth.py` 覆盖初始化、登录、会话校验、CSRF。
- [x] 新增 `tests/test_api_keys.py` 覆盖 key 创建、启停、哈希存储、鉴权。
- [x] 新增 `tests/test_credential_pool.py` 覆盖健康优先轮询、冷却、失败回退。
- [x] 新增 `tests/test_reporting.py` 覆盖请求日志落库和报表查询。

### 16.2 手工验收

- [ ] 首次启动后只能通过 bootstrap 密码完成后台初始化。
- [ ] 后台可新增两组豆包凭据，并看到状态变化。
- [ ] 后台可创建两个 API Key，并分别调用 `/v1/tts`。
- [ ] 停用一个 API Key 后，对应请求返回 `401`。
- [ ] 人工制造一组失效豆包凭据后，请求会切到其他健康凭据或进入受控失败。
- [ ] 后台报表能看到请求数量、成功率、失败记录。

## 17. 风险与对策

### 17.1 首次初始化被抢占风险

风险：

- 若匿名首访即可设置后台密码，公网部署时会被直接接管。

对策：

- 强制 `TTS_ADMIN_BOOTSTRAP_PASSWORD` 参与初始化流程。

### 17.2 Cookie 池污染风险

风险：

- 若采用纯轮询，失效 Cookie、被 block Cookie 会反复参与调度，导致整体成功率被拖垮。

对策：

- 使用健康优先轮询 + 冷却机制，而不是傻轮询。

### 17.3 SQLite 膨胀风险

风险：

- 若报表无限增长，SQLite 文件会持续膨胀，查询和备份都会变差。

对策：

- 增加报表保留天数和显式清理策略。

### 17.4 安全泄漏风险

风险：

- 若后台回显完整 Cookie 或 API Key，页面截图、日志、调试信息都可能泄漏秘密。

对策：

- 后台只显示脱敏值；API Key 只在创建时显示一次。

### 17.5 流式重试一致性风险

风险：

- 流式接口在已输出部分音频后再切换凭据，会破坏结果一致性，也无法再修改 HTTP 状态码。

对策：

- 流式接口只允许在首个音频 chunk 前做一次受控切换，之后禁止跨凭据重试。

## 18. 当前已知缺口与后续补强建议

以下项目不否定当前二期主线已经落地，但也不能被包装成“已经足够稳妥”。若要面向公网或长期运维，这些补强项应该明确记录，而不是继续装作不存在。

### 18.1 后台会话安全仍然偏弱

现状：

- 后台会话基于签名 Cookie，当前只有过期时间，没有服务端撤销机制。
- `/admin/login` 当前未实现失败限速、锁定或节流。
- 当前后台还没有“修改管理员密码”入口。

影响：

- 若管理 Cookie 泄漏，在 Cookie 过期前无法通过服务端主动失效。
- 若后台暴露到公网，登录口会成为暴力尝试入口。
- 运维侧缺少正式的密码轮换路径。

建议：

- 后续增加管理员会话撤销机制，例如会话版本号、密码更新时间校验或服务端 session 存储。
- 为登录接口增加失败限速、短时锁定或反向代理层限流。
- 增加后台修改管理员密码页面，并在密码变更后强制旧会话失效。

### 18.2 豆包凭据在后台页面中仍可能暴露过多

现状：

- SQLite 中需要保存完整 `sessionid`、`sid_guard`、`uid_tt`，这是当前功能设计的客观要求。
- 后台凭据编辑页当前会将已有凭据值写回页面表单数据源，以便直接编辑。

影响：

- 若页面出现 XSS、浏览器插件泄漏、调试工具导出或终端设备被入侵，完整凭据暴露面会扩大。
- 后台“显示脱敏值”和“前端页面里仍携带明文值”之间存在安全预期不一致。

建议：

- 后续改为“编辑时默认不回显明文”，只允许留空保持不变或重新输入覆盖。
- 为 SQLite 文件权限、备份与转储过程补充更严格的运维约束。
- 若后续部署场景更敏感，可再评估增加静态加密或外部秘密存储。

### 18.3 凭据池状态机还不完整

现状：

- 当前已实现健康优先轮询、失败冷却、一次受控切换。
- 数据模型中已经存在 `invalid` 状态，但当前主要失败路径仍以“冷却后再参与调度”为主。

影响：

- 对于长期失效或稳定不可用的凭据，系统可能会周期性重复尝试，而不是明确摘除。
- 账号池会持续携带“已知坏账号”，拖累整体成功率和排障效率。

建议：

- 后续补充连续失败阈值、失效原因分类和自动标记 `invalid` 的策略。
- 后台应支持人工恢复 `invalid` 凭据，避免只能通过直接改库处理。

### 18.4 SQLite 目前只有初始化，没有正式迁移体系

现状：

- 当前数据库初始化依赖 `CREATE TABLE IF NOT EXISTS`。
- 当前尚未引入 `schema_version`、迁移脚本、`PRAGMA user_version` 或显式升级流程。
- 当前常用查询字段仍缺少数据库层唯一约束和索引约束。

影响：

- 后续一旦调整表结构、补列或改约束，升级路径会变脆，容易落入“手工改库”。
- API Key 查找、报表过滤、日志清理在数据量上升后会更容易退化成全表扫描。
- 缺少数据库层约束时，重复 API Key 哈希、重复凭据记录等问题只能靠应用层兜底。

建议：

- 后续增加数据库版本号和显式迁移流程。
- 为 `api_keys.key_hash`、报表常用过滤列、日志时间列补上唯一约束或索引。
- 需要明确豆包凭据的去重策略，避免同一组 Cookie 被重复录入。

### 18.5 报表清理逻辑不应继续停留在请求热路径

现状：

- 当前请求日志写入后会按保留天数执行清理。

影响：

- 在请求量上升后，日志清理会与业务请求竞争 SQLite 写锁和 I/O。
- 将维护操作放在热路径上，不利于稳定性和响应时间控制。

建议：

- 后续改为启动时清理、定时清理、后台手工清理，或独立后台任务清理。
- 若日志规模继续增长，需要进一步评估归档与冷数据处理方案。

### 18.6 当前仍缺少完整的运维闭环验证

现状：

- 自动化测试已经覆盖二期主路径。
- 手工验收清单仍有未勾选项。
- Docker 文档已更新，但当前仓库并未记录一次完整的容器实机验证结果。

影响：

- 当前可以说“代码功能已实现并通过自动化测试”，但还不能偷换成“部署闭环已经完成”。
- 若直接把这套状态宣传成“完全可上生产”，结论会失真。

建议：

- 按文档手工验收清单完成一次真实后台操作和真实接口调用验证。
- 在具备条件的环境中完成 Docker 实机验证，并把观察结果补回文档。

## 19. 推荐执行顺序

1. 先完成 SQLite 基线和配置迁移。
2. 再完成后台初始化与单密码登录。
3. 再完成 API Key 管理和公开接口鉴权升级。
4. 再完成豆包凭据池与健康优先轮询。
5. 最后补报表页面、测试合成页、文档和部署说明。

## 20. 最终建议

- 二期的核心不是“做后台页面”，而是把当前服务从环境变量驱动升级成数据库驱动的可治理系统。
- 管理端与调用端必须分离：后台密码只管管理，API Key 只管外部调用。
- 豆包凭据池必须按健康状态调度，不能做没有冷却的纯轮询。
- 若要保持项目正确、稳定、可维护，这份计划的优先级应该是：安全边界先于页面观感，持久化先于花哨交互，失败策略先于功能堆砌。
