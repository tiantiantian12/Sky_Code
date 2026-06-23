"""
自定义模型管理服务
支持用户添加、编辑、删除自定义模型配置
"""

import json
import os
from typing import Optional


CUSTOM_MODELS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "custom_models.json")


class CustomModel:
    """自定义模型配置"""

    def __init__(self, name: str, provider: str, model_id: str,
                 base_url: str, api_key: str, description: str = "",
                 model_type: str = "text"):
        self.name = name
        self.provider = provider
        self.model_id = model_id
        self.base_url = base_url
        self.api_key = api_key
        self.description = description
        self.model_type = model_type  # "text" 或 "image"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "provider": self.provider,
            "model_id": self.model_id,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "description": self.description,
            "model_type": self.model_type
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'CustomModel':
        return cls(
            name=data.get("name", ""),
            provider=data.get("provider", ""),
            model_id=data.get("model_id", ""),
            base_url=data.get("base_url", ""),
            api_key=data.get("api_key", ""),
            description=data.get("description", ""),
            model_type=data.get("model_type", "text")
        )


class CustomModelService:
    """自定义模型管理服务"""

    def __init__(self):
        self._config = {}
        self._models: dict[str, CustomModel] = {}
        self._load()

    def _load(self):
        """从文件加载配置"""
        if os.path.exists(CUSTOM_MODELS_FILE):
            try:
                with open(CUSTOM_MODELS_FILE, 'r', encoding='utf-8') as f:
                    self._config = json.load(f)
                    models_data = self._config.get("models", {})
                    for model_id, model_data in models_data.items():
                        self._models[model_id] = CustomModel.from_dict(model_data)
            except Exception as e:
                print(f"加载自定义模型配置失败: {e}")

    def _save(self):
        """保存配置到文件"""
        try:
            # 更新 models 部分
            self._config["models"] = {model_id: model.to_dict() for model_id, model in self._models.items()}
            with open(CUSTOM_MODELS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self._config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存自定义模型配置失败: {e}")

    def get_all(self) -> dict[str, CustomModel]:
        """获取所有自定义模型"""
        return self._models.copy()

    def get(self, model_id: str) -> Optional[CustomModel]:
        """获取指定模型"""
        return self._models.get(model_id)

    def add(self, model: CustomModel) -> bool:
        """添加自定义模型"""
        model_id = f"{model.provider}/{model.model_id}"
        if model_id in self._models:
            return False
        self._models[model_id] = model
        self._save()
        return True

    def update(self, model_id: str, model: CustomModel) -> bool:
        """更新自定义模型"""
        if model_id not in self._models:
            return False
        self._models[model_id] = model
        self._save()
        return True

    def delete(self, model_id: str) -> bool:
        """删除自定义模型"""
        if model_id not in self._models:
            return False
        del self._models[model_id]
        self._save()
        return True

    def get_model_list(self) -> list[dict]:
        """获取模型列表（用于 UI 显示）"""
        result = []
        for model_id, model in self._models.items():
            result.append({
                "id": model_id,
                "name": model.name,
                "provider": model.provider,
                "model_id": model.model_id,
                "description": model.description
            })
        return result
