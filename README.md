# 豆包 TTS 逆向工程客户端

基于 WebSocket 协议逆向的豆包文本转语音 Python 客户端。

## 安装

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## ⚠️ 配置 Cookie（必须）

本工具需要豆包登录态 Cookie 才能使用。

### 获取 Cookie

1. 打开浏览器访问 [豆包](https://www.doubao.com) 并**登录账号**
2. 按 `F12` 打开开发者工具
3. 切换到 **Application** 标签页
4. 左侧展开 **Cookies** → 点击 `https://www.doubao.com`
5. 找到以下**三个必需字段**并复制其值：

| Cookie 名称 | 说明 |
|-------------|------|
| `sessionid` | 登录会话 ID |
| `sid_guard` | 会话守护令牌 |
| `uid_tt` | 用户唯一标识 |

![Cookie 位置示意](https://img.shields.io/badge/Application-Cookies-blue)

### 配置 Cookie

**方式一：命令行保存为 JSON（推荐）**

```bash
python doubao_tts.py "测试" --cookie "sessionid=xxx; sid_guard=xxx; uid_tt=xxx" --save-cookie
```

Cookie 将保存到 `.cookie.json` 文件，后续使用无需再次提供。

**方式二：手动创建 JSON 配置文件**

在项目目录下创建 `.cookie.json` 文件：

```json
{
  "cookie": {
    "sessionid": "你的sessionid",
    "sid_guard": "你的sid_guard",
    "uid_tt": "你的uid_tt"
  }
}
```

**方式三：兼容旧版 `.cookie`**

旧版纯文本 `.cookie` 仍然支持，内容格式不变：

```bash
echo "sessionid=你的sessionid; sid_guard=你的sid_guard; uid_tt=你的uid_tt" > .cookie
```

### Cookie 有效期

- Cookie 有效期约 **30 天**
- 过期后需重新登录豆包并获取新的 Cookie
- 如果出现 `HTTP 200` 握手失败，请优先检查 Cookie 是否过期、缺字段，或仍在使用占位符

## 快速使用

### 命令行

```bash
# 基础用法
python doubao_tts.py "你好，世界" -o hello.aac

# 指定语音角色
python doubao_tts.py "欢迎使用豆包" -s yangguang -o welcome.aac

# 调整语速和音调
python doubao_tts.py "快速朗读测试" --speed 0.5 --pitch 0.2 -o fast.aac

# 命中 block 时显式开启退避重试（默认关闭）
python doubao_tts.py "需要重试的文本" --retry-on-block --retry-max-retries 2 --retry-backoff-seconds 1.5

# 在退避上增加可选随机抖动（这里表示上下浮动 20%）
python doubao_tts.py "需要重试的文本" --retry-on-block --retry-max-retries 2 --retry-backoff-seconds 1.5 --retry-backoff-jitter-ratio 0.2

# 查看可用语音
python doubao_tts.py --list-speakers ""
```

说明：

- 对外兼容入口仍然是 `doubao_tts.py`
- 实际 CLI 逻辑已拆到 `doubao_tts_cli.py`
- 库逻辑保留在 `doubao_tts.py`，避免命令行参数解析继续污染库代码
- `block` 退避重试默认关闭，只有显式传入 `--retry-on-block` 才会启用

### Python API

```python
from doubao_tts import DoubaoTTS, TTSConfig, SPEAKERS

# 方式1: 简单使用
# 默认会读取项目目录下的 .cookie.json / .cookie
tts = DoubaoTTS()
result = tts.synthesize_sync("你好，世界")
if result.success:
    with open("output.aac", "wb") as f:
        f.write(result.audio_data)
    print(result.event_order)        # 例如: ["open_success", "sentence_start", "sentence_end", "finish"]
    print(result.audio_chunk_count)  # 实际收到的二进制音频块数量
    print(result.json_messages[-1])  # 最后一条 JSON 响应，排查错误时很有用

# 方式2: 自定义配置
config = TTSConfig(
    speaker="zh_male_yangguang_conversation_v4_wvae_bigtts",
    speech_rate=0.2,  # 稍快
    pitch=0,
    format="aac",
    verbose=True,  # 输出底层连接日志
    retry_on_block=True,  # 显式开启 block 退避重试
    retry_max_retries=2,
    retry_backoff_seconds=1.5,
    retry_backoff_multiplier=2.0,
    retry_backoff_jitter_ratio=0.2,  # 可选抖动比例，上下浮动 20%
)
tts = DoubaoTTS(config)
result = tts.synthesize_sync("自定义语音测试")

# 方式3: 链式调用
tts = DoubaoTTS()
result = (tts
    .set_speaker("rap")
    .set_speed(0.3)
    .synthesize_sync("说唱风格的文本"))

# 方式4: 异步使用
import asyncio

async def main():
    tts = DoubaoTTS()
    result = await tts.synthesize("异步合成测试")
    print(f"音频大小: {len(result.audio_data)} bytes")

asyncio.run(main())
```

说明：

- `DoubaoTTS()` 会自动尝试读取本地 `.cookie.json`，其次回退到旧版 `.cookie`
- 如果你在 Jupyter、FastAPI、异步框架里使用，请直接 `await tts.synthesize(...)`，不要调用 `synthesize_sync()`
- `TTSResult` 会返回事件序列、原始 JSON 响应、音频块数、是否收到 `finish`、关闭码等协议元数据，便于排错
- `block` 退避重试默认关闭；启用后只对 `error_code=710022002` 或错误文本为 `block` 的结果生效
- `retry_max_retries` 表示额外重试次数，不包含首轮请求
- `retry_backoff_jitter_ratio` 是可选抖动比例，`0.2` 表示基准退避时间上下浮动 20%，默认 `0.0` 关闭

### 流式播放

```python
import asyncio
from doubao_tts import DoubaoTTS

async def stream_play():
    tts = DoubaoTTS()
    
    audio_buffer = []
    
    def on_chunk(chunk):
        audio_buffer.append(chunk)
        print(f"收到音频块: {len(chunk)} bytes")
    
    def on_sentence(text):
        print(f"开始朗读: {text}")
    
    result = await tts.synthesize(
        "这是一段很长的文本，会被分成多个句子来朗读。每个句子会触发回调函数。",
        on_audio_chunk=on_chunk,
        on_sentence_start=on_sentence,
    )

asyncio.run(stream_play())
```

## 协议观测脚本

如果你要记录一次真实请求的原始返回，不要再手写临时命令，直接用：

```bash
python scripts/observe_session.py "日志观察文本"
```

默认输出：

- 日志文件：`logs/session_observation.log`
- 音频文件：`logs/session_observation.aac`

可选参数：

- `--speaker`：切换语音角色
- `--format`：切换输出格式
- `--output-dir`：指定日志目录
- `--cookie`：临时覆盖本地配置

## HTTP 服务模式

如果你要把当前仓库作为独立服务给其他应用调用，不要复用 CLI 流程，直接启动 HTTP 服务。

### 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `TTS_COOKIE` | 否 | 空 | 首次部署时可用它自动种入第一组豆包凭据；若 SQLite 已有凭据，可不再依赖它 |
| `TTS_DEFAULT_SPEAKER` | 否 | `taozi` | 默认 speaker 简称或完整 ID |
| `TTS_DEFAULT_FORMAT` | 否 | `aac` | 默认音频格式，当前仅支持 `aac`、`mp3` |
| `TTS_HOST` | 否 | `127.0.0.1` | 服务监听地址 |
| `TTS_PORT` | 否 | `8080` | 服务监听端口 |
| `TTS_LOG_LEVEL` | 否 | `INFO` | 服务日志级别 |
| `TTS_RETRY_ON_BLOCK` | 否 | `false` | 是否在命中 block 时执行退避重试 |
| `TTS_RETRY_MAX_RETRIES` | 否 | `0` | block 最大额外重试次数 |
| `TTS_RETRY_BACKOFF_SECONDS` | 否 | `1.0` | block 首次退避秒数 |
| `TTS_RETRY_BACKOFF_MULTIPLIER` | 否 | `2.0` | block 退避倍率 |
| `TTS_RETRY_BACKOFF_JITTER_RATIO` | 否 | `0.0` | block 退避抖动比例 |
| `TTS_REQUEST_TIMEOUT_SECONDS` | 否 | `35.0` | 单次 HTTP 请求的服务侧超时 |
| `TTS_MAX_CONCURRENCY` | 否 | `4` | 服务级并发上限 |
| `TTS_AUTH_TOKEN` | 否 | 空 | 已废弃；当前版本不再校验它 |
| `TTS_ENABLE_METRICS` | 否 | `true` | 是否启用 `/metrics` |
| `TTS_SQLITE_PATH` | 否 | 用户目录下的 `.doubao-tts/tts_service.db` | 管理后台和后续报表使用的 SQLite 文件路径 |
| `TTS_SQLITE_JOURNAL_MODE` | 否 | `WAL` | SQLite journal mode；测试或 Windows 兼容场景可改为 `DELETE` |
| `TTS_SESSION_SECRET` | 否 | 空 | 管理后台 session 签名密钥；要启用后台登录就必须配置 |
| `TTS_ADMIN_BOOTSTRAP_PASSWORD` | 否 | 空 | 管理后台首次初始化使用的引导密码；不应长期当正式管理密码 |
| `TTS_SECURE_COOKIES` | 否 | 自动判断 | 强制后台 session / CSRF cookie 使用 `Secure` 标志 |

### 本地启动

PowerShell：

```powershell
$env:TTS_COOKIE = "sessionid=...; sid_guard=...; uid_tt=..."
$env:TTS_LOG_LEVEL = "INFO"
python -m service
```

或直接使用 `uvicorn`：

```bash
python -m uvicorn service.app:app --host 127.0.0.1 --port 8080
```

### 管理后台

当前已经落地的是完整的二期后台基础面，不再只是登录壳子。已提供：

- `/admin/setup`：首次初始化正式管理密码
- `/admin/login`：后台登录
- `/admin`：总览页
- `/admin/settings`：服务参数设置页
- `/admin/accounts`：豆包凭据池管理页
- `/admin/api-keys`：API Key 创建与启停页
- `/admin/reports`：调用报表页
- `/admin/test-tts`：受控测试合成页

启动后台前至少补这两个环境变量：

```powershell
$env:TTS_SESSION_SECRET = "replace-with-a-long-random-secret"
$env:TTS_ADMIN_BOOTSTRAP_PASSWORD = "bootstrap-only-password"
python -m service
```

然后访问：

```text
http://127.0.0.1:8080/admin/setup
```

说明：

- bootstrap 密码只用于首次初始化，不该长期当正式管理密码用。
- 管理后台密码和公开接口凭据不是一回事，别混着用。
- `TTS_DEFAULT_*`、超时、并发、重试等参数第一次会按环境变量种进 SQLite，之后应通过 `/admin/settings` 管理，不要再指望改 env 热更新。
- `TTS_COOKIE` 也只适合当首次种子；后续凭据管理应在 `/admin/accounts` 完成。
- `/metrics` 现在只认后台登录态，不再接受兼容 token 或普通 API Key。

### 基础接口

#### 健康检查

```bash
curl http://127.0.0.1:8080/healthz
```

示例响应：

```json
{
  "status": "ok",
  "ready": true,
  "setup_completed": true,
  "enabled_api_keys": 1,
  "total_accounts": 2,
  "healthy_accounts": 2,
  "detail": null
}
```

说明：

- `status=ok` 且 `ready=true` 才表示服务真正可对外承接请求。
- 如果后台未初始化、没有启用中的 API Key、或没有健康凭据，`/healthz` 会返回 `503` 和 `status=not_ready`。

#### 获取 speaker 列表

```bash
curl http://127.0.0.1:8080/v1/speakers
```

#### 获取音频二进制

先在后台创建 API Key，再调用公开接口。调用时必须带：

```text
Authorization: Bearer <api-key>
```

```bash
curl -X POST http://127.0.0.1:8080/v1/tts \
  -H "Authorization: Bearer <api-key>" \
  -H "Content-Type: application/json" \
  -d '{"text":"你好，欢迎使用服务模式","speaker":"taozi","format":"aac","speed":0,"pitch":0}' \
  --output output.aac
```

#### 流式返回音频

```bash
curl -X POST http://127.0.0.1:8080/v1/tts/stream \
  -H "Authorization: Bearer <api-key>" \
  -H "Content-Type: application/json" \
  -d '{"text":"这是一段用于流式输出的文本","speaker":"taozi","format":"aac"}' \
  --output stream_output.aac
```

#### 查看指标

```bash
curl http://127.0.0.1:8080/metrics
```

`/metrics` 现在只接受后台登录态；如果 `TTS_ENABLE_METRICS=false`，则返回 `503`。如果你要让 Prometheus 抓，应该在反向代理层做内网访问控制或单独加认证，不要再依赖服务内兼容 token。

当前提供的指标包括：总请求数、成功数、失败数、超时数、上游失败数、未授权请求数、流式请求数、当前 in-flight 数。

### 服务器部署

最小建议：Linux 服务器上使用虚拟环境 + `systemd` 托管，不要直接把开发命令扔进 shell 后台跑。

`/etc/systemd/system/doubao-tts.service` 示例：

```ini
[Unit]
Description=Doubao TTS Service
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/doubao-tts
Environment="TTS_COOKIE=sessionid=...; sid_guard=...; uid_tt=..."
Environment="TTS_HOST=0.0.0.0"
Environment="TTS_PORT=8080"
Environment="TTS_SQLITE_PATH=/var/lib/doubao-tts/tts_service.db"
Environment="TTS_SESSION_SECRET=replace-with-a-long-random-secret"
Environment="TTS_ADMIN_BOOTSTRAP_PASSWORD=bootstrap-only-password"
ExecStart=/opt/doubao-tts/.venv/bin/python -m service
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

启用方式：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now doubao-tts
sudo systemctl status doubao-tts
```

### Docker 部署

构建镜像：

```bash
docker build -t doubao-tts:latest .
```

运行容器：

```bash
docker run --rm -p 8080:8080 \
  -e TTS_COOKIE='sessionid=...; sid_guard=...; uid_tt=...' \
  -e TTS_HOST=0.0.0.0 \
  -e TTS_SQLITE_PATH=/data/tts_service.db \
  -e TTS_SESSION_SECRET='replace-with-a-long-random-secret' \
  -e TTS_ADMIN_BOOTSTRAP_PASSWORD='bootstrap-only-password' \
  -v doubao-tts-data:/data \
  doubao-tts:latest
```

如果你要在容器里长期运行：

- 必须把 SQLite 文件挂到持久卷，不然后台配置、API Key、凭据池和报表都会跟容器一起丢。
- 首次种子可以用 `TTS_COOKIE`，后续凭据维护走 `/admin/accounts`。
- 不要把裸服务直接暴露到公网；至少加反向代理、HTTPS 和访问控制。

## 可用语音角色

| 简称 | 完整 ID | 描述 |
|------|---------|------|
| taozi | zh_female_taozi_conversation_v4_wvae_bigtts | 桃子 - 女声对话 |
| shuangkuai | zh_female_shuangkuai_emo_v3_wvae_bigtts | 爽快 - 女声 |
| tianmei | zh_female_tianmei_conversation_v4_wvae_bigtts | 甜美 - 女声 |
| qingche | zh_female_qingche_moon_bigtts | 清澈 - 女声 |
| yangguang | zh_male_yangguang_conversation_v4_wvae_bigtts | 阳光 - 男声 |
| chenwen | zh_male_chenwen_moon_bigtts | 沉稳 - 男声 |
| rap | zh_male_rap_mars_bigtts | 说唱 - 男声 |
| en_female | en_female_sarah_conversation_bigtts | 英文女声 |
| en_male | en_male_adam_conversation_bigtts | 英文男声 |

## 协议说明

补充的真实调用观察记录见 `docs/protocol_observation.md`。

### WebSocket 端点
```
wss://ws-samantha.doubao.com/samantha/audio/tts
```

### 请求参数
| 参数 | 说明 |
|------|------|
| speaker | 语音角色 ID |
| format | 音频格式 (aac/mp3) |
| speech_rate | 语速 (-1.0 ~ 1.0) |
| pitch | 音调 (-1.0 ~ 1.0) |
| language | 语言代码 |

### 消息格式

**发送文本:**
```json
{"event": "text", "text": "要朗读的内容"}
{"event": "finish"}
```

**接收响应:**
```json
{"event": "open_success", "code": 0, "message": ""}
{"event": "sentence_start", "sentence_start_result": {"readable_text": "..."}}
[Binary AAC/MP3 数据]
{"event": "sentence_end", "code": 0, "message": ""}
{"event": "finish", "code": 0, "message": ""}
```

## 注意事项

1. 此项目仅供学习研究使用
2. 请勿用于商业用途
3. 接口可能随时变更
4. 建议合理控制请求频率

## License

MIT
