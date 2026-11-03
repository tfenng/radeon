#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A/B：常驻连接 vs 每次新建连接，看后续请求的响应速度是否改善。

A: 一个 client 常驻（复用 TCP/TLS），连续 N 次
B: 每次新建 client（每次都重新握手）
两者同样节奏、同样 prompt，记录 TTFT 分布。
"""
import os, time, statistics, json
import httpx2 as httpx
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, "..", ".env"))
BASE = os.getenv("BASE_URL"); KEY = os.getenv("API_KEY"); M = os.getenv("DS_MODEL")
HDR = {"Authorization": f"Bearer {KEY}"}
PROMPT = "一句话解释什么是LLM"
N = int(os.getenv("N", "40"))
GAP = float(os.getenv("GAP", "3.5"))   # ≥3.2s 以内不撞 rpm 20/min


def probe(client):
    body = {"model": M, "messages": [{"role": "user", "content": PROMPT}],
            "reasoning_effort": "none", "stream": True, "max_tokens": 64}
    t0 = time.perf_counter(); first = None; st = None; err = ""
    try:
        with client.stream("POST", f"{BASE}/chat/completions", json=body, headers=HDR) as r:
            st = r.status_code
            if st != 200:
                err = r.read()[:80].decode("utf-8", "replace")
            else:
                for line in r.iter_lines():
                    if line.startswith("data:") and line.strip() != "data: [DONE]":
                        if first is None:
                            first = time.perf_counter() - t0
    except Exception as e:
        err = type(e).__name__
    return st, first, err


def new_client():
    return httpx.Client(trust_env=False, timeout=httpx.Timeout(180.0, connect=15.0),
                        limits=httpx.Limits(max_connections=20, max_keepalive_connections=20,
                                            keepalive_expiry=300.0))


def run(label, persistent):
    ttfts, errs = [], []
    client = new_client() if persistent else None
    for i in range(1, N + 1):
        c = client if persistent else new_client()
        st, ttft, err = probe(c)
        if not persistent:
            c.close()
        if ttft:
            ttfts.append(ttft)
            mark = "  <== 慢" if ttft > 5 else ""
            print(f"  [{label}] #{i:2d} ttft={ttft:6.3f}s{mark}", flush=True)
        else:
            errs.append(f"{st}/{err}")
            print(f"  [{label}] #{i:2d} 失败 {st} {err[:60]}", flush=True)
        time.sleep(GAP)
    if client:
        client.close()
    if ttfts:
        srt = sorted(ttfts)
        p90 = srt[max(0, int(len(srt) * 0.9) - 1)]
        print(f"\n[{label}] n={len(ttfts)} 中位={statistics.median(ttfts):.3f}s "
              f"p90={p90:.3f}s max={max(ttfts):.3f}s >5s占比="
              f"{sum(1 for x in ttfts if x > 5) / len(ttfts):.0%} 失败={errs}\n")
    else:
        print(f"[{label}] 全部失败 {errs}\n")


print(f"N={N} 间隔={GAP}s（A 先跑，B 后跑，各约 {N * GAP / 60:.1f} 分钟）\n")
run("常驻连接", True)
run("每次新建", False)
