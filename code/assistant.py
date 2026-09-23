#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI来电助手 · Mac + ADB 无 App 架构
====================================
数据流:
  iPhone... 不, 一加A机(USB) --adb--> 本程序(Mac)
  响铃检测(dumpsys telephony.registry) → 策略判断 → 10s 自动接听(KEYCODE_HEADSETHOOK)
  → afplay 播放 greeting → (提示音) → ffmpeg 录音(半双工, 通话结束即停)
  → mlx-whisper 本地转写 → ollama 本地 LLM 摘要 → transcripts/ summaries/

用法:
  python3 assistant.py --check         # 环境自检
  python3 assistant.py --list-devices  # 列出 AVFoundation 录音设备(填配置用)
  python3 assistant.py                 # 常驻运行
  python3 assistant.py --once          # 打印一次通话状态(调试解析)

依赖: macOS 自带 afplay/osascript; brew: adb, ffmpeg, ollama; pipx: mlx-whisper(可选)
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess as sp
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOG = logging.getLogger("assistant")

# ---------------------------------------------------------------- 配置 -------

DEFAULTS = {
    "adb_path": "adb",
    "auto_delay_sec": 10,          # 响铃多少秒后自动接听
    "max_record_sec": 180,         # 留言最长录音时长, 超时由脚本挂断
    "call_volume_keys": 15,        # 接通后按几次音量上(拉满通话音量, 供录音通道拾取听筒声; 0=禁用)
    "denoise_level": "rnn",        # 降噪档位: off | band(仅带通) | fft(谱减法) | rnn(RNNoise 神经网络, 最激进)
    "rnn_model_url": "https://raw.githubusercontent.com/GregorR/rnnoise-models/master/somnolent-hogwash-2018-09-01/sh.rnnn",
    "answer_mode": "all",          # all=全部自动接听 | whitelist=仅白名单号码
    "whitelist": [],               # answer_mode=whitelist 时生效, 子串匹配
    "blocklist": [],               # 永不自动接听的号码, 子串匹配(如推销号段)
    "active_hours": None,          # null=全天; 或 {"start":"22:00","end":"08:00"} 可跨午夜
    "greeting_file": "greetings/zh-CN.wav",
    "beep": True,                  # greeting 播完后播放系统提示音再开始录音
    "ffmpeg_audio_device": ":0",   # 用 --list-devices 查询后填写
    "whisper_model": "mlx-community/whisper-medium-mlx",
    "whisper_initial_prompt": "以下是普通话的句子。",   # 偏置简体中文; 多语言场景可置空
    "ollama_model": "qwen2.5:7b-instruct-q4_K_M",
    "ollama_url": "http://127.0.0.1:11434/api/generate",
}

class Config(dict):
    """config.json + 默认值合并, 属性式访问 cfg.auto_delay_sec"""
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(k) from e

def load_config(path: Path) -> Config:
    if not path.exists():
        sys.exit(f"[配置] 未找到 {path}。请 `cp config.example.json config.json` 后修改。")
    cfg = Config(DEFAULTS)
    with open(path, encoding="utf-8") as f:
        cfg.update(json.load(f))
    return cfg

def in_active_window(cfg: Config, now=None) -> bool:
    w = cfg.active_hours
    if not w:
        return True
    t = (now or datetime.now()).time()
    s = datetime.strptime(w["start"], "%H:%M").time()
    e = datetime.strptime(w["end"], "%H:%M").time()
    if s <= e:
        return s <= t <= e
    return t >= s or t <= e          # 跨午夜窗口, 如 22:00–08:00

# ---------------------------------------------------------------- ADB 封装 ---

class Adb:
    KEY_HEADSET_HOOK = "85"          # 接听/挂断耳机键
    KEY_END_CALL = "6"               # ENDCALL

    def __init__(self, path="adb"):
        self.path = path

    def _run(self, *args, timeout=10) -> str:
        r = sp.run([self.path, *args], capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f"adb {' '.join(args)} 失败: {r.stderr.strip()[:200]}")
        return r.stdout

    def device_ok(self) -> bool:
        out = self._run("devices")
        return bool(re.search(r"\tdevice\b", out))

    def call_state(self) -> int:
        """0=idle 1=ringing 2=offhook。telephony.registry 是跨版本最稳的来源。"""
        out = self._run("shell", "dumpsys", "telephony.registry")
        m = re.search(r"mCallState=(\d)", out)
        return int(m.group(1)) if m else 0

    def incoming_number(self) -> str:
        out = self._run("shell", "dumpsys", "telephony.registry")
        m = re.search(r"mCallIncomingNumber=([^\s]*)", out)
        return (m.group(1) if m and m.group(1).lower() != "null" else "").strip()

    def answer(self):
        self._run("shell", "input", "keyevent", self.KEY_HEADSET_HOOK)

    def hangup(self):
        self._run("shell", "input", "keyevent", self.KEY_END_CALL)

