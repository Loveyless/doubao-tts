---
name: doubao-tts-service-client
description: 将应用代码接入一个已经部署好的 Doubao TTS HTTP 服务。适用于项目需要调用现成的 `/v1/tts` 或 `/v1/tts/stream` 接口、接健康检查、获取 speaker 列表、封装客户端，或编写接入文档的场景。触发关键词包括：接入自建 TTS 服务、给其他仓库增加 TTS 能力、根据服务地址和 API Key 生成调用封装。
---

# Doubao TTS 服务接入

## 快速开始

- 必须同时具备服务 `base URL` 和 `API Key`。只有服务器地址，调不通 `/v1/tts` 和 `/v1/tts/stream`。
- 默认优先使用环境变量，例如 `DOUBAO_TTS_BASE_URL` 和 `DOUBAO_TTS_API_KEY`，除非目标项目已经有更强的配置约定。
- `https://tts.example.com` 只是部署示例，不要硬编码进业务代码。
- 客户端出问题前，先打 `/healthz`。如果 `ready=false`，问题在服务端初始化或凭据状态，不在接入代码。

## 接入流程

1. 给目标项目增加可配置的 `base URL` 和 `API Key`。
2. 封装一个薄客户端，不要把裸 HTTP 请求散落到业务代码里。
3. 按返回方式选择接口：
   - `/v1/tts`：一次性返回完整音频二进制
   - `/v1/tts/stream`：返回二进制流，不是 SSE
4. 明确处理失败类型：
   - `401`：缺少或错误的 API Key
   - `400`：请求体不合法
   - `502` / `504`：上游失败或超时
   - `503`：服务未就绪、流式被禁用或没有健康凭据
5. 在接入业务流程前，先用 `/v1/speakers` 和一段短文本做最小验证。

## 接入规则

- API Key 走 `Authorization: Bearer <api-key>`，不要放 URL 参数。
- 请求体使用 JSON，`text` 必填；`speaker`、`format`、`speed`、`pitch` 可选。
- `200` 响应按音频二进制处理，不要成功时还去解析 JSON。
- 有需要时保留并透出这些响应头：
  - `X-TTS-Speaker`
  - `X-TTS-Format`
  - `X-TTS-Attempt-Count`
- 不要把应用接到 `/metrics`；那个接口只给后台登录态用。
- 如果用户还没有 API Key，引导去服务后台的 `/admin/api-keys` 创建。
- 如果 `/healthz` 显示未初始化，先完成 `/admin/setup`，再去 `/admin/accounts` 配凭据。

## 参考资料

- 给其他项目写客户端、环境变量说明、健康检查或排障文档时，读取 [references/api.md](references/api.md)。
