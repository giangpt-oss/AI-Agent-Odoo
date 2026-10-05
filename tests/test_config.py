"""Verification test for project setup and configuration loading."""
from app.core.config import get_settings


def test_settings_load():
    settings = get_settings()
    assert settings.APP_NAME == "Enterprise-AI-Agent"
    assert settings.PORT == 8000
    assert settings.DEFAULT_LLM_PROVIDER in ["gemini", "openai"]
    assert settings.EMERGENCY_KILL_SWITCH is False
    print("[OK] Config loaded successfully:", settings.APP_NAME, "| Mode:", settings.APP_ENV)


if __name__ == "__main__":
    test_settings_load()
