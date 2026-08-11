# Windows-MCP 开发指南

## 项目概述

Windows-MCP 是一个 Python MCP 服务器，桥接 AI Agent 与 Windows 操作系统，实现桌面自动化控制。

## 构建与开发命令

```bash
uv sync                    # 安装依赖
uv run windows-mcp         # 运行 MCP 服务器
ruff format .              # 格式化代码
ruff check .               # 代码检查
ruff check --fix .         # 代码检查并自动修复
pytest                     # 运行全部测试
pytest tests/test_xxx.py   # 运行单个测试文件
```

**包管理器**：UV。**Python**：3.13+。

## 架构

代码遵循分层服务架构，位于 `src/windows_mcp/`：

**入口** — `__main__.py`：在 FastMCP 服务器上注册 MCP 工具。使用异步生命周期初始化 Desktop、WatchDog 和 Analytics。

**Desktop 服务** — `desktop/service.py`：高层编排器。管理窗口操作、截图、鼠标/键盘操作和剪贴板。

**Tree 服务** — `tree/service.py`：捕获 Windows 无障碍树，识别交互元素和可滚动区域。

**UIA 封装** — `uia/`：基于 `comtypes` 的 Windows UIAutomation COM API 底层抽象。

**WatchDog** — `watchdog/service.py`：独立线程监控 UI 焦点变化。

**虚拟桌面管理** — `vdm/core.py`：跟踪 Windows 虚拟桌面窗口归属。

**A2A 框架** — `mobile/`：Agent-to-Agent 编排层，包含技能系统、DAG 执行器、LLM 规划器和 A2A 网络。

## 代码风格

- 格式化/检查：**Ruff**（行长 100）
- 命名：PEP 8
- 类型标注：函数签名必需

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `WINDOWS_MCP_SCREENSHOT_SCALE` | `1.0` | 截图缩放比例 |
| `WINDOWS_MCP_SCREENSHOT_BACKEND` | `auto` | 截图后端 |
| `WINDOWS_MCP_DEBUG` | `false` | 调试模式 |
| `ANONYMIZED_TELEMETRY` | `true` | 匿名遥测 |

## 安全说明

本服务器拥有完整的系统访问权限，没有沙箱隔离。Shell 和 App 等工具可以执行不可逆操作。建议在虚拟机或 Windows Sandbox 中部署。
