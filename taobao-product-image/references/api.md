# API 契约与错误码

本文件描述 skill 使用的两个后端 API 的关键细节，给 agent 排查错误时参考。

## OpenAI Images API

### POST `/v1/images/edits`（图生图）

**请求**：multipart/form-data
- `image`: 二进制图片（必需）
- `prompt`: 文本引导
- `model`: 如 `gpt-image-1-mini`
- `size`: 如 `1024x1024`
- `quality`: `low` / `medium` / `high`
- `n`: 固定 `1`

**响应**：
```json
{
  "created": 1234567890,
  "data": [
    {"b64_json": "<base64 PNG>"}
  ]
}
```
或：
```json
{
  "data": [{"url": "https://..."}]
}
```

gateway 同时支持两种返回，自动转成 bytes。

### POST `/v1/images/generations`（文生图）

**请求**：JSON
```json
{
  "model": "gpt-image-1-mini",
  "prompt": "...",
  "size": "1536x1024",
  "quality": "low",
  "n": 1
}
```

**响应**：同上。

### 支持的 size

| 比例 | size | 适用 |
|------|------|------|
| 1:1 | `1024x1024` | 主图 |
| 3:2 | `1536x1024` | A+ 详情页 |
| 2:3 | `1024x1536` | 竖幅模特图 |
| 自定义 | gpt-image-1 系列 | 任意纵横比 |

### 模型矩阵（2026-07 价格）

| model | low | medium | high | 备注 |
|-------|-----|--------|------|------|
| `gpt-image-1-mini` | $0.005 | $0.011 | $0.040 | 默认，便宜 |
| `gpt-image-1` | $0.025 | $0.065 | $0.210 | 高质量 |
| `gpt-image-2` | $0.006 | $0.053 | $0.211 | 2026-10 替代 -1 |

**注意**：`gpt-image-1` 系列将于 2026-10-23 被 `gpt-image-2` 替代，但接口形态不变。

### 错误码

| HTTP | 含义 | gateway 行为 |
|------|------|-------------|
| 400 | 参数非法 / 内容被拒 | 抛 `ContentRejectedError`，不重试 |
| 401/402/403 | key 错误 / 余额不足 | 抛 `AuthError`，停止 |
| 429 | 限速 | 抛 `ContentRejectedError`，不重试 |
| 5xx | 服务端 | 抛 `TransientError`，自动重试 1 次 |

### 响应体 error 字段

```json
{
  "error": {
    "message": "...",
    "type": "invalid_request_error",
    "code": "content_policy_violation"
  }
}
```

`code` 含 `moderation` / `policy` / `safety` → 内容被拒，让用户改 prompt。

---

## 智谱 CogVideoX API

### POST `/api/paas/v4/videos/generations`（提交任务）

**鉴权**：JWT Bearer token（用 `id.secret` 签 HS256，1 小时过期）

**请求**：JSON
```json
{
  "model": "CogVideoX-Flash",
  "image_request": {"url": "data:image/jpeg;base64,..."},
  "prompt": "...",
  "video_request": {
    "duration": 5,
    "resolution": "1920x1080"
  }
}
```

**响应**：
```json
{
  "id": "<task_id>",
  "model": "CogVideoX-Flash",
  "task_status": "PROCESSING"
}
```

### GET `/api/paas/v4/async-result/<task_id>`（轮询）

**响应**：
```json
{
  "task_status": "PROCESSING",
  "requestId": "..."
}
```

SUCCESS 时：
```json
{
  "task_status": "SUCCESS",
  "video_result": {
    "url": "https://...",
    "cover_image_url": "https://..."
  }
}
```

FAIL 时：
```json
{
  "task_status": "FAIL",
  "fail": {...}
}
```

### 错误码

| HTTP | 含义 | gateway 行为 |
|------|------|-------------|
| 401 | key 错误或 JWT 过期 | 抛 `AuthError` |
| 400 | 参数非法 / 内容违规 | 抛 `ContentRejectedError` |
| 429 | 限速 / 积分不足 | 抛 `ContentRejectedError` |
| 5xx | 服务端 | 自动重试 1 次 |

### JWT 签名

智谱 JWT 用 `id.secret` 格式 key：
- header: `{"alg":"HS256","sign_type":"SIGN"}`
- payload: `{"api_key":"<id>","exp":<now_ms+3600000>,"timestamp":<now_ms>}`
- 签名：HS256 with secret

skill 内 `_gateway._zhipu_jwt()` 用 stdlib hmac + hashlib 实现，不依赖 PyJWT。

---

## 自检命令

```bash
python3 <skill>/scripts/generate.py --self-test
```

会跑一次最小 A+ 文生图调用（`quality=low`，约 $0.005），如果连通正常会输出 `PASS: generated test image -> <path>`，否则按错误码分流。

**建议每次配完 key 都先跑一次自检**，确认模型名、base_url、key 都对。
