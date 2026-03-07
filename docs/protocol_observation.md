# 协议观察记录

该文档记录一次基于本地 `.cookie.json` 的真实调用观察结果，用于后续维护 `doubao_tts.py` 的完成态判断和日志设计。

## 观察入口

- 固化脚本：`scripts/observe_session.py`
- 观察日志文件：`logs/session_observation.log`
- 产物文件：`logs/session_observation.aac`
- 观察文本：`日志观察：这是一次真实调用，用于记录服务端事件序列。`
- 当前实现回归日志：`logs/cli_verbose_run.log`

## 实际观察到的事件序列

按接收顺序，服务端返回了以下内容：

1. `open_success`
2. `sentence_start`
3. 多个二进制音频块
4. `sentence_end`
5. `finish`
6. WebSocket 以 `code=1000` 正常关闭

同一次调用中，音频不是单块返回，而是被拆成多个二进制帧。该次观察一共收到 24 个二进制块，总大小为 `41357 bytes`。

## 原始日志摘录

```text
recv[1]=json {"event": "open_success", "code": 0, "message": ""}
recv[2]=json {"event": "sentence_start", "code": 0, "message": "", "readable_text": "日志观察：这是一次真实调用，用于记录服务端事件序列。"}
recv[3]=binary 675 bytes
...
recv[26]=binary 33 bytes
recv[27]=json {"event": "sentence_end", "code": 0, "message": ""}
recv[28]=json {"event": "finish", "code": 0, "message": ""}
close=websocket code=1000 reason='1000-'
```

## 对代码的直接结论

- 不能再把“超时后退出”当成正常完成。服务端已经提供了显式 `finish` 事件，这个信号更可靠。
- 成功判定不该只看“有没有收到字节”。至少要结合 `open_success`、句子事件配对情况，以及 `finish` 或正常关闭。
- 音频数据天然是分块流式返回，后续如果支持实时播放或边下边播，应该围绕二进制分块设计，而不是假设一次性返回完整文件。
- 当前实现已把这些协议事实映射到 `TTSResult.event_order`、`TTSResult.audio_chunk_count`、`TTSResult.finish_received`、`TTSResult.close_code` 等字段里。

## 当前实现回归检查

当前代码已将 `finish` 事件纳入完成态判断。一次 CLI 回归日志中可以看到如下输出：

```text
[INFO] 连接成功
[INFO] 句子开始: CLI 日志验证：检查 finish 事件处理。...
[INFO] 收到 finish 事件，服务端已完成输出
[INFO] 合成完成, 音频大小: 32425 bytes
```

## 错误响应观察

在连续测试过程中，还观察到服务端直接返回 `event` 为空字符串的错误 JSON：

```json
{"event": "", "code": 710022002, "message": "block"}
```

这说明错误处理不能只盯着 `event == "error"`，还必须保留原始 JSON 响应并检查 `code != 0`。

## block 处理策略

当前实现对 `{"event": "", "code": 710022002, "message": "block"}` 做了显式可配的退避重试支持，但默认关闭：

- 默认不重试，避免无脑放大风控压力
- 只有显式启用 `retry_on_block=True` 或 CLI 传入 `--retry-on-block` 时才会生效
- 退避参数由 `retry_max_retries`、`retry_backoff_seconds`、`retry_backoff_multiplier` 控制
- 可选抖动由 `retry_backoff_jitter_ratio` 控制，`0.2` 表示每次实际等待时间会在基准退避上下浮动 20%
