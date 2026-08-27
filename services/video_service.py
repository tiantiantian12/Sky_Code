"""
视频生成服务模块
支持 Agnes Video API 文生视频 / 图生视频

增强功能：
  - 自动提示词增强：防止视频变形，增加人物一致性、场景连贯性与逻辑性
  - 时长参数映射：不同时长对应不同的 num_frames / frame_rate / 推荐分辨率
"""

import os
import json
import time
import requests
from typing import Optional, List, Dict, Tuple
from PySide6.QtCore import QObject, Signal

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "custom_models.json",
)

# 轮询间隔（秒）
_POLL_INTERVAL = 5
# 最大轮询次数
_MAX_POLL_ATTEMPTS = 120

# ── 时长 → 参数映射 ──────────────────────────────────────────
# 不同视频时长对应的参数不一样：
#   约 3 秒  → num_frames: 81,  frame_rate: 24（适合短视频、动效片段）
#   约 5 秒  → num_frames: 121, frame_rate: 24（默认，适合一般场景）
#   约 10 秒 → num_frames: 241, frame_rate: 24（适合较长的叙事片段）
#   约 18 秒 → num_frames: 441, frame_rate: 24（最长，适合完整故事段落）
# 每种时长还附带推荐分辨率（宽x高），可根据实际需求覆盖。
DURATION_PARAMS: Dict[str, Tuple[int, int, int, int]] = {
    # 时长标签: (num_frames, frame_rate, width, height)
    "约3秒":  (81,  24, 768, 1280),   # 竖屏，适合手机短视频
    "约5秒":  (121, 24, 1152, 768),   # 横屏 3:2，通用默认
    "约10秒": (241, 24, 1152, 768),   # 横屏 3:2
    "约18秒": (441, 24, 1152, 768),   # 横屏 3:2
}


def get_duration_params(duration_label: str) -> Tuple[int, int, int, int]:
    """根据时长标签返回 (num_frames, frame_rate, width, height)

    Args:
        duration_label: 时长标签，如 "约3秒"、"约5秒"、"约10秒"、"约18秒"

    Returns:
        (num_frames, frame_rate, width, height)
    """
    return DURATION_PARAMS.get(duration_label, (121, 24, 1152, 768))


# ── 提示词增强 ────────────────────────────────────────────────
# 以下后缀会自动追加到用户提示词末尾，用于提升视频质量：
#   - 人物一致性：同一人物在整段视频中外观、服装、体型保持不变
#   - 场景连贯性：场景过渡自然，不出现跳切或突变
#   - 逻辑性：动作和事件有因果关联，符合物理规律
#   - 防变形：避免面部扭曲、肢体畸变、物体闪烁
_VIDEO_PROMPT_SUFFIX = (
    "\n\n【质量要求】"
    "\n1. 人物一致性：同一人物在整个视频中必须保持相同的外貌特征（面部、发型、体型、服装），不得发生变化或变形。"
    "\n2. 场景连贯性：场景过渡自然流畅，背景、光照、天气条件保持连续一致，不得出现跳切或突变。"
    "\n3. 动作逻辑性：人物动作和物体运动符合物理规律，有合理的因果关系，不得出现不自然的瞬移或变形。"
    "\n4. 画面稳定性：避免画面抖动、闪烁、撕裂，保持稳定的镜头运动和构图。"
    "\n5. 细节保真度：面部表情自然，手指和肢体形态正确，纹理和材质保持一致，不得出现扭曲或模糊。"
    "\n6. 时间连贯性：视频前后帧之间在颜色、亮度、运动方向上保持一致，不得出现颜色跳变或方向反转。"
)

# 图生视频额外提示（强调与输入图片保持一致）
_IMAGE_VIDEO_PROMPT_SUFFIX = (
    "\n\n【图生视频额外要求】"
    "\n7. 首帧一致性：视频首帧必须与输入参考图片在构图、人物外貌、场景布局上高度一致。"
    "\n8. 自然延展：从参考图片状态出发，动作和场景变化自然合理，不得出现突兀的跳变。"
)


def _enhance_prompt(prompt: str, is_image_mode: bool = False) -> str:
    """增强用户提示词，追加视频质量与一致性要求。

    在用户原始提示词后自动追加一段标准化的质量要求，
    用于防止视频变形、增加人物一致性与场景连贯性。

    Args:
        prompt: 用户原始提示词
        is_image_mode: 是否为图生视频模式

    Returns:
        增强后的完整提示词
    """
    if not prompt:
        return prompt
    enhanced = prompt.strip() + _VIDEO_PROMPT_SUFFIX
    if is_image_mode:
        enhanced += _IMAGE_VIDEO_PROMPT_SUFFIX
    return enhanced


def _load_config() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def list_video_models() -> List[Dict]:
    """返回可选视频模型 [{id, name, provider, model_id}]"""
    config = _load_config()
    models = []
    for key, mc in config.get("models", {}).items():
        if mc.get("model_type") != "video":
            continue
        models.append({
            "id": key,
            "name": mc.get("name", key),
            "provider": mc.get("provider", ""),
            "model_id": mc.get("model_id", key),
        })
    return models


def _get_agnes_video_config() -> dict:
    """获取第一个 model_type=video 的配置"""
    config = _load_config()
    for model_id, model_config in config.get("models", {}).items():
        if model_config.get("model_type") == "video":
            return model_config
    raise ValueError("未配置视频生成模型，请检查 config/custom_models.json")


