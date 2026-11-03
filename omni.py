#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OmniVoice Hello World —— 服务端连通性验证（纯标准库，零依赖）

用法:
    export OMNIVOICE_BASE_URL="http://127.0.0.1:8880"   # 你的服务端地址，不要带 /v1
    export OMNIVOICE_API_KEY="sk-xxxx"                  # 没有鉴权可留空
    python3 omnivoice_hello.py
    python3 omnivoice_hello.py "直接传入要合成的文本"

可选环境变量:
    OMNIVOICE_MODEL      默认 omnivoice
    OMNIVOICE_VOICE      默认 alloy（服务端除 instructions 外还认识的 OpenAI 风格音色）
    OMNIVOICE_TEXT       要合成的文本
    OMNIVOICE_OUT        输出文件，默认 hello.wav
    OMNIVOICE_TIMEOUT    单次请求超时秒数，默认 180（首次加载模型可能很慢）

退出码: 0 全部通过 / 1 有步骤失败
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import wave

from dotenv import load_dotenv

# Load settings from .env (existing environment variables take precedence).
load_dotenv()


BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8880").rstrip("/")
# 很多人会把 OpenAI 风格的 base_url 直接粘过来（带 /v1），这里自动纠正
if BASE_URL.endswith("/v1"):
    BASE_URL = BASE_URL[: -len("/v1")]
API_KEY = os.getenv("API_KEY", "").strip()
MODEL="OmniVoice"
VOICE = "female"
TEXT = os.getenv(
    "OMNIVOICE_TEXT",
    "你好，世界。这是一次 OmniVoice 服务端的连通性测试，如果你能听到这句话，说明一切正常。",
)
if len(sys.argv) > 1:  # 允许: python3 omnivoice_hello.py "要合成的文本"
    TEXT = sys.argv[1]
OUT_PATH = os.getenv("OMNIVOICE_OUT", "hello.wav")
TIMEOUT = float(os.getenv("OMNIVOICE_TIMEOUT", "180"))


# ---------------------------------------------------------------- 基础工具
def _headers(extra: dict | None = None) -> dict:
    h = {"User-Agent": "omnivoice-hello/1.0", "Accept": "*/*"}
    if API_KEY:
        # omnivoice-server 的鉴权就是标准 Bearer；多数网关/中转站也用这个
        h["Authorization"] = f"Bearer {API_KEY}"
    if extra:
        h.update(extra)
    return h


def _request(method: str, path: str, body: dict | None = None) -> tuple[int, dict, bytes]:
    """返回 (status, headers, raw_bytes)。HTTP 4xx/5xx 不抛异常，交给调用方判断。"""
    url = f"{BASE_URL}{path}"
    data = None
    hdrs = _headers()
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()
    except urllib.error.URLError as e:
        raise ConnectionError(f"连不上 {url} -> {e.reason}") from e


def _preview(raw: bytes, limit: int = 400) -> str:
    if not raw:
        return "<empty body>"
    if raw[:4] == b"RIFF" or raw[:4] == b"OggS" or raw[:3] == b"ID3" or raw[:2] == b"\xff\xfb":
        return f"<binary audio {len(raw)} bytes>"
    try:
        return raw.decode("utf-8", "replace")[:limit]
    except Exception:
        return f"<{len(raw)} bytes>"


def _wav_info(path: str) -> str:
    try:
        with wave.open(path, "rb") as w:
            frames, rate = w.getnframes(), w.getframerate()
            return f"{frames / rate:.2f}s @ {rate}Hz / {w.getnchannels()}ch / {w.getsampwidth() * 8}bit"
    except Exception:
        return "非 WAV 或无法解析（mp3 等格式正常）"


def _ok(step: str, detail: str = "") -> None:
    print(f"  ✅ {step}" + (f" —— {detail}" if detail else ""))


def _fail(step: str, detail: str = "") -> None:
    print(f"  ❌ {step}" + (f" —— {detail}" if detail else ""))





def check_models() -> bool:
    print("[2/3] 模型列表 GET /v1/models")
    try:
        status, _, raw = _request("GET", "/v1/models")
    except ConnectionError as e:
        _fail("请求失败", str(e))
        return False

    if status != 200:
        _fail(f"HTTP {status}", _preview(raw))
        return False

    try:
        ids = [m.get("id") for m in json.loads(raw).get("data", [])]
    except Exception:
        _ok("可访问（返回非标准 JSON）", _preview(raw, 200).replace("\n", " "))
        return True

    _ok("鉴权通过", f"可用模型: {', '.join(str(i) for i in ids[:8]) or '<空>'}")
    if ids and MODEL not in ids:
        print(f"     ⚠️  列表里没有 '{MODEL}'，但服务端可能仍能合成，继续尝试。")
    return True


def check_speech() -> bool:
    print(f"[3/3] 语音合成 POST /v1/audio/speech  ->  {OUT_PATH}")
    payload = {
        "model": MODEL,
        # 关键：voice / instructions 二选一，instructions 优先级最高
        "voice": VOICE,
        "input": TEXT,
        "response_format": "wav",  # wav 便于本地校验时长；也可换 mp3 / pcm
        "speed": 1.0,
    }
    print(f"     请求体: {json.dumps(payload, ensure_ascii=False)}")

    t0 = time.perf_counter()
    try:
        status, headers, raw = _request("POST", "/v1/audio/speech", payload)
    except ConnectionError as e:
        _fail("请求失败", str(e))
        return False
    elapsed = time.perf_counter() - t0

    if status != 200:
        _fail(f"HTTP {status}（耗时 {elapsed:.2f}s）", _preview(raw))
        hint = {
            401: "API_KEY 无效 / 没带 Authorization 头",
            403: "Key 无权访问该模型或该路径",
            404: "BASE_URL 或路径不对（确认地址末尾不要再加 /v1）",
            422: "参数不被接受，试试删掉 voice 只留 model+input",
            429: "限流或余额不足",
        }.get(status)
        if hint:
            print(f"     💡 {hint}")
        return False

    if len(raw) < 1000 or raw[:4] != b"RIFF":
        _fail("返回内容不是音频", _preview(raw))
        return False

    with open(OUT_PATH, "wb") as f:
        f.write(raw)

    _ok("合成成功", f"{len(raw):,} bytes, 耗时 {elapsed:.2f}s, 音频 {_wav_info(OUT_PATH)}")
    for k in ("X-Audio-Duration-S", "X-Synthesis-Latency-S"):
        if k in headers:
            print(f"     {k}: {headers[k]}")
    if headers.get("X-No-Speech-Detected") == "true":
        print("     ⚠️  服务端检测到生成音频里没有人声，换个文本/音色再试")
    print(f"     ▶ 播放: afplay {OUT_PATH}  (macOS) / aplay {OUT_PATH} (Linux) / start {OUT_PATH} (Windows)")
    return True


def main() -> int:
    print("=" * 68)
    print(" OmniVoice Hello World")
    print(f" BASE_URL : {BASE_URL}")
    print(f" API_KEY  : {'已设置 (***' + API_KEY[-4:] + ')' if API_KEY else '未设置（无鉴权模式）'}")
    print(f" MODEL    : {MODEL}    VOICE: {VOICE}")
    print("=" * 68)

    if not API_KEY:
        print("  ⚠️  未检测到 OMNIVOICE_API_KEY；如果服务端要求鉴权，会返回 401。\n")

    results = [check_speech()]
    print("-" * 68)
    if all(results):
        print(" 🎉 全部通过：服务端可用，音频已保存到", os.path.abspath(OUT_PATH))
        return 0
    print(" ⛔ 有步骤失败，见上面的 ❌ 行。")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)