"""把各種瀏覽器 TLS 指紋都拿去打 chutes，確認是否為 ClientHello 指紋問題。

用法：
    $env:CHUTES_API_KEY="cpk_....(真 key)"
    .venv\\Scripts\\python.exe chutes_fingerprints.py            # 連續快速打
    .venv\\Scripts\\python.exe chutes_fingerprints.py --delay 3  # 每筆間隔 3 秒
    .venv\\Scripts\\python.exe chutes_fingerprints.py --repeat 3 # 同一組跑 3 輪
    .venv\\Scripts\\python.exe chutes_fingerprints.py --http1   # 強制 HTTP/1.1
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from curl_cffi import requests
from curl_cffi.const import CurlHttpVersion

URL = "https://llm.chutes.ai/v1/chat/completions"
MODEL = "deepseek-ai/DeepSeek-V4-Flash-0731-TEE"
A_UA = "ai/6.0.185 ai-sdk/provider-utils/4.0.50 runtime/node.js/24"

BODY = {
    "model": MODEL,
    "messages": [{"role": "user", "content": "hi"}],
    "max_tokens": 8,
    "stream": False,
}

TARGETS = [
    "chrome99",
    "chrome104",
    "chrome110",
    "chrome116",
    "chrome120",
    "chrome124",
    "chrome131",
    "chrome133a",
    "chrome136",
    "chrome142",
    "chrome145",
    "chrome146",
    "chrome150",
    "chrome",
    "edge99",
    "edge101",
    "firefox133",
    "firefox147",
    "safari17_0",
    "safari18_0",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="測試各瀏覽器指紋對 chutes 的結果")
    parser.add_argument("--delay", type=float, default=0.0, help="每筆之間間隔幾秒")
    parser.add_argument("--repeat", type=int, default=1, help="整組重複幾輪")
    parser.add_argument("--http1", action="store_true", help="強制 HTTP/1.1（不協商 h2）")
    args = parser.parse_args()
    http_version = CurlHttpVersion.V1_1 if args.http1 else None

    key = os.environ.get("CHUTES_API_KEY", "").strip()
    if not key:
        sys.exit("請先設定環境變數 CHUTES_API_KEY")

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "zh-TW",
        "User-Agent": A_UA,
    }

    for run in range(1, args.repeat + 1):
        print(f"\n########## run {run}/{args.repeat}  (delay={args.delay}s, http1={args.http1}) ##########")
        bad: list[str] = []
        for target in TARGETS:
            extra = {"http_version": http_version} if http_version is not None else {}
            try:
                resp = requests.post(URL, impersonate=target, headers=headers, json=BODY, timeout=60, **extra)
                status: int | str = resp.status_code
                body = "" if resp.status_code == 200 else resp.text[:60].replace("\n", " ")
            except Exception as exc:  # noqa: BLE001
                status, body = f"ERR({exc})", ""
            if status != 200:
                bad.append(str(target))
            mark = "OK " if status == 200 else "!! "
            print(f"  {mark}{status!s:<6} {target:<11} {body}")
            if args.delay:
                time.sleep(args.delay)
        print(f"  -> run {run} 非 200: {', '.join(bad) if bad else '(無)'}")


if __name__ == "__main__":
    main()
