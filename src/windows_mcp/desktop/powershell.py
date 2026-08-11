"""Static PowerShell command executor utility."""

import base64
import logging
import os
import shutil
import subprocess

from windows_mcp.desktop.utils import run_with_graceful_timeout

logger = logging.getLogger(__name__)


def _build_subprocess_env() -> tuple[dict[str, str], str]:
    """Build a subprocess environment plus a best-effort PATH search string."""
    env = os.environ.copy()
    env["NO_COLOR"] = "1"

    machine_path = ""
    user_path = ""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ) as machine_key:
            machine_path = winreg.QueryValueEx(machine_key, "PATH")[0]
            if ".EXE" not in env.get("PATHEXT", ""):
                env["PATHEXT"] = winreg.QueryValueEx(machine_key, "PATHEXT")[0]

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as user_key:
                user_path = winreg.QueryValueEx(user_key, "PATH")[0]
        except FileNotFoundError:
            user_path = ""
    except Exception:
        if ".EXE" not in env.get("PATHEXT", ""):
            env["PATHEXT"] = ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC;.CPL;.PY;.PYW"

    search_path = ";".join(filter(None, [machine_path, user_path, env.get("PATH", "")]))
    if search_path:
        env["PATH"] = search_path
    return env, search_path


def _get_shell_fallbacks(shell_name: str) -> list[str]:
    """Return well-known absolute paths for PowerShell executables."""
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    program_files = list(
        dict.fromkeys(
            filter(
                None,
                [
                    os.environ.get("ProgramFiles"),
                    os.environ.get("ProgramW6432"),
                    os.environ.get("LOCALAPPDATA"),
                ],
            )
        )
    )

    match shell_name:
        case "powershell":
            return [
                os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
                os.path.join(system_root, "SysNative", "WindowsPowerShell", "v1.0", "powershell.exe"),
            ]
        case "pwsh":
            fallbacks = []
            for base in program_files:
                fallbacks.extend(
                    [
                        os.path.join(base, "Microsoft", "WindowsApps", "pwsh.exe"),
                        os.path.join(base, "Programs", "PowerShell", "7", "pwsh.exe"),
                        os.path.join(base, "Programs", "PowerShell", "7-preview", "pwsh.exe"),
                        os.path.join(base, "Programs", "PowerShell", "6", "pwsh.exe"),
                        os.path.join(base, "PowerShell", "7", "pwsh.exe"),
                        os.path.join(base, "PowerShell", "7-preview", "pwsh.exe"),
                        os.path.join(base, "PowerShell", "6", "pwsh.exe"),
                    ]
                )
            return fallbacks
        case _:
            return []


def _resolve_shell_executable(shell: str, search_path: str) -> str | None:
    """Resolve a shell command to an absolute executable path when possible.

    On Windows, subprocess executable lookup does not reliably honor the PATH supplied
    via the child-process `env`, so we prefer resolving to an absolute path up front.
    """
    if os.path.isabs(shell):
        return shell if os.path.exists(shell) else None

    candidates = [shell]
    if not shell.lower().endswith(".exe"):
        candidates.append(f"{shell}.exe")

    for candidate in candidates:
        resolved = shutil.which(candidate, path=search_path) or shutil.which(candidate)
        if resolved:
            return resolved

    shell_name = os.path.basename(shell).lower().replace(".exe", "")
    for candidate in _get_shell_fallbacks(shell_name):
        if os.path.exists(candidate):
            return candidate

    return None


class PowerShellExecutor:
    """Static utility class for executing PowerShell commands."""

    @staticmethod
    def execute_command(
        command: str, timeout: int = 10, shell: str | None = None
    ) -> tuple[str, int]:
        try:
            # $OutputEncoding: controls how PS5.1 encodes output written to its stdout pipe.
            # Without this set to UTF-8, PS5.1 uses the system codepage and native process
            # stdout is silently lost when Python reads the pipe.
            # [Console]::OutputEncoding: controls how PS decodes bytes from native exe stdout.
            utf8_command = (
                "$OutputEncoding = [System.Text.Encoding]::UTF8; "
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                f"{command}"
            )
            encoded = base64.b64encode(utf8_command.encode("utf-16le")).decode("ascii")
            env, search_path = _build_subprocess_env()

            if shell is None:
                shell = (
                    _resolve_shell_executable("pwsh", search_path)
                    or _resolve_shell_executable("powershell", search_path)
                    or "powershell"
                )
            else:
                shell = _resolve_shell_executable(shell, search_path) or shell

            args = [shell, "-NoProfile"]
            # Only older Windows PowerShell (5.1) uses -OutputFormat Text successfully here
            shell_name = os.path.basename(shell).lower().replace(".exe", "")
            if shell_name == "powershell":
                args.extend(["-OutputFormat", "Text"])
            args.extend(["-EncodedCommand", encoded])

            result = run_with_graceful_timeout(
                args,
                stdin=subprocess.DEVNULL,  # Prevent child processes from inheriting the MCP pipe stdin
                capture_output=True,  # No errors='ignore' - let subprocess return bytes
                timeout=timeout,
                cwd=os.path.expanduser(path="~"),
                env=env,
            )
            # Handle both bytes and str output (subprocess behavior varies by environment)
            stdout = result.stdout
            stderr = result.stderr
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            return stdout or stderr, result.returncode
        except subprocess.TimeoutExpired:
            return "Command execution timed out", 1
        except Exception as e:
            return f"Command execution failed: {type(e).__name__}: {e}", 1
