"""模型基礎定義模組"""

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
