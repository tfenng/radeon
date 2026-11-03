#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断 TTFT 分布：同连接连续请求 / 响应头 / 空闲后重连"""
import os, time, json
import httpx2 as httpx
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, "..", ".env"))
BASE = os.getenv("BASE_URL"); KEY = os.getenv("API_KEY"); M = os.getenv("DS_MODEL")
PROMPT = "一句话解释什么是LLM"

def once(client, tag, model=M):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "reasoning_effort": "none",
        "stream": True,
    }
    t0 = time.perf_counter()
    first = None
    txt = []
    with client.stream("POST", f"{BASE}/chat/completions", json=body,
                       headers={"Authorization": f"Bearer {KEY}"}) as r:
        hdrs = dict(r.headers)
        status = r.status_code
        for line in r.iter_lines():
            if line.startswith("data:") and line.strip() != "data: [DONE]":
                if first is None:
                    first = time.perf_counter() - t0
                try:
                    obj = json.loads(line[5:])
                    for ch in obj.get("choices", []):
                        c = (ch.get("delta") or {}).get("content")
                        if c: txt.append(c)
                except Exception:
                    pass
    total = time.perf_counter() - t0
    print(f"  {tag:26s} status={status} ttft={first if first is None else round(first,3)}s total={total:.3f}s out={''.join(txt)[:24]!r}")
    return hdrs

print("== A. 同一连接连发 8 次 ==")
c = httpx.Client(trust_env=False, timeout=httpx.Timeout(120.0, connect=15.0))
h = once(c, "req#1")
print("  响应头:", json.dumps(h, ensure_ascii=False)[:600])
for i in range(2, 9):
    once(c, f"req#{i}")

print("\n== B. 新建连接（模拟每次开新进程）==")
for i in range(2, 4):
    c2 = httpx.Client(trust_env=False, timeout=httpx.Timeout(120.0, connect=15.0))
    once(c2, f"newconn#{i}")
    c2.close()

print("\n== C. 空闲 60s 后，用同一连接再请求（看是否被卸载）==")
time.sleep(60)
once(c, "after 60s idle (same conn)")
once(c, "immediate after")

print("\n== D. 空闲 60s 后，新连接请求 ==")
time.sleep(60)
c3 = httpx.Client(trust_env=False, timeout=httpx.Timeout(120.0, connect=15.0))
once(c3, "after 60s idle (new conn)")
