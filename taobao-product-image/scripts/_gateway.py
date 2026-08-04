"""HTTP gateway for taobao-product-image skill.

Pure stdlib HTTP client (urllib + json + hmac). No requests/openai SDK.

Covers:
  - OpenAI /images/edits (multipart, image+prompt) — image-to-image
  - OpenAI /images/generations (JSON, text-to-image) — for A+ detail page
  - Zhipu CogVideoX async submit + poll (JWT auth, image-to-video)

Compatible with Python 3.9+.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    from PIL import Image  # type: ignore
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class GatewayError(Exception):
    """Base gateway error with HTTP status and raw body."""

    def __init__(self, message: str, status: int = 0, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


class AuthError(GatewayError):
    """401/402/403 — stop, don't retry."""


class ContentRejectedError(GatewayError):
    """400/429 with policy/moderation flag — don't retry, user must fix prompt."""


class TransientError(GatewayError):
    """Network/timeout/5xx — caller may retry once."""


# ---------------------------------------------------------------------------
# Image encoding & fetching
# ---------------------------------------------------------------------------

def encode_image(path_or_url: str, max_long_edge: int = 1568) -> Tuple[str, str, bytes]:
    """Resolve an image input into (mime_type, filename, raw_bytes).

    - Local path → read bytes; if Pillow is available, re-encode to JPEG q87
      with long edge <= max_long_edge. Falls back to raw bytes if Pillow missing.
    - HTTP(S) URL → fetched raw (no re-encoding), mime sniffed from URL/headers.

    Returns (mime, filename, bytes) always (no sentinel).
    """
    if path_or_url.startswith(("http://", "https://")):
        data = _fetch_url_raw(path_or_url)
        # Sniff mime from magic bytes.
        mime = _sniff_mime(data)
        fname = path_or_url.rsplit("/", 1)[-1].split("?")[0] or "image.jpg"
        return (mime, fname, data)

    p = Path(path_or_url)
    if not p.exists():
        raise GatewayError(f"本地图片不存在: {path_or_url}")
    raw = p.read_bytes()
    filename = p.name
    ext = p.suffix.lower()

    if _HAS_PIL:
        try:
            img = Image.open(io.BytesIO(raw))
            w, h = img.size
            scale = max_long_edge / max(w, h)
            if scale < 1.0:
                img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=87, optimize=True)
            return ("image/jpeg", Path(filename).stem + ".jpg", buf.getvalue())
        except Exception:
            pass  # fall through

    mime = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(ext, "application/octet-stream")
    return (mime, filename, raw)