def create_video_task(
    prompt: str,
    height: int = 768,
    width: int = 1152,
    num_frames: int = 121,
    frame_rate: int = 24,
    image: Optional[str] = None,
) -> Dict:
    """
    创建视频生成任务（支持文生视频和图生视频）

    参数:
        prompt:     提示词（必填）
        image:      输入图片 URL 或本地路径。提供则进行图生视频，否则为文生视频

    返回:
        {
            "task_id": "task_xxx",
            "video_id": "video_xxx",
            "status": "queued",
            ...
        }
    """
    cfg = _get_agnes_video_config()
    base_url = cfg["base_url"].rstrip("/")
    api_key = cfg["api_key"]
    model = cfg.get("model_id", "agnes-video-v2.0")

    # 自动增强提示词：追加人物一致性、场景连贯性等质量要求
    enhanced_prompt = _enhance_prompt(prompt, is_image_mode=bool(image))

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "prompt": enhanced_prompt,
        "height": height,
        "width": width,
        "num_frames": num_frames,
        "frame_rate": frame_rate,
    }

    # 图生视频：附加 image 字段（自动将本地路径转为 base64 data URL）
    if image:
        payload["image"] = _prepare_image_payload(image)

    resp = requests.post(
        f"{base_url}/v1/videos",
        json=payload,
        headers=headers,
        timeout=(10, 30),
    )
    resp.raise_for_status()
    data = resp.json()
    return data


def _prepare_image_payload(image: str) -> str:
    """将图片输入转为 API 可接受的格式

    若已是 http URL 或 data: URL，直接返回；
    若是本地文件路径，转为 base64 data URL
    """
    if image.startswith(("http://", "https://", "data:")):
        return image
    # 本地文件 → base64 data URL
    import base64
    import mimetypes
    mime, _ = mimetypes.guess_type(image)
    mime = mime or "image/png"
    with open(image, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def get_video_result(video_id: str) -> Dict:
    """
    查询视频生成结果

    返回:
        {
            "video_id": "video_xxx",
            "status": "completed" | "in_progress" | "queued" | "failed",
            "progress": 0-100,
            "url": "https://...mp4",  # 仅在 completed 时可用
            "error": None | {...},
            ...
        }
    """
    cfg = _get_agnes_video_config()
    api_key = cfg["api_key"]

    headers = {
        "Authorization": f"Bearer {api_key}",
    }
    params = {
        "video_id": video_id,
    }

    resp = requests.get(
        f"{cfg['base_url'].rstrip('/')}/agnesapi",
        headers=headers,
        params=params,
        timeout=(10, 30),
    )
    resp.raise_for_status()
    data = resp.json()
    return data


def generate_video(
    prompt: str,
    height: int = 768,
    width: int = 1152,
    num_frames: int = 121,
    frame_rate: int = 24,
    image: Optional[str] = None,
    status_callback=None,
) -> str:
    """
    生成视频（阻塞式，会轮询直到完成或失败）

    参数:
        image:  输入图片 URL 或本地路径（提供则为图生视频）

    返回:
        成功时返回视频 URL (str)
        失败时抛出异常
    """
    # 步骤1: 创建任务
    if status_callback:
        mode = "图生视频" if image else "文生视频"
        status_callback(f"正在创建{mode}任务...")

    task_data = create_video_task(
        prompt=prompt,
        height=height,
        width=width,
        num_frames=num_frames,
        frame_rate=frame_rate,
        image=image,
    )

    video_id = task_data.get("video_id")
    if not video_id:
        raise ValueError(f"创建任务失败，未获取到 video_id: {json.dumps(task_data, ensure_ascii=False)}")

    seconds = task_data.get("seconds", "?")
    size = task_data.get("size", "?")

    if status_callback:
        status_callback(f"任务已创建 (video_id: {video_id}, 时长: {seconds}s, 分辨率: {size})")
        status_callback(f"状态: {task_data.get('status', 'unknown')}")

    # 步骤2: 轮询直到完成
    for attempt in range(1, _MAX_POLL_ATTEMPTS + 1):
        result = get_video_result(video_id)
        status = result.get("status", "")
        progress = result.get("progress", 0)

        if status_callback:
            status_callback(f"[{attempt}/{_MAX_POLL_ATTEMPTS}] 状态: {status}, 进度: {progress}%")

        if status == "completed":
            url = result.get("url", "")
            if not url:
                raise ValueError(f"视频生成完成但未返回下载 URL: {json.dumps(result, ensure_ascii=False)}")
            if status_callback:
                status_callback("视频生成完成！")
            return url

        if status == "failed":
            error_info = result.get("error", "未知错误")
            raise RuntimeError(f"视频生成失败: {error_info}")

        if attempt < _MAX_POLL_ATTEMPTS:
            time.sleep(_POLL_INTERVAL)

    raise TimeoutError(f"视频生成超时（已等待 {_MAX_POLL_ATTEMPTS * _POLL_INTERVAL} 秒）")


class VideoWorker(QObject):
    """视频生成工作线程（支持文生视频和图生视频）"""
    finished = Signal(str)       # 返回视频 URL
    error = Signal(str)
    status_log = Signal(str)

    def __init__(
        self,
        prompt: str,
        height: int = 768,
        width: int = 1152,
        num_frames: int = 121,
        frame_rate: int = 24,
        image: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.prompt = prompt
        self.height = height
        self.width = width
        self.num_frames = num_frames
        self.frame_rate = frame_rate
        self.image = image

    def _status(self, msg: str):
        if msg:
            self.status_log.emit(msg + "\n")

    def run(self):
        try:
            result = generate_video(
                prompt=self.prompt,
                height=self.height,
                width=self.width,
                num_frames=self.num_frames,
                frame_rate=self.frame_rate,
                image=self.image,
                status_callback=self._status,
            )
            self.finished.emit(result)
        except Exception as e:
            msg = str(e).strip()
            if not msg:
                msg = f"{type(e).__name__}: 未知错误，请重试"
            self.error.emit(msg)
