# MiniMax Agent API Integration Guide

## Overview

This guide explains how to connect the MiniMax Agent platform (https://agent.minimaxi.com/) to your LLM Agent app by capturing browser API requests and wrapping them as a custom model provider.

## Architecture

```
Browser (manual login) -> Capture cookies/token -> MiniMaxAdapter -> OpenAI-compatible API -> LLM Agent App
```

## Files Created

| File | Purpose |
|------|---------|
| `analyze_minimax/capture_with_response.py` | Selenium-based browser capture tool |
| `services/minimax_adapter.py` | Core adapter: MiniMax API -> OpenAI format |
| `services/minimax_provider.py` | Provider integration for the app |
| `config/minimax_config.json` | Configuration file |
| `test/test_minimax_adapter.py` | Test script |

## Step-by-Step Setup

### Step 1: Capture Browser Data

Run the capture tool:

```bash
python analyze_minimax/capture_with_response.py
```

This will:
1. Open a Chrome browser to agent.minimaxi.com
2. Wait for you to login and send a test message
3. Capture all API requests, responses, cookies, and JWT token

### Step 2: Configure Credentials

After capture, you will get:
- `captured_token_*.txt` - JWT token
- `captured_cookies_*.txt` - Browser cookies
- `chat_api_analysis.json` - Chat API request/response format

Update `config/minimax_config.json`:

```json
{
  "token": "eyJhbGciOiJIUzI1NiIs...",
  "cookie_str": "_gc_usr_id_cs0_d0=...; _token=...",
  "bot_id": "your_bot_id_here"
}
```

### Step 3: Test Connection

```bash
python test/test_minimax_adapter.py
```

### Step 4: Use in App

Select "MiniMax Agent" from the model dropdown in the app.

## API Endpoints Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/matrix/api/v1/chat/send_msg` | POST | Send message to agent |
| `/matrix/api/v1/chat/get_chat_detail` | GET | Get chat detail/response |
| `/matrix/api/v1/chat/continue_run_agent` | POST | Continue agent run |
| `/matrix/api/v1/chat/stop_run_agent` | POST | Stop agent run |
| `/matrix/api/v1/bot/list` | GET | List available bots |
| `/matrix/api/v1/bot/get` | GET | Get bot details |
| `/matrix/api/v1/claw/model/list` | GET | List available models |
| `/matrix/api/v1/claw/skills` | GET | List agent skills |

## Adapting to Actual Response Format

After running the capture tool, the actual response format from the chat API will be revealed. You may need to update `minimax_adapter.py`'s `_extract_response_content()` method to handle the specific format.

Common response formats to expect:
1. **JSON with reply field**: `{"data": {"reply": "response text"}}`
2. **SSE stream**: Lines of `data: {"content": "chunk"}` events
3. **Chat history**: `{"data": {"messages": [...]}}`

## Troubleshooting

- **401 Unauthorized**: Token expired, re-run capture tool
- **Connection timeout**: Check network/proxy settings
- **Empty response**: May need to update `_extract_response_content()` with actual format
- **Bot not found**: Check `bot_id` in config matches available bots
