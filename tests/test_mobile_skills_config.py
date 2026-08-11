from windows_mcp.mobile.skills import DEFAULT_SKILL_CONFIG_ENV, load_skill_registry


def test_load_skill_registry_from_custom_config(tmp_path):
    config_path = tmp_path / "skills.toml"
    config_path.write_text(
        """
version = 1

[[skills]]
name = "capture_desktop_state"
enabled = true
description = "Custom capture skill"
examples = ["截图一下"]
triggers = ["截图"]

[[skills]]
name = "open_or_focus_app"
enabled = false
""".strip(),
        encoding="utf-8",
    )

    registry = load_skill_registry(config_path)
    views = registry.to_views()

    assert [view.name for view in views] == ["capture_desktop_state"]
    assert views[0].description == "Custom capture skill"
    assert registry.select("打开QQ") is None
    assert registry.select("截图一下") is not None


def test_load_skill_registry_from_env_path(tmp_path, monkeypatch):
    config_path = tmp_path / "skills.toml"
    config_path.write_text(
        """
version = 1

[[skills]]
name = "open_or_focus_app"
enabled = true
settings = { open_wait_seconds = 0.5, focus_wait_seconds = 0.25 }
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv(DEFAULT_SKILL_CONFIG_ENV, str(config_path))

    registry = load_skill_registry()
    skill = registry.select("打开QQ")

    assert skill is not None
    assert skill.spec.name == "open_or_focus_app"
    assert getattr(skill, "open_wait_seconds") == 0.5
    assert getattr(skill, "focus_wait_seconds") == 0.25