# ---------------------------------------------------------------- 通知/音频 --

def notify(msg: str):
    LOG.info("[通知] %s", msg)
    try:
        sp.run(["osascript", "-e",
                f'display notification "{msg}" with title "AI来电助手"'],
               capture_output=True, timeout=5)
    except Exception as e:
        LOG.warning("通知失败: %s", e)

def play(path: Path):
    r = sp.run(["afplay", str(path)])
    if r.returncode != 0:
        raise RuntimeError(f"afplay 播放失败: {path}")

def ensure_rnn_model(cfg: Config) -> str | None:
    """RNNoise 模型: 本地有就用, 没有联网下载一次; 失败返回 None(回退 fft 降噪)"""
    model_dir = BASE / "models"
    model_path = model_dir / "somnolent-hogwash.rnnn"
    if model_path.exists() and model_path.stat().st_size > 1_000_000:
        return str(model_path)
    try:
        model_dir.mkdir(exist_ok=True)
        LOG.info("下载 RNNoise 降噪模型(约 8MB, 仅一次)…")
        urllib.request.urlretrieve(cfg.rnn_model_url, model_path)
        LOG.info("模型已就绪: %s", model_path)
        return str(model_path)
    except Exception as e:
        LOG.warning("RNNoise 模型下载失败, 回退 fft 降噪: %s", e)
        return None

def denoise_chain(cfg: Config) -> str:
    """按配置档位生成 ffmpeg 滤波链。rnn = RNNoise 神经网络降噪, 对稳态/非稳态噪声都激进"""
    lvl = str(cfg.denoise_level)
    if lvl == "off":
        return "highpass=f=80,lowpass=f=3800"
    if lvl == "fft":
        return "highpass=f=100,lowpass=f=3400,afftdn=nr=28:nf=-34,tn=1"
    if lvl == "rnn":
        model = ensure_rnn_model(cfg)
        if model:
            return ("highpass=f=100,lowpass=f=3400,"
                    f"afftdn=nr=12:nf=-28,arnndn=m={model},"
                    "speechnorm=e=6.25:r=0.00001:l=1")
        return "highpass=f=100,lowpass=f=3400,afftdn=nr=28:nf=-34,tn=1"
    return "highpass=f=80,lowpass=f=3800"            # band

def start_recording(cfg: Config, wav: Path) -> sp.Popen:
    """16kHz 单声道 WAV + 按配置降噪; 调用方负责 terminate()"""
    chain = denoise_chain(cfg)
    LOG.info("降噪链[%s]: %s", cfg.denoise_level, chain)
    return sp.Popen(
        ["ffmpeg", "-y", "-f", "avfoundation",
         "-i", cfg.ffmpeg_audio_device,
         "-af", chain, "-ar", "16000", "-ac", "1", str(wav)],
        stdout=sp.DEVNULL, stderr=sp.DEVNULL)

# ---------------------------------------------------------------- 交互弹窗 ---

def ask_user_pickup(number: str, timeout: int):
    """后台线程弹 macOS 对话框: 人在电脑旁可提前接听(不计留言流程)"""
    result = {}
    num = re.sub(r'["\\]', "", number or "未知号码")            # 防注入 AppleScript 字符串

    def _dialog():
        # AppleScript 字符串内不可靠支持 \n, 用 linefeed 拼接
        script = (
            'set msg to "来电: ' + num + '" & linefeed & linefeed & '
            '"点击「接听」立即接起(人工通话, 不录留言)。不操作则 '
            + str(timeout) + ' 秒后自动接听并开启留言。"\n'
            'display dialog msg with title "AI来电助手" '
            'buttons {"稍后处理", "接听"} giving up after ' + str(timeout)
        )
        r = sp.run(["osascript", "-e", script],
                   capture_output=True, text=True, timeout=timeout + 10)
        result["btn"] = "answer" if "接听" in (r.stdout or "") else None

    t = threading.Thread(target=_dialog, daemon=True)
    t.start()
    return result

