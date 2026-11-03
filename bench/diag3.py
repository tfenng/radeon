#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证：RPM=20 限流是否导致请求被排队变慢"""
import os, time, json
import httpx2 as httpx
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, "..", ".env"))
BASE = os.getenv("BASE_URL"); KEY = os.getenv("API_KEY"); M = os.getenv("DS_MODEL")
HDR = {"Authorization": f"Bearer {KEY}"}

c = httpx.Client(trust_env=False, timeout=httpx.Timeout(180.0, connect=15.0))
print(" i |  ttft(s) | total(s) | remain_rpm | reset_rpm | out")
for i in range(1, 26):
    body = {"model": M, "messages": [{"role": "user", "content": "hi"}],
            "reasoning_effort": "none", "stream": True, "max_tokens": 16}
    t0 = time.perf_counter(); first = None; txt = []; h = {}
    with c.stream("POST", f"{BASE}/chat/completions", json=body, headers=HDR) as r:
        h = dict(r.headers); status = r.status_code
        for line in r.iter_lines():
            if line.startswith("data:") and line.strip() != "data: [DONE]":
                if first is None: first = time.perf_counter() - t0
                try:
                    obj = json.loads(line[5:])
                    for ch in obj.get("choices", []):
                        cc = (ch.get("delta") or {}).get("content")
                        if cc: txt.append(cc)
                except Exception: pass
    total = time.perf_counter() - t0
    print(f"{i:2d} | {first if first is None else round(first,3):8} | {total:8.3f} | "
          f"{h.get('x-ratelimit-remaining-user-rpm','?'):>10} | {h.get('x-ratelimit-reset-user-rpm','?'):>10} | "
          f"status={status} {''.join(txt)[:16]!r}")
