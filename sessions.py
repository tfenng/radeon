#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
会话持久化：sessions/ 目录下每个会话一个 JSON 文件。

文件结构（sessions/<id>.json）：
    {
      "id": "20261103-142530-a1b2",
      "title": "第一句话的前 30 个字",
      "model": "DeepSeek-V4-Flash",
      "created_at": "2026-11-03T14:25:30",
      "updated_at": "2026-11-03T14:26:10",
      "messages": [{"role": "user", "content": "..."},
                   {"role": "assistant", "content": "..."}]
    }
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime

ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
TITLE_MAX = 40


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


class SessionStore:
    """线程安全的会话存储（原子写：先写临时文件再 replace）。"""

    def __init__(self, directory: str):
        self.dir = os.path.abspath(directory)
        os.makedirs(self.dir, exist_ok=True)
        self._lock = threading.RLock()   # 可重入：append_turn 持锁后会再调 save()

    # ---------------------------------------------------------- 内部
    def _path(self, sid: str) -> str | None:
        if not sid or not ID_RE.match(sid):
            return None
        p = os.path.normpath(os.path.join(self.dir, sid + ".json"))
        if not p.startswith(self.dir + os.sep):
            return None
        return p

    # ---------------------------------------------------------- 读
    def get(self, sid: str) -> dict | None:
        p = self._path(sid)
        if not p or not os.path.isfile(p):
            return None
        try:
            with open(p, encoding="utf-8") as f:
                sess = json.load(f)
        except Exception:
            return None
        if not isinstance(sess, dict):
            return None
        sess.setdefault("messages", [])
        return sess

    def list(self) -> list[dict]:
        """按更新时间倒序列出会话摘要（不含正文）。"""
        out = []
        try:
            names = os.listdir(self.dir)
        except OSError:
            return out
        for name in names:
            if not name.endswith(".json"):
                continue
            sess = self.get(name[:-5])
            if not sess:
                continue
            out.append({
                "id": sess.get("id", name[:-5]),
                "title": sess.get("title") or "(空会话)",
                "model": sess.get("model", ""),
                "created_at": sess.get("created_at", ""),
                "updated_at": sess.get("updated_at", ""),
                "messages_count": len(sess.get("messages", [])),
            })
        out.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
        return out

    # ---------------------------------------------------------- 写
    def create(self, model: str = "") -> dict:
        now = datetime.now()
        sid = now.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex()
        sess = {
            "id": sid,
            "title": "新会话",
            "model": model,
            "created_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
            "updated_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
            "messages": [],
        }
        self.save(sess)
        return sess

    def save(self, sess: dict) -> None:
        p = self._path(sess.get("id", ""))
        if not p:
            return
        sess["updated_at"] = _now()
        with self._lock:
            fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(sess, f, ensure_ascii=False, indent=2)
                os.replace(tmp, p)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

    def append_turn(self, sid: str, messages: list[dict], assistant: str,
                    model: str = "") -> dict | None:
        """写入一轮对话：messages 为本轮请求的完整历史，最后追加 AI 回复。"""
        with self._lock:
            sess = self.get(sid)
            if not sess:
                sess = self.create(model)
            sess["messages"] = [
                {"role": m.get("role", "user"), "content": m.get("content", "")}
                for m in messages
                if m.get("role") in ("user", "assistant")
            ]
            if assistant:
                sess["messages"].append({"role": "assistant", "content": assistant})
            if model:
                sess["model"] = model
            # 标题取第一条用户消息
            for m in sess["messages"]:
                if m["role"] == "user" and m["content"].strip():
                    t = m["content"].strip().replace("\n", " ")
                    sess["title"] = t[:TITLE_MAX] + ("…" if len(t) > TITLE_MAX else "")
                    break
            self.save(sess)
            return sess

    def rename(self, sid: str, title: str) -> dict | None:
        sess = self.get(sid)
        if not sess:
            return None
        sess["title"] = (title or "").strip()[:TITLE_MAX] or "(空会话)"
        self.save(sess)
        return sess

    def delete(self, sid: str) -> bool:
        p = self._path(sid)
        if not p or not os.path.isfile(p):
            return False
        with self._lock:
            try:
                os.unlink(p)
                return True
            except OSError:
                return False
