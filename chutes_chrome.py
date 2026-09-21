"""用 curl_cffi 模擬 Chrome 的 TLS/HTTP2 指紋，確認 403 是否為指紋造成。

用法（不需要安裝到專案環境，uv 會用臨時環境）：
    $env:CHUTES_API_KEY="cpk_....(真 key)"
    uv run --with curl_cffi python chutes_chrome.py

判讀：
  - 若 chrome 指紋 -> 403，而 curl_cffi 的其它指紋或前面 Node 測試 -> 200
    => 確定是 Chromium TLS/h2 指紋被擋。
  - 若全部 200 => 不是 TLS 指紋，需往 Electron 特有行為（例如它的 net 模組設定）追。
"""

from __future__ import annotations

import os
import sys

URL = "https://llm.chutes.ai/v1/chat/completions"
MODEL = "deepseek-ai/DeepSeek-V4-Flash-0731-TEE"
A_UA = "ai/6.0.185 ai-sdk/provider-utils/4.0.50 runtime/node.js/24"

BODY = {
    "model": MODEL,
    "messages": [
        {"content": "test", "role": "system"},
        {"content": "hi", "role": "user"},
    ],
    "reasoning_effort": "none",
    "max_tokens": 16,
    "stream": False,
}


def main() -> None:
    try:
        from curl_cffi import requests
    except ImportError:
        sys.exit("缺少 curl_cffi，請改用：uv run --with curl_cffi python chutes_chrome.py")

    key = os.environ.get("CHUTES_API_KEY", "").strip()
    if not key:
        sys.exit("請先設定環境變數 CHUTES_API_KEY（真的 chutes key）")

    # 模擬不同瀏覽器的 TLS/h2 指紋
    for impersonate in ["chrome", "chrome124", "chrome110", "safari", "firefox"]:
        try:
            resp = requests.post(
                URL,
                impersonate=impersonate,  # type: ignore[arg-type]
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=BODY,
                timeout=60,
            )
            print(
                f"{'OK ' if resp.status_code == 200 else '!! '}{resp.status_code}  "
                f"impersonate={impersonate:<9} via={resp.headers.get('via', '-')}"
            )
            if resp.status_code != 200:
                print(f"       body: {resp.text[:160]!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"ERR  impersonate={impersonate:<9} {exc}")

    # 額外：Chrome 指紋但沿用 A app 的 Node UA（測「指紋與 UA 不匹配」）
    try:
        resp = requests.post(
            URL,
            impersonate="chrome",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "User-Agent": A_UA,
            },
            json=BODY,
            timeout=60,
        )
        print(f"{'OK ' if resp.status_code == 200 else '!! '}{resp.status_code}  impersonate=chrome + A's Node UA")
    except Exception as exc:  # noqa: BLE001
        print(f"ERR  chrome + A UA: {exc}")


if __name__ == "__main__":
    main()
