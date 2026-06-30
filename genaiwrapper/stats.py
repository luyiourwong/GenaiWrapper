"""統計模組，追蹤請求轉發數據"""

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class UsageInfo(BaseModel):
    """Token 使用資訊"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0


class PricingInfo(BaseModel):
    """價格資訊 (單位: USD per 1M tokens)"""

    input: float
    cached_input: float
    output: float


@dataclass
class ModelStats:
    """單一模型的統計數據"""

    request_count: int = 0
    message_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    streaming_count: int = 0
    non_streaming_count: int = 0
    cache_hit_count: int = 0
    cached_tokens: int = 0
    input_cost: float = 0.0
    cached_cost: float = 0.0
    output_cost: float = 0.0

    @property
    def total_cost(self) -> float:
        """總成本"""
        return self.input_cost + self.cached_cost + self.output_cost


@dataclass
class GlobalStats:
    """全域統計數據"""

    start_time: datetime = field(default_factory=datetime.now)
    total_requests: int = 0
    total_messages: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_streaming: int = 0
    total_non_streaming: int = 0
    total_cache_hits: int = 0
    total_cached_tokens: int = 0
    total_input_cost: float = 0.0
    total_cached_cost: float = 0.0
    total_output_cost: float = 0.0
    models: dict[str, ModelStats] = field(default_factory=lambda: defaultdict(ModelStats))

    @property
    def total_cost(self) -> float:
        """總成本"""
        return self.total_input_cost + self.total_cached_cost + self.total_output_cost

    def to_summary(self) -> str:
        """生成統計簡報字串"""
        uptime = datetime.now() - self.start_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)

        lines = [
            "\n" + "=" * 50,
            "📊 GenaiWrapper 運行簡報",
            "=" * 50,
            f"運行時間: {hours:02d}:{minutes:02d}:{seconds:02d}",
            "-" * 50,
            f"總請求數: {self.total_requests}",
            f"  Streaming 請求: {self.total_streaming}",
            f"  非 Streaming 請求: {self.total_non_streaming}",
            f"總訊息數: {self.total_messages}",
            f"總輸入 Token: {self.total_prompt_tokens:,}",
            f"總輸出 Token: {self.total_completion_tokens:,}",
            f"  緩存命中次數: {self.total_cache_hits}",
            f"  緩存 Token 總數: {self.total_cached_tokens:,}",
            "-" * 50,
            f"💰 總成本: ${self.total_cost:.4f}",
            f"   輸入成本: ${self.total_input_cost:.4f}",
            f"   緩存成本: ${self.total_cached_cost:.4f}",
            f"   輸出成本: ${self.total_output_cost:.4f}",
        ]

        if self.models:
            lines.extend(
                [
                    "-" * 50,
                    "各模型統計:",
                ]
            )
            for model, stats in sorted(self.models.items()):
                lines.extend(
                    [
                        f"  📦 {model}:",
                        f"     請求數: {stats.request_count} (S {stats.streaming_count}/P {stats.non_streaming_count})",
                        f"     訊息數: {stats.message_count}",
                        f"     輸入 Token: {stats.prompt_tokens:,}",
                        f"     輸出 Token: {stats.completion_tokens:,}",
                        f"     緩存 Token: {stats.cached_tokens:,} ({stats.cache_hit_count} 次)",
                        f"     💰 成本: ${stats.total_cost:.4f}",
                        f"     輸入 ${stats.input_cost:.4f} (${stats.cached_cost:.4f}), 輸出 ${stats.output_cost:.4f}",
                    ]
                )

        lines.append("=" * 50)
        return "\n".join(lines)


class StatsManager:
    """統計管理器，提供線程安全的統計更新"""

    def __init__(self) -> None:
        self._stats = GlobalStats()
        self._lock = threading.Lock()

    def record_request(
        self,
        model: str,
        message_count: int,
        is_streaming: bool,
        usage: UsageInfo,
        pricing: Optional[PricingInfo] = None,
    ) -> None:
        """記錄一次請求"""
        with self._lock:
            # 計算價格
            input_cost = 0.0
            cached_cost = 0.0
            output_cost = 0.0

            if pricing:
                # 輸入成本 = (總輸入 - 緩存) * 輸入價格 / 1M
                non_cached_tokens = usage.prompt_tokens - usage.cached_tokens
                input_cost = non_cached_tokens * pricing.input / 1_000_000
                # 緩存成本 = 緩存 Token * 緩存價格 / 1M
                cached_cost = usage.cached_tokens * pricing.cached_input / 1_000_000
                # 輸出成本 = 輸出 Token * 輸出價格 / 1M
                output_cost = usage.completion_tokens * pricing.output / 1_000_000

            # 更新全域統計
            self._stats.total_requests += 1
            self._stats.total_messages += message_count
            self._stats.total_prompt_tokens += usage.prompt_tokens
            self._stats.total_completion_tokens += usage.completion_tokens
            self._stats.total_input_cost += input_cost
            self._stats.total_cached_cost += cached_cost
            self._stats.total_output_cost += output_cost

            if is_streaming:
                self._stats.total_streaming += 1
            else:
                self._stats.total_non_streaming += 1

            # 更新緩存統計
            if usage.cached_tokens > 0:
                self._stats.total_cache_hits += 1
                self._stats.total_cached_tokens += usage.cached_tokens

            # 更新模型統計
            model_stats = self._stats.models[model]
            model_stats.request_count += 1
            model_stats.message_count += message_count
            model_stats.prompt_tokens += usage.prompt_tokens
            model_stats.completion_tokens += usage.completion_tokens
            model_stats.input_cost += input_cost
            model_stats.cached_cost += cached_cost
            model_stats.output_cost += output_cost

            if is_streaming:
                model_stats.streaming_count += 1
            else:
                model_stats.non_streaming_count += 1

            if usage.cached_tokens > 0:
                model_stats.cache_hit_count += 1
                model_stats.cached_tokens += usage.cached_tokens

    def get_summary(self) -> str:
        """取得統計簡報"""
        with self._lock:
            return self._stats.to_summary()

    def print_summary(self) -> None:
        """輸出統計簡報到日誌"""
        summary = self.get_summary()
        # 直接 print 確保在退出時可見
        print(summary)


# 全域統計管理器實例
stats_manager = StatsManager()
