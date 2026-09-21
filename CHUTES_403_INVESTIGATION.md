# Chutes `llm.chutes.ai` 403 調查報告

> 調查日期：2026-09-21
> 症狀：Cherry Studio（Electron/Chromium）連 `https://llm.chutes.ai/v1/chat/completions` 固定 403，但同一顆 API key 用 curl 正常。

---

## 1. 結論（TL;DR）

**根因：本機防毒（ESET / Kaspersky）的 SSL/TLS 攔截（MITM）。**

Cherry Studio（Chromium/Electron）直連時，HTTPS 被防毒接管，改由**防毒自己的 TLS 堆疊**去連 `llm.chutes.ai`；chutes 前面的 **Google Front End / Cloud Armor 會擋這種非瀏覽器/非一般 client 的 TLS 連線**，回傳 `403` 與 `via: 1.1 google`。

未經攔截的路徑（curl / Node / Python）以及「走 loopback proxy」都正常，因此只有「Chromium 類 App、且只對 chutes」會中。

- 403 **不是** Chutes API 的驗證錯誤（那會是 401 JSON），而是 **Google 邊緣層的 HTML 403**。
- **與 Bearer key 格式無關**（request 內容與正常請求逐 byte 相同）。

---

## 2. 環境

| 項目 | 值 |
|---|---|
| OS | Windows 11 Pro |
| Client A（有問題） | Cherry Studio 2.0.14（Electron / Chromium） |
| Client B（對照） | curl 8.7.1 (Schannel，無 HTTP/2)、Node 22 (undici / `node:http2`)、Python 3.12、curl_cffi (BoringSSL) |
| Endpoint | `https://llm.chutes.ai/v1/chat/completions` |
| 上游解析 | `llm.chutes.ai` → `34.111.142.178`（無 AAAA、無 HTTPS RR） |
| 防毒 | ESET（`ESET SSL Filter CA` 在根憑證區，SSL/TLS 掃描開啟）；另一台 Kaspersky「全開」也重現 |
| 重現範圍 | 兩台機器、兩個不同地點皆重現 |

---

## 3. 症狀

同一顆 API key：

| 客戶端 | 路徑 | 結果 |
|---|---|---|
| Cherry Studio | 直連 | **403** |
| curl / Node / Python / curl_cffi | 直連 | 200 |
| Cherry Studio | 走 loopback HTTP proxy（CONNECT tunnel） | 200 |

### 403 回應（來自 Google 邊緣）

```
HTTP/1.1 403
alt-svc: clear
content-length: 134
content-type: text/html; charset=UTF-8
date: ...
document-policy: include-js-call-stacks-in-crash-reports
via: 1.1 google

<!doctype html><meta charset="utf-8"><meta name=viewport content="width=device-width, initial-scale=1"><title>403</title>403 Forbidden
```

### 正常 200 回應（同端點，對照）

```
server: nginx
x-chutes-invocationid: 360a9f0d-...
x-chutes-quota-total: 0
x-chutes-quota-used: 0
x-chutes-quota-remaining: 0
x-chutes-rl-user: 60
via: 1.1 google
alt-svc: clear
```

> 注意：`alt-svc: clear` 在正常 200 也會出現，因此它**不是** 403 專屬的線索（一度被誤判為 HTTP/3 線索）。

---

## 4. Cherry Studio 實際送出的請求（以 `echo_auth.py` / `mitm_proxy.py` 攔截）

### Headers

```
host: 'llm.chutes.ai'
connection: 'keep-alive'
content-length: '155'
accept-encoding: 'gzip, deflate, br, zstd'
accept-language: 'zh-TW'
sec-fetch-dest: 'empty'
sec-fetch-mode: 'no-cors'
sec-fetch-site: 'none'
user-agent: 'ai/6.0.185 ai-sdk/provider-utils/4.0.50 runtime/node.js/24'
authorization: 'Bearer cpk_...'
content-type: 'application/json'
```

### Body（model check，非串流）

```json
{
  "model": "deepseek-ai/DeepSeek-V4-Flash-0731-TEE",
  "reasoning_effort": "none",
  "messages": [
    { "role": "system", "content": "test" },
    { "role": "user", "content": "hi" }
  ]
}
```

### Body（實際對話，串流）

