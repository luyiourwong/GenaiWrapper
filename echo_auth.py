"""簡易 Echo 診斷伺服器（單檔、獨立於 GenaiWrapper 主程式）。

用途：把任何請求原樣 echo 回去，並詳細解析傳入的 Authorization header，
用來比對 A app / B app 送出的 Bearer token 格式差異。

啟動：
    .venv\\Scripts\\python.exe echo_auth.py
    （預設 http://127.0.0.1:8787，可用 --host / --port 調整）

測試：
    KEY="cpk_34503d0081714b2bad5b2da3dad0f054.27641b7d51775737bc12d4c5a9532c52.nmOCEgAI4pPdglKBn6u0W3m7ymmjjgVx"
    curl -i -X POST http://127.0.0.1:8787/v1/chat/completions \
      -H "Authorization: Bearer $KEY" \
      -H "Content-Type: application/json" \
      -d '{"model":"test","messages":[{"role":"user","content":"hi"}]}'
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import logging
import re
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("echo-auth")

# X 網站 key 的「結構」樣本（非真實憑證），只用來比對格式。
REFERENCE_KEY = "cpk_34503d0081714b2bad5b2da3dad0f054.27641b7d51775737bc12d4c5a9532c52.nmOCEgAI4pPdglKBn6u0W3m7ymmjjgVx"

_HEX_RE = re.compile(r"[0-9a-fA-F]+")
_B64URL_RE = re.compile(r"[A-Za-z0-9_-]+={0,2}")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

# 異常代碼 -> 人類可讀說明（同時放進 console log 與 JSON 回應）
ANOMALY_MESSAGES = {
    "missing_header": "完全沒有收到 Authorization header",
    "multiple_headers": "收到多個 Authorization header（正常應只有 1 個）",
    "leading_trailing_space": "header 值前後含多餘空白",
    "missing_scheme": "缺少 scheme（沒有空白分隔），整串被當成 token",
    "scheme_not_bearer": "scheme 不是 Bearer",
    "token_contains_whitespace": "token 內含空白/定位字元（可能被應用程式截斷或折行）",
    "control_characters": "含控制字元（傳輸過程可能被破壞）",
    "non_ascii": "含非 ASCII 字元（編碼處理可能有問題）",
    "quotes_present": "含引號（可能被上層程式包成字串後沒拆掉）",
    "trailing_padding": "token 結尾含 '=' padding",
    "segment_count_mismatch": "以 '.' 切分的區段數與 X 網站 key 樣本不同",
    "segment_length_mismatch": "某個區段的長度與 X 網站 key 樣本不同",
}


def _decode_base64url(value: str) -> bytes | None:
    """嘗試用 base64url 解碼，失敗回傳 None。"""
    if not value:
        return None
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError):
        return None


def _segment_kind(value: str) -> str:
    if not value:
        return "empty"
    if _HEX_RE.fullmatch(value):
        return "hex"
    if _B64URL_RE.fullmatch(value):
        return "base64url"
    return "other"


def analyze_auth(auth_values: list[str]) -> dict[str, Any]:
    """解析 Authorization header，回傳結構化分析結果。"""
    ref_segments = REFERENCE_KEY.split(".")
    result: dict[str, Any] = {
        "present": bool(auth_values),
        "occurrence_count": len(auth_values),
        "raw": None,
        "raw_repr": None,
        "length": 0,
        "scheme": None,
        "token": None,
        "token_length": 0,
        "segments": [],
        "matches_reference_structure": False,
        "anomalies": [],
    }
    anomalies: list[str] = result["anomalies"]

    if not auth_values:
        anomalies.append("missing_header")
        return result

    raw = auth_values[0]
    result["raw"] = raw
    result["raw_repr"] = repr(raw)
    result["length"] = len(raw)

    if len(auth_values) > 1:
        anomalies.append("multiple_headers")
    if raw != raw.strip():
        anomalies.append("leading_trailing_space")
    if _CONTROL_RE.search(raw):
        anomalies.append("control_characters")
    if any(ord(ch) > 127 for ch in raw):
        anomalies.append("non_ascii")
    if '"' in raw or "'" in raw:
        anomalies.append("quotes_present")

    parts = raw.strip().split(" ", 1)
    if len(parts) == 2:
        result["scheme"], token = parts[0], parts[1].strip()
        if result["scheme"].lower() != "bearer":
            anomalies.append("scheme_not_bearer")
    else:
        token = parts[0].strip()
        anomalies.append("missing_scheme")

    result["token"] = token
    result["token_length"] = len(token)
    if " " in token or "\t" in token:
        anomalies.append("token_contains_whitespace")
    if token.endswith("="):
        anomalies.append("trailing_padding")

    segments = token.split(".")
    if len(segments) != len(ref_segments):
        anomalies.append("segment_count_mismatch")

    for index, seg in enumerate(segments):
        kind = _segment_kind(seg)
        decoded = _decode_base64url(seg)
        info: dict[str, Any] = {
            "index": index,
            "length": len(seg),
            "kind": kind,
            "value": seg,
            "base64url_decodable": decoded is not None,
            "decoded_bytes": len(decoded) if decoded else None,
        }
        if index < len(ref_segments):
            ref_len = len(ref_segments[index])
            info["reference_length"] = ref_len
            info["length_matches_reference"] = len(seg) == ref_len
            if len(seg) != ref_len and "segment_length_mismatch" not in anomalies:
                anomalies.append("segment_length_mismatch")

        notes = []
        if seg.startswith("cpk_"):
            tail = seg[4:]
            notes.append(
                "'cpk_' 前綴 + 32 hex（符合樣本）"
                if _HEX_RE.fullmatch(tail) and len(tail) == 32
                else f"'cpk_' 後非 32 hex（實際 {len(tail)}）"
            )
        elif seg and _HEX_RE.fullmatch(seg):
            notes.append("全為 hex 字元")
        if "=" in seg:
            notes.append("含 base64 padding '='")
        info["notes"] = notes
        result["segments"].append(info)

    result["matches_reference_structure"] = len(segments) == len(ref_segments) and all(
        len(s) == len(r) for s, r in zip(segments, ref_segments)
    )
    return result


def describe(analysis: dict[str, Any]) -> str:
    """把分析結果轉成好讀的多行字串（給 console 用）。"""
    if not analysis["present"]:
        return "Authorization: <不存在>"
    lines = [
        f"Authorization raw : {analysis['raw_repr']}",
        f"長度={analysis['length']} scheme={analysis['scheme']!r} token長度={analysis['token_length']}",
        f"結構符合 X key 樣本: {analysis['matches_reference_structure']}",
        "區段:",
    ]
    for seg in analysis["segments"]:
        extra = f"  [{', '.join(seg['notes'])}]" if seg["notes"] else ""
        lines.append(
            f"  [{seg['index']}] len={seg['length']} kind={seg['kind']} "
            f"b64url={seg['base64url_decodable']} value={seg['value']}{extra}"
        )
    if analysis["anomalies"]:
        lines.append("異常:")
        for code in analysis["anomalies"]:
            lines.append(f"  - {code}: {ANOMALY_MESSAGES.get(code, '')}")
    else:
        lines.append("異常: 無")
    return "\n".join(lines)


app = FastAPI(title="Echo Auth Inspector")


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def echo(full_path: str, request: Request) -> JSONResponse:
    """原樣 echo 請求，並附上 Authorization header 分析。"""
    body = await request.body()
    body_text = body.decode("utf-8", errors="replace") if body else None
    body_json: Any | None = None
    if body_text:
        try:
            body_json = json.loads(body_text)
        except json.JSONDecodeError:
            body_json = None

    analysis = analyze_auth(request.headers.getlist("authorization"))
    header_dump = "\n".join(f"  {name}: {value!r}" for name, value in request.headers.items())
    logger.info(
        "收到 %s /%s\nheaders:\n%s\n%s",
        request.method,
        full_path,
        header_dump,
        describe(analysis),
    )

    payload = {
        "echo": {
            "method": request.method,
            "path": request.url.path,
            "query": dict(request.query_params),
            "client": request.client.host if request.client else None,
            "headers": [{"name": k, "value": v, "value_repr": repr(v)} for k, v in request.headers.items()],
            "body_size": len(body),
            "body_text": body_text,
            "body_json": body_json,
        },
        "auth": analysis,
        "auth_anomaly_details": [ANOMALY_MESSAGES.get(code, code) for code in analysis["anomalies"]],
    }

    # 讓 curl -i 不用看 body 也能快速看到重點（header 只能是 ASCII）
    headers = {
        "X-Echo-Auth-Scheme": str(analysis["scheme"]).encode("ascii", "ignore").decode() or "none",
        "X-Echo-Auth-Token-Length": str(analysis["token_length"]),
        "X-Echo-Auth-Matches-Reference": str(analysis["matches_reference_structure"]).lower(),
        "X-Echo-Auth-Anomalies": ",".join(analysis["anomalies"]) or "none",
    }
    return JSONResponse(content=payload, headers=headers)


def main() -> None:
    parser = argparse.ArgumentParser(description="Echo + Authorization header 診斷伺服器")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    logger.info("Echo Auth Inspector 啟動於 http://%s:%s", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
