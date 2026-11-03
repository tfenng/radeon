#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""区分：模型全局冷启动 vs 每连接冷启动"""
import os, time, json
import httpx2 as httpx
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, "..", ".env"))
BASE = os.getenv("BASE_URL"); KEY = os.getenv("API_KEY"); M = os.getenv("DS_MODEL")
HDR = {"Authorization": f"Bearer {KEY}"}
PROMPT = "一句话解释什么是LLM"

def chat(client, tag, model=M, prompt=PROMPT):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "reasoning_effort": "none", "stream": True}
    t0 = time.perf_counter(); first = None; txt = []
    with client.stream("POST", f"{BASE}/chat/completions", json=body, headers=HDR) as r:
        for line in r.iter_lines():
            if line.startswith("data:") and line.strip() != "data: [DONE]":
                if first is None: first = time.perf_counter() - t0
                try:
                    obj = json.loads(line[5:])
                    for ch in obj.get("choices", []):
                        c = (ch.get("delta") or {}).get("content")
                        if c: txt.append(c)
                except Exception: pass
    print(f"  {tag:38s} ttft={first if first is None else round(first,3)}s out={''.join(txt)[:20]!r}")

def models(client, tag):
    t0 = time.perf_counter()
    r = client.get(f"{BASE}/models", headers=HDR)
    print(f"  {tag:38s} {time.perf_counter()-t0:.3f}s status={r.status_code}")

def mk():
    return httpx.Client(trust_env=False, timeout=httpx.Timeout(120.0, connect=15.0))

print("== 场景1：空闲后新连接A(A1=慢) -> 同连接A2 -> 新连接B ==")
print("  [先等 90s 让服务端完全空闲]")
time.sleep(90)
cA = mk(); chat(cA, "A1 新连接(空闲后)")
chat(cA, "A2 同一连接")
cB = mk(); chat(cB, "B  新连接(紧跟A2)")
chat(cB, "B2 同一连接")

print("\n== 场景2：空闲后，先打 /models（不调模型）能否预热 ==")
time.sleep(90)
cC = mk(); models(cC, "/models (空闲后)")
chat(cC, "C1 /models之后的第一发")

print("\n== 场景3：空闲后，先用小模型预热，再用目标模型 ==")
time.sleep(90)
cD = mk(); chat(cD, "D1 先用 MiniCPM5-2B 预热", model="MiniCPM5-2B", prompt="hi")
chat(cD, "D2 再用 DeepSeek-V4-Flash")
