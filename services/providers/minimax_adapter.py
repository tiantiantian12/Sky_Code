"""
MiniMax Agent API Adapter (based on real captured data)
Wraps the proprietary MiniMax Agent API (agent.minimaxi.com) into
an OpenAI-compatible interface.

API Flow (captured from browser):
  1. POST /archon/api/v1/agent/{agent_name}/session  -> create session
  2. POST https://agent-stream.minimaxi.com/archon/api/v1/session/{session_id}/message -> send message (SSE stream)
  3. GET  /archon/api/v1/session/{session_id} -> get session status
  4. GET  /archon/api/v1/agent -> list agents
  5. GET  /archon/api/v1/config -> list models + config

SSE Event Types:
  type 10: connection established
  type 2:  user message echo
  type 6:  assistant message chunk (has agent_message_chunk.content)
  type 3:  thinking/reasoning chunk
  type 5:  tool call
"""

import json
import time
import uuid
import requests
from typing import Optional, List, Dict, Generator, Any


class MiniMaxAdapter:
    """MiniMax Agent API -> OpenAI Compatible Adapter"""

    BASE_URL = "https://agent.minimaxi.com"
    STREAM_URL = "https://agent-stream.minimaxi.com"

    # Common query params that the web app always sends
    COMMON_PARAMS = {
        "device_platform": "web",
        "biz_id": "3",
        "app_id": "3001",
        "version_code": "22201",
        "timezone_offset": "28800",
        "sys_language": "zh",
        "lang": "zh",
        "device_id": "30156090",
        "os_name": "Windows",
        "browser_name": "Chrome",
        "browser_language": "zh-CN",
        "browser_platform": "Win32",
        "client": "web",
    }

    def __init__(
        self,
        cookie_str: Optional[str] = None,
        token: Optional[str] = None,
        agent_name: Optional[str] = None,
    ):
        self.agent_name = agent_name  # Will be auto-detected if not set
        self._session_id = None
        self._agent_list_cache = None

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": "application/json",
            "Origin": self.BASE_URL,
            "Referer": f"{self.BASE_URL}/",
        })

        if cookie_str:
            self._set_cookies(cookie_str)
        if token:
            self.session.cookies.set("_token", token, domain=".minimaxi.com")
            self.session.headers["Authorization"] = f"Bearer {token}"

    def _set_cookies(self, cookie_str: str):
        for item in cookie_str.split(";"):
            item = item.strip()
            if "=" in item:
                key, value = item.split("=", 1)
                self.session.cookies.set(key.strip(), value.strip())
                if key.strip() == "_token":
                    self.session.headers["Authorization"] = f"Bearer {value.strip()}"

    def _common_params(self) -> dict:
        params = dict(self.COMMON_PARAMS)
        params["unix"] = str(int(time.time() * 1000))
        return params

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        resp = self.session.request(method, url, timeout=(30, 120), **kwargs)
        resp.raise_for_status()
        return resp

    # ========== Agent & Model Discovery ==========

    def list_agents(self) -> List[Dict]:
        """List available agents"""
        params = self._common_params()
        resp = self._request("GET", f"{self.BASE_URL}/archon/api/v1/agent", params=params)
        data = resp.json()
        return data.get("agents", [])

    def get_config(self) -> Dict:
        """Get available models and config"""
        params = self._common_params()
        params["region"] = "cn"
        resp = self._request("GET", f"{self.BASE_URL}/archon/api/v1/config", params=params)
        return resp.json()

    def list_models(self) -> List[Dict]:
        """List available models"""
        config = self.get_config()
        return config.get("models", [])

    def get_user_info(self) -> Dict:
        """Get current user info"""
        params = self._common_params()
        resp = self._request("GET", f"{self.BASE_URL}/v1/api/user/info", params=params)
        return resp.json()

    def _ensure_agent(self) -> str:
        """Ensure we have an agent_name, auto-detect if needed"""
        if self.agent_name:
            return self.agent_name
        agents = self.list_agents()
        if agents:
            # Prefer agents with agent_role == "mavis" or the first one
            for a in agents:
                if a.get("agent_role") == "mavis":
                    self.agent_name = a["name"]
                    return self.agent_name
            self.agent_name = agents[0]["name"]
            return self.agent_name
        raise ValueError("No agents found")

    # ========== Session Management ==========

    def create_session(self, model: str = "minimax/MiniMax-M3") -> str:
        """
        Create a new chat session.
        Returns session_id.
        """
        agent = self._ensure_agent()
        params = self._common_params()
        url = f"{self.BASE_URL}/archon/api/v1/agent/{agent}/session"
        resp = self._request("POST", url, params=params, json={"model": model})
        data = resp.json()
        self._session_id = data["session_id"]
        return self._session_id

    def get_session(self, session_id: Optional[str] = None) -> Dict:
        """Get session details"""
        sid = session_id or self._session_id
        if not sid:
            raise ValueError("No session_id")
        params = self._common_params()
        resp = self._request("GET", f"{self.BASE_URL}/archon/api/v1/session/{sid}", params=params)
        return resp.json()

    # ========== Chat / Message ==========

    def send_message(
        self,
        content: str,
        session_id: Optional[str] = None,
        model_id: str = "MiniMax-M3",
        variant: str = "thinking",
    ) -> Generator[Dict, None, None]:
        """
        Send a message and yield SSE events.
        Each yielded item is a parsed JSON dict from the SSE stream.
        """
        sid = session_id or self._session_id
        if not sid:
            sid = self.create_session(model=f"minimax/{model_id}")

        params = self._common_params()
        url = f"{self.STREAM_URL}/archon/api/v1/session/{sid}/message"

        body = {
            "content": content,
            "model": {
                "provider_id": "minimax",
                "model_id": model_id,
                "variant": variant,
            },
            "turn_id": str(uuid.uuid4()),
            "enable_team": True,
            "worktreeMode": False,
        }

        resp = self.session.post(url, params=params, json=body, stream=True, timeout=120)
        resp.raise_for_status()

        buffer = ""
        for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
            if not chunk:
                continue
            buffer += chunk
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    data_str = line[5:]
                    try:
                        event = json.loads(data_str)
                        yield event
                    except json.JSONDecodeError:
                        continue

    def get_reply(
        self,
        content: str,
        session_id: Optional[str] = None,
        model_id: str = "MiniMax-M3",
        variant: str = "thinking",
    ) -> str:
        """
        Send a message and return the complete reply text.
        Collects all type=6 (content) chunks.
        """
        parts = []
        for event in self.send_message(content, session_id, model_id, variant):
            etype = event.get("type")
            if etype == 6:
                chunk = event.get("agent_message_chunk", {})
                text = chunk.get("content", "")
                if text:
                    parts.append(text)
        return "".join(parts)

    # ========== OpenAI-Compatible Interface ==========

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: str = "minimax-agent",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stream: bool = False,
    ) -> Any:
        """
        OpenAI-compatible chat completion.
        """
        # Extract user message
        user_message = ""
        for msg in reversed(messages):
            if msg["role"] == "user":
                user_message = msg["content"]
                break
        if not user_message:
            raise ValueError("No user message found")

        # Extract system prompt
        system_prompt = ""
        for msg in messages:
            if msg["role"] == "system":
                system_prompt = msg["content"]
                break
        if system_prompt:
            user_message = f"[System: {system_prompt}]\n\n{user_message}"

        # Parse model name to get model_id
        model_id = "MiniMax-M3"
        if model and model != "minimax-agent":
            # e.g. "minimax/MiniMax-M3" or "MiniMax-M2.7"
            parts = model.split("/")
            model_id = parts[-1]

        if stream:
            return self._stream_completion(user_message, model, model_id)
        else:
            return self._sync_completion(user_message, model, model_id)

    def _sync_completion(self, message: str, model: str, model_id: str) -> Dict:
        content = self.get_reply(message, model_id=model_id)
        return {
            "id": f"chatcmpl-minimax-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": -1, "completion_tokens": -1, "total_tokens": -1},
        }

    def _stream_completion(self, message: str, model: str, model_id: str) -> Generator[str, None, None]:
        for event in self.send_message(message, model_id=model_id):
            etype = event.get("type")
            if etype == 6:
                chunk = event.get("agent_message_chunk", {})
                text = chunk.get("content", "")
                if text:
                    chunk_data = {
                        "id": f"chatcmpl-minimax-{int(time.time())}",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"

        # Final chunk
        final = {
            "id": f"chatcmpl-minimax-{int(time.time())}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(final)}\n\n"
        yield "data: [DONE]\n\n"

    # ========== Connection Test ==========

    def test_connection(self) -> Dict:
        result = {
            "status": "unknown",
            "base_url": self.BASE_URL,
            "has_token": bool(self.session.headers.get("Authorization")),
            "has_cookies": bool(self.session.cookies),
            "agent_name": self.agent_name,
        }
        try:
            agents = self.list_agents()
            result["status"] = "connected"
            result["agent_count"] = len(agents)
            result["agents"] = [
                {"name": a.get("name", ""), "display": a.get("display_name", ""), "role": a.get("agent_role", "")}
                for a in agents[:5]
            ]
            models = self.list_models()
            result["models"] = [
                {"id": m.get("model_id", ""), "display": m.get("display_name", "")}
                for m in models[:10]
            ]
        except requests.exceptions.HTTPError as e:
            result["status"] = "auth_error"
            result["error"] = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
        except Exception as e:
            result["status"] = "connection_error"
            result["error"] = str(e)
        return result
