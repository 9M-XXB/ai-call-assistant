# AI来电助手（OPPO 系列手机 + MacBook 特化版）

> **本项目由 ZCode（编码智能体，模型 GLM-5.3-Flash）创建与调试。**

来电响铃 10 秒无人接听时自动接起，向对方播放自定义的多语言问候语（含录音告知），请对方留言并录音，挂断后在本地完成语音转写与 AI 摘要。**全程不依赖运营商语音信箱，手机端零安装**（只靠 USB + ADB）。

> **⚠️ 适用范围说明**：本项目是针对 **OPPO 系列手机（含一加 OnePlus，ColorOS/OxygenOS 系统）+ MacBook 笔记本（Apple Silicon）** 的特化流程。
> - 自动接听依赖 ADB `input keyevent`，已在 **一加 Ace 5 至尊版（ColorOS 15）** 上验证；其他 OPPO/一加机型需自行验证；
> - 依赖 ColorOS 特有的「USB 调试（安全设置）」开关来模拟按键输入，其他系统没有对应概念；
> - 音频链路（扬声器/麦克风摆放、电平标定、Blue Yeti 麦克风）在 **MacBook Air** 上完成；
> - 其他品牌手机或 Windows/Linux 电脑可参考架构自行适配，核心限制（Android 不开放通话音频流）是共通的。

## 工作原理

```
一加手机(USB/adb)                    MacBook Air
┌──────────────────┐   dumpsys     ┌─────────────────────────────────┐
│ 原生系统, 零安装   │◄─────────────►│ assistant.py 状态机              │
└────────┬─────────┘   keyevent   │  响铃检测 → 策略判断 → 10s 自动接听│
         │ 声学                    │  → 播放问候语 → 提示音 → 录音     │
         ▼                        │  → 挂断 → Whisper 转写 → LLM 摘要 │
   Mac 扬声器 ─→ 手机麦克风(上行)   │  → transcripts/ summaries/      │
   手机听筒 ─→ 外置麦克风 ─→ Mac录音 │                                 │
└─────────────────────────────────┘
```

关键设计：

- **半双工录音**：问候语播完才开始录音，天然免去回声消除；
- **通话音量自动拉满**：接通后连按"音量上"，保证听筒输出可供拾取；
- **语音频带滤波**：录音实时做 80–3800Hz 带通，抑制低频隆隆声与高频嘶声；
- **本地优先**：转写（mlx-whisper）与摘要（ollama）全部本机完成，无云端依赖。

## 仓库结构

```
├── README.md            # 本文件
└── code/
    ├── assistant.py         # 主程序（单文件，Python ≥3.9）
    ├── config.example.json  # 配置样例
    └── README.md            # 安装、配置、命令、故障速查（代码文档）
```

> 设计调研与部署细节文档（可行性分析 / 部署方案 / 电脑方案详解）仅本地留存，不入库。

## 快速开始

完整步骤见 [code/README.md](code/README.md)，概要：

```bash
brew install --cask android-platform-tools ffmpeg
brew install ollama && brew services start ollama
pipx install mlx-whisper        # 可选，缺失时只保存录音

cp code/config.example.json code/config.json
python3 code/assistant.py --check        # 环境自检
python3 code/assistant.py --record-test  # 录音通道标定
python3 code/assistant.py                # 常驻运行
```

## 隐私说明

本仓库**不包含**任何通话录音、日志、问候语音频与个人配置（见 `.gitignore`）：

- `recordings/ transcripts/ summaries/ logs/`——运行期产生，含个人通话内容；
- `greetings/`——问候语音频，含个人信息（如时差/回拨时段），按 code/README 的说明在本地生成；
- `config.json`——个人配置，首次使用从 `config.example.json` 复制修改。

录音告知：问候语文本内含"留言将被录音"提示；录音与摘要仅存本地。

## 项目目标与现状

**目标**：让笔记本 + 一部安卓手机组成"AI 秘书"，不在场时自动接听电话、播报问候语、采集留言并生成摘要。

**现状**（2026-09-22）：单机全链路已在真机（一加 Ace 5 至尊版 + MacBook Air + Blue Yeti）上验证——来电检测、10 秒自动接听、问候语播放、留言录音均正常；**转写（mlx-whisper medium）与本地摘要（ollama + qwen2.5-7B）已装机并实测通过**；录音信噪比经 RNNoise 神经网络降噪与摆位调优后持续改善中。
