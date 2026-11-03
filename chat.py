#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AMD chat CLI（OpenAI 兼容接口）。

只发一次请求，打印真实耗时（不重试、不并发、不对冲）：
  - TTFT：首 token 到达耗时
  - 总耗时：完整响应耗时
  - token 用量

实测（2026-11-03，同一 prompt，AMD 开发者门户）：
    DeepSeek-V4-Flash   中位 2.4s   max 114s  ← 动态 router 会命中冷副本，长尾很重
    MiniCPM5-2B         中位 2.3s   max 5.5s
    Qwen3.8-Flash-Next  中位 4.0s   max 8.1s

配置来自 .env：BASE_URL / DS_MODEL / API_KEY（rpm 20/min，日额度 $1）

用法：
    python chat.py                                  # 默认 prompt
    python chat.py "你的问题"
    python chat.py --model MiniCPM5-2B "..."         # 换模型
    python chat.py --effort low "..."                 # 思考强度 none|minimal|low|medium|high
    python chat.py --no-stream "..."                 # 非流式（等完整响应）
    python chat.py --max-tokens 256 "..."
    python chat.py --timeout 120 "..."
    python chat.py --proxy socks5://127.0.0.1:7897 "..." # 需要走代理时
"""

from __future__ import annotations

import os
import re
import sys
import time

from dotenv import load_dotenv
from openai import OpenAI
from openai import RateLimitError, APIConnectionError, BadRequestError

from amd_api import describe, split_models

try:  # openai 3.42 用的是 httpx2（httpx v2 发行包）
    import httpx2 as httpx
except ImportError:  # 兼容纯 httpx 环境
    import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))

QUOTA_NOTE = "AMD 开发者门户 key：rpm 20/min，日额度 $1"
DEFAULT_PROMPT = (
    "Translate English to Chinese: The quick brown fox jumps over the lazy dog."
)
VALID_EFFORTS = ("none", "minimal", "low", "medium", "high")
DEBUG = os.getenv("CHAT_DEBUG", "0") == "1"


def parse_args(argv: list[str]):
    model = ""
    list_models = False
    effort = os.getenv("CHAT_EFFORT", "none")
    stream = os.getenv("CHAT_STREAM", "1") != "0"
    max_tokens = int(os.getenv("CHAT_MAX_TOKENS", "0") or 0)
    proxy = os.getenv("CHAT_PROXY", "")
    timeout = float(os.getenv("CHAT_TIMEOUT", "120"))
    rest: list[str] = []

    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--no-stream":
            stream = False
        elif a == "--stream":
            stream = True
        elif a == "--effort":
            i += 1; effort = argv[i]
        elif a.startswith("--effort="):
            effort = a.split("=", 1)[1]
        elif a == "--model":
            i += 1; model = argv[i]
        elif a.startswith("--model="):
            model = a.split("=", 1)[1]
        elif a == "--max-tokens":
            i += 1; max_tokens = int(argv[i])
        elif a.startswith("--max-tokens="):
            max_tokens = int(a.split("=", 1)[1])
        elif a == "--proxy":
            i += 1; proxy = argv[i]
        elif a.startswith("--proxy="):
            proxy = a.split("=", 1)[1]
        elif a == "--list-models":
            list_models = True
        elif a == "--timeout":
            i += 1; timeout = float(argv[i])
        elif a.startswith("--timeout="):
            timeout = float(a.split("=", 1)[1])
        elif a in ("-h", "--help"):
            print(__doc__); raise SystemExit(0)
        else:
            rest.append(a)
        i += 1

    if effort not in VALID_EFFORTS:
        raise SystemExit(f"--effort 只能是 {VALID_EFFORTS} 之一，收到 {effort!r}")
    return (rest[0] if rest else DEFAULT_PROMPT, model, effort, stream,
            max_tokens, proxy, timeout, list_models)


(PROMPT, MODEL, EFFORT, STREAM, MAX_TOKENS, PROXY, TIMEOUT, LIST_MODELS) = parse_args(sys.argv[1:])

BASE_URL = (os.getenv("BASE_URL") or "").strip()
API_KEY = (os.getenv("API_KEY") or "").strip()
MODEL = MODEL or os.getenv("DS_MODEL") or "DeepSeek-V4-Flash"

if not (BASE_URL and API_KEY):
    raise SystemExit("缺少 BASE_URL / API_KEY，请在 .env 中配置 AMD 开发者门户的凭证。")


def build_http_client(proxy: str = ""):
    """自建 httpx client：trust_env=False -> 彻底不读环境里的 all_proxy/ALL_PROXY 等变量。

    背景：httpx2 只认 http/https/socks5/socks5h 四种 scheme，环境里常见的
    `ALL_PROXY=socks://127.0.0.1:7897/` 会在建 client 时直接抛
    "ValueError: Unknown scheme for proxy URL"。
    需要走代理时用 --proxy / CHAT_PROXY 显式指定（socks:// 自动转 socks5://）。
    """
    proxy = (proxy or "").strip()
    kwargs = dict(
        trust_env=False,
        timeout=httpx.Timeout(TIMEOUT, connect=15.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20,
                            keepalive_expiry=60.0),
    )
    if proxy:
        if proxy.startswith("socks://"):
            proxy = "socks5://" + proxy[len("socks://"):]
        try:
            import socksio  # noqa: F401
        except ImportError:
            raise SystemExit(
                "使用 SOCKS 代理需要 socksio：uv add \"httpx2[socks]\"\n"
                f"（当前 proxy={proxy}）")
        kwargs["proxy"] = proxy
    return httpx.Client(**kwargs)


def make_client() -> OpenAI:
    # max_retries=0：只发一次请求，不做 SDK 层面的自动重试，
    # 保证打印出来的是真实的单次调用耗时
    return OpenAI(base_url=BASE_URL, api_key=API_KEY,
                  http_client=build_http_client(PROXY), max_retries=0, timeout=TIMEOUT)


# --------------------------------------------------------------- think 剥离
class _ThinkStripper:
    """剥离内联 <think>...</think>（部分模型会把思考写进 content）。"""

    def __init__(self):
        self.buf = ""
        self.in_think = False

    def feed(self, text: str) -> str:
        self.buf += text
        out = []
        while self.buf:
            if not self.in_think:
                m = re.search(r"<think>", self.buf)
                if not m:
                    # 可能是半个标签，先留住末尾几个字符
                    keep = len("<think>") - 1
                    if len(self.buf) > keep and "<think>" not in self.buf[-keep:]:
                        out.append(self.buf[:-keep]); self.buf = self.buf[-keep:]
                    else:
                        out.append(self.buf); self.buf = ""
                    break
                out.append(self.buf[:m.start()])
                self.buf = self.buf[m.end():]
                self.in_think = True
            else:
                m = re.search(r"</think>", self.buf)
                if not m:
                    self.buf = ""
                    break
                self.buf = self.buf[m.end():]
                self.in_think = False
        return "".join(out)

    def flush(self) -> str:
        rest, self.buf = self.buf, ""
        return rest


def build_kwargs(with_effort: bool) -> dict:
    kw = {"model": MODEL, "messages": [{"role": "user", "content": PROMPT}]}
    if with_effort:
        kw["reasoning_effort"] = EFFORT
    if MAX_TOKENS:
        kw["max_tokens"] = MAX_TOKENS
    return kw


def _is_effort_error(e: Exception) -> bool:
    """部分模型不接受 reasoning_effort，报错文案有 'reasoning_effort' / 'reasoning effort' 两种写法"""
    msg = str(e).lower()
    return "reasoning_effort" in msg or "reasoning effort" in msg


def run_stream(kwargs: dict) -> tuple[str, float | None, float, object]:
    """流式：单次请求，逐 token 打印，记录 TTFT 与总耗时。"""
    t0 = time.perf_counter()
    ttft = None
    usage = None
    stripper = _ThinkStripper()
    client = make_client()
    try:
        stream = client.chat.completions.create(stream=True, **kwargs)
        for ev in stream:
            if not ev.choices:
                u = getattr(ev, "usage", None)
                if u:
                    usage = u
                continue
            piece = getattr(ev.choices[0].delta, "content", None)
            if piece:
                if ttft is None:
                    ttft = time.perf_counter() - t0
                clean = stripper.feed(piece)
                if clean:
                    sys.stdout.write(clean)
                    sys.stdout.flush()
        tail = stripper.flush()
        if tail:
            sys.stdout.write(tail)
    finally:
        client.close()
    return "", ttft, time.perf_counter() - t0, usage


def run_plain(kwargs: dict) -> tuple[str, float, object]:
    """非流式：单次请求，等完整响应。"""
    t0 = time.perf_counter()
    client = make_client()
    try:
        resp = client.chat.completions.create(**kwargs)
    finally:
        client.close()
    return resp.choices[0].message.content, time.perf_counter() - t0, resp.usage


def print_models() -> None:
    """列出可用的 chat 模型（自动过滤掉 TTS / OCR / decision 等非 chat 模型）。"""
    client = make_client()
    try:
        data = client.models.list()
    finally:
        client.close()
    payload = {"data": [{"id": m.id,
                         "architecture": getattr(m, "architecture", None),
                         "supported_parameters": getattr(m, "supported_parameters", None),
                         "providers": getattr(m, "providers", None),
                         "pricing": getattr(m, "pricing", None),
                         "context_length": getattr(m, "context_length", None)}
                        for m in data.data]}
    chat, other = split_models(payload)
    print(f"{'模型':26s} {'视觉':4s} {'工具':4s} {'上下文':>10s}  价格(prompt/completion)")
    for m in chat:
        d = describe(m)
        print(f"{d['id']:26s} {'✓' if d['vision'] else '-':4s} {'✓' if d['tools'] else '-':4s} "
              f"{str(d['context_length']):>10s}  {d['pricing_prompt']}/{d['pricing_completion']}")
    if other:
        print("\n（非 chat 模型，已过滤："
              + ", ".join(m.get("id") for m in other) + "）")


def main() -> None:
    print("=" * 66)
    print(f" PROVIDER   : amd   ({BASE_URL})")
    print(f"              └ {QUOTA_NOTE}")
    print(f" MODEL      : {MODEL}")
    print(f" EFFORT     : {EFFORT}   STREAM: {STREAM}   MAX_TOKENS: {MAX_TOKENS or '不限'}")
    print(f" PROMPT     : {PROMPT}")
    print("=" * 66)

    kwargs = build_kwargs(with_effort=True)
    if STREAM:
        try:
            _, ttft, total, usage = run_stream(kwargs)
        except BadRequestError as e:
            # 部分模型不接受 reasoning_effort，去掉后重发一次（只重发这一次）
            if not _is_effort_error(e):
                raise
            if DEBUG:
                print(f"[debug] reasoning_effort 不被支持，去掉后重试: {str(e)[:120]}",
                      file=sys.stderr)
            _, ttft, total, usage = run_stream(build_kwargs(with_effort=False))
        print()
        print(f"\n[耗时] TTFT(首 token) {ttft:.3f}s | 总耗时 {total:.3f}s")
        if ttft and ttft > 5:
            print(f"[说明] {ttft:.1f}s 属于 AMD 动态 router 命中冷副本的长尾，不是本地开销")
    else:
        try:
            text, total, usage = run_plain(kwargs)
        except BadRequestError as e:
            if not _is_effort_error(e):
                raise
            text, total, usage = run_plain(build_kwargs(with_effort=False))
        print(_ThinkStripper().feed(text) or text)
        print(f"\n[耗时] 总耗时 {total:.3f}s")

    if usage:
        print("[用量] prompt=%s completion=%s total=%s"
              % (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens))


try:
    if LIST_MODELS:
        print_models()
    else:
        main()
except RateLimitError as e:
    raise SystemExit(f"❌ 429 限流/无模型权限：{str(e)[:200]}")
except APIConnectionError as e:
    raise SystemExit(f"❌ 连接失败：{str(e)[:200]}")
