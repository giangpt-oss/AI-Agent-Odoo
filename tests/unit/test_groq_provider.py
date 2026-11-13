import json
import pytest
from unittest.mock import MagicMock, patch

from app.domain.gateway_context import GatewayUserContext
from app.services.natural_pipeline import NaturalPipeline
from app.services.gateway_agent_dispatcher import GatewayAgentDispatcher


@pytest.fixture
def test_context():
    return GatewayUserContext(
        user_id=1,
        telegram_user_id=999,
        permissions={"tasks.write", "calendar.events"},
        timezone="Asia/Ho_Chi_Minh",
    )


@pytest.mark.asyncio
async def test_groq_call_raw_mocked():
    pipeline = NaturalPipeline()

    mock_chat_completion = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = '{"status": "ok", "provider": "groq"}'
    mock_chat_completion.choices = [mock_choice]

    mock_openai_client = MagicMock()
    mock_openai_client.chat.completions.create.return_value = mock_chat_completion

    with patch("openai.OpenAI", return_value=mock_openai_client), \
         patch("app.services.natural_pipeline.get_settings") as mock_settings:
        mock_cfg = MagicMock()
        mock_cfg.DEFAULT_LLM_PROVIDER = "groq"
        mock_cfg.GROQ_API_KEY = "gsk_test123"
        mock_cfg.OPENAI_API_KEY = None
        mock_cfg.GEMINI_API_KEY = None
        mock_cfg.GROQ_BASE_URL = "https://api.groq.com/openai/v1"
        mock_cfg.INTENT_LLM_MODEL = "openai/gpt-oss-120b"
        mock_cfg.DEFAULT_LLM_MODEL = "openai/gpt-oss-120b"
        mock_settings.return_value = mock_cfg

        res = await pipeline._call_gemini_raw("Hello test", response_schema={"type": "object"})
        assert "groq" in res
        mock_openai_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_openai_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "openai/gpt-oss-120b"
        assert call_kwargs["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_groq_parse_action_mocked(test_context):
    dispatcher = GatewayAgentDispatcher()

    mock_chat_completion = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = '```json\n{"action": "tasks.create", "arguments": {"name": "Làm báo cáo"}}\n```'
    mock_chat_completion.choices = [mock_choice]

    mock_openai_client = MagicMock()
    mock_openai_client.chat.completions.create.return_value = mock_chat_completion

    with patch("openai.OpenAI", return_value=mock_openai_client), \
         patch("app.core.config.get_settings") as mock_settings:
        mock_cfg = MagicMock()
        mock_cfg.DEFAULT_LLM_PROVIDER = "groq"
        mock_cfg.GROQ_API_KEY = "gsk_test123"
        mock_cfg.OPENAI_API_KEY = None
        mock_cfg.GEMINI_API_KEY = None
        mock_cfg.GROQ_BASE_URL = "https://api.groq.com/openai/v1"
        mock_cfg.INTENT_LLM_MODEL = "openai/gpt-oss-120b"
        mock_cfg.DEFAULT_LLM_MODEL = "openai/gpt-oss-120b"
        mock_settings.return_value = mock_cfg

        action_data = await dispatcher._call_llm_parse_action(test_context, "Nhắc tôi làm báo cáo")
        assert action_data is not None
        assert action_data["action"] == "tasks.create"
        assert action_data["arguments"]["name"] == "Làm báo cáo"

