import json
import os
import subprocess
import sys

from dotenv import load_dotenv
from openai import OpenAI

try:  # openai 3.42 用的是 httpx2（httpx v2 发行包）
    import httpx2 as httpx
except ImportError:  # 兼容纯 httpx 环境
    import httpx

# Load settings from .env (existing environment variables take precedence).
load_dotenv()

# 只用 AMD 开发者门户那套接口
base_url = (os.getenv("BASE_URL") or "").strip()
api_key = (os.getenv("API_KEY") or "").strip()
model = os.getenv("DS_MODEL", "DeepSeek-V4-Flash")

if not base_url or not api_key:
    raise SystemExit(
        "Missing BASE_URL and/or API_KEY. "
        "Set them in .env (or export them) before running."
    )

client = OpenAI(
    base_url=base_url,
    api_key=api_key,
    # trust_env=False：不读环境里的 all_proxy/ALL_PROXY，
    # 避免 socks:// 这类非 httpx2 认识的 scheme 直接把 client 创建搞崩
    http_client=httpx.Client(trust_env=False, timeout=httpx.Timeout(120.0, connect=15.0)),
)


def read_file(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def write_file(path: str, content: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"已写入 {path}（{len(content)} 字符）"


def run_shell(command: str) -> str:
    # 第 1 课先用最朴素的权限控制：每条命令人工确认
    if input(f"\n[需要确认] 执行命令：{command}\n允许？(y/n) ").strip().lower() != "y":
        return "用户拒绝执行该命令"
    try:
        p = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return "命令超时（60 秒）"
    return (p.stdout + p.stderr)[-4000:] or "(无输出)"  # 截断，避免撑爆上下文

def _tool(name, desc, props):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": {k: {"type": "string", "description": v} for k, v in props.items()},
                "required": list(props),
            },
        },
    }

TOOL_FUNCS = {"read_file": read_file, "write_file": write_file, "run_shell": run_shell}

TOOLS = [
    _tool("read_file", "读取文件内容", {"path": "文件路径"}),
    _tool("write_file", "写入文件（覆盖）", {"path": "文件路径", "content": "完整文件内容"}),
    _tool("run_shell", "执行 shell 命令并返回输出", {"command": "要执行的命令"}),
]
SYSTEM = (
    "你是一个能操作本地文件和命令行的编程助手。"
    "需要时调用工具，完成任务后直接用文字总结，不要再调用工具。"
)
MAX_STEPS = 20  # 防止死循环：Agent 最多转 20 圈

def run(task: str) -> None:
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": task},
    ]
    for step in range(1, MAX_STEPS + 1):
        resp = client.chat.completions.create(model=model, messages=messages, tools=TOOLS)
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))

        # 模型没有要调用工具 = 它认为任务做完了，循环结束
        if not msg.tool_calls:
            print(f"\n[完成，共 {step} 步]\n{msg.content}")
            return

        # 模型要调用工具：执行，并把结果作为 tool 消息喂回去
        for call in msg.tool_calls:
            name = call.function.name
            print(f"[第 {step} 步] {name}({call.function.arguments[:80]})")
            try:
                args = json.loads(call.function.arguments)
                result = TOOL_FUNCS[name](**args)
            except Exception as e:  # 工具出错也要告诉模型，让它自己修正
                result = f"工具执行出错：{type(e).__name__}: {e}"
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    print(f"\n[达到 {MAX_STEPS} 步上限，已停止]")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('用法：python agent.py "你的任务"')
    run(sys.argv[1])
