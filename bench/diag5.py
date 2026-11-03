#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""限速采样 40 发，统计 TTFT 分布 + 超 RPM 行为"""
import os, time, json, statistics
import httpx2 as httpx
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, "..", ".env"))
BASE = os.getenv("BASE_URL"); KEY = os.getenv("API_KEY"); M = os.getenv("DS_MODEL")
HDR = {"Authorization": f"Bearer {KEY}"}
c = httpx.Client(trust_env=False, timeout=httpx.Timeout(180.0, connect=15.0))

def fire(prompt="一句话解释什么是LLM"):
    body = {"model": M, "messages": [{"role": "user", "content": prompt}],
            "reasoning_effort": "none", "stream": True, "max_tokens": 64}
    t0 = time.perf_counter(); first = None; txt = []; st = None; err = ""
    try:
        with c.stream("POST", f"{BASE}/chat/completions", json=body, headers=HDR) as r:
            st = r.status_code
            for line in r.iter_lines():
                if line.startswith("data:"):
                    if line.strip() == "data: [DONE]": break
                    if first is None: first = time.perf_counter() - t0
                    try:
                        obj = json.loads(line[5:])
                        for ch in obj.get("choices", []):
                            cc = (ch.get("delta") or {}).get("content")
                            if cc: txt.append(cc)
                    except Exception: pass
                elif st != 200:
                    err += line
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    return st, first, "".join(txt), err

print("阶段1：限速采样（每 3.2s 一发，40 发，约 2 分钟）")
ttfts, slow, bad = [], [], []
for i in range(1, 41):
    st, ttft, txt, err = fire()
    if ttft: ttfts.append(ttft)
    if ttft and ttft > 5: slow.append((i, round(ttft, 2)))
    if st != 200: bad.append((i, st, err[:120]))
    flag = " <== 慢" if (ttft and ttft > 5) else ""
    print(f"  #{i:2d} status={st} ttft={ttft if ttft is None else round(ttft,3)}s{flag}")
    time.sleep(3.2)

print(f"\n成功 {len(ttfts)}/40  中位 {statistics.median(ttfts):.3f}s  均值 {statistics.mean(ttfts):.3f}s  "
      f"p90 {sorted(ttfts)[int(len(ttfts)*0.9)]:.3f}s  max {max(ttfts):.3f}s")
print(f"慢请求(>5s) {len(slow)} 次: {slow}")
print(f"非 200: {bad}")

print("\n阶段2：连续打 22 发（不 sleep，探测 RPM 超限行为）")
for i in range(1, 23):
    st, ttft, txt, err = fire("hi")
    print(f"  #{i:2d} status={st} ttft={ttft if ttft is None else round(ttft,3)}s "
          f"{('ERR ' + err) if err else txt[:20]!r}")
    if st != 200 and err:
        break
