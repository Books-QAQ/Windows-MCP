import subprocess

import windows_mcp.desktop.powershell as powershell_module


def test_resolve_shell_executable_falls_back_to_known_powershell_path(monkeypatch):
    expected = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

    monkeypatch.setattr(powershell_module.shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        powershell_module.os.path,
        "exists",
        lambda path: path == expected,
    )
    monkeypatch.setenv("SystemRoot", r"C:\Windows")

    assert powershell_module._resolve_shell_executable("powershell", "") == expected


def test_execute_command_uses_resolved_absolute_shell(monkeypatch):
    expected_shell = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    captured = {}

    monkeypatch.setattr(
        powershell_module,
        "_build_subprocess_env",
        lambda: ({"PATH": r"C:\Windows\System32"}, r"C:\Windows\System32"),
    )
    monkeypatch.setattr(
        powershell_module,
        "_resolve_shell_executable",
        lambda shell, _search_path: expected_shell if shell == "powershell" else None,
    )

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, b"ok", b"")

    monkeypatch.setattr(powershell_module, "run_with_graceful_timeout", fake_run)

    response, status = powershell_module.PowerShellExecutor.execute_command(
        "Write-Output 'ok'",
        timeout=5,
        shell="powershell",
    )

    assert captured["args"][0] == expected_shell
    assert response == "ok"
    assert status == 0
