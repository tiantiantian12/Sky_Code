"""
MiniMax Agent Provider - Integrates MiniMax into the LLM Agent app as a custom model.

This module provides a drop-in replacement for api_service functions when
using MiniMax Agent as the backend. It wraps the MiniMax adapter into the
same interface used by the existing OpenAI-compatible providers.

Usage:
  1. First run capture_with_response.py to get cookies/token
  2. Configure minimax_config.json with captured credentials
  3. Select "MiniMax Agent" as the model in the app
"""

import json
import os
import time
from typing import Optional, List, Dict, Generator

from services.providers.minimax_adapter import MiniMaxAdapter

_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config")
_MINIMAX_CONFIG_PATH = os.path.join(_CONFIG_DIR, "minimax_config.json")


def load_minimax_config() -> dict:
    """Load MiniMax configuration"""
    if os.path.exists(_MINIMAX_CONFIG_PATH):
        with open(_MINIMAX_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_minimax_config(config: dict):
    """Save MiniMax configuration"""
    os.makedirs(_CONFIG_DIR, exist_ok=True)
    with open(_MINIMAX_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def get_adapter() -> MiniMaxAdapter:
    """Get configured MiniMax adapter instance"""
    config = load_minimax_config()

    cookie_str = config.get("cookie_str", "")
    token = config.get("token", "")
    bot_id = config.get("bot_id", "")

    # Also try loading token from captured file
    if not token:
        token_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "analyze_minimax", "captured_token.txt"
        )
        if os.path.exists(token_file):
            with open(token_file, "r", encoding="utf-8") as f:
                token = f.read().strip()

    # Also try loading cookies from captured file
    if not cookie_str:
        import glob
        cookie_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "analyze_minimax"
        )
        cookie_files = sorted(glob.glob(os.path.join(cookie_dir, "captured_cookies_*.txt")), reverse=True)
        if cookie_files:
            with open(cookie_files[0], "r", encoding="utf-8") as f:
                cookie_str = f.read().strip()

    return MiniMaxAdapter(
        cookie_str=cookie_str or None,
        token=token or None,
        bot_id=bot_id or None,
    )


def minimax_chat_completion(
    messages: List[Dict[str, str]],
    model: str = "minimax-agent",
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> str:
    """
    Non-streaming chat completion via MiniMax Agent.
    Compatible interface with api_service.chat_completion()
    """
    adapter = get_adapter()
    result = adapter.chat_completion(
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=False,
    )
    choices = result.get("choices", [])
    if choices:
        return choices[0].get("message", {}).get("content", "")
    return ""


def minimax_chat_stream(
    messages: List[Dict[str, str]],
    model: str = "minimax-agent",
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> Generator[str, None, None]:
    """
    Streaming chat completion via MiniMax Agent.
    Yields text chunks compatible with api_service.chat_completion_stream()
    """
    adapter = get_adapter()
    for chunk in adapter.chat_completion(
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
    ):
        # Parse SSE chunk
        chunk = chunk.strip()
        if not chunk or not chunk.startswith("data: "):
            continue
        data_str = chunk[6:].strip()
        if data_str == "[DONE]":
            return
        try:
            data = json.loads(data_str)
            content = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
            if content:
                yield content
        except (json.JSONDecodeError, IndexError, KeyError):
            continue


def test_minimax_connection() -> dict:
    """Test connection to MiniMax Agent API"""
    adapter = get_adapter()
    return adapter.test_connection()


def get_minimax_bot_list() -> List[Dict]:
    """Get list of available bots"""
    adapter = get_adapter()
    return adapter.list_bots()


def get_minimax_model_list() -> List[Dict]:
    """Get list of available models"""
    adapter = get_adapter()
    return adapter.list_models()
