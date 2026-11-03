#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AMD Chat —— 后端 API（只提供接口，前端在 web/ 目录，独立静态文件）

启动：
    python web.py                      # http://127.0.0.1:8000
    python web.py --port 8080
    python web.py --model DeepSeek-V4-Flash --effort none
    python web.py --sessions-dir ./sessions

接口：
    GET  /                 静态前端（web/index.html）
    GET  /api/health       健康检查
    GET  /api/models       可用的 chat 模型（已过滤掉 TTS/OCR/decision）+ 各模型实测耗时
    GET  /api/stats        进程内历史耗时统计
    GET  /api/sessions     会话列表（摘要）
    POST /api/sessions     新建会话 -> {"session": {...}}
    GET  /api/sessions/<id>    会话详情（含完整消息）
    DELETE /api/sessions/<id>  删除会话
    PATCH /api/sessions/<id>   改标题 body: {"title": "..."}
    POST /api/chat         SSE 流式对话
        body: {"messages": [{"role":"user","content":"..."}], "model": "...", "effort": "none",
               "session_id": "20261103-142530-a1b2"}
        也兼容 {"prompt": "..."} 单轮写法
        事件: {"type":"delta","text":...} / {"type":"done","meta":{...}} / {"type":"error","message":...}

特点：
    - 全进程共用一个 OpenAI client（连接池常驻，省掉每次请求的 TCP+TLS 握手）
    - 请求串行化（AMD 端有 rpm/并发上限，并发只会互相挤占）
    - max_retries=0、不做对冲：跑出来的是真实单次调用耗时
    - 零额外依赖（stdlib http.server）
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import statistics
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv
from openai import OpenAI
from openai import RateLimitError, APIConnectionError, BadRequestError

from amd_api import describe, split_models
from sessions import SessionStore

try:  # openai 3.42 用的是 httpx2（httpx v2 发行包）
    import httpx2 as httpx
except ImportError:  # 兼容纯 httpx 环境
    import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(HERE, "web")
load_dotenv(os.path.join(HERE, ".env"))

BASE_URL = (os.getenv("BASE_URL") or "").strip()
API_KEY = (os.getenv("API_KEY") or "").strip()
DEFAULT_MODEL = os.getenv("DS_MODEL", "DeepSeek-V4-Flash")
VALID_EFFORTS = ("none", "minimal", "low", "medium", "high")

if not (BASE_URL and API_KEY):
    raise SystemExit("缺少 BASE_URL / API_KEY，请在 .env 中配置 AMD 开发者门户的凭证。")


