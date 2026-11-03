#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量化：连接握手开销 / 冷启动 vs 热请求"""
import os, time
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
BASE, KEY, M = os.getenv("BASE_URL"), os.getenv("API_KEY"), os.getenv("DS_MODEL")
MSG = [{"role": "user", "content": "Translate English to Chinese: The quick brown fox jumps over the lazy dog."}]

def one(client, tag):
    t0 = time.perf_counter()
    r = client.chat.completions.create(model=M, messages=MSG, reasoning_effort="none")
    d = time.perf_counter() - t0
    print(f"  {tag:34s} {d:6.3f}s")
    return d

print("== 同一 client 连续 3 次（连接复用）==")
c = OpenAI(base_url=BASE, api_key=KEY, timeout=120.0, max_retries=1)
one(c, "第1次(新连接)"); one(c, "第2次(复用连接)"); one(c, "第3次(复用连接)")

print("\n== 每个请求都新建 client（不复用连接）==")
for i in range(3):
    one(OpenAI(base_url=BASE, api_key=KEY, timeout=120.0, max_retries=1), f"第{i+1}次(新 client)")
