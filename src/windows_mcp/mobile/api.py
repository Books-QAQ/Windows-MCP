from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from windows_mcp.env import load_project_dotenv
from windows_mcp.mobile.runtime import default_skill_views
from windows_mcp.mobile.schemas import CommandRequest, SkillView, TaskView
from windows_mcp.mobile.service import MobileTaskService

INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
  <title>Windows MCP Mobile</title>
  <style>
    :root {
      --bg: #f5f7fb;
      --border: #305aa8;
      --text: #18243d;
      --muted: #6c7792;
      --user: #dfe8ff;
      --ai: #eef3fb;
      --accent: #305aa8;
      --danger: #c04a4a;
      --error: #b33131;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: radial-gradient(circle at top, #ffffff 0%, var(--bg) 55%, #edf2fb 100%);
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      color: var(--text);
      display: flex;
      justify-content: center;
      align-items: center;
      padding: 18px;
    }
    .phone {
      width: min(100%, 430px);
      min-height: 82vh;
      background: linear-gradient(180deg, #ffffff 0%, #f7f9fe 100%);
      border: 3px solid var(--border);
      border-radius: 48px;
      padding: 28px 18px 20px;
      box-shadow: 0 18px 50px rgba(27, 54, 104, 0.14);
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    .title {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 0 8px;
    }
    .badge {
      border: 3px solid var(--border);
      border-radius: 16px;
      min-width: 74px;
      min-height: 74px;
      display: grid;
      place-items: center;
      font-size: 18px;
      font-weight: 700;
      background: rgba(255,255,255,0.88);
    }
    .chat {
      flex: 1;
      border-radius: 28px;
      background: rgba(255,255,255,0.7);
      padding: 8px 4px;
      overflow: auto;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .row {
      display: flex;
      width: 100%;
    }
    .row.ai { justify-content: flex-start; }
    .row.user { justify-content: flex-end; }
    .bubble {
      max-width: 78%;
      border-radius: 20px;
      padding: 14px 16px;
      line-height: 1.55;
      font-size: 15px;
      border: 2px solid rgba(48, 90, 168, 0.25);
      white-space: pre-wrap;
      word-break: break-word;
      box-shadow: 0 8px 18px rgba(48, 90, 168, 0.08);
    }
    .row.ai .bubble { background: var(--ai); }
    .row.user .bubble { background: var(--user); }
    .bubble.error {
      border-color: rgba(179, 49, 49, 0.28);
      background: #fff2f2;
      color: var(--error);
    }
    .screenshot {
      margin-top: 12px;
      border-radius: 18px;
      overflow: hidden;
      border: 2px solid rgba(48, 90, 168, 0.18);
      background: #fff;
    }
    .screenshot img {
      width: 100%;
      display: block;
    }
    .composer {
      display: grid;
      grid-template-columns: 1fr auto auto;
      gap: 10px;
      padding: 6px 2px 0;
    }
    .composer textarea {
      width: 100%;
      min-height: 60px;
      max-height: 140px;
      resize: vertical;
      border-radius: 18px;
      border: 3px solid var(--border);
      background: #ffffff;
      padding: 14px 16px;
      font-size: 15px;
      color: var(--text);
      outline: none;
    }
    .composer button {
      border: none;
      border-radius: 18px;
      padding: 0 18px;
      min-width: 78px;
      color: #fff;
      font-size: 15px;
      font-weight: 700;
      cursor: pointer;
      box-shadow: 0 10px 20px rgba(48, 90, 168, 0.2);
    }
    #send {
      background: var(--accent);
    }
    #cancel {
      background: var(--danger);
      box-shadow: 0 10px 20px rgba(192, 74, 74, 0.22);
    }
    #cancel:disabled {
      background: #d8dbe6;
      box-shadow: none;
      color: #8088a0;
      cursor: not-allowed;
    }
    .composer button:disabled {
      opacity: 0.65;
    }
    .hint {
      font-size: 12px;
      color: var(--muted);
      padding: 0 6px;
    }
  </style>
