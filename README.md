<div align="center">
  <h1>🪟 Windows-MCP</h1>

  <a href="https://github.com/Books-QAQ/Windows-MCP/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License">
  </a>
  <img src="https://img.shields.io/badge/python-3.13+-blue?style=flat-square&logo=python" alt="Python">
  <img src="https://img.shields.io/badge/platform-Windows%2010%2F11-blue?style=flat-square&logo=windows" alt="Platform">
  <img src="https://img.shields.io/badge/tests-313%20passed-brightgreen?style=flat-square" alt="Tests">
  <img src="https://img.shields.io/badge/A2A-5%20phases-orange?style=flat-square" alt="A2A">
</div>

<br>

**Windows-MCP** 是一个轻量级开源 MCP 服务器，桥接 AI Agent 与 Windows 操作系统，支持**文件操作、应用控制、UI 交互、自动化测试**等任务。

> 基于 [CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP) 二次开发。

## ✨ 核心特性

- **无缝 Windows 集成** — 原生操控 Windows UI 元素、启动应用、控制窗口、模拟用户输入。
- **任意 LLM 驱动** — 不依赖计算机视觉或微调模型，兼容任何 LLM 提供商。
- **17 个 MCP 工具** — Click、Type、Scroll、Move、Shortcut、Screenshot、Snapshot、App、Shell、Scrape、MultiSelect、MultiEdit、Clipboard、Process、Notification、Registry、Wait。
- **A2A 编排框架** — LLM 任务规划、DAG 并行执行、结构化技能输出、多 Agent 协作委托。
- **轻量开源** — 最少依赖，MIT 协议。

## 🧠 A2A 框架

在原项目基础上增加了一整套 Agent-to-Agent 编排层：

| 组件 | 说明 |
|------|------|
| **技能系统** | 5 个内置技能（打开应用、截图、搜索、文件操作、剪贴板），插件化架构 + JSON Schema 约束 |
| **DAG 执行器** | 拓扑排序、同层并行执行、跨节点上下文传递（`$step_id.field`） |
| **LLM 规划器** | 自然语言 → `TaskGraph` JSON，支持 OpenAI 兼容 API，自动降级关键词匹配 |
| **智能验证器** | 两层校验：规则检查（进程/文件/剪贴板）+ 可选视觉模型对比 |
| **A2A 网络** | `AgentCard` 能力发现、`AgentRegistry` 注册中心、FastAPI 远程任务委托 |

### 使用示例

```python
from windows_mcp.mobile.runtime import create_mobile_runtime

agent, skills = create_mobile_runtime()

# 简单指令（关键词匹配）
agent.run_instruction("打开QQ")

# DAG 顺序链
agent.run_sequential([
    {"skill": "open_or_focus_app", "params": {"instruction": "打开Chrome"}},
    {"skill": "capture_desktop_state"},
])

# LLM 智能规划（需配置 PLANNER_API_KEY 环境变量）
agent.run_instruction_smart("打开浏览器搜索Python教程然后截图保存")
```

## 🛠️ 安装

> 首次安装依赖解析约需 1–2 分钟。

### 环境要求

