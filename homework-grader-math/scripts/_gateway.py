"""
模型服务调用基础设施：视觉/文本对话、图片预处理、JSON 解析。

本技能不绑定任何 agent runtime，配置自包含（见 _config.py）。
核心函数：
- call_chat()              调 OpenAI 兼容 API（支持图+文 multimodal，也支持纯文本）
- call_with_retry()        带重试（限流/网络错误退避）
- encode_image_data_uri()  图片预处理（长边≤1600px、JPEG q85 压缩、base64）
- extract_json_from_text() 健壮 JSON 解析（去围栏、括号深度匹配）
- resolve_gateway_config() 解析连接信息（委托给 _config，返回 base_url/api_key/model）
- check_vision_support()   视觉能力自检

设计原则：
- 只用 Python 标准库（urllib）做 HTTP，不依赖 requests
- Pillow 懒加载，缺失时降级为原始字节 base64
- 视觉模型不可用时抛 VisionNotSupportedError，由上层决定是否走 OCR 兜底
- 单题 LLM 失败不阻断整次批改（上层有兜底）
"""

import base64
import io
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_GATEWAY_HOST = "127.0.0.1"
DEFAULT_GATEWAY_PORT = 18789
DEFAULT_MODEL = "openclaw/default"
MAX_RETRIES = 2
RETRY_DELAY = 5  # 秒
MAX_IMAGE_DIM = 1600
REQUEST_TIMEOUT = 300  # 秒


class VisionNotSupportedError(Exception):
    """模型不支持图片处理时抛出（能力缺失，重试无用）。"""
    pass


class VisionUnavailableError(Exception):
    """视觉服务不可用时抛出（超时/断开/限流，可重试或换模型）。"""
    pass


# ---------------------------------------------------------------------------
# 错误识别
# ---------------------------------------------------------------------------

def _is_vision_error(error_body: str, status_code: int) -> bool:
    """判断 API 错误是否表示模型不支持视觉。"""
    if status_code not in (400, 422, 500):
        return False
    body_lower = error_body.lower()
    keywords = [
        "does not support image", "not support images",
        "unsupported content_type",
        "vision", "multimodal", "不支持图片",
    ]
    return any(kw in body_lower for kw in keywords)


def is_blind_response(text: str) -> bool:
    """判断模型回复是否表示它看不到图片。

    覆盖口语化表达（"没看到你发的图""图片呢""重新上传"等）。
    """
    blind_phrases = [
        "未检测到图片", "没有附上图片", "没看到任何", "无法看到图片",
        "未检测到图片或文件", "没有收到图片", "请上传", "请发送图片",
        # 口语化
        "没看到", "看不到", "没有图片", "图片呢", "你发图了吗",
        "重新上传", "重新发", "没收到图", "没收到你",
        "没有附件", "没有看到图", "图片附件",
        # 英文
        "no image", "cannot see", "not detected", "didn't receive",
        "no attachment", "can't see",
    ]
    t = text.lower()
    if any(p.lower() in t for p in blind_phrases):
        return True
    return bool(re.search(r"没有收到.{0,12}图", text))


# ---------------------------------------------------------------------------
# 图片预处理
# ---------------------------------------------------------------------------

def encode_image_data_uri(image_path: str) -> str:
    """把图片编码成 data URI。

    用 Pillow 做预处理：长边超过 1600px 缩放、RGBA/P 转 RGB、JPEG quality 85 压缩。
    Pillow 缺失时降级为读取原始字节直接 base64。
    """
    ext = Path(image_path).suffix.lower()
    media_types = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".gif": "image/gif",
        ".bmp": "image/bmp", ".tiff": "image/tiff",
    }
    media_type = media_types.get(ext, "image/jpeg")

    if HAS_PIL:
        img = Image.open(image_path)
        if img.width > MAX_IMAGE_DIM or img.height > MAX_IMAGE_DIM:
            img.thumbnail((MAX_IMAGE_DIM, MAX_IMAGE_DIM), Image.LANCZOS)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        fmt = "JPEG" if media_type == "image/jpeg" else "PNG"
        media_type = "image/jpeg" if fmt == "JPEG" else "image/png"
        img.save(buf, format=fmt, quality=85)
        raw = buf.getvalue()
    else:
        with open(image_path, "rb") as f:
            raw = f.read()

    b64 = base64.standard_b64encode(raw).decode("utf-8")
    return f"data:{media_type};base64,{b64}"


# ---------------------------------------------------------------------------
# 视觉/文本对话调用
# ---------------------------------------------------------------------------

