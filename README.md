# GenaiWrapper

本地轉發器，將 OpenAI 相容的 API 請求轉發到 GCP Vertex AI 的第三方模型端點。

## 功能特色

- 完全相容 OpenAI API 格式 (`/v1/chat/completions`)
- 支援 Streaming (SSE) 與非 Streaming 模式
- 自動管理 Google Cloud Access Token 刷新
- 支援 Windows 背景常駐執行
- 支援第三方模型（如 `xai/grok-4.20-reasoning`）

## 🛠️ 安裝

### 1. 設置虛擬環境並安裝依賴

```bash
# 使用 uv 安裝依賴
uv sync
```

### 2. 設定環境變數

複製 `.env.example` 為 `.env` 並填入您的設定：

```bash
cp .env.example .env
```

編輯 `.env` 檔案：

```env
# Google Cloud 設定
PROJECT_ID=your-project-id
REGION=your-region
ENDPOINT=your-endpoint

# 服務設定（可選）
HOST=127.0.0.1
PORT=8000
```

### 3. 確認 Google Cloud 認證

確保您的環境已設定 Google Cloud 認證：

```bash
# 登入 Google Cloud
gcloud auth login

# 或設定應用程式預設憑證
gcloud auth application-default login

# 或放置 SA 金鑰
GOOGLE_APPLICATION_CREDENTIALS=YOUR_SERVICE_ACCOUNT.json
```

## 🚀 使用方式

### 方式一：前景執行（除錯用）

```bash
# 使用 bat 腳本
start_server.bat

# 或直接使用 uvicorn
python -m uvicorn genaiwrapper.main:app --host 127.0.0.1 --port 8000
```

### 方式二：背景執行（常駐服務）

```bash
# 使用 vbs 腳本（無視窗）
start_server.vbs
```

### 測試 API

服務啟動後，可以使用 `curl` 測試：

```bash
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"xai/grok-4.20-reasoning","stream":true,"messages":[{"role":"user","content":"Hello"}]}'
```

或使用 Python:

```python
import httpx

response = httpx.post(
    "http://127.0.0.1:8000/v1/chat/completions",
    json={
        "model": "xai/grok-4.20-reasoning",
        "stream": True,
        "messages": [{"role": "user", "content": "Hello"}]
    }
)

for line in response.iter_lines():
    print(line.decode("utf-8"))
```

### 停止服務

如果使用背景執行，可以透過工作管理員尋找並終止 `pythonw.exe` 程序，或使用以下命令：

```bash
# 尋找並終止相關程序
taskkill /F /IM pythonw.exe
```

## ⚙️ 開發

### Linting
Linting settings are defined in [pyproject.toml](pyproject.toml) under `[tool.ruff]`.
```shell
ruff check genaiwrapper --fix
ruff format genaiwrapper
```

### Unit Testing & Coverage
Pytest settings are defined in [pyproject.toml](pyproject.toml) under `[tool.pytest]`.
```shell
pytest --cov=genaiwrapper tests
```

## 🤖 AI-Assisted Development Guidelines
This project fully supports Vibecoding.
- Basic Guidelines: An AI development guide is provided in [AGENTS.md](AGENTS.md).
- Model Context Protocol: This project has configured MCP connections for [Qwen Code](https://github.com/QwenLM/qwen-code). You can find the relevant configurations in the [.qwen](.qwen) directory.
