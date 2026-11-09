# AMD 开发者门户接口实验台

基于 **AMD 开发者门户（Radeon API，OpenAI 兼容）** 的一组独立 Python 入口：命令行对话、Web 聊天界面、工具调用 Agent、语音合成连通性测试。

四个入口**互不依赖**，各自可以单独 `python xxx.py` 启动；`amd_api.py` / `sessions.py` 是被它们复用的支撑模块。

---

## 目录

- [环境准备](#环境准备)
- [配置](#配置)
- [四个独立入口](#四个独立入口)
  - [1. `chat.py` — 命令行对话 CLI](#1-chatpy--命令行对话-cli)
  - [2. `web.py` — Web 聊天（后端 API + 前端）](#2-webpy--web-聊天后端-api--前端)
  - [3. `agent.py` — 工具调用 Agent](#3-agentpy--工具调用-agent)
  - [4. `omni.py` — OmniVoice 语音合成连通性测试](#4-omnipy--omnivoice-语音合成连通性测试)
- [两个支撑模块](#两个支撑模块)
- [目录结构](#目录结构)
- [HTTP 接口一览](#http-接口一览)
- [常见问题](#常见问题)

---

## 环境准备

要求 Python ≥ 3.13，推荐用 [uv](https://docs.astral.sh/uv/) 管理虚拟环境：

```bash
uv sync                 # 安装依赖（openai / python-dotenv）
source .venv/bin/activate
```

依赖极少：`openai>=3.42.1`（OpenAI 兼容客户端）+ `python-dotenv`（读 `.env`）。
`web.py` 的 HTTP 服务端用标准库 `http.server`，前端是纯静态文件，不引入任何前端框架。

> 走 SOCKS 代理时需要额外装 `socksio`：`uv add "httpx2[socks]"`。

---

## 配置

所有入口共用根目录的 `.env`（从 `env.sample` 复制）：

```bash
cp env.sample .env
```

```ini
# AMD 开发者门户地址（OpenAI 兼容，路径含 /v1）
BASE_URL=https://developer.amd.com.cn/radeon/api/v1

# 默认模型
DS_MODEL=DeepSeek-V4-Flash

# 你的 API Key
API_KEY=rc-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

| 变量 | 作用 | 默认值 |
|---|---|---|
| `BASE_URL` | 接口根地址 | 无（必填） |
| `API_KEY` | 鉴权 Key | 无（必填） |
| `DS_MODEL` | 默认模型 | `DeepSeek-V4-Flash` |

`chat.py` / `web.py` 另外支持这些可选环境变量：

| 变量 | 作用 | 默认值 |
|---|---|---|
| `CHAT_PROXY` | 显式代理，如 `socks5://127.0.0.1:7897` | 空（不读系统 `all_proxy`） |
| `CHAT_TIMEOUT` | 单次请求超时（秒） | `120` |
| `CHAT_EFFORT` | 思考强度 | `none` |
| `CHAT_STREAM` | 是否流式（`0` 关闭） | `1` |
| `CHAT_MAX_TOKENS` | 限制输出长度 | `0`（不限） |
| `CHAT_DEBUG` | 打印调试信息 | `0` |

> ⚠️ `.env` 里是真实凭证，**不要提交到 Git**（`.gitignore` 已忽略）。仓库里的 `env.sample` 是占位符。

> 额度提示：AMD 开发者门户 Key 限 **20 次/分钟**，日额度 **$1**。

---

## 四个独立入口

### 1. `chat.py` — 命令行对话 CLI

一次性发一个请求，打印**真实耗时**（不重试、不并发、不对冲），用来测模型延迟。

```bash
python chat.py                                    # 用内置默认 prompt
python chat.py "你的问题"
python chat.py --model MiniCPM5-2B "..."
python chat.py --effort low "..."                  # none|minimal|low|medium|high
python chat.py --no-stream "..."                  # 非流式，等完整响应
python chat.py --max-tokens 256 "..."
python chat.py --timeout 120 "..."
python chat.py --proxy socks5://127.0.0.1:7897 "..."  # 需要走代理时
python chat.py --list-models                      # 列出可用 chat 模型
```

**功能点**

- **TTFT + 总耗时 + token 用量**：流式下逐 token 打印并统计首 token 到达时间；`TTFT > 5s` 会提示这是 AMD 动态 router 命中冷副本的长尾，不是本地开销。
- **`<think>` 剥离**：部分模型把思考过程内联写进 `content`，内置 `_ThinkStripper` 增量过滤掉，不会漏字、也不会把半截标签漏到正文里。
- **`reasoning_effort` 兼容降级**：模型若不支持该参数（400 且报错文案含 `reasoning_effort`），自动去掉后**只重发一次**。
- **`--list-models`**：调用 `/v1/models`，用 `amd_api` 的规则过滤掉 TTS / OCR / decision 等非 chat 模型，输出视觉 / 工具 / 上下文长度 / 价格。

**实测参考**（同一 prompt，AMD 开发者门户）

| 模型 | 中位耗时 | 最大耗时 |
|---|---|---|
| `DeepSeek-V4-Flash` | 2.4s | 114s ← 动态 router 长尾很重 |
| `MiniCPM5-2B` | 2.3s | 5.5s |
| `Qwen3.8-Flash-Next` | 4.0s | 8.1s |

---

### 2. `web.py` — Web 聊天（后端 API + 前端）

浏览器里的多轮流式聊天界面，附带会话持久化和延迟统计。

```bash
python web.py                                     # http://127.0.0.1:8000
python web.py --port 8080
python web.py --host 0.0.0.0
python web.py --model DeepSeek-V4-Flash --effort none
python web.py --sessions-dir ./sessions
```

| 参数 | 说明 | 默认值 |
|---|---|---|
| `--host` | 监听地址 | `127.0.0.1` |
| `--port` | 监听端口 | `8000` |
| `--timeout` | 单次请求超时（秒） | `120` |
| `--model` | 默认模型（前端下拉可临时切） | `DS_MODEL` |
| `--effort` | 默认思考强度 | `none` |
| `--sessions-dir` | 会话持久化目录 | `./sessions` |

**功能点**

- **SSE 流式输出**：`/api/chat` 逐 token 推送 `delta` 事件，结束推 `done`（带 `ttft` / `total` / `usage` / `session_id`），异常推 `error`。
- **常驻 client**：全进程共用一个 `OpenAI` client，HTTP 连接池常驻，省掉每次请求的 TCP + TLS 握手。
- **请求串行化**：AMD 端有 rpm / 并发上限，用锁把请求排队，避免互相挤占配额。
- **`max_retries=0`、不对冲**：跑出来的就是真实单次调用耗时。
- **会话持久化**：每个会话一个 JSON 文件（原子写：先写临时文件再 `os.replace`），支持列表 / 详情 / 改名 / 删除。
- **延迟统计**：`/api/stats` 给出 TTFT 中位数 / P90 / 最大值、>5s 慢请求数，并按模型分组；`/api/models` 会把统计挂到对应模型上。
- **前端零依赖**：`web/index.html` + `app.js` + `app.css` + `markdown.js` 纯静态文件，由后端直接托管。

---

### 3. `agent.py` — 工具调用 Agent

最小可用的 function-calling Agent：模型可以读写文件、执行 shell 命令。

```bash
python agent.py "统计当前目录下所有 .py 文件的行数"
```

**工具集**

| 工具 | 说明 |
|---|---|
| `read_file` | 读取文件内容 |
| `write_file` | 写入文件（覆盖） |
| `run_shell` | 执行 shell 命令，**每条都要人工输入 `y` 确认**，超时 60s，输出截断到末尾 4000 字符防止撑爆上下文 |

**执行流程**

1. 把任务 + 工具定义发给模型；
2. 模型返回 `tool_calls` → 执行工具 → 结果作为 `tool` 消息回喂；
3. 模型不再请求工具 → 认为任务完成，打印总结；
4. 最多 **20 步**（`MAX_STEPS`）防止死循环。

工具执行抛错时不会中断，而是把 `类型: 消息` 告诉模型，让它自己修正。

---

### 4. `omni.py` — OmniVoice 语音合成连通性测试

纯标准库（除 `python-dotenv` 外零依赖）的 TTS 冒烟测试：打一次 `/v1/audio/speech`，把 wav 存到本地并校验。

```bash
python omni.py                                   # 默认文本
python omni.py "要合成的文本"
```

| 环境变量 | 作用 | 默认值 |
|---|---|---|
| `BASE_URL` | 服务端地址（自动去掉结尾的 `/v1`） | `http://127.0.0.1:8880` |
| `API_KEY` | 鉴权（留空表示无鉴权） | 空 |
| `OMNIVOICE_TEXT` | 要合成的文本 | 内置中文测试句 |
| `OMNIVOICE_OUT` | 输出文件 | `hello.wav` |
| `OMNIVOICE_TIMEOUT` | 单次请求超时（秒） | `180`（首次加载模型很慢） |

**行为**

- 请求体：`model=OmniVoice`、`voice=female`、`response_format=wav`、`speed=1.0`。
- 校验返回体是 `RIFF` 头的音频，并用 `wave` 模块读出时长 / 采样率 / 声道 / 位深。
- 失败时按状态码给出提示：`401` Key 无效、`403` 无权限、`404` 地址或路径不对、`422` 参数不被接受、`429` 限流或余额不足。
- 打印服务端返回的 `X-Audio-Duration-S`、`X-Synthesis-Latency-S`；若 `X-No-Speech-Detected=true` 会提示换文本 / 音色。
- 退出码：`0` 全部通过 / `1` 有步骤失败。

> 注意：代码实际读取的是 `BASE_URL` / `API_KEY`，模型与音色在代码中固定为 `OmniVoice` / `female`；文档头部提到的 `OMNIVOICE_BASE_URL`、`OMNIVOICE_MODEL`、`OMNIVOICE_VOICE` 目前并未被读取。

---

## 两个支撑模块

这两个不是启动入口，被上面的脚本 `import` 复用。

### `amd_api.py` — 模型目录解析

AMD 的 `/v1/models` 是全量目录，混着三类**不能 chat** 的模型。本模块按三条规则判定是否为可用 chat 模型：

1. `architecture.output_modalities` 含 `text`；
2. `supported_parameters` 非空（chat 模型有 `temperature` / `max_tokens` / `tools` 等）；
3. `providers[0].tools` 为 `true`。

| 函数 | 作用 |
|---|---|
| `is_chat_model(m)` | 判定单个模型是否可 chat |
| `describe(m)` | 精简成 UI 需要的字段：`id` / `vision` / `tools` / `reasoning` / `context_length` / 价格 |
| `split_models(payload)` | 拆成 `(chat 模型列表, 非 chat 模型列表)` |

被过滤掉的典型模型：`OmniVoice`（`output_modalities=['audio']`，走 `/v1/audio/speech`）、`MinerU2.5-Pro`（`['ocr']`）、`Clef` / `d3` / `d3-flash`（`['decision']`，内部决策路由）。

### `sessions.py` — 会话持久化

`sessions/` 目录下**每个会话一个 JSON 文件**：

```json
{
  "id": "20261103-142530-a1b2",
  "title": "第一句话的前 40 个字",
  "model": "DeepSeek-V4-Flash",
  "created_at": "2026-11-03T14:25:30",
  "updated_at": "2026-11-03T14:26:10",
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ]
}
```

`SessionStore` 提供 `create` / `get` / `list` / `save` / `append_turn` / `rename` / `delete`：

- **线程安全**：可重入锁保护写入。
- **原子写**：先写 `*.tmp` 再 `os.replace`，避免半截文件。
- **路径安全**：会话 ID 需匹配 `^[A-Za-z0-9_-]{4,64}$`，且归一化路径必须仍在会话目录内，防目录穿越。

---

## 目录结构

```
mmx/
├── chat.py          # 入口 1：命令行对话 CLI
├── web.py           # 入口 2：Web 后端（HTTP API）
├── agent.py         # 入口 3：工具调用 Agent
├── omni.py          # 入口 4：OmniVoice TTS 连通性测试
├── amd_api.py       # 支撑：模型目录解析（被 chat.py / web.py 复用）
├── sessions.py      # 支撑：会话持久化（被 web.py 复用）
├── web/             # 前端静态文件（零依赖）
│   ├── index.html
│   ├── app.js
│   ├── app.css
│   └── markdown.js
├── env.sample       # .env 模板
├── pyproject.toml   # uv 项目配置
└── sessions/        # 运行时生成的会话目录（已 gitignore）
```

---

## HTTP 接口一览

`web.py` 暴露的全部接口：

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/` | 静态前端（`web/index.html`） |
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/models` | 可用 chat 模型（已过滤 TTS/OCR/decision）+ 各模型实测耗时 |
| `GET` | `/api/stats` | 进程内历史耗时统计（TTFT 中位/P90/最大、慢请求数） |
| `GET` | `/api/sessions` | 会话列表（摘要） |
| `POST` | `/api/sessions` | 新建会话 → `{"session": {...}}` |
| `GET` | `/api/sessions/<id>` | 会话详情（含完整消息） |
| `PATCH` | `/api/sessions/<id>` | 改标题，body `{"title": "..."}` |
| `DELETE` | `/api/sessions/<id>` | 删除会话 |
| `POST` | `/api/chat` | SSE 流式对话 |

`/api/chat` 请求体：

```json
{
  "messages": [{"role": "user", "content": "你好"}],
  "model": "DeepSeek-V4-Flash",
  "effort": "none",
  "session_id": "20261103-142530-a1b2"
}
```

也兼容单轮写法 `{"prompt": "你好"}`。SSE 事件：

```json
{"type": "delta", "text": "增量文本"}
{"type": "done",  "meta": {"model": "...", "ttft": 1.23, "total": 2.45, "usage": {...}, "session_id": "..."}}
{"type": "error", "message": "..."}
```

---

## 常见问题

**Q：启动就报「缺少 BASE_URL / API_KEY」**
A：`.env` 没配或变量名写错。`chat.py` / `web.py` / `agent.py` 都要求这两个变量非空。

**Q：想走代理，但报 `Unknown scheme for proxy URL`**
A：`httpx2` 只认 `http/https/socks5/socks5h`，环境里常见的 `ALL_PROXY=socks://...` 会直接失败。代码已设 `trust_env=False` 彻底不读环境代理，请改用 `--proxy`（`chat.py`）或 `CHAT_PROXY`（`chat.py` / `web.py`）。`socks://` 会自动转成 `socks5://`，但需要额外装 `socksio`。

**Q：模型返回里混着 `<think>...</think>`**
A：`chat.py` 和 `web.py` 已内置剥离；`agent.py` 未做处理，正文里的思考内容会直接显示。

**Q：首 token 要等好几秒 / 甚至几十秒**
A：这是 AMD 动态 router 命中冷副本的长尾（`DeepSeek-V4-Flash` 实测最大 114s），不是本地开销。换 `MiniCPM5-2B` 等小模型会稳定很多。

**Q：429 了**
A：20 次/分钟，且日额度 $1。`web.py` 的请求已经串行化，但频繁刷屏仍会触发限流。

**Q：`omni.py` 报 404**
A：`BASE_URL` 末尾不要带 `/v1`（代码会自动纠正你填的值），请求路径本身已经是 `/v1/audio/speech`。