```json
{
  "model": "Qwen/Qwen3.8-27B-TEE",
  "messages": ["..."],
  "stream": true,
  "stream_options": { "include_usage": true }
}
```

### Authorization header 結構分析

`Bearer cpk_<32 hex>.<32 hex>.<32 char base64url>`（token 長度 102，共三段 `[36, 32, 32]`）

- 無多餘空白、無控制字元、無非 ASCII、無引號、無重複 header
- **結構完全正常**，與 curl 使用的 key 相同

---

## 5. 測試紀錄與結果

| # | 測試 | 目的 | 結果 |
|---|---|---|---|
| 1 | `echo_auth.py` 攔截 Cherry Studio header/body | 檢查 key 格式與 header | 一切正常，無 anomaly |
| 2 | curl 預設 + Cherry Studio 完整 header | 排除 header / UA | **200** |
| 3 | Node fetch（HTTP/1.1） | 排除 HTTP/1.1 | **200** |
| 4 | Node `http2` | 排除 HTTP/2 | **200** |
| 5 | Node h2 + Cherry Studio 完整 header（含 `sec-fetch-*`、無 `Accept`） | 排除「h2 指紋 + 瀏覽器特徵」組合 | **200** |
| 6 | curl `-4`（IPv4） | IPv4 連線 | **200** |
| 7 | curl `-6`（IPv6） | IPv6 連線 | 連線失敗（該機無 IPv6 路由；也無 AAAA） |
| 8 | curl_cffi 模擬 `chrome` / `chrome124` / `chrome110` / `safari` / `firefox`（BoringSSL） | 排除 TLS 指紋 (JA3/JA4) | **全部 200** |
| 9 | curl_cffi `chrome` + Cherry Studio 的 Node UA | 排除「指紋與 UA 不匹配」 | **200** |
| 10 | 以管理員封鎖 outbound UDP/443（規則 Enabled=True 已驗證）後直連 | 排除 HTTP/3 (QUIC) | 仍 **403** |
| 11 | 刪除 Chromium `Network Persistent State`（清 alt-svc 快取）後直連 | 排除 alt-svc 快取 | 仍 **403** |
| 12 | `Resolve-DnsName` A / AAAA / HTTPS(65) | DNS / ECH / h3 廣告 | A=`34.111.142.178`；**無 AAAA**；**無 HTTPS RR** |
| 13 | `curl https://dns.google/resolve?...&type=HTTPS` | 確認無 HTTPS RR（無 alpn/ech） | 無 Answer |
| 14 | 系統 DNS vs Google DoH | DNS 一致性 | 皆 `34.111.142.178` |
| 15 | `Get-NetTCPConnection` 監看 Cherry Studio 直連 | 是否連錯節點 | 連到 **`34.111.142.178`（正確）** |
| 16 | 讀取系統 Proxy 設定 | 排除系統 proxy / WPAD | `ProxyEnable=0`、無 `AutoConfigURL` |
| 17 | `mitm_proxy.py`（MITM 模式）攔截 | 取得完整請求 | 與 echo 一致；**轉發後 200** |
| 18 | `mitm_proxy.py --tunnel`（純 TCP 轉發，Chromium 的 TLS/h2 原封不動） | 分離「連線路徑」 vs 「Chromium TLS/h2」 | **200** |
| 19 | Python 直連看憑證 issuer | 是否被防毒攔截 | `Go Daddy Secure Certificate Authority - G2`（**真憑證，未被攔截**） |
| 20 | Edge 直連看憑證 issuer | Chromium 類是否被攔截 | `ESET SSL Filter CA`（**被攔截**） |
| 21 | 根憑證區查詢 | 防毒攔截是否啟用 | `ESET SSL Filter CA` 存在 |

---

## 6. 逐一排除的因素