def httpx_client(timeout: float):
    """自建 httpx client：trust_env=False -> 彻底不读环境里的 all_proxy/ALL_PROXY 等变量。

    背景：httpx2 只认 http/https/socks5/socks5h 四种 scheme，环境里常见的
    `ALL_PROXY=socks://127.0.0.1:7897/` 会在建 client 时直接抛
    "ValueError: Unknown scheme for proxy URL"。
    """
    proxy = (os.getenv("CHAT_PROXY") or "").strip()
    kwargs = dict(
        trust_env=False,
        timeout=httpx.Timeout(timeout, connect=15.0),
        limits=httpx.Limits(max_connections=32, max_keepalive_connections=32,
                            keepalive_expiry=300.0),
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


# ----------------------------------------------------------------- 常驻 client
class ChatService:
    """全进程共用一个 client：HTTP 连接池常驻，避免每次请求重新握手。"""

    def __init__(self, timeout: float = 120.0, default_model: str = DEFAULT_MODEL,
                 default_effort: str = "none"):
        self.timeout = timeout
        self.default_model = default_model
        self.default_effort = default_effort
        self.lock = threading.Lock()   # 串行化：AMD 端有 rpm/并发上限
        self.history: list[dict] = []
        self.started = time.time()
        self._models_cache: tuple[float, dict] | None = None
        self.client = OpenAI(
            base_url=BASE_URL,
            api_key=API_KEY,
            http_client=httpx_client(timeout),
            max_retries=0,               # 只发一次，测真实耗时
            timeout=timeout,
        )

    # ------------------------------------------------------------ 模型列表
    def models(self) -> dict:
        """可用的 chat 模型（缓存 60s）；非 chat 模型（OmniVoice/OCR/decision）单列。"""
        now = time.time()
        if self._models_cache and now - self._models_cache[0] < 60:
            return self._models_cache[1]
        try:
            with self.lock:
                data = self.client.models.list()
            items = []
            for m in data.data:
                items.append({
                    "id": m.id,
                    "architecture": getattr(m, "architecture", None),
                    "supported_parameters": getattr(m, "supported_parameters", None),
                    "providers": getattr(m, "providers", None),
                    "pricing": getattr(m, "pricing", None),
                    "context_length": getattr(m, "context_length", None),
                })
            chat, other = split_models({"data": items})
            out = {"chat": [describe(m) for m in chat],
                   "other": [m.get("id") for m in other]}
        except Exception:
            out = {"chat": [{"id": self.default_model}], "other": []}
        self._models_cache = (now, out)
        return out

    # ------------------------------------------------------------ 对话流
    def stream(self, messages: list[dict], model: str, effort: str):
        """返回事件生成器：('delta', text) / ('done', meta) / ('error', msg)"""
        t0 = time.perf_counter()
        ttft = None
        usage = None

        def _run(payload: dict):
            """真正发一次请求，返回 (ttft, usage)；思考 token 不进正文。"""
            nonlocal ttft, usage
            with self.lock:
                stream = self.client.chat.completions.create(**payload)
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
                        yield piece

        payload = {"model": model, "messages": messages, "stream": True}
        if effort:
            payload["reasoning_effort"] = effort
        try:
            for piece in _run(payload):
                yield ("delta", piece)
        except BadRequestError as e:
            msg = str(e).lower()
            if "reasoning_effort" not in msg and "reasoning effort" not in msg:
                yield ("error", f"400 {str(e)[:200]}")
                return
            # 该模型不支持 reasoning_effort -> 去掉重发一次
            payload.pop("reasoning_effort", None)
            ttft = None
            try:
                for piece in _run(payload):
                    yield ("delta", piece)
            except Exception as e2:
                yield ("error", f"{type(e2).__name__}: {str(e2)[:200]}")
                return
        except RateLimitError as e:
            yield ("error", f"429 限流：{str(e)[:200]}")
            return
        except Exception as e:
            yield ("error", f"{type(e).__name__}: {str(e)[:200]}")
            return

        total = time.perf_counter() - t0
        meta = {
            "model": model,
            "ttft": ttft,
            "total": total,
            "usage": {"prompt": usage.prompt_tokens,
                      "completion": usage.completion_tokens,
                      "total": usage.total_tokens} if usage else None,
        }
        with self.lock:
            self.history.append(meta)
            if len(self.history) > 200:
                del self.history[:-200]
        yield ("done", meta)

    # ------------------------------------------------------------ 统计
    def stats(self) -> dict:
        h = [x["ttft"] for x in self.history if x["ttft"]]
        t = [x["total"] for x in self.history]

        def pct(v, p):
            if not v:
                return None
            s = sorted(v)
            return s[max(0, int(len(s) * p) - 1)]

        by_model: dict[str, dict] = {}
        for x in self.history:
            d = by_model.setdefault(x.get("model") or "-",
                                    {"requests": 0, "ttfts": [], "totals": []})
            d["requests"] += 1
            if x.get("ttft"):
                d["ttfts"].append(x["ttft"])
            d["totals"].append(x.get("total") or 0)
        for d in by_model.values():
            tt = sorted(d.pop("ttfts"))
            d["ttft_median"] = statistics.median(tt) if tt else None
            d["ttft_max"] = max(tt) if tt else None
            d["slow_gt5s"] = sum(1 for v in tt if v > 5)
            d["total_median"] = statistics.median(d["totals"]) if d["totals"] else None

        return {
            "uptime_s": round(time.time() - self.started, 1),
            "base_url": BASE_URL,
            "default_model": self.default_model,
            "requests": len(self.history),
            "by_model": by_model,
            "ttft_median": statistics.median(h) if h else None,
            "ttft_p90": pct(h, 0.9),
            "ttft_max": max(h) if h else None,
            "total_median": statistics.median(t) if t else None,
            "total_max": max(t) if t else None,
            "slow_gt5s": sum(1 for x in t if x > 5),
        }


SERVICE: ChatService | None = None
STORE: SessionStore | None = None


# ----------------------------------------------------------------- HTTP 层
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AMDChat/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[web] %s\n" % (fmt % args))

    # ---------------------------------------------------------- 工具
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _read_json(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._json(400, {"error": "bad json"})
            return None

    def _static(self, path: str):
        """提供 web/ 目录下的静态文件。"""
        rel = "index.html" if path in ("/", "") else unquote(path.lstrip("/"))
        target = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not target.startswith(WEB_DIR) or not os.path.isfile(target):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        ctype, _ = mimetypes.guess_type(target)
        if ctype is None:
            ctype = "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(target, "rb") as f:
            data = f.read()
        self._send(200, data, ctype, {"Cache-Control": "no-cache"})

    # ---------------------------------------------------------- 路由
    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/sessions"):
            assert STORE is not None
            rest = path[len("/api/sessions"):].strip("/")
            if not rest:
                self._json(200, {"sessions": STORE.list()})
                return
            sess = STORE.get(rest)
            if not sess:
                self._json(404, {"error": "session not found"})
                return
            self._json(200, {"session": sess})
            return
        if path.startswith("/api/"):
            if path == "/api/health":
                self._json(200, {"ok": True, "uptime_s": round(time.time() - SERVICE.started, 1)})
                return
            if path == "/api/models":
                ms = SERVICE.models()
                stats = SERVICE.stats().get("by_model", {})
                out = []
                for m in ms.get("chat", []):
                    s = stats.get(m["id"], {})
                    out.append({**m, "stats": {
                        "requests": s.get("requests", 0),
                        "ttft_median": s.get("ttft_median"),
                        "ttft_max": s.get("ttft_max"),
                        "slow_gt5s": s.get("slow_gt5s", 0),
                    }})
                self._json(200, {"models": out, "other": ms.get("other", []),
                                 "default": SERVICE.default_model})
                return
            if path == "/api/stats":
                self._json(200, SERVICE.stats())
                return
            self._json(404, {"error": "not found"})
            return
        self._static(path)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/sessions":
            assert STORE is not None
            body = self._read_json()
            sess = STORE.create((body or {}).get("model") or SERVICE.default_model)
            self._json(200, {"session": sess})
            return
        if path != "/api/chat":
            self._json(404, {"error": "not found"})
            return
        req = self._read_json()
        if req is None:
            return

        # 支持两种入参：多轮 messages / 单轮 prompt
        messages = req.get("messages")
        if not messages:
            prompt = (req.get("prompt") or "").strip()
            messages = [{"role": "user", "content": prompt}] if prompt else []
        messages = [m for m in messages if (m.get("content") or "").strip()]
        if not messages:
            self._json(400, {"error": "empty messages"})
            return
        # 只保留 role/content，顺序固定为 user/assistant 交替
        clean = [{"role": m["role"], "content": m["content"]}
                 for m in messages if m.get("role") in ("user", "assistant")]

        model = req.get("model") or SERVICE.default_model
        effort = req.get("effort") or SERVICE.default_effort
        if effort not in VALID_EFFORTS:
            effort = "none"
        session_id = (req.get("session_id") or "").strip()

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        # HTTP/1.1 下无 Content-Length 的响应以“关连接”作为结束标记
        self.close_connection = True

        def write(obj):
            self.wfile.write(f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()

        acc: list[str] = []
        try:
            for kind, payload in SERVICE.stream(clean, model, effort):
                if kind == "delta":
                    acc.append(payload)
                    write({"type": "delta", "text": payload})
                elif kind == "done":
                    # 落盘保存这一轮对话
                    meta = dict(payload)
                    try:
                        sess = STORE.append_turn(session_id, clean, "".join(acc), model)
                        if sess:
                            session_id = sess["id"]
                            meta["session_id"] = sess["id"]
                            meta["title"] = sess["title"]
                    except Exception as e:
                        meta["session_error"] = str(e)[:120]
                    write({"type": "done", "meta": meta})
                else:
                    write({"type": "error", "message": str(payload)})
        except (BrokenPipeError, ConnectionResetError):
            pass  # 浏览器提前断开

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/api/sessions/"):
            assert STORE is not None
            sid = path[len("/api/sessions/"):].strip("/")
            ok = STORE.delete(sid)
            self._json(200 if ok else 404, {"ok": ok})
            return
        self._json(404, {"error": "not found"})

    def do_PATCH(self):
        path = urlparse(self.path).path
        if path.startswith("/api/sessions/"):
            assert STORE is not None
            sid = path[len("/api/sessions/"):].strip("/")
            body = self._read_json()
            if body is None:
                return
            sess = STORE.rename(sid, body.get("title") or "")
            if not sess:
                self._json(404, {"error": "session not found"})
                return
            self._json(200, {"session": sess})
            return
        self._json(404, {"error": "not found"})


def main():
    global SERVICE
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--model", default=DEFAULT_MODEL, help="默认模型（前端下拉里可临时切）")
    ap.add_argument("--effort", default="none", choices=list(VALID_EFFORTS),
                    help="默认思考强度")
    ap.add_argument("--sessions-dir", default=os.path.join(HERE, "sessions"),
                    help="会话持久化目录（每个会话一个 JSON 文件）")
    args = ap.parse_args()

    if not os.path.isdir(WEB_DIR):
        raise SystemExit(f"找不到前端目录 {WEB_DIR}")

    global STORE
    STORE = SessionStore(args.sessions_dir)

    SERVICE = ChatService(timeout=args.timeout, default_model=args.model,
                          default_effort=args.effort)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    print(f"Web Chat 已启动: http://{args.host}:{args.port}")
    print(f"  BASE_URL : {BASE_URL}")
    print(f"  MODEL    : {args.model}   EFFORT: {args.effort}")
    print(f"  前端目录 : {WEB_DIR}")
    print(f"  会话目录 : {os.path.abspath(args.sessions_dir)}")
    print("  常驻一个 client（连接池复用）；Ctrl+C 退出")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