- Python 3.13+
- [UV](https://docs.astral.sh/uv/) 包管理器

<details>
  <summary>Claude Desktop</summary>

**方式 A：PyPI 安装（推荐）**
```json
{
  "mcpServers": {
    "windows-mcp": {
      "command": "uvx",
      "args": ["windows-mcp"]
    }
  }
}
```

**方式 B：源码安装**
```shell
git clone https://github.com/Books-QAQ/Windows-MCP.git
cd Windows-MCP
```
```json
{
  "mcpServers": {
    "windows-mcp": {
      "command": "uv",
      "args": ["--directory", "<项目路径>", "run", "windows-mcp"]
    }
  }
}
```

**MSIX 版本（Windows Store）**：配置文件位于 `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\`。如 Electron 未继承 PATH，请使用 `uv.exe` 的完整绝对路径。
</details>

<details>
  <summary>Claude Code</summary>

```shell
# PyPI
claude mcp add --transport stdio windows-mcp -- uvx windows-mcp

# 源码
git clone https://github.com/Books-QAQ/Windows-MCP.git
claude mcp add --transport stdio windows-mcp -- uv --directory "<路径>" run windows-mcp
```
</details>

<details>
  <summary>其他客户端（Perplexity / Gemini / Qwen / Codex）</summary>

```json
{
  "mcpServers": {
    "windows-mcp": {
      "command": "uvx",
      "args": ["windows-mcp"]
    }
  }
}
```

源码安装：将 `"command": "uvx"` 替换为 `"command": "uv"`，`"args": ["--directory", "<路径>", "run", "windows-mcp"]`。
</details>

## 🖥️ 运行模式

### 本地模式（默认）

```shell
uvx windows-mcp                           # stdio（默认）
uvx windows-mcp --transport sse --host localhost --port 8000
uvx windows-mcp --transport streamable-http --host localhost --port 8000
```

### 远程模式

> 需要在 [windowsmcp.io](https://windowsmcp.io) 注册账号。

环境变量配置：
- `MODE=remote`
- `SANDBOX_ID=<沙箱ID>`
- `API_KEY=<API密钥>`

## ⚙️ 环境变量

### A2A 规划器与验证器

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PLANNER_API_KEY` | 无 | LLM 任务规划的 API Key |
| `PLANNER_BASE_URL` | `https://api.deepseek.com/v1` | LLM API 地址 |
| `PLANNER_MODEL` | `deepseek-chat` | 规划模型名称 |
| `VALIDATOR_API_KEY` | 无 | 视觉验证的 API Key |
| `VALIDATOR_MODEL` | `deepseek-chat` | 视觉验证模型 |

### 截图与快照

| 变量 | 默认值 | 说明 |
|---|---|---|
| `WINDOWS_MCP_SCREENSHOT_SCALE` | `1.0` | 缩放比例（0.1–1.0），4K 屏建议 `0.5` 以避免超过 1MB 限制 |
| `WINDOWS_MCP_SCREENSHOT_BACKEND` | `auto` | 截图后端：`auto` / `dxcam` / `mss` / `pillow` |
| `WINDOWS_MCP_PROFILE_SNAPSHOT` | 关闭 | 设为 `1`/`true` 启用性能计时日志 |

### 遥测与调试

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ANONYMIZED_TELEMETRY` | `true` | 设为 `false` 关闭匿名遥测 |
| `WINDOWS_MCP_DEBUG` | `false` | 设为 `1`/`true` 启用调试日志 |

## 🔨 MCP 工具列表

| 工具 | 功能 |
|------|------|
| **Click** | 按坐标或 UI 元素标签点击 |
| **Type** | 输入文本，支持清空/回车 |
| **Scroll** | 垂直/水平滚动 |
| **Move** | 移动鼠标或拖拽 |
| **Shortcut** | 键盘快捷键（`Ctrl+C`、`Alt+Tab` 等） |
| **Wait** | 等待指定时长 |
| **Screenshot** | 快速截图，含光标和窗口信息 |
| **Snapshot** | 完整桌面状态，含交互元素和 DOM 提取 |
| **App** | 启动/缩放/切换应用 |
| **Shell** | 执行 PowerShell 命令 |
| **Scrape** | 抓取网页内容 |
| **MultiSelect** | 多选文件/复选框 |
| **MultiEdit** | 多字段同时输入 |
| **Clipboard** | 读写剪贴板 |
| **Process** | 列出或终止进程 |
| **Notification** | 发送 Windows 通知 |
| **Registry** | 读写注册表 |

## 📝 已知限制

- 无法选中段落中的特定文本（依赖无障碍树）。
- `Type` 工具适用于文本输入，不适合 IDE 编程场景。
- 不适用于游戏自动化。

## 🪪 开源协议

MIT License — 详见 [LICENSE](LICENSE)。