| 因素 | 排除理由 |
|---|---|
| Bearer key 格式 / 內容 | MITM 攔到的 header 與 body 和正常 curl 請求完全相同 |
| 所有 header（含 `sec-fetch-*`、缺 `Accept`） | 全套照抄給 curl / Node 皆 200 |
| User-Agent 被 WAF 擋 | 用 Cherry Studio 的 Node UA 測也 200 |
| HTTP/1.1 vs HTTP/2 | 兩者皆 200 |
| TLS 指紋 (JA3/JA4) | curl_cffi 以 BoringSSL 模擬 chrome / safari / firefox 皆 200 |
| HTTP/3 (QUIC) | 管理員確實封鎖 UDP/443 後直連仍 403；且無 HTTPS RR |
| ECH | `llm.chutes.ai` 沒有 HTTPS RR（無 ECHConfig） |
| 連錯節點 / DNS | 系統與 DoH 皆 `34.111.142.178`，且實測 Cherry Studio 直連就是該 IP |
| 系統 Proxy / WPAD | `ProxyEnable=0`、無 `AutoConfigURL` |
| IPv6 | 無 AAAA，該機也無 IPv6 路由 |
| Chromium 的 TLS/h2 本身 | 純 TCP tunnel（不終結 TLS）下，Chromium 自己完成 TLS/h2 仍 200 |
| alt-svc 快取 | 刪除 `Network Persistent State` 後仍 403 |

**剩下的唯一變因：防毒 SSL/TLS 攔截。**

---

## 7. 根因推論鏈

1. Cherry Studio（Chromium/Electron）直連 `llm.chutes.ai` 時，**HTTPS 被 ESET 攔截**（Edge 顯示 `ESET SSL Filter CA`；Python/curl/Node 顯示真憑證 `GoDaddy`，代表只有 Chromium 類被攔）。
2. 防毒以**自己的 TLS 堆疊**重新與 chutes 建立連線。
3. chutes 前面的 **Google Front End / Cloud Armor** 對這種 TLS/指紋連線回 **403**（`via: 1.1 google` + 通用 HTML）。
4. 走 loopback proxy 時，Chromium 的 TLS 是連到 `127.0.0.1`，**防毒會跳過 loopback** → 未攔截 → Chromium 自己的 TLS 直達 → 200。
5. 其他 API 沒有這種邊緣過濾，所以防毒攔截對它們無感 → 這就是「為什麼只有 chutes 有事」。

---

## 8. Issue 草稿（可提交；建議開在 Chutes 端）

```markdown
# llm.chutes.ai returns edge 403 for Chromium clients behind antivirus SSL interception (MITM); non-intercepted clients work

## Environment
- OS: Windows 11 Pro
- Client A: Cherry Studio 2.0.14 (Electron/Chromium)
- Client B (control): curl 8.7.1 (Schannel), Node 22 (undici / node:http2), Python, curl_cffi (BoringSSL)
- Endpoint: https://llm.chutes.ai/v1/chat/completions
- Network: two different machines/locations, both reproduce
- Antivirus: ESET (ESET SSL Filter CA present, SSL/TLS scanning ON); a Kaspersky machine with all protection ON also reproduces
- Upstream: llm.chutes.ai -> 34.111.142.178 (no AAAA, no HTTPS RR)

## Actual
Same API key:
- Cherry Studio (direct)            -> 403
- curl / Node / Python / curl_cffi  -> 200
- Cherry Studio via local HTTP proxy (CONNECT tunnel over loopback) -> 200

The 403 is a Google edge response, not a Chutes API error:

    content-type: text/html; charset=UTF-8
    via: 1.1 google
    alt-svc: clear
    document-policy: include-js-call-stacks-in-crash-reports

    <!doctype html><meta charset="utf-8">...<title>403</title>403 Forbidden

## Expected
Cherry Studio should receive the normal OpenAI-compatible response (HTTP 200 / SSE), as it does with every other OpenAI-compatible provider using the same key.

## Eliminated (each returned 200)
- Bearer key format/content: MITM capture shows the exact same Authorization header and JSON body as the working curl request
- Headers (including sec-fetch-*, missing Accept) and User-Agent
- HTTP/1.1 vs HTTP/2
- TLS fingerprint: curl_cffi impersonating chrome / chrome110 / chrome124 / safari / firefox (BoringSSL) all 200
- HTTP/3: blocking outbound UDP/443 with an enabled Windows Firewall rule (verified) still 403; and no HTTPS RR/advertised h3
- ECH: llm.chutes.ai publishes no HTTPS RR (DNS type 65 returns no answer)
- Wrong node / DNS: system DNS and Google DoH both return 34.111.142.178; Get-NetTCPConnection shows Cherry Studio's direct connection going to 34.111.142.178
- System proxy / WPAD: ProxyEnable=0, no AutoConfigURL
- IPv6: no AAAA record
- Chromium's own TLS/h2: with a plain TCP tunnel (no TLS termination, so Chromium performs its own TLS/h2 to Chutes) the same request returns 200

## Root cause (analysis)
The only remaining variable is local antivirus SSL/TLS interception:
- A plain Python TLS connection to llm.chutes.ai sees the real certificate (issuer: "Go Daddy Secure Certificate Authority - G2") -> Python/curl/Node are NOT intercepted
- Microsoft Edge to llm.chutes.ai sees issuer "ESET SSL Filter CA" -> Chromium-based apps ARE intercepted and re-originated by the AV
- Routing Cherry Studio through a loopback proxy bypasses the AV interception and returns 200

=> TLS connections re-originated by the antivirus are rejected with 403 by the Google edge in front of Chutes, while non-intercepted connections succeed. Other providers do not show this, which suggests stricter TLS/fingerprint filtering at Chutes' edge.

## Suggestion
- Chutes: check whether Google Front End / Cloud Armor is rejecting TLS/HTTP connections originating from security-product MITM stacks, and/or return a distinguishable error instead of the generic Google 403 HTML.
- Alternative: document that endpoints fronted by the Google edge may 403 enterprise SSL-inspection traffic.

## Workaround
- Route the client through a local CONNECT tunnel proxy (loopback traffic is not intercepted)
- Or add llm.chutes.ai to the antivirus SSL/TLS exclusion list
```

