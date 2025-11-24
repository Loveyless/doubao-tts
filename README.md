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

**方式一：命令行保存（推荐）**

```bash
python doubao_tts.py "测试" --cookie "sessionid=xxx; sid_guard=xxx; uid_tt=xxx" --save-cookie
```

Cookie 将保存到 `.cookie` 文件，后续使用无需再次提供。

**方式二：手动创建配置文件**

在项目目录下创建 `.cookie` 文件：

```bash
echo "sessionid=你的sessionid; sid_guard=你的sid_guard; uid_tt=你的uid_tt" > .cookie
```

### Cookie 有效期

- Cookie 有效期约 **30 天**
- 过期后需重新登录豆包并获取新的 Cookie
- 如果出现连接失败，请先检查 Cookie 是否过期

## 快速使用

### 命令行

```bash
# 基础用法
python doubao_tts.py "你好，世界" -o hello.aac

# 指定语音角色
python doubao_tts.py "欢迎使用豆包" -s yangguang -o welcome.aac

# 调整语速和音调
python doubao_tts.py "快速朗读测试" --speed 0.5 --pitch 0.2 -o fast.aac

# 查看可用语音
python doubao_tts.py --list-speakers ""
```

### Python API

```python
from doubao_tts import DoubaoTTS, TTSConfig, SPEAKERS

# 方式1: 简单使用
tts = DoubaoTTS()
result = tts.synthesize_sync("你好，世界")
if result.success:
    with open("output.aac", "wb") as f:
        f.write(result.audio_data)

# 方式2: 自定义配置
config = TTSConfig(
    speaker="zh_male_yangguang_conversation_v4_wvae_bigtts",
    speech_rate=0.2,  # 稍快
    pitch=0,
    format="aac"
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
```

## 注意事项

1. 此项目仅供学习研究使用
2. 请勿用于商业用途
3. 接口可能随时变更
4. 建议合理控制请求频率

## License

MIT
