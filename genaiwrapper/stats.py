"""統計模組，追蹤請求轉發數據"""

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class UsageInfo(BaseModel):
    """Token 使用資訊"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0


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
    models: dict[str, ModelStats] = field(default_factory=lambda: defaultdict(ModelStats))

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
            f"總訊息數: {self.total_messages}",
            f"總輸入 Token: {self.total_prompt_tokens:,}",
            f"總輸出 Token: {self.total_completion_tokens:,}",
            f"Streaming 請求: {self.total_streaming}",
            f"非 Streaming 請求: {self.total_non_streaming}",
            f"緩存命中次數: {self.total_cache_hits}",
            f"緩存 Token 總數: {self.total_cached_tokens:,}",
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
                        f"     請求數: {stats.request_count}",
                        f"     訊息數: {stats.message_count}",
                        f"     輸入 Token: {stats.prompt_tokens:,}",
                        f"     輸出 Token: {stats.completion_tokens:,}",
                        f"     Streaming/非: {stats.streaming_count}/{stats.non_streaming_count}",
                        f"     緩存命中: {stats.cache_hit_count} 次",
                        f"     緩存 Token: {stats.cached_tokens:,}",
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
    ) -> None:
        """記錄一次請求"""
        with self._lock:
            # 更新全域統計
            self._stats.total_requests += 1
            self._stats.total_messages += message_count
            self._stats.total_prompt_tokens += usage.prompt_tokens
            self._stats.total_completion_tokens += usage.completion_tokens

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
