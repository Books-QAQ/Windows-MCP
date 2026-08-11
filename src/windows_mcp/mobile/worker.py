import json
import sys

from windows_mcp.env import load_project_dotenv
from windows_mcp.mobile.runtime import create_mobile_runtime


def main() -> int:
    load_project_dotenv()

    raw_payload = sys.stdin.buffer.read()
    if not raw_payload:
        raise RuntimeError("No task payload received.")

    payload = json.loads(raw_payload.decode("utf-8"))
    instruction = payload["instruction"]
    model = payload.get("model")

    agent, _ = create_mobile_runtime()
    result = agent.run_instruction(instruction, model)

    response = {
        "message": result.message,
        "screenshot": result.screenshot.model_dump(),
        "raw_agent_response": result.raw_agent_response,
    }
    sys.stdout.write(json.dumps(response, ensure_ascii=False))
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
