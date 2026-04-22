#!/usr/bin/env python3
"""Draft engagement replies using OpenRouter API.

Space-agnostic: loads voice prompt, model selection, and example path
from comms-config.yaml. Supports prompt A/B testing and model comparison.

Features:
- Configurable model per brand (default: anthropic/claude-sonnet-4)
- Prompt versioning for continuous improvement
- Temperature/top_p control per config
- Token usage logging for cost tracking
- Retry with fallback models on failure
"""

import json
import os
import time
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import event_logger

DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-sonnet-4"
DEFAULT_FALLBACK = "google/gemini-2.5-flash-preview"


def _load_voice_template(config: dict) -> str:
    """Load voice prompt template from config."""
    voice_cfg = config.get("voice", {})
    template = voice_cfg.get("prompt_template")
    if template:
        return template

    return """You are drafting a reply tweet.

## Target tweet
Author: {author}
Tweet: {content}
URL: {url}

## Voice
- Conversational and insightful
- Under 280 characters
- No emojis, no hashtags, no links

Return ONLY the reply text."""


def _get_api_key() -> str:
    """Get OpenRouter API key from env."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        # Try Hermes env location as fallback
        hermes_env = Path.home() / ".hermes" / ".env"
        if hermes_env.exists():
            for line in hermes_env.read_text().splitlines():
                line = line.strip()
                if line.startswith("OPENROUTER_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    break
    if not key:
        raise ValueError(
            "OPENROUTER_API_KEY not found in environment or ~/.hermes/.env"
        )
    return key


def _build_system_prompt(config: dict) -> str:
    """Build system prompt from brand config."""
    brand = config.get("brand", {})
    name = brand.get("name", "Brand")
    handle = brand.get("handle", "@brand")

    base = f"""You are the social media voice for {name} ({handle}).
You draft reply tweets that are sharp, insightful, and conversational.
Never use emojis, hashtags, or links unless explicitly instructed.
Stay under 280 characters. Make ONE point well."""

    # Add brand-specific voice instructions if present
    voice_cfg = config.get("voice", {})
    extra_instructions = voice_cfg.get("system_instructions")
    if extra_instructions:
        base += f"\n\n{extra_instructions}"

    return base


def _call_openrouter(
    messages: list,
    model: str,
    api_key: str,
    temperature: float = 0.7,
    top_p: float = 0.9,
    max_tokens: int = 150,
    timeout: int = 60,
) -> dict:
    """Call OpenRouter API. Returns full response dict."""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
    }

    body = json.dumps(payload).encode()
    req = Request(OPENROUTER_URL, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("HTTP-Referer", "https://plur.ai")
    req.add_header("X-Title", "PLUR Engagement Drafting")

    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        error_body = e.read().decode()
        raise Exception(f"OpenRouter error {e.code}: {error_body}")


def draft_reply(
    conversation: dict,
    config: dict = None,
    prompt_version: str = None,
) -> dict:
    """Generate a reply draft using OpenRouter API.

    Args:
        conversation: dict with author, content, url keys
        config: comms config dict (or auto-loaded)
        prompt_version: Optional prompt variant for A/B testing

    Returns:
        Dict with keys: text, model, prompt_version, tokens_used, finish_reason
    """
    from comms_config import load_config
    if config is None:
        config = load_config()

    voice_cfg = config.get("voice", {})
    model_cfg = config.get("model", {})

    # Model selection
    model = model_cfg.get("primary", DEFAULT_MODEL)
    fallback = model_cfg.get("fallback", DEFAULT_FALLBACK)
    temperature = model_cfg.get("temperature", 0.7)
    top_p = model_cfg.get("top_p", 0.9)
    max_tokens = model_cfg.get("max_tokens", 150)

    # Prompt template
    template = _load_voice_template(config)
    user_prompt = template.format(
        author=conversation.get("author", "unknown"),
        content=conversation.get("content", ""),
        url=conversation.get("url", ""),
    )

    # If example replies path is configured, prepend instruction
    example_path = voice_cfg.get("example_replies_path")
    if example_path:
        full_example_path = DATA_DIR / example_path
        if full_example_path.exists():
            examples = full_example_path.read_text()
            user_prompt = (
                f"Study these example replies to understand the voice and rhythm:\n\n"
                f"{examples[:2000]}\n\n"
                f"---\n\n"
                f"{user_prompt}"
            )

    # Track prompt version for A/B testing
    version = prompt_version or voice_cfg.get("prompt_version", "default")

    messages = [
        {"role": "system", "content": _build_system_prompt(config)},
        {"role": "user", "content": user_prompt},
    ]

    api_key = _get_api_key()

    # Try primary model, then fallback
    used_model = model
    try:
        response = _call_openrouter(
            messages=messages,
            model=model,
            api_key=api_key,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
        )
    except Exception as primary_error:
        event_logger.log_event("error", {
            "stage": "draft",
            "model": model,
            "error": str(primary_error),
            "fallback_attempted": True,
        })
        try:
            response = _call_openrouter(
                messages=messages,
                model=fallback,
                api_key=api_key,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
            )
            used_model = fallback
        except Exception as fallback_error:
            raise Exception(
                f"Draft failed on primary ({model}): {primary_error} "
                f"and fallback ({fallback}): {fallback_error}"
            )

    # Extract response text
    choice = response.get("choices", [{}])[0]
    text = choice.get("message", {}).get("content", "").strip()
    finish_reason = choice.get("finish_reason", "unknown")

    # Extract token usage
    usage = response.get("usage", {})
    tokens_used = usage.get("total_tokens", 0)
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)

    # Enforce length
    max_len = voice_cfg.get("max_length", 280)
    if len(text) > max_len:
        for i in range(max_len - 1, 0, -1):
            if text[i] in ".!":
                text = text[: i + 1]
                break
        else:
            text = text[: max_len - 3] + "..."

    # Log for A/B testing and cost tracking
    event_logger.log_event("draft", {
        "model": used_model,
        "prompt_version": version,
        "tokens_used": tokens_used,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "finish_reason": finish_reason,
        "target_author": conversation.get("author", "unknown"),
        "chars": len(text),
    })

    return {
        "text": text,
        "model": used_model,
        "prompt_version": version,
        "tokens_used": tokens_used,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "finish_reason": finish_reason,
    }


def draft_reply_simple(conversation: dict, config: dict = None) -> str:
    """Simple interface: returns just the draft text."""
    result = draft_reply(conversation, config=config)
    return result["text"]
