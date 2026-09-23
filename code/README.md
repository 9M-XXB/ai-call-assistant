# AI来电助手 · 代码文档（Mac + ADB 无 App 架构）

代码：[`assistant.py`](assistant.py)（单文件，Python ≥3.9，无强依赖第三方包）+ [`config.example.json`](config.example.json)。
上层设计与原理见 [../电脑方案详解.md](../电脑方案详解.md)；本文只讲代码怎么装、怎么配、怎么跑。

---

## 1. 架构总览

```
一加A机(USB/adb)                 MacBook Air
┌────────────────┐   dumpsys    ┌──────────────────────────────┐
│ 原生系统,零安装 │◄────────────►│ assistant.py 状态机           │
└───────┬────────┘  keyevent   │  IDLE                        │
        │ 声学                  │   └响铃→策略判断→10s→接听     │
        ▼ (空气中)             │   ACTIVE: afplay greeting    │
   Mac 扬声器 → A机麦克风       │          → 提示音            │
   A机听筒 → (可选USB麦) → Mac麦 │          → ffmpeg 录音       │
                               │   挂断→ mlx-whisper → ollama │
                               │   → transcripts/ summaries/  │
                               └──────────────────────────────┘
```

三个设计要点（改代码前先读）：

1. **半双工录音**：greeting 用 `afplay` 播完之后才启动 ffmpeg——播放与录音不重叠，免去回声消除；代价是对方在 greeting 期间开口的话录不到（可接受）。
2. **只录自动接听的通话**：用户在 10 秒内自己接（手机上接或点 Mac 弹窗"接听"）的通话**不录音**——录音告知只存在于 greeting 里，人工接的通话没有告知，故不录。这是有意为之的合规取向。
3. **状态源唯一**：通话状态只从 `dumpsys telephony.registry` 的 `mCallState`（0/1/2）读取，这是 Android 各版本最稳定的监控口；`mCallIncomingNumber` 取来电号码。

## 2. 安装

```bash
# Mac 侧（brew + pipx）
brew install --cask android-platform-tools   # adb
brew install ffmpeg                           # 录音
brew install ollama && brew services start ollama
ollama pull qwen2.5:7b-instruct-q4_K_M        # 8GB 内存机器换 qwen2.5:3b 并同步改配置
pipx install mlx-whisper                      # Apple Silicon 转写（可选，缺失时只存录音不改跑）

# 手机侧（一次性）
# 设置→关于本机→版本信息→连点"版本号"7次 → 开发者选项 → 打开 USB 调试
# 若自动接听指令无效：再打开「USB 调试（安全设置）」（ColorOS 允许 ADB 模拟输入）
# 首次连接勾选「一律允许使用这台计算机进行调试」
```

然后：

```bash
cd code
cp config.example.json config.json
python3 assistant.py --list-devices   # 记下麦克风设备号 → 填入 config.json 的 ffmpeg_audio_device
python3 assistant.py --check          # 环境自检，全部 ✅ 再运行
python3 assistant.py                  # 常驻运行（建议 tmux 或 launchd）
```

系统权限：系统设置 → 隐私与安全性 → 麦克风 → 勾选你的终端 App。

## 3. 配置项（config.json）

| 键 | 默认 | 说明 |
|---|---|---|
| `adb_path` | `"adb"` | adb 可执行文件路径 |
| `auto_delay_sec` | `10` | 响铃多少秒后自动接听 |
| `max_record_sec` | `180` | 留言最长时长，超过由脚本发 ENDCALL 挂断 |
| `call_volume_keys` | `15` | 接通后自动按 N 次"音量上"把通话音量拉满（让对方声音在听筒处足够响，供录音通道拾取；`0` 禁用） |
| `answer_mode` | `"all"` | `all` 全部自动接听；`whitelist` 仅白名单 |
| `whitelist` | `[]` | 号码子串匹配列表，如 `["138"]` |
| `blocklist` | `[]` | 永不自动接听的号码子串（命中只弹通知） |
| `active_hours` | `null` | `null`=全天；`{"start":"22:00","end":"08:00"}` 支持跨午夜 |
| `greeting_file` | `greetings/zh-CN.wav` | 相对 `code/` 的路径；多语言多存几份换着用 |
| `beep` | `true` | greeting 后播放系统"叮"提示音再录音 |
| `ffmpeg_audio_device` | `":0"` | AVFoundation 音频设备号（`--list-devices` 查询） |
| `whisper_model` | `mlx-community/whisper-medium-mlx` | HuggingFace 上的 mlx 模型仓；首次运行自动下载 |
| `ollama_model` / `ollama_url` | qwen2.5:7b / 本地 11434 | 本地摘要 LLM；换云 API 时改 `summarize()` |

**greeting 制作**：16kHz 单声道，**建议 ≤15 秒**（过长对方会在留言前挂断），内容**建议含录音告知**（如"本次通话将被录音"，合规要求因地区而异）。可用自己录音、`say -v Tingting`（macOS 本地合成）、`edge-tts`（免费）或 ElevenLabs 克隆生成；多语言各存 `greetings/` 一份。改文件内容不改文件名即可生效，无需重启服务。

## 4. 命令与日常使用

| 命令 | 用途 |
|---|---|
| `python3 assistant.py --check` | 自检：adb/连接/greeting/ffmpeg/whisper/ollama 六项 |
| `python3 assistant.py --list-devices` | 列出 AVFoundation 录音设备，填配置 |
| `python3 assistant.py --record-test 6` | **录音通道标定**：倒计时后录 6 秒，此期间对着手机听筒缝弹指/说话，输出 mean/max 音量并给出合格判定（max ≥ -20dB 为合格） |
| `python3 assistant.py --once` | 打印当前通话状态与来电号码（调试解析用） |
| `python3 assistant.py` | 常驻运行 |