> 若要改開在 **Cherry Studio**，把最後「Suggestion」改成：
> 建議新增「忽略系統憑證 / 繞過企業 SSL 攔截」或「自訂 TLS/憑證」選項。

---

## 9. Workaround

1. **立即**：掛著本機 CONNECT tunnel proxy（`mitm_proxy.py --tunnel`），Cherry Studio proxy 設為 `http://127.0.0.1:8888`。
2. **根治**：請 MIS 將 `llm.chutes.ai` 加入防毒的 SSL/TLS 例外清單。
3. **遊戲機（Kaspersky）**：網路設定 → 加密連線掃描 → 排除 `llm.chutes.ai`（或暫時關閉以驗證）。

---

## 10. 附錄：本次使用的工具

皆為獨立腳本，**未修改專案任何 tracked 檔案**。

| 檔案 | 用途 |
|---|---|
| `echo_auth.py` | 簡易 Echo 伺服器，解析並顯示傳入的 `Authorization` header 結構與異常 |
| `chutes_bisect.py` | Header / HTTP 版本 / IPv4-6 的逐一比對（curl + Node h1/h2） |
| `chutes_chrome.py` | 以 curl_cffi 模擬各瀏覽器 TLS 指紋 |
| `mitm_proxy.py` | HTTPS 攔截 proxy（MITM 模式可看完整請求；`--tunnel` 為純 TCP 轉發，不需憑證） |

### 常用指令

```powershell
# Echo 診斷伺服器
.venv\Scripts\python.exe echo_auth.py --port 8787

# Header / 版本 / 指紋比對（需 CHUTES_API_KEY）
$env:CHUTES_API_KEY="cpk_..."
.venv\Scripts\python.exe chutes_bisect.py
.venv\Scripts\python.exe chutes_chrome.py

# HTTPS 攔截 proxy
.venv\Scripts\python.exe mitm_proxy.py            # MITM（需信任 CA）
.venv\Scripts\python.exe mitm_proxy.py --tunnel   # 純 TCP 轉發

# 匯入 / 移除 MITM CA
Import-Certificate -FilePath "$env:TEMP\genaiwrapper_mitm\ca.crt" -CertStoreLocation Cert:\CurrentUser\Root
Get-ChildItem Cert:\CurrentUser\Root | Where-Object Subject -like "*GenaiWrapper MITM CA*" | Remove-Item

# 查看憑證 issuer（判斷是否被防毒攔截）
.venv\Scripts\python.exe -c "import ssl,socket;c=ssl.create_default_context();s=c.wrap_socket(socket.create_connection(('llm.chutes.ai',443)),server_hostname='llm.chutes.ai');print(s.getpeercert()['issuer'])"

# 清理本次調查的暫時變更
Remove-NetFirewallRule -DisplayName "Block QUIC"
uv pip uninstall --python .venv\Scripts\python.exe curl_cffi
```
