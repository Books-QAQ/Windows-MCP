<div align="center">
  <h1>🪟 Windows-MCP</h1>

  <a href="https://github.com/Books-QAQ/Windows-MCP/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  </a>
  <img src="https://img.shields.io/badge/python-3.13%2B-blue" alt="Python">
  <img src="https://img.shields.io/badge/platform-Windows%207–11-blue" alt="Platform: Windows 7 to 11">
</div>

<br>

**Windows-MCP** is a lightweight, open-source MCP server that bridges AI agents with the Windows operating system, enabling tasks such as **file navigation, application control, UI interaction, QA testing,** and more.

> This is a fork of [CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP) with significant enhancements to the A2A (Agent-to-Agent) framework.

## ✨ Key Features

- **Seamless Windows Integration** — Interacts natively with Windows UI elements, opens apps, controls windows, simulates user input.
- **Use Any LLM (Vision Optional)** — No dependency on computer vision techniques or fine-tuned models. Works with any LLM provider.
- **Rich Toolset** — 17 tools: Click, Type, Scroll, Move, Shortcut, Screenshot, Snapshot, App, Shell, Scrape, MultiSelect, MultiEdit, Clipboard, Process, Notification, Registry, Wait.
- **A2A Framework** — LLM-powered task planning, DAG execution with parallel batches, structured skill outputs, and multi-agent delegation.
- **Lightweight & Open-Source** — Minimal dependencies, MIT license.

## 🧠 A2A Framework (New)

This fork adds a complete Agent-to-Agent orchestration layer on top of the existing MCP tools:

| Component | Description |
|-----------|-------------|
| **Skill System** | 5 built-in skills (open_app, screenshot, search, file_ops, clipboard) with pluggable architecture and JSON Schema I/O |
| **DAG Executor** | Topological ordering, parallel batch execution, cross-node context passing (`$step_id.field`) |
| **LLM Planner** | Converts natural language to `TaskGraph` JSON via OpenAI-compatible APIs, falls back to keyword matching |
| **Smart Validator** | Two-layer verification: rule-based checks (process/file/clipboard) + optional vision model |
| **A2A Network** | `AgentCard` discovery, `AgentRegistry`, remote task delegation via FastAPI gateway |

### Usage

```python
from windows_mcp.mobile.runtime import create_mobile_runtime

agent, skills = create_mobile_runtime()

# Simple instruction (keyword match)
agent.run_instruction("打开QQ")

# DAG sequential chain
agent.run_sequential([
    {"skill": "open_or_focus_app", "params": {"instruction": "打开Chrome"}},
    {"skill": "capture_desktop_state"},
])

# LLM-powered planning (requires PLANNER_API_KEY env var)
agent.run_instruction_smart("打开浏览器搜索Python教程然后截图保存")
```

## 🛠️ Installation

> First-time install may take 1–2 minutes for dependency resolution.

### Prerequisites