## 3.5 录音通道（录对方留言）

对方的声音从 A 机**顶部听筒缝**出来（手机保持听筒模式，脚本接通后已自动拉满通话音量）。录音设备由 `ffmpeg_audio_device` 指定（Mac 内置麦克风或 USB 麦），三种拾音摆法：

| 方案 | 硬件 | 摆法 | 质量 |
|---|---|---|---|
| 内置麦 | 无 | 手机**横放在 Mac 左掌托前缘**：底边（上行麦）朝左侧扬声器格栅，**顶边（听筒）朝向键盘左上的麦克风开孔区**；用 `--record-test` 反复微调位置 | 中（依赖机型麦克风位置，务必标定） |
| **USB 领夹麦（推荐）** | ¥80–300 | 手机留在桌面原位（底边朝 Mac），麦头用胶带固定在**听筒缝旁 5–10mm**；`ffmpeg_audio_device` 改为该设备号 | 好 |
| 有线耦合 | USB-C 转 3.5mm 转接头 + 廉价耳麦 | 耳机麦贴 Mac 扬声器格栅（greeting 进上行）、耳塞贴 Mac 麦克风孔（对方声音进录音）——全数字路径 | 最好；转接头需带 PD 透传否则手机无法边用边充电 |

**标定流程**（换位置/换设备后必做一次）：`python3 assistant.py --record-test 6` → 倒计时 3 秒后在听筒缝处弹指或说话 → 看判定。合格后固定位置，以后不再挪动。

运行期行为：

- **人在电脑旁**：来电时 Mac 弹窗（含"接听"按钮）——点"接听"立即人工接起，**本通不录留言**；什么都不点则 10 秒后自动接听并进入留言流程；10 秒内在手机上自己接也安全，脚本检测到 offhook 自动让路。
- **人不在**：自动接听 → greeting → 提示音 → 对方留言（最长 180 秒，超时脚本挂断）→ 挂断后本地转写 + 摘要 → Mac 通知。
- 产物：`recordings/*.wav`（16kHz 单声道，约 2MB/分钟）、`transcripts/*.txt`、`summaries/*.md`（含元信息+摘要+原始转写）、`logs/assistant.log`。

## 5. 已知限制与扩展点

| 限制 | 原因 / 现状 | 想改的话 |
|---|---|---|
| 自动接通后无法"掐断"greeting | 脚本无法区分"它接的"和"你接的" | 加全局热键 kill afplay 进程，或 greeting 缩短 |
| 自动接通后你拿起手机说话，greeting 仍播完 | 同上 | 同上 |
| 对方在 greeting 期间开口的内容丢失 | 半双工时序（设计换取免回声消除） | 引入 sox/自适应滤波做软件 AEC 后改并行录 |
| 手工接听的通话不录音 | 合规取向（无录音告知） | 若改，需自行解决告知义务 |
| `dumpsys` 字段随系统版本可能变化 | ColorOS/更新可能改格式 | 用 `--once` 核对，调 `call_state()` 的正则 |
| `KEYCODE_HEADSETHOOK` 接听在个别 ROM 失效 | OEM 定制 | 开「USB 调试（安全设置）」；仍无效则改 scrcpy 触摸接听按钮 |
| 摘要走本地 ollama，8GB 机器偏慢 | 模型内存 | 换 3B 模型，或改 `summarize()` 调云端 API（注意隐私取舍） |

## 6. launchd 常驻（可选）

```xml
<!-- ~/Library/Launchers/com.aicall.assistant.plist 略——用下面命令最简 -->
```

```bash
# 最简常驻：tmux
brew install tmux && tmux new -d -s aicall 'python3 ~/.../code/assistant.py'

# 或 launchd：保存为 ~/Library/LaunchAgents/com.aicall.assistant.plist 后
#   launchctl load ~/Library/LaunchAgents/com.aicall.assistant.plist
```

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.aicall.assistant</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Users/YOU/AICallAssistant/assistant.py</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/aicall.out</string>
  <key>StandardErrorPath</key><string>/tmp/aicall.err</string>
</dict></plist>
```

> 把路径换成实际位置；`KeepAlive` 保证脚本崩溃自动重启。另记得防止 Mac 系统休眠（`caffeinate -s` 或电源设置），否则服务随睡眠停摆。

## 7. 故障速查

| 症状 | 处置 |
|---|---|
| `--check` 提示手机未连接 | 换数据线（纯充电线是头号原因）→ 换 USB 口 → 插拔触发授权弹窗 |
| 响铃但不自动接 | `--once` 看 mCallState 是否为 1；手动 `adb shell input keyevent 85` 验证；无效则开「USB 调试（安全设置）」 |
| 对方说 greeting 声音小 | Mac 输出音量调高、手机挪近扬声器、确认没人动过摆放 |
| 录音文件是空的/无声 | 系统设置→隐私→麦克风→勾选终端；ffmpeg 设备号是否选对 |
| **录音全是 -91dB 数字静音** | **macOS 麦克风权限未授予运行服务的 App（终端）**——系统不报错只返回静音。命令行子进程不触发弹窗，必须手动授权：服务在哪个终端跑，就授权哪个终端 App。验证：`ffmpeg -i 录音 -af volumedetect -f null -` 看 max_volume |
| 录音 max 触顶 0.0dB（削波） | 麦克风增益太高：调低 Yeti 的 GAIN 或拉远距离，目标 max 在 -12 ~ -3dB |
| 摘要一直失败 | `ollama list` 确认模型已拉取；`curl 127.0.0.1:11434/api/tags` 看服务 |
| 转写中文乱/漏 | whisper 换 large-v3 系 mlx 模型（更慢更准），或留言提示对方说普通话 |