</head>
<body>
  <main class="phone">
    <header class="title">
    </header>
    <section id="chat" class="chat">
      <div class="row ai">
      </div>
    </section>
    <form id="composer" class="composer">
      <textarea id="instruction" placeholder="输入" required></textarea>
      <button id="send" type="submit">发送</button>
      <button id="cancel" type="button" disabled>终止</button>
    </form>
  </main>
  <script>
    const chat = document.getElementById("chat");
    const form = document.getElementById("composer");
    const input = document.getElementById("instruction");
    const send = document.getElementById("send");
    const cancel = document.getElementById("cancel");

    let activeTaskId = null;
    let waitingRow = null;

    const taskStatus = {
      pending: "任务已创建，正在排队...",
      running: "正在执行，请稍候...",
      cancelling: "已发送终止请求，正在等待当前步骤安全结束...",
      cancelled: "任务已终止。",
      failed: "执行失败，请稍后重试。"
    };

    function appendBubble(role, text, screenshot, isError = false) {
      const row = document.createElement("div");
      row.className = `row ${role}`;
      const bubble = document.createElement("div");
      bubble.className = isError ? "bubble error" : "bubble";
      bubble.textContent = text;

      if (screenshot && screenshot.base64_data) {
        const wrap = document.createElement("div");
        wrap.className = "screenshot";
        const img = document.createElement("img");
        img.alt = "当前界面状态截图";
        img.src = `data:${screenshot.mime_type || "image/png"};base64,${screenshot.base64_data}`;
        wrap.appendChild(img);
        bubble.appendChild(wrap);
      }

      row.appendChild(bubble);
      chat.appendChild(row);
      chat.scrollTop = chat.scrollHeight;
      return row;
    }

    function setRunningState(isRunning) {
      send.disabled = isRunning;
      cancel.disabled = !isRunning;
      input.disabled = isRunning;
      if (!isRunning) {
        activeTaskId = null;
        waitingRow = null;
        input.focus();
      }
    }

    async function pollTask(taskId) {
      while (true) {
        const response = await fetch(`/mobile/tasks/${taskId}`);
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.detail || "获取任务状态失败");
        }

        if (!waitingRow) {
          return;
        }

        if (payload.status === "completed") {
          waitingRow.remove();
          appendBubble(
            "ai",
            "操作已完成，当前界面状态如下：",
            payload.result?.screenshot || null
          );
          return;
        }

        if (payload.status === "cancelled") {
          waitingRow.remove();
          appendBubble("ai", payload.error || taskStatus.cancelled, null, true);
          return;
        }

        if (payload.status === "failed") {
          waitingRow.remove();
          appendBubble("ai", payload.error || taskStatus.failed, null, true);
          return;
        }

        waitingRow.querySelector(".bubble").textContent =
          taskStatus[payload.status] || taskStatus.running;
        await new Promise((resolve) => setTimeout(resolve, 1200));
      }
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const instruction = input.value.trim();
      if (!instruction || activeTaskId) return;

      appendBubble("user", instruction);
      input.value = "";
      waitingRow = appendBubble("ai", taskStatus.pending);
      setRunningState(true);

      try {
        const response = await fetch("/mobile/tasks", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ instruction })
        });
        const payload = await response.json();
        if (!response.ok) {
          waitingRow.remove();
          appendBubble("ai", payload.detail || "提交任务失败。", null, true);
          setRunningState(false);
          return;
        }

        activeTaskId = payload.id;
        await pollTask(payload.id);
      } catch (error) {
        if (waitingRow) {
          waitingRow.remove();
        }
        appendBubble("ai", `请求失败：${error.message}`, null, true);
      } finally {
        setRunningState(false);
      }
    });

    cancel.addEventListener("click", async () => {
      if (!activeTaskId || !waitingRow) return;

      cancel.disabled = true;
      waitingRow.querySelector(".bubble").textContent = taskStatus.cancelling;

      try {
        const response = await fetch(`/mobile/tasks/${activeTaskId}/cancel`, {
          method: "POST"
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.detail || "终止任务失败");
        }
        waitingRow.querySelector(".bubble").textContent =
          taskStatus[payload.status] || taskStatus.cancelling;
      } catch (error) {
        appendBubble("ai", `终止失败：${error.message}`, null, true);
        cancel.disabled = false;
      }
    });
  </script>
</body>
</html>
"""


def create_app(
    task_service: MobileTaskService | None = None,
    *,
    skill_catalog: list[SkillView] | None = None,
) -> FastAPI:
    load_project_dotenv()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if task_service is not None:
            app.state.task_service = task_service
            app.state.skill_catalog = skill_catalog or default_skill_views()
            yield
            await app.state.task_service.close()
            return

        app.state.task_service = MobileTaskService()
        app.state.skill_catalog = skill_catalog or default_skill_views()
        yield
        await app.state.task_service.close()

    app = FastAPI(
        title="Windows-MCP Mobile Gateway",
        description="Phone-to-desktop gateway that uses Claude to control Windows through Windows-MCP.",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/healthz")
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(INDEX_HTML)

    @app.post("/mobile/commands/run", response_model=TaskView)
    async def run_command(request: CommandRequest) -> TaskView:
        return await app.state.task_service.run_now(request.instruction, request.model)

    @app.post("/mobile/tasks", response_model=TaskView, status_code=202)
    async def create_task(request: CommandRequest) -> TaskView:
        return await app.state.task_service.create_task(request.instruction, request.model)

    @app.get("/mobile/tasks/{task_id}", response_model=TaskView)
    async def get_task(task_id: str) -> TaskView:
        task = await app.state.task_service.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return task

    @app.post("/mobile/tasks/{task_id}/cancel", response_model=TaskView)
    async def cancel_task(task_id: str) -> TaskView:
        task = await app.state.task_service.cancel_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return task

    @app.get("/mobile/skills", response_model=list[SkillView])
    async def list_skills() -> list[SkillView]:
        return list(app.state.skill_catalog)

    return app
