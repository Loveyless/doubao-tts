# Doubao TTS 服务 API 说明

## 必要输入

- `base_url`：例如 `https://tts.example.com`
- `api_key`：在服务后台创建

推荐使用的接入配置：

- `DOUBAO_TTS_BASE_URL`
- `DOUBAO_TTS_API_KEY`

如果目标项目已经有统一配置模块或密钥管理，就沿用现有方案，不要再发明第二套配置入口。

## 接口概览

- `GET /healthz`
  - 不需要 API Key
  - 用于就绪检查
- `GET /v1/speakers`
  - 不需要 API Key
  - 返回可用的 speaker 简称和完整 ID
- `POST /v1/tts`
  - 需要 `Authorization: Bearer <api-key>`
  - 返回完整音频二进制
- `POST /v1/tts/stream`
  - 需要 `Authorization: Bearer <api-key>`
  - 返回音频二进制流

应用侧不要接 `/metrics`。这个接口只给后台登录态，不是公开调用接口。

## 请求体

```json
{
  "text": "你好，欢迎使用服务模式",
  "speaker": "taozi",
  "format": "aac",
  "speed": 0,
  "pitch": 0
}
```

规则：

- `text` 必填，且不能为空白
- `speaker` 可选
- `format` 可选，允许值：`aac`、`mp3`
- `speed` 和 `pitch` 可选，范围 `-1.0` 到 `1.0`

## 非流式调用示例

### curl

```bash
curl -X POST "$DOUBAO_TTS_BASE_URL/v1/tts" \
  -H "Authorization: Bearer $DOUBAO_TTS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text":"你好，欢迎使用服务模式","speaker":"taozi","format":"aac"}' \
  --output output.aac
```

### Python `httpx`

```python
import httpx

base_url = "https://tts.example.com"
api_key = "替换成你的 API Key"

payload = {
    "text": "你好，欢迎使用服务模式",
    "speaker": "taozi",
    "format": "aac",
}

with httpx.Client(timeout=30.0) as client:
    response = client.post(
        f"{base_url}/v1/tts",
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
    )
    response.raise_for_status()
    audio_bytes = response.content
    speaker = response.headers.get("X-TTS-Speaker")
    audio_format = response.headers.get("X-TTS-Format")
```

### TypeScript / fetch

```ts
const baseUrl = process.env.DOUBAO_TTS_BASE_URL!;
const apiKey = process.env.DOUBAO_TTS_API_KEY!;

const res = await fetch(`${baseUrl}/v1/tts`, {
  method: "POST",
  headers: {
    "Authorization": `Bearer ${apiKey}`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    text: "你好，欢迎使用服务模式",
    speaker: "taozi",
    format: "aac",
  }),
});

if (!res.ok) {
  throw new Error(`TTS failed: ${res.status} ${await res.text()}`);
}

const audio = Buffer.from(await res.arrayBuffer());
const speaker = res.headers.get("x-tts-speaker");
const format = res.headers.get("x-tts-format");
```

## 流式调用示例

### curl

```bash
curl -X POST "$DOUBAO_TTS_BASE_URL/v1/tts/stream" \
  -H "Authorization: Bearer $DOUBAO_TTS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text":"这是一段用于流式输出的文本","speaker":"taozi","format":"aac"}' \
  --output stream_output.aac
```

### Python `httpx`

```python
import httpx

base_url = "https://tts.example.com"
api_key = "替换成你的 API Key"

with httpx.stream(
    "POST",
    f"{base_url}/v1/tts/stream",
    headers={"Authorization": f"Bearer {api_key}"},
    json={"text": "流式测试", "speaker": "taozi", "format": "aac"},
    timeout=30.0,
) as response:
    response.raise_for_status()
    with open("stream_output.aac", "wb") as output:
        for chunk in response.iter_bytes():
            if chunk:
                output.write(chunk)
```

## 健康检查语义

示例：

```json
{
  "status": "not_ready",
  "ready": false,
  "setup_completed": false,
  "enabled_api_keys": 0,
  "total_accounts": 0,
  "healthy_accounts": 0,
  "detail": "Admin setup has not been completed"
}
```

解释：

- `200` 且 `ready=true`：服务真正可用
- `503` 且 `ready=false`：服务进程活着，但还不能对外承接请求

常见的 `not_ready` 原因：

- 后台初始化未完成
- 没有启用中的 API Key
- 没有健康的豆包凭据

## 失败处理

- `400`：请求格式或参数不合法
- `401`：API Key 缺失或错误
- `502`：上游握手 / Cookie / block 类失败
- `504`：上游超时
- `503`：服务未就绪、没有健康凭据，或流式被禁用

成功响应按二进制读取；失败响应解析 JSON：

```json
{
  "error": "unauthorized",
  "detail": "Invalid API key"
}
```

## 排障顺序

1. 先打 `/healthz`
2. 如果 `setup_completed=false`，先完成 `/admin/setup`
3. 如果 `enabled_api_keys=0`，去 `/admin/api-keys` 创建 key
4. 如果 `healthy_accounts=0`，去 `/admin/accounts` 增加凭据
5. 再验证 `/v1/speakers`
6. 最后用一段短文本重试 `/v1/tts`，别一上来就怀疑业务代码
