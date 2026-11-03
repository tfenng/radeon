#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM-5.3-Flash：不同 reasoning_effort 下的思考量与耗时"""
import os, time, json, statistics
import httpx2 as httpx
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, "..", ".env"))
BASE = os.getenv("BASE_URL"); KEY = os.getenv("API_KEY")
MODEL = "GLM-5.3-Flash"
HDR = {"Authorization": f"Bearer {KEY}"}

c = httpx.Client(trust_env=False, timeout=httpx.Timeout(300.0, connect=15.0))


def probe(effort=None, prompt="Hello", stream=False):
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}]}
    if effort:
        body["reasoning_effort"] = effort
    if stream:
        body["stream"] = True
    t0 = time.perf_counter()
    first = None
    r = c.post(f"{BASE}/chat/completions", json=body, headers=HDR)
    total = time.perf_counter() - t0
    if r.status_code != 200:
        return {"status": r.status_code, "err": r.text[:150], "total": total}
    j = r.json()
    msg = (j.get("choices") or [{}])[0].get("message") or {}
    u = j.get("usage") or {}
    return {
        "status": 200,
        "total": total,
        "reasoning": len(msg.get("reasoning") or ""),
        "content": len(msg.get("content") or ""),
        "completion": u.get("completion_tokens"),
        "reasoning_tokens": (u.get("completion_tokens_details") or {}).get("reasoning_tokens"),
        "text": (msg.get("content") or "")[:60],
    }


print("== 非流式：各 reasoning_effort ==")
for eff in [None, "none", "minimal", "low", "medium", "high"]:
    r = probe(eff)
    if r["status"] != 200:
        print(f"  effort={str(eff):8s} -> {r['status']} {r['err'][:110]}")
    else:
        print(f"  effort={str(eff):8s} -> 总耗时 {r['total']:7.3f}s | 思考 {r['reasoning_tokens']:>4} tok"
              f" | 正文 {r['completion']:>4} tok | {r['text']!r}")
    time.sleep(4)

print("\n== 流式：TTFT（各 reasoning_effort）==")
def stream_probe(effort):
    body = {"model": MODEL, "messages": [{"role": "user", "content": "Hello"}], "stream": True}
    if effort:
        body["reasoning_effort"] = effort
    t0 = time.perf_counter(); first = None; think_first = None
    with c.stream("POST", f"{BASE}/chat/completions", json=body, headers=HDR) as r:
        if r.status_code != 200:
            return {"status": r.status_code, "err": r.read()[:120].decode("utf-8", "replace")}
        for line in r.iter_lines():
            if line.startswith("data:") and line.strip() != "data: [DONE]":
                try:
                    o = json.loads(line[5:])
                except Exception:
                    continue
                for ch in o.get("choices", []):
                    d = ch.get("delta") or {}
                    if d.get("reasoning_content") and think_first is None:
                        think_first = time.perf_counter() - t0
                    if d.get("content") and first is None:
                        first = time.perf_counter() - t0
    return {"status": 200, "first": first, "think_first": think_first,
            "total": time.perf_counter() - t0}

for eff in [None, "none", "low"]:
    r = stream_probe(eff)
    if r["status"] != 200:
        print(f"  effort={str(eff):8s} -> {r['status']} {r['err'][:110]}")
    else:
        f = f"{r['first']:.3f}s" if r['first'] else "None"
        t = f"{r['think_first']:.3f}s" if r['think_first'] else "None"
        print(f"  effort={str(eff):8s} -> 思考首token {t:>8} | 正文首token {f:>8} | 总 {r['total']:7.3f}s")
    time.sleep(4)
