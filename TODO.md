# TODO / 路线图

> 双语文件：每项先列中文目标与拆解，后附 English summary。
> 完成某项后在此勾选并注明日期，同时更新 README「项目目标与现状」。

---

## 1. 从"记录留言"升级为"能分析、能回话、能对话"

**目标**：通过接入轻量化本地模型或云端大模型 API，让助手不止有记录能力，还具备
**分析（理解来电意图）→ 回话（向对方播报/应答）→ 对话（多轮往复）** 的完整能力。
回话风格通过**预设（persona preset）**切换，例如：

- **追根究底型**：多轮追问，尽可能采集更多信息——来电人身份、联系方式、事项细节、紧急程度、是否需要回电；
- **简洁告知型**：把该提供的信息说完即礼貌结束并挂断，不展开对话。

**现状与差距**：当前链路是单轮半双工——问候语 → 对方留言 → 挂断后离线转写 + 摘要。
没有实时转写，没有语音合成出口，没有轮次决策逻辑。

**拆解**：

- [ ] 分轮（或实时）转写来电者语音：mlx-whisper 按段转写（本地）或云端 ASR
- [ ] 语音合成出口：macOS `say` / edge-tts / Piper（本地）或云端 TTS
- [ ] 对话决策大脑：ollama 小模型（本地优先）或云端 LLM API（隐私取舍需在文档中明示）
- [ ] 风格预设机制：`config.json` 增加 `persona` 字段 + 提示词模板（`probing` / `minimal` / 自定义）
- [ ] 工程关键问题：轮次检测（判断对方说没说完）、端到端时延控制、并行"播放+录音"时的回声消除（当前半双工架构需重新评估）、主动挂断的时机判断

<details open>
<summary><strong>English summary</strong></summary>

Upgrade from passive message-taking to a conversational agent by integrating
lightweight local models or cloud LLM APIs: analyze the caller's intent, speak
replies, and hold a multi-turn dialogue. Conversation style is switchable via
presets, e.g. a "probing" persona that asks follow-up questions to capture as
much information as possible, vs. a "minimal" persona that delivers the needed
information and hangs up politely.

</details>

---

## 2. 获取手机 OS 尽可能高的权限：完全静音下也能完成对话

**目标**：拿到手机 OS 尽可能高的权限，使手机处于**完全静音**状态（铃声/媒体/通话音量全为 0、静音开关打开）时，
"自动接听 → 问候语 → 留言"全链路依然可靠完成，不依赖人工干预，也不依赖当前"连按音量上"的补救手段。

**现状与差距**：现在依赖声学耦合 + 普通 ADB shell 权限：接通后连按 15 次 VOLUME_UP 拉满通话音量，
整条链路建立在"听筒出声、麦克风拾音"的物理通路上；权限等级最低，能做的系统干预有限。

**拆解（按权限从低到高）**：

- [ ] ADB 层增强：响铃/接通事件时自动检查并设置各音频流（`media volume`、`cmd audio`），确保通话流音量最大且未静音；监测静音开关状态并告警
- [ ] Shizuku：通过 ADB 激活的特权进程，免 root 调用部分系统 API（音量流控制、系统设置修改）
- [ ] Root（Magisk）：解锁 bootloader 后可获得最高权限（注意：ColorOS 解锁限制多、会清数据、失保，需谨慎评估）；root 后经 Magisk 模块触达 signature/privileged 级音频接口（如通话音频回路 `REMOTE_SUBMIX` / `CAPTURE_AUDIO_OUTPUT`），实现**纯数字链路**——彻底摆脱麦克风/扬声器物理摆放
- [ ] 终极形态：数字方式直接注入/截取通话音频，声学耦合降级为备份方案

<details open>
<summary><strong>English summary</strong></summary>

Obtain the highest achievable privileges on the phone OS — ADB automation →
Shizuku → root/Magisk — so that the full "auto-answer → greeting → message"
flow completes reliably even with the phone fully muted (all volumes at zero,
silent switch on), ultimately replacing the acoustic coupling with a direct
digital audio path into/out of the call stream.

</details>

---

## 3. ⭐ 最终目标：通用化——手机装一个 App 就是完全体

> 同类产品盘点、公开 API 能力边界、路线对比（A 默认拨号器+免提回路 / B 特权数字链路 / C 云托管对照）与分步实施计划，详见本地调研文档《通用化调研.md》（按约定不入库）。

**目标**：把整套能力收进**一个普通安卓 App**：手机只需安装这一个 App（不需要电脑、不需要第二台设备、
不限手机型号），即获得**完全体功能**——包括当前已实现的部分（响铃监测、10 秒延时自动接听、
自定义多语言问候语、留言录音、本地转写 + AI 摘要），以及 TODO 1/2 的全部能力（分析/回话/多轮对话、
风格预设、完全静音可用）。

**现状与差距**：当前是"Mac + 数据线 + 声学耦合"的双机特化方案；通用化要在 App 内部重建
感知（来电监测）、执行（接听/挂断/音量）、音频（问候出口 + 留言入口）三条链路。

**拆解（分级路线）**：

**A. 普通 App 可达的完全体（公开 API，任何 Android 9+ 手机）**

- [ ] 自动接听：`InCallService`（成为默认电话应用，最稳）或 `ANSWER_PHONE_CALLS` 运行时权限 + `acceptRingingCall()`——替代 ADB keyevent
- [ ] 来电监测与策略：`CallScreeningService` + `TelecomManager`；白/黑名单、时段策略沿用现有设计
- [ ] 单机音频回路（替代跨设备声学耦合）：接通后经 `InCallService.setAudioRoute()` 切免提，TTS 问候语走"扬声器 → 麦克风"回路播入通话；同一路径录对方留言
- [ ] 端侧 AI 全家桶：whisper.cpp / sherpa-onnx（ASR）+ MediaPipe LLM Inference / llama.cpp 小模型（对话决策 + 摘要）+ 端侧 TTS，全离线
- [ ] 风格预设与音量自主权：App 内经 `AudioManager` 直接管理铃声/通话各音频流，静音开关不再影响链路
- 已知天花板：免提回路音质有限；第三方 App 仍拿不到通话流的**数字**音频（`CAPTURE_AUDIO_OUTPUT` 为 signature 级，Android 设计如此）

**B. 数字链路完全体（需特权，与 TODO 2 汇合）**

- [ ] root/Magisk 模块或系统签名，打通通话流的数字注入/截取，音质与可靠性不再受声学限制
- [ ] 或：与 OEM 合作/随 ROM 定制的系统级"来电秘书"（Pixel Call Screen、Bixby 智能接听即此类）

**C. 备选架构（记录用，不作为目标）**

- [ ] 云托管代接：运营商呼叫转移 → 云端语音机器人。彻底摆脱机型与本地算力，但重新引入运营商/云端依赖与隐私取舍——与"手机上自足"的初衷相悖，仅作对照

<details open>
<summary><strong>English summary</strong></summary>

Ultimate goal — generalization: a single ordinary Android app delivers the
complete feature set (everything already working, plus TODO 1–2) on any phone
model with no computer involved. Phase A reaches this within public APIs
(`InCallService` auto-answer, speakerphone loopback for greeting & recording,
fully on-device ASR/LLM/TTS, in-app audio-stream control); Phase B reaches the
fully digital call-audio path via privileged access (root/system signature, or
OEM-level integration); a cloud-hosted call-forwarding variant is recorded
only as a contrasting alternative, since it reintroduces carrier/cloud
dependency.

</details>
