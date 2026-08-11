import os
from pathlib import Path

from windows_mcp.env import load_project_dotenv


def test_load_project_dotenv_reads_root_env_file(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    package_dir = project_root / "src" / "windows_mcp"
    package_dir.mkdir(parents=True)
    env_file = project_root / ".env"
    env_file.write_text("MODEL_PROVIDER=openai_compatible\nMODEL_NAME=qwen-max\n", encoding="utf-8")

    fake_module = package_dir / "env.py"
    fake_module.write_text("", encoding="utf-8")

    monkeypatch.chdir(project_root)
    monkeypatch.setattr("windows_mcp.env.Path.resolve", lambda self: fake_module)
    monkeypatch.delenv("MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)

    load_project_dotenv()

    assert os.getenv("MODEL_PROVIDER") == "openai_compatible"
    assert os.getenv("MODEL_NAME") == "qwen-max"
