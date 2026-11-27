"""Reusable LLM client with connection pooling, automatic failover (429/503/timeout), and token/latency telemetry."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Optional

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class LLMCallMetrics:
    provider: str = ""
    model: str = ""
    latency_ms: float = 0.0
    prompt_tokens: int = 0
    output_tokens: int = 0
    failover_used: bool = False


last_llm_metrics: ContextVar[Optional[LLMCallMetrics]] = ContextVar("last_llm_metrics", default=None)

_client_lock = threading.Lock()
_gemini_clients: dict[tuple[str, int], Any] = {}
_openai_clients: dict[tuple[str, str, int], Any] = {}


def _get_gemini_client(api_key: str, timeout_ms: int):
    key = (api_key, timeout_ms)
    with _client_lock:
        client = _gemini_clients.get(key)
        if client is None:
            from google import genai
            from google.genai import types

            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    timeout=timeout_ms,
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
            if len(_gemini_clients) > 16:
                _gemini_clients.clear()
            _gemini_clients[key] = client
        return client


def _get_openai_client(api_key: str, base_url: str, timeout_s: float):
    key = (api_key, base_url, int(round(timeout_s)))
    with _client_lock:
        client = _openai_clients.get(key)
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_s)
            if len(_openai_clients) > 16:
                _openai_clients.clear()
            _openai_clients[key] = client
        return client


def _is_failover_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return True
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code in (429, 500, 502, 503, 504):
        return True
    msg = str(exc).upper()
    return any(tok in msg for tok in ("429", "RESOURCE_EXHAUSTED", "RATE_LIMIT", "503", "UNAVAILABLE", "TIMEOUT"))


def _available_providers(settings) -> list[str]:
    has_gemini = isinstance(getattr(settings, "GEMINI_API_KEY", None), str) and bool(settings.GEMINI_API_KEY.strip())
    has_groq = isinstance(getattr(settings, "GROQ_API_KEY", None), str) and bool(settings.GROQ_API_KEY.strip())
    has_openai = isinstance(getattr(settings, "OPENAI_API_KEY", None), str) and bool(settings.OPENAI_API_KEY.strip())

    order: list[str] = []
    pref = getattr(settings, "DEFAULT_LLM_PROVIDER", "gemini")
    if pref == "groq" and has_groq:
        order.append("groq")
    elif pref == "openai" and has_openai:
        order.append("openai")
    elif has_gemini:
        order.append("gemini")

    for p, ok in (("gemini", has_gemini), ("groq", has_groq), ("openai", has_openai)):
        if ok and p not in order:
            order.append(p)
    return order


_GEMINI_FSA_STRIP_KEYS = frozenset({
    "minLength", "maxLength", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "pattern",
    "minItems", "maxItems", "title", "format",
})


def _simplify_gemini_schema(obj: Any) -> Any:
    """Strip length/range/regex bounds that explode Gemini's structured-output FSA;
    Pydantic still validates all exact constraints on the returned JSON."""
    if isinstance(obj, list):
        return [_simplify_gemini_schema(item) for item in obj]
    if isinstance(obj, dict):
        return {
            k: _simplify_gemini_schema(v)
            for k, v in obj.items()
            if k not in _GEMINI_FSA_STRIP_KEYS
        }
    return obj


def _is_schema_too_complex_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "too many states" in msg or ("invalid_argument" in msg and "schema" in msg)


def _is_thinking_level_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "thinking level" in msg and ("not supported" in msg or "invalid_argument" in msg)


def build_gemini_thinking_config(model: str, types_module: Any, *, fallback: bool = False) -> Any:
    """Build a model-compatible ThinkingConfig for Gemini 3.x models.

    Gemini 3.8+ models reject thinking_level='MINIMAL' with 400 INVALID_ARGUMENT,
    so we default to 'LOW' for 3.6+ non-lite models and omit thinking_config on fallback.
    """
    if fallback:
        return None
    lower = (model or "").lower()
    if not lower.startswith("gemini-3"):
        return None
    if "lite" in lower or lower.startswith(("gemini-3.0", "gemini-3.1", "gemini-3.5")):
        return types_module.ThinkingConfig(thinking_level="MINIMAL")
    return types_module.ThinkingConfig(thinking_level="LOW")


async def _invoke_provider(
    provider: str,
    settings,
    prompt: str,
    response_schema: Any,
    model_override: Optional[str],
    timeout_s: float,
) -> tuple[str, LLMCallMetrics]:
    t0 = time.perf_counter()

    if provider in ("groq", "openai"):
        api_key = settings.GROQ_API_KEY if provider == "groq" else settings.OPENAI_API_KEY
        base_url = getattr(settings, "GROQ_BASE_URL", "https://api.groq.com/openai/v1") if provider == "groq" else "https://api.openai.com/v1"
        model = model_override or settings.INTENT_LLM_MODEL or settings.DEFAULT_LLM_MODEL or "openai/gpt-oss-120b"
        if "gemini" in model.lower():
            model = "openai/gpt-oss-120b" if provider == "groq" else "gpt-4o-mini"

        def _sync_oai():
            client = _get_openai_client(api_key, base_url, timeout_s)
            content_prompt = prompt
            if response_schema:
                content_prompt += (
                    "\n\nTrả về duy nhất một JSON object tuân thủ JSON Schema sau. "
                    "Chỉ sử dụng các trường và giá trị phù hợp với schema của yêu cầu này.\n"
                    + json.dumps(response_schema, ensure_ascii=False)
                )
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": [{"role": "user", "content": content_prompt}],
                "temperature": 0.1,
            }
            if response_schema:
                kwargs["response_format"] = {"type": "json_object"}
            resp = client.chat.completions.create(**kwargs)
            usage = getattr(resp, "usage", None)
            return (
                resp.choices[0].message.content or "",
                int(getattr(usage, "prompt_tokens", 0) or 0),
                int(getattr(usage, "completion_tokens", 0) or 0),
            )

        text, p_tok, o_tok = await asyncio.wait_for(asyncio.to_thread(_sync_oai), timeout=timeout_s)
        metrics = LLMCallMetrics(
            provider=provider,
            model=model,
            latency_ms=(time.perf_counter() - t0) * 1000,
            prompt_tokens=p_tok,
            output_tokens=o_tok,
        )
        return text, metrics

    # Gemini provider (minimum allowed HTTP deadline is 10s)
    api_key = settings.GEMINI_API_KEY
    model = model_override or settings.INTENT_LLM_MODEL or settings.DEFAULT_LLM_MODEL or "gemini-3.5-flash-lite"
    http_timeout_ms = max(10000, int(timeout_s * 1000))
    gemini_schema = _simplify_gemini_schema(response_schema) if response_schema else None

    def _sync_gemini(use_schema: bool = True, fallback_thinking: bool = False):
        from google.genai import types

        client = _get_gemini_client(api_key, http_timeout_ms)
        contents = prompt
        if not use_schema and response_schema:
            contents = (
                prompt
                + "\n\nTrả về duy nhất một JSON object hợp lệ tuân thủ JSON Schema sau:\n"
                + json.dumps(gemini_schema, ensure_ascii=False)
            )
        thinking_cfg = build_gemini_thinking_config(model, types, fallback=fallback_thinking)
        resp = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
                response_json_schema=gemini_schema if use_schema else None,
                max_output_tokens=2048 if response_schema else None,
                thinking_config=thinking_cfg,
            ),
        )
        usage = getattr(resp, "usage_metadata", None)
        return (
            resp.text or "",
            int(getattr(usage, "prompt_token_count", 0) or 0),
            int(getattr(usage, "candidates_token_count", 0) or 0),
        )

    try:
        text, p_tok, o_tok = await asyncio.wait_for(asyncio.to_thread(_sync_gemini, True, False), timeout=timeout_s)
    except Exception as exc:
        if _is_thinking_level_error(exc):
            logger.warning("gemini_thinking_fallback model=%s retrying_without_thinking_config", model)
            text, p_tok, o_tok = await asyncio.wait_for(asyncio.to_thread(_sync_gemini, True, True), timeout=timeout_s)
        elif gemini_schema is not None and _is_schema_too_complex_error(exc):
            logger.warning("gemini_schema_fallback reason=too_many_states retrying_with_prompt_schema")
            text, p_tok, o_tok = await asyncio.wait_for(asyncio.to_thread(_sync_gemini, False, False), timeout=timeout_s)
        else:
            raise
    metrics = LLMCallMetrics(
        provider="gemini",
        model=model,
        latency_ms=(time.perf_counter() - t0) * 1000,
        prompt_tokens=p_tok,
        output_tokens=o_tok,
    )
    return text, metrics


async def generate_json(
    prompt: str,
    response_schema: Any = None,
    *,
    model_override: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
    settings_override: Any = None,
) -> str:
    """Generate structured JSON using primary LLM with automatic single failover on 429/503/timeout."""
    settings = settings_override if settings_override is not None else get_settings()
    providers = _available_providers(settings)
    if not providers:
        raise ValueError("No LLM API key configured (GEMINI_API_KEY / GROQ_API_KEY / OPENAI_API_KEY)")

    eff_timeout = float(timeout_seconds) if timeout_seconds is not None else (28.0 if response_schema else 20.0)
    eff_timeout = max(1.0, min(eff_timeout, 28.0))
    deadline = time.time() + eff_timeout

    last_exc: Optional[Exception] = None
    has_failover = len(providers) > 1
    for idx, provider in enumerate(providers[:2]):
        remaining = deadline - time.time()
        if idx > 0 and remaining < 2.5:
            break
        attempt_timeout = max(2.0, remaining)
        if idx == 0 and has_failover and eff_timeout >= 16.0:
            # Reserve time for secondary provider failover if primary hangs/times out
            attempt_timeout = min(attempt_timeout, max(10.0, eff_timeout - 7.0))
        try:
            text, metrics = await _invoke_provider(
                provider,
                settings,
                prompt,
                response_schema,
                model_override if idx == 0 else None,
                attempt_timeout,
            )
            metrics.failover_used = idx > 0
            last_llm_metrics.set(metrics)
            logger.info(
                "llm_call provider=%s model=%s latency_ms=%.1f prompt_tok=%d out_tok=%d failover=%s",
                metrics.provider,
                metrics.model,
                metrics.latency_ms,
                metrics.prompt_tokens,
                metrics.output_tokens,
                metrics.failover_used,
            )
            return text
        except Exception as exc:
            last_exc = exc
            if idx == 0 and len(providers) > 1 and _is_failover_error(exc):
                logger.warning(
                    "llm_failover from=%s to=%s reason=%s",
                    provider,
                    providers[1],
                    type(exc).__name__,
                )
                continue
            raise
    assert last_exc is not None
    raise last_exc
