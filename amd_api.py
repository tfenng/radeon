#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AMD /v1/models 的小工具：筛出真正能 chat 的模型。

AMD 的模型目录是全量的，混着三类非 chat 模型：
  - OmniVoice      output_modalities=['audio']   → TTS，走 /v1/audio/speech
  - MinerU2.5-Pro  output_modalities=['ocr']     → OCR
  - Clef / d3 / d3-flash  output_modalities=['decision'] → 内部决策路由，不对外 chat

判定依据（来自 /v1/models 的字段）：
  1. architecture.output_modalities 里含 'text'
  2. supported_parameters 非空（chat 模型有 temperature/max_tokens/top_p/stream/tools…）
  3. providers[].tools 为 true
"""

from __future__ import annotations


def _output_modalities(m: dict) -> list[str]:
    arch = m.get("architecture") or {}
    return arch.get("output_modalities") or []


def is_chat_model(m: dict) -> bool:
    """是否是可用的 chat 模型"""
    if "text" not in _output_modalities(m):
        return False
    if not (m.get("supported_parameters") or []):
        return False
    providers = m.get("providers") or [{}]
    return bool(providers[0].get("tools"))


def describe(m: dict) -> dict:
    """给 UI 用的精简信息"""
    arch = m.get("architecture") or {}
    providers = m.get("providers") or [{}]
    p = providers[0]
    return {
        "id": m.get("id"),
        "vision": "image" in (arch.get("input_modalities") or []),
        "tools": bool(p.get("tools")),
        "reasoning": bool(p.get("reasoning")),
        "context_length": m.get("context_length"),
        "pricing_prompt": (m.get("pricing") or {}).get("prompt"),
        "pricing_completion": (m.get("pricing") or {}).get("completion"),
    }


def split_models(models_payload: dict) -> tuple[list[dict], list[dict]]:
    """返回 (chat 模型列表, 非 chat 模型列表)"""
    chat, other = [], []
    for m in models_payload.get("data", []):
        (chat if is_chat_model(m) else other).append(m)
    return chat, other
