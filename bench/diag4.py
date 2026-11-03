#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证竞速请求（hedged requests）能否绕开冷副本"""
import os, time, json, statistics
from concurrent.futures import ThreadPoolExecutor
import httpx2 as httpx
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, "..", ".env"))
BASE = os.getenv("BASE_URL"); KEY = os.getenv("API_KEY"); M = os.getenv("DS_MODEL")
HDR = {"Authorization": f"Bearer {KEY}"}
PROMPT = "一句话解释什么是LLM"


def fire(idx, model=M):
    c = httpx.Client(trust_env=False, timeout=httpx.Timeout(120.0, connect=15.0))
    body = {"model": model, "messages": [{"role": "user", "content": PROMPT}],
            "reasoning_effort": "none", "stream": True, "max_tokens": 64}
    t0 = time.perf_counter(); first = None; txt = []
    try:
        with c.stream("POST", f"{BASE}/chat/completions", json=body, headers=HDR) as r:
            for line in r.iter_lines():
                if line.startswith("data:") and line.strip() != "data: [DONE]":
                    if first is None: first = time.perf_counter() - t0
                    try:
                        obj = json.loads(line[5:])
                        for ch in obj.get("choices", []):
                            cc = (ch.get("delta") or {}).get("content")
                            if cc: txt.append(cc)
                    except Exception: pass
    except Exception as e:
        return (idx, None, f"ERR {type(e).__name__}")
    finally:
        c.close()
    return (idx, first, "".join(txt))


TRIALS = int(os.getenv("TRIALS", "8"))
PAR = int(os.getenv("PAR", "3"))
allslow = []
print(f"每轮并行 {PAR} 发（各自独立连接），共 {TRIALS} 轮\n")
print(" 轮 |  竞速结果(最小TTFT) |  各发 TTFT                 | min/max")
for t in range(1, TRIALS + 1):
    with ThreadPoolExecutor(max_workers=PAR) as ex:
        res = list(ex.map(fire, range(PAR)))
    ttfts = [r[1] for r in res if r[1] is not None]
    best = min(ttfts) if ttfts else None
    slow = [x for x in ttfts if x > 5]
    allslow.append(len(slow))
    print(f" {t:2d} |  {best if best is None else round(best,3):>8}s |  "
          f"{[round(x,2) for x in ttfts]}  | {round(min(ttfts),2) if ttfts else '-'}/{round(max(ttfts),2) if ttfts else '-'}"
          f"   慢副本 {len(slow)}/{len(ttfts)}")

print("\n每轮命中慢副本(>5s)的个数分布:", allslow)
print(f"单发命中慢副本概率 ≈ {sum(allslow)/(TRIALS*PAR):.0%}")
print(f"竞速(PAR={PAR})后仍全慢的概率 ≈ {(sum(allslow)/(TRIALS*PAR))**PAR:.0%}")