# ---------------------------------------------------------------- 策略 -------

def policy_allow(cfg: Config, number: str) -> bool:
    for pat in cfg.blocklist:
        if pat and pat in number:
            LOG.info("策略拦截: %s 命中黑名单 %s", number, pat)
            return False
    if cfg.answer_mode == "whitelist":
        ok = any(pat and pat in number for pat in cfg.whitelist)
        if not ok:
            LOG.info("策略放行失败: %s 不在白名单", number or "(空)")
            return False
    return True

# ---------------------------------------------------------------- 转写/摘要 --

def transcribe(wav: Path, cfg: Config) -> str | None:
    """调用 mlx_whisper CLI(pipx/pip 安装均可)。未安装时留言仍保存, 只跳过转写"""
    exe = shutil.which("mlx_whisper") or str(Path.home() / ".local/bin/mlx_whisper")
    if not Path(exe).exists():
        notify("未安装 mlx-whisper, 跳过转写(录音已保存)")
        return None
    LOG.info("转写中 (%s)…", cfg.whisper_model)
    outdir = BASE / "transcripts"
    outdir.mkdir(exist_ok=True)
    r = sp.run([exe, str(wav), "--model", cfg.whisper_model,
                "--output-dir", str(outdir), "--output-format", "txt",
                "--initial-prompt", cfg.whisper_initial_prompt],
               capture_output=True, text=True, timeout=3600)
    txt_file = outdir / (wav.stem + ".txt")
    if r.returncode != 0 or not txt_file.exists():
        LOG.warning("转写失败: %s", (r.stderr or "")[-300:])
        notify("转写失败, 录音已保存")
        return None
    text = txt_file.read_text(encoding="utf-8").strip()
    return text or None

SUMMARY_PROMPT = """你是电话留言整理助手。下面是一段打进来的留言的语音转写文本, 请只输出以下 Markdown:

## 一句话概要
## 来电人
(是谁/自称/是否留下联系方式, 不知道写"未提及")
## 诉求清单
(- 条目式)
## 是否需要回电
(是/否 + 一句理由)

转写文本:
{transcript}
"""

def summarize(transcript: str, cfg: Config) -> str | None:
    if not transcript:
        return None
    body = json.dumps({"model": cfg.ollama_model,
                       "prompt": SUMMARY_PROMPT.format(transcript=transcript),
                       "stream": False}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(
                cfg.ollama_url, data=body, headers={"Content-Type": "application/json"}),
                timeout=180) as resp:
            return json.loads(resp.read()).get("response", "").strip() or None
    except Exception as e:
        LOG.warning("ollama 摘要失败: %s", e)
        notify("本地摘要失败, 转写文本已保存")
        return None

def post_process(wav: Path, cfg: Config):
    """录音 → 转写 → 摘要 → 落盘。供通话结束与 --process 共用"""
    transcript = transcribe(wav, cfg)
    summary = summarize(transcript or "", cfg)
    if summary:
        md = (f"# 留言摘要 · {wav.stem}\n\n{summary}\n\n---\n\n"
              f"## 原始转写\n\n{transcript or '(无)'}\n")
        (BASE / "summaries" / (wav.stem + ".md")).write_text(md, encoding="utf-8")
        notify("留言摘要已生成, 见 summaries/")
    elif transcript:
        notify("转写已保存(摘要不可用), 见 transcripts/")
    return transcript, summary

# ---------------------------------------------------------------- 通话流程 ---

