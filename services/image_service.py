"""
图片生成服务模块
支持 SiliconFlow API 与 ChatGPT Chrome 网页出图（Image 2）
"""

import os
import json
import requests
import base64
from typing import Optional, List, Dict
from PySide6.QtCore import QObject, Signal

from services.providers.chatgpt_image_service import CHATGPT_IMAGE2_MODEL_ID, generate_image_browser

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "custom_models.json",
)


def _load_config() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def list_image_models() -> List[Dict]:
    """返回可选图片模型 [{id, name, provider, model_id, use_browser}]"""
    config = _load_config()
    models = []
    for key, mc in config.get("models", {}).items():
        if mc.get("model_type") != "image":
            continue
        models.append({
            "id": key,
            "name": mc.get("name", key),
            "provider": mc.get("provider", ""),
            "model_id": mc.get("model_id", key),
            "use_browser": bool(mc.get("use_browser")) or mc.get("provider") == "chatgpt",
        })
    if not any(m["model_id"] == CHATGPT_IMAGE2_MODEL_ID for m in models):
        models.insert(0, {
            "id": "chatgpt/image2",
            "name": "ChatGPT Image 2 (Chrome)",
            "provider": "chatgpt",
            "model_id": CHATGPT_IMAGE2_MODEL_ID,
            "use_browser": True,
        })
    return models


def is_chatgpt_browser_image_model(model_id: str) -> bool:
    if not model_id:
        return False
    mid = model_id.lower()
    if mid == CHATGPT_IMAGE2_MODEL_ID:
        return True
    if "chatgpt" in mid and "image" in mid:
        return True
    for m in list_image_models():
        if m.get("model_id") == model_id and m.get("use_browser"):
            return True
    return False


def _get_siliconflow_config() -> dict:
    config = _load_config()
    for model_id, model_config in config.get("models", {}).items():
        if model_config.get("provider") == "siliconflow" and model_config.get("model_type") == "image":
            return model_config
    raise ValueError("未配置 SiliconFlow 图片模型，请检查 config/custom_models.json")


def _generate_siliconflow_image(
    prompt: str,
    image_url: Optional[str] = None,
    model: str = "Kwai-Kolors/Kolors",
    image_size: str = "1024x1024",
    num_inference_steps: int = 20,
    guidance_scale: float = 7.5,
    seed: Optional[int] = None,
) -> str:
    sf = _get_siliconflow_config()
    base_url = sf["base_url"].rstrip("/")
    api_key = sf["api_key"]

    url = f"{base_url}/images/generations"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model,
        "prompt": prompt,
        "image_size": image_size,
        "num_inference_steps": num_inference_steps,
        "guidance_scale": guidance_scale,
    }
    if image_url:
        payload["image"] = image_url
    if seed is not None:
        payload["seed"] = seed

    resp = requests.post(url, json=payload, headers=headers, timeout=(30, 120))
    resp.raise_for_status()
    data = resp.json()
    images = data.get("data", [])
    if not images:
        raise ValueError(f"API 未返回图片: {json.dumps(data, ensure_ascii=False)}")
    return images[0].get("url", "")


def generate_image(
    prompt: str,
    image_url: Optional[str] = None,
    model: str = "Kwai-Kolors/Kolors",
    image_size: str = "1024x1024",
    num_inference_steps: int = 20,
    guidance_scale: float = 7.5,
    seed: Optional[int] = None,
    status_callback=None,
) -> str:
    """
    生成图片。ChatGPT Image 2 返回本地文件路径；Kolors 返回远程 URL。
    """
    if is_chatgpt_browser_image_model(model):
        ref_path = None
        if image_url and image_url.startswith("data:"):
            import tempfile
            header, b64data = image_url.split(",", 1)
            ext = ".png"
            if "jpeg" in header:
                ext = ".jpg"
            fd, ref_path = tempfile.mkstemp(suffix=ext)
            os.close(fd)
            with open(ref_path, "wb") as f:
                f.write(base64.b64decode(b64data))
        elif image_url and os.path.isfile(image_url):
            ref_path = image_url
        return generate_image_browser(
            prompt=prompt,
            ref_image_path=ref_path,
            status_callback=status_callback,
        )

    return _generate_siliconflow_image(
        prompt=prompt,
        image_url=image_url,
        model=model,
        image_size=image_size,
        num_inference_steps=num_inference_steps,
        guidance_scale=guidance_scale,
        seed=seed,
    )


def upload_image_to_base64(image_path: str) -> str:
    """将本地图片转为 data URL（用于图生图上传）"""
    ext = os.path.splitext(image_path)[1].lower().lstrip(".")
    mime_map = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "webp": "image/webp", "bmp": "image/bmp",
    }
    mime = mime_map.get(ext, "image/png")
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


class ImageWorker(QObject):
    """图片生成工作线程"""
    finished = Signal(str)
    error = Signal(str)
    status_log = Signal(str)

    def __init__(
        self,
        prompt: str,
        image_url: Optional[str] = None,
        model: str = "Kwai-Kolors/Kolors",
        image_size: str = "1024x1024",
        num_inference_steps: int = 20,
        guidance_scale: float = 7.5,
        seed: Optional[int] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.prompt = prompt
        self.image_url = image_url
        self.model = model
        self.image_size = image_size
        self.num_inference_steps = num_inference_steps
        self.guidance_scale = guidance_scale
        self.seed = seed

    def _status(self, msg: str):
        if msg:
            self.status_log.emit(msg + "\n")

    def run(self):
        try:
            result = generate_image(
                prompt=self.prompt,
                image_url=self.image_url,
                model=self.model,
                image_size=self.image_size,
                num_inference_steps=self.num_inference_steps,
                guidance_scale=self.guidance_scale,
                seed=self.seed,
                status_callback=self._status,
            )
            self.finished.emit(result)
        except Exception as e:
            msg = str(e).strip()
            if not msg:
                msg = f"{type(e).__name__}: 未知错误，请重试"
            self.error.emit(msg)
