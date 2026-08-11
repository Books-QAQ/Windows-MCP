from windows_mcp.mobile.tools import DesktopAutomationTools


class FakeDesktop:
    def __init__(self, running: bool) -> None:
        self.running = running
        self.calls: list[tuple[str, str]] = []

    def is_app_running(self, name: str) -> bool:
        return self.running

    def app(self, mode: str, name: str, loc, size):
        self.calls.append((mode, name))
        if mode == "switch":
            return f"Switched to {name} window."
        if mode == "launch":
            return f"{name} launched."
        raise AssertionError(f"unexpected mode: {mode}")


def test_open_app_switches_when_window_already_exists():
    tools = DesktopAutomationTools(FakeDesktop(running=True))

    result = tools.open_app(name="QQ")

    assert result == "Switched to QQ window."
    assert tools.desktop.calls == [("switch", "QQ")]


def test_open_app_launches_when_window_does_not_exist():
    tools = DesktopAutomationTools(FakeDesktop(running=False))

    result = tools.open_app(name="QQ")

    assert result == "QQ launched."
    assert tools.desktop.calls == [("launch", "QQ")]