def wait_state(adb: Adb, want: int, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if adb.call_state() == want:
            return True
        time.sleep(0.5)
    return False

def handle_ringing(cfg: Config, adb: Adb):
    number = adb.incoming_number()
    LOG.info("响铃: %s", number or "(未知号码)")
    notify(f"来电 {number or '未知号码'} — {cfg.auto_delay_sec} 秒后将自动接听")

    dialog = ask_user_pickup(number, cfg.auto_delay_sec)
    deadline = time.time() + cfg.auto_delay_sec
    while time.time() < deadline:
        if dialog.get("btn") == "answer":                      # 人在电脑旁, 人工接起
            adb.answer()
            if wait_state(adb, 2, 5):
                notify("已人工接听, 本通不录留言")
            return
        st = adb.call_state()
        if st == 2:
            LOG.info("用户已在手机上自行接听, 本通不录留言")
            return
        if st == 0:
            LOG.info("响铃结束(对方放弃或被处理)")
            return
        time.sleep(0.5)

    if not in_active_window(cfg):
        LOG.info("当前不在 active_hours 窗口内, 不自动接听")
        return
    if not policy_allow(cfg, number):
        return

    adb.answer()
    if not wait_state(adb, 2, 5):
        LOG.warning("接听指令未生效(检查 USB调试(安全设置))")
        notify("自动接听失败, 见日志")
        return
    LOG.info("已自动接听")
    run_session(cfg, adb, number)

def run_session(cfg: Config, adb: Adb, number: str):
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = f"{ts}-{re.sub(r'[^0-9+]', '', number) or 'unknown'}"
    wav = BASE / "recordings" / f"{stem}.wav"

    # 接通先把通话音量拉满: 对方声音从听筒出来越响, 录音通道拾取越干净
    keys = int(cfg.call_volume_keys)
    if keys > 0:
        for _ in range(keys):
            adb._run("shell", "input", "keyevent", "24")    # VOLUME_UP → 通话流
        LOG.info("通话音量已拉满 (%d 次 VOLUME_UP)", keys)

    if cfg.beep:
        play(Path("/System/Library/Sounds/Ping.aiff"))          # 经典留言"叮"
    LOG.info("播放 greeting: %s", cfg.greeting_file)
    play(BASE / cfg.greeting_file)                              # 期间不录音 → 免回声消除

    notify("请在提示音后留言")
    rec = start_recording(cfg, wav)
    LOG.info("录音开始: %s", wav.name)
    start = time.time()
    while time.time() - start < cfg.max_record_sec:
        if adb.call_state() != 2:                               # 对方挂断
            break
        time.sleep(1)
    else:
        LOG.info("达到最长录音时长, 主动挂断")
        adb.hangup()
    time.sleep(0.5)
    rec.terminate()
    rec.wait(timeout=10)
    LOG.info("录音结束: %.0f 秒", time.time() - start)

    post_process(wav, cfg)

# ---------------------------------------------------------------- 自检 -------

def cmd_check(cfg: Config, adb: Adb) -> int:
    ok = True
    def step(name, cond, hint=""):
        nonlocal ok
        print(f"{'✅' if cond else '❌'} {name}" + (f"  — {hint}" if (hint and not cond) else ""))
        ok = ok and cond

    try:
        adb._run("version")
        step("adb 可用", True)
    except Exception as e:
        step("adb 可用", False, str(e)); return 1
    step("手机已连接且已授权", adb.device_ok(), "adb devices 查看; 换数据线/重新授权")
    g = BASE / cfg.greeting_file
    step(f"greeting 文件存在 ({cfg.greeting_file})", g.exists(), "放入 greetings/ 或改配置")
    r = sp.run(["ffmpeg", "-version"], capture_output=True)
    step("ffmpeg 已安装", r.returncode == 0, "brew install ffmpeg")
    try:
        exe = shutil.which("mlx_whisper") or str(Path.home() / ".local/bin/mlx_whisper")
        ok = Path(exe).exists()
    except Exception:
        ok = False
    step("mlx-whisper CLI 可用", ok, "pipx install mlx-whisper (缺失时只保存录音, 不转写)")
    try:
        with urllib.request.urlopen(cfg.ollama_url.replace("/api/generate", "/api/tags"), timeout=5):
            step("ollama 服务在线", True)
    except Exception:
        step("ollama 服务在线", False, "brew services start ollama 或 ollama serve")
    print("\n全部通过后运行: python3 assistant.py")
    return 0 if ok else 1

def cmd_list_devices():
    r = sp.run(["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
               capture_output=True, text=True)
    for line in (r.stderr or "").splitlines():
        if "AVFoundation" in line or "audio" in line.lower() or "video" in line.lower():
            print(line)
    print("\n在 config.json 的 ffmpeg_audio_device 中填 \":<音频设备号>\"")

def cmd_process(cfg: Config, wav_path: str):
    """对已有录音手动执行 转写+摘要(终端打印结果, 同时落盘)"""
    wav = Path(wav_path).expanduser()
    if not wav.exists():
        print(f"❌ 文件不存在: {wav}"); return 1
    print(f"处理: {wav.name} (转写模型 {cfg.whisper_model}, 首次运行会下载模型)…")
    transcript, summary = post_process(wav, cfg)
    print("\n===== 转写 =====")
    print(transcript or "(无转写结果)")
    print("\n===== 摘要 =====")
    print(summary or "(摘要不可用, 检查 ollama)")
    return 0

def cmd_record_test(cfg: Config, seconds: int):
    """录音通道标定: 对着手机听筒位置发声, 检查 Mac 录到的电平是否足够"""
    out = BASE / "logs" / "mic_test.wav"
    print(f"录音 {seconds} 秒 — 请在此期间对着【手机听筒缝】弹指或正常说话…")
    for i in range(3, 0, -1):
        print(f"  {i}…"); time.sleep(1)
    rec = start_recording(cfg, out)
    time.sleep(seconds)
    rec.terminate(); rec.wait(timeout=10)

    r = sp.run(["ffmpeg", "-hide_banner", "-i", str(out), "-af", "volumedetect",
                "-f", "null", "-"], capture_output=True, text=True)
    max_db = None
    for line in (r.stderr or "").splitlines():
        if "mean_volume" in line or "max_volume" in line:
            print("  " + line.strip())
        m = re.search(r"max_volume: (-?[\d.]+) dB", line)
        if m:
            max_db = float(m.group(1))

    if max_db is None:
        print("❌ 未能分析录音(检查 ffmpeg_audio_device 配置)"); return 1
    if max_db >= -20:
        print(f"✅ 拾音良好 (max {max_db}dB ≥ -20dB), 这个位置可以固定下来")
    elif max_db >= -35:
        print(f"⚠️ 拾音偏弱 (max {max_db}dB), 把听筒凑近麦克风/麦头, 或提高手机通话音量")
    else:
        print(f"❌ 几乎没拾到声音 (max {max_db}dB), 位置不对——先确认听筒方向, 再重跑标定")
    return 0

# ---------------------------------------------------------------- 入口 -------

def setup_logging():
    LOG.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%m-%d %H:%M:%S")
    for h in (logging.StreamHandler(), logging.FileHandler(BASE / "logs" / "assistant.log", encoding="utf-8")):
        h.setFormatter(fmt)
        LOG.addHandler(h)

def main():
    for d in ("recordings", "transcripts", "summaries", "logs"):
        (BASE / d).mkdir(exist_ok=True)
    setup_logging()

    ap = argparse.ArgumentParser(description="AI来电助手 (Mac+ADB)")
    ap.add_argument("--config", default=str(BASE / "config.json"))
    ap.add_argument("--check", action="store_true", help="环境自检")
    ap.add_argument("--list-devices", action="store_true", help="列出录音设备")
    ap.add_argument("--record-test", metavar="SEC", nargs="?", const=5, type=int,
                    help="录音通道标定 N 秒(默认 5), 对听筒发声看电平")
    ap.add_argument("--process", metavar="WAV", help="对已有录音执行 转写+摘要")
    ap.add_argument("--once", action="store_true", help="打印一次通话状态(调试)")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    adb = Adb(cfg.adb_path)

    if args.list_devices:
        return cmd_list_devices()
    if args.record_test:
        return cmd_record_test(cfg, args.record_test)
    if args.process:
        return cmd_process(cfg, args.process)
    if args.once:
        print(f"mCallState={adb.call_state()} (0=idle 1=ringing 2=offhook), "
              f"号码={adb.incoming_number() or '(空)'}")
        return 0
    if args.check:
        return cmd_check(cfg, adb)

    if not adb.device_ok():
        notify("手机未连接, 服务待机中; 接入后自动恢复")
    LOG.info("服务启动: delay=%ss mode=%s greeting=%s",
             cfg.auto_delay_sec, cfg.answer_mode, cfg.greeting_file)
    notify("AI来电助手已启动")
    fail_streak = 0
    while True:
        try:
            if adb.call_state() == 1:
                handle_ringing(cfg, adb)
            fail_streak = 0
        except KeyboardInterrupt:
            notify("AI来电助手已退出")
            return 0
        except Exception as e:
            fail_streak += 1
            LOG.exception("循环异常: %s", e)
            if fail_streak == 3:                                 # 只在确认断开时提醒一次, 避免刷屏
                notify("手机连接异常, 请检查 USB 线/授权; 恢复后自动继续")
            time.sleep(5)                                        # ADB 抖动等瞬态错误, 退避后继续
        time.sleep(1)

if __name__ == "__main__":
    sys.exit(main())