- Python 3.13+
- [UV](https://docs.astral.sh/uv/) package manager

<details>
  <summary>Claude Desktop</summary>

**Option A: PyPI (Recommended)**
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

**Option B: From Source**
```shell
git clone https://github.com/Books-QAQ/Windows-MCP.git
cd Windows-MCP
```
```json
{
  "mcpServers": {
    "windows-mcp": {
      "command": "uv",
      "args": ["--directory", "<path>", "run", "windows-mcp"]
    }
  }
}
```

**MSIX (Windows Store)**: Config lives at `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\`. Use full path to `uv.exe` if Electron doesn't inherit PATH.
</details>

<details>
  <summary>Claude Code</summary>

```shell
# PyPI
claude mcp add --transport stdio windows-mcp -- uvx windows-mcp

# From source
git clone https://github.com/Books-QAQ/Windows-MCP.git
claude mcp add --transport stdio windows-mcp -- uv --directory "<path>" run windows-mcp
```
</details>

<details>
  <summary>Other Clients (Perplexity / Gemini / Qwen / Codex)</summary>

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

From source: replace `"command": "uvx"` with `"command": "uv"` and `"args": ["--directory", "<path>", "run", "windows-mcp"]`.
</details>

## 🖥️ Modes

### Local Mode (Default)

```shell
uvx windows-mcp                           # stdio (default)
uvx windows-mcp --transport sse --host localhost --port 8000
uvx windows-mcp --transport streamable-http --host localhost --port 8000
```

### Remote Mode

> Requires an account at [windowsmcp.io](https://windowsmcp.io).

Set environment variables:
- `MODE=remote`
- `SANDBOX_ID=<your-sandbox-id>`
- `API_KEY=<your-api-key>`

## ⚙️ Environment Variables

### A2A Planner & Validator (New)

| Variable | Default | Description |
|---|---|---|
| `PLANNER_API_KEY` | _(none)_ | API key for LLM-based task planning |
| `PLANNER_BASE_URL` | `https://api.deepseek.com/v1` | LLM API endpoint |
| `PLANNER_MODEL` | `deepseek-chat` | Model name for planning |
| `VALIDATOR_API_KEY` | _(none)_ | API key for vision-based validation |
| `VALIDATOR_MODEL` | `deepseek-chat` | Model for vision validation |

### Screenshot & Snapshot

| Variable | Default | Description |
|---|---|---|
| `WINDOWS_MCP_SCREENSHOT_SCALE` | `1.0` | Scale factor (0.1–1.0). Use `0.5` for 4K displays to stay under 1MB limit. |
| `WINDOWS_MCP_SCREENSHOT_BACKEND` | `auto` | `auto` / `dxcam` / `mss` / `pillow` |
| `WINDOWS_MCP_PROFILE_SNAPSHOT` | _(disabled)_ | Set to `1`/`true` to emit timing logs |

### Telemetry & Debug

| Variable | Default | Description |
|---|---|---|
| `ANONYMIZED_TELEMETRY` | `true` | Set to `false` to disable |
| `WINDOWS_MCP_DEBUG` | `false` | Set to `1`/`true` for verbose logging |

## 🔨 MCP Tools

- **Click** — Click at coordinates or UI element label
- **Type** — Type text with optional clear/press_enter
- **Scroll** — Vertical/horizontal scrolling
- **Move** — Move mouse or drag-and-drop
- **Shortcut** — Keyboard shortcuts (`Ctrl+C`, `Alt+Tab`, etc.)
- **Wait** — Pause for specified duration
- **Screenshot** — Fast screenshot with cursor/window info
- **Snapshot** — Full desktop state with interactive elements, DOM extraction
- **App** — Launch, resize, switch applications
- **Shell** — Execute PowerShell commands
- **Scrape** — Extract webpage content
- **MultiSelect** — Select multiple items with optional Ctrl
- **MultiEdit** — Type into multiple fields at once
- **Clipboard** — Read/set Windows clipboard
- **Process** — List or kill processes
- **Notification** — Send Windows toast notifications
- **Registry** — Read/write/delete/list Registry keys

## 📊 Telemetry

Anonymous usage data is collected by default to help improve the server. No personal information, tool arguments, or outputs are tracked. Set `ANONYMIZED_TELEMETRY=false` to disable.

## 📝 Limitations

- Cannot select specific text sections within a paragraph (relies on accessibility tree).
- `Type` tool is designed for text input, not IDE programming.
- Not suitable for video game automation.

## 🪪 License

MIT License — see [LICENSE](LICENSE).

## 🙏 Acknowledgements

Built on top of excellent open-source projects:
- [CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP) — original project
- [Python-UIAutomation-for-Windows](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows)
- [PyAutoGUI](https://github.com/asweigart/pyautogui)

## 🤝 Contributing

Contributions welcome! See [CONTRIBUTING](CONTRIBUTING).

Made with ❤️ by [Books-QAQ](https://github.com/Books-QAQ)

## Citation

```bibtex
@software{
  author       = {Books-QAQ},
  title        = {Windows-MCP: Lightweight MCP server for Windows desktop automation},
  year         = {2024},
  publisher    = {GitHub},
  url          = {https://github.com/Books-QAQ/Windows-MCP}
}
```
