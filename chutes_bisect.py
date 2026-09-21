"""追查 Cherry Studio(A app) 對 llm.chutes.ai 拿到 403 的真正原因。

已知事實：
  - 同 key curl 可通（curl 是 Schannel + 無 HTTP/2，等於 HTTP/1.1）
  - curl + 完整 A app header 也是 200 -> header 不是原因
  - Node fetch(undici, HTTP/1.1) 也是 200
  - Cherry Studio 是 Chromium，走 HTTP/2
=> 唯一未測到的變數：HTTP/2。

用法（PowerShell）：
    $env:CHUTES_API_KEY="cpk_....(真 key)"
    .venv\\Scripts\\python.exe chutes_bisect.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

URL = "https://llm.chutes.ai/v1/chat/completions"
ORIGIN = "https://llm.chutes.ai"
MODEL = "deepseek-ai/DeepSeek-V4-Flash-0731-TEE"  # 與 A app 相同
A_UA = "ai/6.0.185 ai-sdk/provider-utils/4.0.50 runtime/node.js/24"

# 從 echo_auth.py 記錄到的 A app header（去掉 client 自行管理的 host/connection/content-length）
A_HEADERS: list[tuple[str, str]] = [
    ("accept-encoding", "gzip, deflate, br, zstd"),
    ("accept-language", "zh-TW"),
    ("sec-fetch-dest", "empty"),
    ("sec-fetch-mode", "no-cors"),
    ("sec-fetch-site", "none"),
    ("user-agent", A_UA),
]

BODY = json.dumps(
    {
        "model": MODEL,
        "messages": [
            {"content": "test", "role": "system"},
            {"content": "hi", "role": "user"},
        ],
        "reasoning_effort": "none",
        "max_tokens": 16,
        "stream": False,
    }
).encode()

# HTTP/1.1 探針：Node global fetch(undici)
NODE_H1_SNIPPET = """
const extra = JSON.parse(process.env.CHUTES_EXTRA_HEADERS || '{}');
const body = JSON.stringify({
  model: process.env.CHUTES_MODEL,
  messages: [{ role: 'user', content: 'hi' }],
  max_tokens: 16, stream: false,
});
fetch(process.env.CHUTES_URL, {
  method: 'POST',
  headers: Object.assign({
    Authorization: 'Bearer ' + process.env.CHUTES_API_KEY,
    'Content-Type': 'application/json',
    'User-Agent': process.env.CHUTES_UA,
  }, extra),
  body,
}).then(async (r) => {
  console.log('status=' + r.status + ' via=' + (r.headers.get('via') || '-'));
  const t = await r.text();
  if (r.status !== 200) console.log('body=' + t.slice(0, 160));
}).catch((e) => console.log('ERR ' + e.message));
"""

# HTTP/2 探針：Node 原生 http2（TLS 與上面相同，只差 h1/h2）
NODE_H2_SNIPPET = """
const http2 = require('node:http2');
const extra = JSON.parse(process.env.CHUTES_EXTRA_HEADERS || '{}');
const body = JSON.stringify({
  model: process.env.CHUTES_MODEL,
  messages: [{ role: 'user', content: 'hi' }],
  max_tokens: 16, stream: false,
});
const client = http2.connect(process.env.CHUTES_ORIGIN);
client.on('error', (e) => { console.log('ERR ' + e.message); process.exit(0); });
const req = client.request(Object.assign({
  ':method': 'POST',
  ':path': '/v1/chat/completions',
  'authorization': 'Bearer ' + process.env.CHUTES_API_KEY,
  'content-type': 'application/json',
  'user-agent': process.env.CHUTES_UA,
}, extra));
let text = '';
req.on('response', (h) => console.log('status=' + h[':status'] + ' via=' + (h['via'] || '-')));
req.on('data', (c) => { text += c; });
req.on('end', () => {
  if (text && !text.startsWith('{"id"')) console.log('body=' + text.slice(0, 200));
  client.close();
});
req.end(body);
"""

# A app 除 UA/Accept 外的瀏覽器特徵 header（給 Node 模擬用）
A_EXTRA_HEADERS = {
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "zh-TW",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "no-cors",
    "sec-fetch-site": "none",
}


def curl_request(key: str, headers: list[tuple[str, str]], extra_args: list[str] | None = None) -> tuple[int, str]:
    """用 curl 發請求，回傳 (status, 回應前200字)。status=0 代表 curl 本身失敗。"""
    cmd = [
        "curl",
        "-s",
        "-w",
        "\n%{http_code}",
        *(extra_args or []),
        "--data-binary",
        "@-",
        URL,
        "-H",
        f"Authorization: Bearer {key}",
        "-H",
        "Content-Type: application/json",
        "-H",
        "Accept:",  # 清掉 curl 預設 Accept，忠實重現沒送 Accept 的客戶端
    ]
    for name, value in headers:
        cmd += ["-H", f"{name}: {value}"]

    proc = subprocess.run(cmd, input=BODY, capture_output=True)
    out = proc.stdout.decode(errors="replace")
    err = proc.stderr.decode(errors="replace").strip()
    if "\n" not in out:
        return 0, err or out[:200]
    body, code_text = out.rsplit("\n", 1)
    code = int(code_text) if code_text.strip().isdigit() else 0
    return code, body[:200]


def report_curl(label: str, key: str, headers: list[tuple[str, str]], extra_args: list[str] | None = None) -> int:
    code, body = curl_request(key, headers, extra_args)
    print(f"{'OK ' if code == 200 else '!! '}{code}  {label}")
    if code != 200 and body:
        print(f"       body: {body!r}")
    return code


def run_node(label: str, snippet: str, key: str, extra_headers: dict[str, str] | None = None) -> None:
    node = shutil.which("node")
    if not node:
        print(f"  ({label}: 找不到 node，略過)")
        return
    env = {
        **os.environ,
        "CHUTES_API_KEY": key,
        "CHUTES_MODEL": MODEL,
        "CHUTES_UA": A_UA,
        "CHUTES_URL": URL,
        "CHUTES_ORIGIN": ORIGIN,
        "CHUTES_EXTRA_HEADERS": json.dumps(extra_headers or {}),
    }
    proc = subprocess.run([node, "-e", snippet], capture_output=True, env=env)
    out = (proc.stdout or proc.stderr).decode(errors="replace").strip()
    print(f"  {label}: {out}")


def main() -> None:
    key = os.environ.get("CHUTES_API_KEY", "").strip()
    if not key:
        sys.exit("請先設定環境變數 CHUTES_API_KEY（真的 chutes key）")

    print("=== 1. 基準（HTTP/1.1, curl）===")
    if report_curl("curl 預設 + A header", key, A_HEADERS) != 200:
        print("⚠ curl 基準不是 200，請先確認 key/模型/網路；以下結果不可信")
        return

    print("\n=== 2. 相同 TLS、不同 HTTP 版本／header 組合（Node）===")
    print("  -- 只帶 UA、最少 header --")
    run_node("node h1 minimal", NODE_H1_SNIPPET, key)
    run_node("node h2 minimal", NODE_H2_SNIPPET, key)
    print("  -- 完整重現 A app 的瀏覽器特徵 header（sec-fetch-*、無 Accept）--")
    run_node("node h1 + A headers", NODE_H1_SNIPPET, key, A_EXTRA_HEADERS)
    run_node("node h2 + A headers", NODE_H2_SNIPPET, key, A_EXTRA_HEADERS)
    print("  → 若 h2+A headers 才 403，就是「h2 指紋 + 瀏覽器特徵 header」組合被擋")

    print("\n=== 3. IPv4 / IPv6（Chromium 會 Happy Eyeballs 優先 IPv6）===")
    report_curl("curl -4 + A header", key, A_HEADERS, ["-4"])
    report_curl("curl -6 + A header", key, A_HEADERS, ["-6"])

    print("\n=== 4. curl 補充 ===")
    print("  你的 curl:", subprocess.run(["curl", "-V"], capture_output=True).stdout.decode().splitlines()[0])
    print("  (Schannel、無 HTTP/2 → 無法用 curl 測 h2)")


if __name__ == "__main__":
    main()