def _sniff_mime(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"  # default


def _fetch_url_raw(url: str, timeout: int = 120) -> bytes:
    """Fetch an HTTP URL into raw bytes."""
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise GatewayError(f"HTTP {e.code} fetching {url}", status=e.code)
    except urllib.error.URLError as e:
        raise TransientError(f"Network error fetching {url}: {e}")


# ---------------------------------------------------------------------------
# Multipart form-data builder (stdlib only)
# ---------------------------------------------------------------------------

def _build_multipart(
    fields: Dict[str, str],
    files: Dict[str, Tuple[str, str, bytes]],
    boundary: str,
) -> bytes:
    """Build a multipart/form-data body. Caller controls boundary."""
    crlf = b"\r\n"
    parts: list = []

    for name, value in fields.items():
        parts.append(f"--{boundary}".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        parts.append(b"")
        parts.append(value.encode("utf-8"))

    for name, (filename, mime, data) in files.items():
        parts.append(f"--{boundary}".encode())
        parts.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode()
        )
        parts.append(f"Content-Type: {mime}".encode())
        parts.append(b"")
        parts.append(data)

    parts.append(f"--{boundary}--".encode())
    parts.append(b"")
    return crlf.join(parts)


# ---------------------------------------------------------------------------
# Core HTTP wrapper with retry policy
# ---------------------------------------------------------------------------

def _http_request(
    method: str,
    url: str,
    headers: Dict[str, str],
    body: Optional[bytes],
    timeout: int = 120,
    retry_transient: bool = True,
) -> Tuple[int, str]:
    """Issue HTTP request with single-retry on transient errors (network/5xx)."""
    last_exc: Optional[TransientError] = None
    attempts = 2 if retry_transient else 1

    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, data=body, method=method, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                err_body = ""
            if e.code in (401, 402, 403):
                raise AuthError(f"Auth failed ({e.code})", status=e.code, body=err_body)
            if e.code == 429:
                raise ContentRejectedError("429 rejected", status=429, body=err_body)
            if e.code >= 500 and attempt < attempts - 1:
                last_exc = TransientError(f"5xx {e.code}", status=e.code, body=err_body)
                time.sleep(2)
                continue
            if e.code == 400:
                raise ContentRejectedError("400 bad request", status=400, body=err_body)
            raise GatewayError(f"HTTP {e.code}", status=e.code, body=err_body)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < attempts - 1:
                last_exc = TransientError(f"Network: {e}")
                time.sleep(2)
                continue
            raise TransientError(f"Network failed after retry: {e}")

    raise last_exc or TransientError("Unknown transient failure")


# ---------------------------------------------------------------------------
# OpenAI image generation
# ---------------------------------------------------------------------------

def call_openai_edits(
    openai_cfg: Dict[str, Any],
    image_input: str,
    prompt: str,
    model: str,
    size: str = "1024x1024",
    quality: str = "low",
) -> bytes:
    """Call /v1/images/edits (image-to-image with prompt guidance).

    image_input: local path or HTTP(S) URL — will be encoded/fetched.
    Returns raw PNG bytes of the generated image.
    """
    base_url = openai_cfg["base_url"].rstrip("/")
    api_key = openai_cfg["api_key"]
    url = f"{base_url}/images/edits"

    mime, fname, data = encode_image(image_input)

    boundary = "----taobao-img-" + uuid.uuid4().hex
    fields = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "quality": quality,
        "n": "1",
    }
    files = {"image": (fname, mime, data)}
    body = _build_multipart(fields, files, boundary)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }

    status, resp_body = _http_request("POST", url, headers, body, timeout=180)
    return _extract_image_bytes(resp_body, status, "edits")


def call_openai_generations(
    openai_cfg: Dict[str, Any],
    prompt: str,
    model: str,
    size: str = "1536x1024",
    quality: str = "low",
) -> bytes:
    """Call /v1/images/generations (text-to-image). Used for A+ detail page."""
    base_url = openai_cfg["base_url"].rstrip("/")
    api_key = openai_cfg["api_key"]
    url = f"{base_url}/images/generations"

    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "quality": quality,
        "n": 1,
    }
    body = json.dumps(payload).encode()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    status, resp_body = _http_request("POST", url, headers, body, timeout=180)
    return _extract_image_bytes(resp_body, status, "generations")