def call_chat(base_url: str, token: str, prompt: str,
              image_path: str | None = None, model: str = DEFAULT_MODEL,
              timeout: int = REQUEST_TIMEOUT, ocr_text: str | None = None) -> str:
    """调用 OpenAI 兼容的 chat completions 接口。

    - image_path 非空：走 multimodal（图+文）
    - ocr_text 非空：走纯文本（把 OCR 文本拼进 prompt）
    - 都为空：纯文本对话
    返回模型回复的文本。
    """
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    if image_path is not None:
        # 视觉模式：图片 + 文本
        data_uri = encode_image_data_uri(image_path)
        payload = {
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_uri}},
                    {"type": "text", "text": prompt},
                ],
            }],
            "max_tokens": 4096,
        }
    elif ocr_text is not None:
        # OCR 文本模式
        full_text = f"{prompt}\n\n--- OCR 识别文本 ---\n{ocr_text}"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": full_text}],
            "max_tokens": 4096,
        }
    else:
        # 纯文本模式
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4096,
        }

    body = json.dumps(payload).encode("utf-8")
    req = Request(f"{base_url}/v1/chat/completions", data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        if _is_vision_error(error_body, e.code):
            raise VisionNotSupportedError(
                f"Model does not support images (HTTP {e.code}): {error_body[:200]}"
            )
        raise
    return result["choices"][0]["message"]["content"]


def call_with_retry(base_url: str, token: str, prompt: str,
                    image_path: str | None = None, model: str = DEFAULT_MODEL,
                    retries: int = MAX_RETRIES, ocr_text: str | None = None,
                    timeout: int = REQUEST_TIMEOUT) -> str:
    """带重试的调用。网络错误/限流按退避重试；视觉不支持不重试。

    限流（HTTP 429/503）用更长退避；普通网络错误用短退避。
    """
    import time
    last_err = None
    for attempt in range(retries + 1):
        try:
            return call_chat(base_url, token, prompt,
                             image_path=image_path, model=model,
                             ocr_text=ocr_text, timeout=timeout)
        except VisionNotSupportedError:
            raise  # 不重试，交给上层兜底
        except HTTPError as e:
            # 限流类错误：更长退避
            if e.code in (429, 503):
                last_err = e
                if attempt < retries:
                    time.sleep(RETRY_DELAY * (attempt + 1) * 2)  # 限流退避加倍
                else:
                    raise
            else:
                last_err = e
                if attempt < retries:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                else:
                    raise
        except (URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                raise
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                raise
    raise last_err  # type: ignore


# ---------------------------------------------------------------------------
# JSON 解析
# ---------------------------------------------------------------------------

def extract_json_from_text(text: str) -> dict:
    """健壮地从模型回复中提取 JSON。

    依次尝试：
    1. 直接 json.loads
    2. 剥 ```json ... ``` 围栏
    3. 括号深度匹配第一个 {...}
    """
    text = text.strip()
    # 1. 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. 剥围栏
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 3. 括号深度匹配
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break

    raise json.JSONDecodeError("No valid JSON found in response", text, 0)


# ---------------------------------------------------------------------------
# 连接配置解析（委托给 _config 模块）
# ---------------------------------------------------------------------------

def resolve_gateway_config(host: str | None = None, port: int | None = None,
                           token: str | None = None, model: str | None = None) -> tuple:
    """解析模型服务连接信息。返回 (base_url, api_key, model)。

    本技能不绑定任何 agent runtime，配置自包含：
    优先级：显式参数 > 环境变量(HWM_*) > config.json > (可选)openclaw fallback。

    注意：返回 (base_url, api_key, model) 三元组，base_url 是完整 URL（如 https://deepkey.top）。
    旧调用方若按 (host, port, token) 接收也能用——base_url 当 host、api_key 当 token。
    """
    from _config import resolve_connection
    conn = resolve_connection(host=host, port=port, token=token, model=model)
    return conn["base_url"], conn["api_key"], conn["model"]


def gateway_base_url(host: str, port: int) -> str:
    """拼接基础 URL（兼容旧调用）。

    注意：现在的 resolve_gateway_config 已直接返回完整 base_url，
    所以此函数主要用于显式传了 host/port 的场景。
    """
    # 如果 host 已经是完整 URL（http 开头），直接返回
    if host and host.startswith("http"):
        return host
    port_str = f":{port}" if port else ""
    return f"http://{host}{port_str}"
    return f"http://{host}:{port}"


# ---------------------------------------------------------------------------
# 视觉能力自检
# ---------------------------------------------------------------------------

def check_vision_support(base_url: str, token: str, model: str = DEFAULT_MODEL) -> bool:
    """自检当前模型通道是否支持图片。

    生成一张写有特定字符串的图片发给模型，看能否读出。
    安装前/首次使用前调用，避免把不能看图的模型丢给老师。
    """
    if not HAS_PIL:
        return False
    try:
        from PIL import Image, ImageDraw
        import tempfile
        # 生成测试图
        img = Image.new("RGB", (300, 100), "white")
        draw = ImageDraw.Draw(img)
        draw.text((40, 35), "MATH42", fill="black")
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp, format="PNG")
            tmp_path = tmp.name
        try:
            resp = call_chat(base_url, token, "请读出图片里的文字，只回复文字内容。",
                             image_path=tmp_path, model=model, timeout=60)
            return "math42" in resp.lower()
        finally:
            Path(tmp_path).unlink(missing_ok=True)
    except VisionNotSupportedError:
        return False
    except Exception:
        return False
