"""
图片生成服务模块
封装 SiliconFlow 文生图 / 图生图 API
"""

import os
import json
import requests
import base64
from typing import Optional
from PySide6.QtCore import QObject, Signal, QThread


_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "custom_models.json")


def _load_config() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_siliconflow_config() -> dict:
    config = _load_config()
    models = config.get("models", {})
    # 查找 SiliconFlow 的图片模型配置
    for model_id, model_config in models.items():
        if model_config.get("provider") == "siliconflow" and model_config.get("model_type") == "image":
            return model_config
    raise ValueError("未配置 SiliconFlow 图片模型，请检查 config/custom_models.json")


def generate_image(
    prompt: str,
    image_url: Optional[str] = None,
    model: str = "Kwai-Kolors/Kolors",
    image_size: str = "1024x1024",
    num_inference_steps: int = 20,
    guidance_scale: float = 7.5,
    seed: Optional[int] = None,
) -> str:
    """
    调用 SiliconFlow 图片生成 API

    Args:
        prompt: 提示词
        image_url: 原图 URL（图生图），为 None 则文生图
        model: 模型名称
        image_size: 图片尺寸
        num_inference_steps: 推理步数
        guidance_scale: 引导强度
        seed: 随机种子

    Returns:
        生成图片的 URL
    """
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

    # 图生图：传入原图 URL
    if image_url:
        payload["image"] = image_url

    if seed is not None:
        payload["seed"] = seed

    resp = requests.post(url, json=payload, headers=headers, timeout=120)
    resp.raise_for_status()
    data = resp.json()

    images = data.get("data", [])
    if not images:
        raise ValueError(f"API 未返回图片: {json.dumps(data, ensure_ascii=False)}")

    return images[0].get("url", "")


def upload_image_to_base64(image_path: str) -> str:
    """将本地图片转为 data URL（用于图生图上传）"""
    ext = os.path.splitext(image_path)[1].lower().lstrip(".")
    mime_map = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "webp": "image/webp", "bmp": "image/bmp"}
    mime = mime_map.get(ext, "image/png")
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


class ImageWorker(QObject):
    """图片生成工作线程"""
    finished = Signal(str)   # 图片 URL
    error = Signal(str)

    def __init__(self, prompt: str, image_url: Optional[str] = None,
                 model: str = "Kwai-Kolors/Kolors", image_size: str = "1024x1024",
                 num_inference_steps: int = 20, guidance_scale: float = 7.5,
                 seed: Optional[int] = None, parent=None):
        super().__init__(parent)
        self.prompt = prompt
        self.image_url = image_url
        self.model = model
        self.image_size = image_size
        self.num_inference_steps = num_inference_steps
        self.guidance_scale = guidance_scale
        self.seed = seed

    def run(self):
        try:
            url = generate_image(
                prompt=self.prompt,
                image_url=self.image_url,
                model=self.model,
                image_size=self.image_size,
                num_inference_steps=self.num_inference_steps,
                guidance_scale=self.guidance_scale,
                seed=self.seed,
            )
            self.finished.emit(url)
        except Exception as e:
            self.error.emit(str(e))