def _extract_image_bytes(resp_text: str, status: int, endpoint: str) -> bytes:
    """Parse OpenAI image response. Handles both b64_json and url formats."""
    try:
        data = json.loads(resp_text)
    except json.JSONDecodeError:
        raise GatewayError(
            f"非 JSON 响应 ({endpoint})", status=status, body=resp_text[:500]
        )

    if "error" in data:
        err = data["error"]
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        code = err.get("code", "") if isinstance(err, dict) else ""
        lc = (msg + " " + code).lower()
        if "moderation" in lc or "policy" in lc or "safety" in lc:
            raise ContentRejectedError(f"内容被拒绝: {msg}", status=status, body=resp_text)
        raise GatewayError(f"API error: {msg}", status=status, body=resp_text)

    images = data.get("data") or []
    if not images:
        raise GatewayError(f"无图片返回 ({endpoint})", status=status, body=resp_text)
    item = images[0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        return _fetch_url_raw(item["url"])
    raise GatewayError(f"未知响应格式 ({endpoint})", status=status, body=resp_text[:500])


# ---------------------------------------------------------------------------
# Zhipu CogVideoX async submit + poll
# ---------------------------------------------------------------------------

def _zhipu_jwt(api_key: str) -> str:
    """Build Zhipu JWT token from api_key (id.secret format).

    Uses HS256, exp = now + 1 hour. Stdlib only — no PyJWT.
    """
    try:
        key_id, secret = api_key.split(".", 1)
    except ValueError:
        raise AuthError(
            "ZHIPU_API_KEY 格式错误（应为 id.secret）",
            status=401, body=api_key[:8] + "...",
        )

    def b64url(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")

    header = {"alg": "HS256", "sign_type": "SIGN"}
    now_ms = int(time.time() * 1000)
    payload = {"api_key": key_id, "exp": now_ms + 3600 * 1000, "timestamp": now_ms}

    header_b64 = b64url(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{b64url(sig)}"


def submit_zhipu_video(
    zhipu_cfg: Dict[str, Any],
    image_b64: str,
    prompt: str,
    model: str = "CogVideoX-Flash",
    duration: int = 5,
    size: str = "1920x1080",
) -> str:
    """Submit a Zhipu image-to-video task. Returns task_id."""
    base_url = zhipu_cfg["base_url"].rstrip("/")
    api_key = zhipu_cfg["api_key"]
    token = _zhipu_jwt(api_key)
    url = f"{base_url}/videos/generations"

    payload = {
        "model": model,
        "image_request": {"url": f"data:image/jpeg;base64,{image_b64}"},
        "prompt": prompt,
        "video_request": {"duration": duration, "resolution": size},
    }
    body = json.dumps(payload).encode()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    status, resp_body = _http_request("POST", url, headers, body, timeout=60)
    try:
        data = json.loads(resp_body)
    except json.JSONDecodeError:
        raise GatewayError("非 JSON 响应 (zhipu submit)", status=status, body=resp_body[:500])

    if "error" in data:
        raise GatewayError(
            f"Zhipu error: {data['error']}", status=status, body=resp_body[:500]
        )
    task_id = data.get("id") or data.get("task_id")
    if not task_id:
        raise GatewayError("无 task_id 返回", status=status, body=resp_body[:500])
    return task_id


def poll_zhipu_task(
    zhipu_cfg: Dict[str, Any],
    task_id: str,
    timeout_sec: int = 600,
    interval_sec: int = 10,
) -> str:
    """Poll Zhipu task until done. Returns video URL."""
    base_url = zhipu_cfg["base_url"].rstrip("/")
    api_key = zhipu_cfg["api_key"]
    token = _zhipu_jwt(api_key)
    url = f"{base_url}/async-result/{task_id}"
    headers = {"Authorization": f"Bearer {token}"}
    deadline = time.time() + timeout_sec

    while time.time() < deadline:
        status, resp_body = _http_request(
            "GET", url, headers, None, timeout=30, retry_transient=True
        )
        try:
            data = json.loads(resp_body)
        except json.JSONDecodeError:
            raise GatewayError(
                "非 JSON 响应 (zhipu poll)", status=status, body=resp_body[:500]
            )

        task_status = data.get("task_status", "")
        if task_status == "SUCCESS":
            video_url = (
                (data.get("video_result") or {}).get("url")
                or data.get("video_url")
            )
            if not video_url:
                raise GatewayError(
                    "SUCCESS 但无 video_url", status=status, body=resp_body[:500]
                )
            return video_url
        if task_status == "FAIL":
            raise GatewayError(
                f"视频生成失败: {data}", status=status, body=resp_body[:500]
            )
        time.sleep(interval_sec)

    raise TransientError(f"轮询超时 {timeout_sec}s, task_id={task_id}")


def download_video(video_url: str, out_path: Path) -> Path:
    """Download a video URL to local path."""
    data = _fetch_url_raw(video_url)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    return out_path
