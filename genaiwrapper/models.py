"""模型定義模組，手動維護支援的模型列表與價格資訊"""

from pydantic import BaseModel


class ModelPricing(BaseModel):
    """模型價格資訊 (單位: USD per 1M tokens)"""

    input: float
    cached_input: float
    output: float


class ModelInfo(BaseModel):
    """模型資訊"""

    id: str
    pricing: ModelPricing


# 手動維護的模型列表
# 價格單位: USD per 1M tokens
SUPPORTED_MODELS: dict[str, ModelInfo] = {
    "google/gemini-3.1-pro-preview": ModelInfo(
        id="google/gemini-3.1-pro-preview",
        pricing=ModelPricing(
            input=2,
            cached_input=0.2,
            output=12,
        ),
    ),
    "google/gemini-3.8-flash": ModelInfo(
        id="google/gemini-3.8-flash",
        pricing=ModelPricing(
            input=0.75,
            cached_input=0.0825,
            output=3.75,
        ),
    ),
    "google/gemini-3.7-flash": ModelInfo(
        id="google/gemini-3.7-flash",
        pricing=ModelPricing(
            input=0.75,
            cached_input=0.0825,
            output=3.75,
        ),
    ),
    "google/gemini-3.6-flash": ModelInfo(
        id="google/gemini-3.6-flash",
        pricing=ModelPricing(
            input=0.75,
            cached_input=0.0825,
            output=3.75,
        ),
    ),
    "google/gemini-3.5-flash": ModelInfo(
        id="google/gemini-3.5-flash",
        pricing=ModelPricing(
            input=1.5,
            cached_input=0.15,
            output=9,
        ),
    ),
    "google/gemini-3.5-flash-lite": ModelInfo(
        id="google/gemini-3.5-flash-lite",
        pricing=ModelPricing(
            input=0.3,
            cached_input=0.03,
            output=2.5,
        ),
    ),
    "google/gemini-3.1-flash-lite": ModelInfo(
        id="google/gemini-3.1-flash-lite",
        pricing=ModelPricing(
            input=0.25,
            cached_input=0.025,
            output=1.5,
        ),
    ),
    "xai/grok-4.6": ModelInfo(
        id="xai/grok-4.6",
        pricing=ModelPricing(
            input=2,
            cached_input=0.5,
            output=6,
        ),
    ),
    "xai/grok-4.3": ModelInfo(
        id="xai/grok-4.3",
        pricing=ModelPricing(
            input=1.25,
            cached_input=0.2,
            output=2.5,
        ),
    ),
    "openai/gpt-oss-120b-maas": ModelInfo(
        id="openai/gpt-oss-120b-maas",
        pricing=ModelPricing(
            input=0.09,
            cached_input=0.09,
            output=0.36,
        ),
    ),
    "zai-org/glm-5.2-maas": ModelInfo(
        id="zai-org/glm-5.2-maas",
        pricing=ModelPricing(
            input=1.4,
            cached_input=0.14,
            output=4.4,
        ),
    ),
}


def get_model_list() -> list[ModelInfo]:
    """取得所有支援的模型列表"""
    return list(SUPPORTED_MODELS.values())


def get_model_by_id(model_id: str) -> ModelInfo | None:
    """根據 ID 取得模型資訊"""
    return SUPPORTED_MODELS.get(model_id)
