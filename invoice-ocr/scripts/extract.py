#!/usr/bin/env python3
"""
Invoice OCR extraction script for fabric cost accounting.
Extracts delivery note data from supplier photos and outputs JSON
matching the default invoice-entry column schema.
Calls the OpenClaw gateway's OpenAI-compatible API.
"""

import argparse
import base64
import io
import json
import os
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"}

DEFAULT_GATEWAY_HOST = "127.0.0.1"
DEFAULT_GATEWAY_PORT = 18789
MAX_RETRIES = 2
RETRY_DELAY = 5  # seconds between retries


class VisionNotSupportedError(Exception):
    """Raised when the model cannot process images."""
    pass


def _is_vision_error(error_body: str, status_code: int) -> bool:
    """Check if an API error indicates vision is not supported."""
    if status_code not in (400, 422, 500):
        return False
    body_lower = error_body.lower()
    keywords = [
        "does not support image", "not support images",
        "unsupported content_type",
        "vision", "multimodal", "不支持图片",
    ]
    return any(kw in body_lower for kw in keywords)

def _is_blind_response(text: str) -> bool:
    """Check if model response indicates it cannot see/process the image."""
    blind_phrases = [
        "未检测到图片", "没有附上图片", "没看到任何", "无法看到图片",
        "未检测到图片或文件", "没有收到图片", "请上传", "请发送图片",
        "no image", "cannot see", "not detected",
    ]
    t = text.lower()
    if any(p in t for p in blind_phrases):
        return True
    return bool(re.search(r"没有收到.{0,12}图片", t))


EXTRACT_PROMPT = """分析这张服装工厂的票据图片，先判断票据类型，再提取结构化数据。

【第一步：判断票据类型】
这是服装工厂的票据录入场景。票据可能是以下两种之一：
- **送货单/出仓单**：面料、辅料供应商发货的单据，有品名、色号、数量、单价等
- **加工单/洗水单**：加工厂（水洗、砂洗、印花、绣花等）的服务单据，有加工项目、件数、加工费

判断依据：
- 如果是购买面料/辅料 → document_type = "delivery"
- 如果是委托加工服务（洗水、砂洗、印花、绣花等）→ document_type = "processing"
- 单据抬头含"洗水"、"水洗"、"砂洗"、"印花"、"绣花"、"加工"等字样 → processing

【第二步：按类型提取数据】
输出 JSON 格式，严格遵循以下 schema：
{
  "document_type": "delivery | processing",
  "document_title": "string (票据抬头/标题，如'销售码单','送货单','购销协议','洗水加工送货单')",
  "delivery_note": {
    "supplier_name": "string or null (供应商/厂家名)",
    "note_number": "string or null (单号)",
    "date": "YYYY-MM-DD or null (开单日期)",
    "customer": "string or null (客户名，即我方公司名)"
  },
  "items": [
    {
      "row_number": 1,
      "material_type": "面料|相色|螺纹|印花|扣子|拉链|砂洗|洗水|织带|拉条|披肩|钉扣|其他",
      "material_name": "string or null (品名/面料名称/加工项目名)",
      "supplier": "string or null (厂家名，如果和表头不同)",
      "fabric_code": "string or null (面料款号/货号。加工单填null)",
      "fabric_code_is_handwritten": true | false,
      "style_number": "string or null (本厂服装款号：仅当编号以26开头或以#结尾时填入；E35101/35101等厂内货号不得填入，填null)",
      "color_code": "string or null (色号/颜色)",
      "unit_price": number or null (单价)",
      "quantity": number or null (数量)",
      "unit": "米|公斤|件|个|码|null (单位)",
      "total_amount": number or null (金额 = 单价 × 数量)",
      "remark": "string or null (备注)"
    }
  ],
  "total_amount": number or null (合计金额)",
  "confidence": "high" | "medium" | "low",
  "needs_review": ["需要人工复核的字段路径"],
  "raw_text_notes": "额外观察说明"
}

关键规则：
1. **document_type 必须先判断**：delivery（送货单）或 processing（加工单）
2. **送货单规则**：
   - 手写内容与打印内容需区分，手写货号标记 fabric_code_is_handwritten: true
   - 货名/品名后面紧跟的手写编号视为面料款号(fabric_code)
   - fabric_code 是供应商的面料产品编号
   - 若款号只出现在摘要、表头、页脚、备注或手写区，而明细行无款号列：将识别到的、**且通过下方【款号 style_number 判定】**的编号写入**每一行** items 的 style_number（整单通常同一款号）；并在 raw_text_notes 中简要注明款号所在区域
3. **加工单规则**：
   - fabric_code 填 null（加工单无面料款号）
   - material_name 填加工项目描述（如"牛仔长裤 2位猫须+风磨+磨边"）
   - style_number 仅当加工相关编号**通过下方【款号 style_number 判定】**时填写（常手写）；否则 null
   - 单价是加工费单价（如 6.8元/件）
   - material_type 填对应的加工类型（砂洗、洗水、印花等）
4. 金额统一为数字，去掉 ¥ 和逗号
5. 无法辨认的字段设为 null 并在 needs_review 中列出
6. 检查明细金额合计是否等于 total_amount，不等则标注 "total_mismatch"
7. 如果有多个明细行，按行号顺序全部提取
8. **【款号 style_number 判定（本厂服装款号）】** — 与 fabric_code/货号区分；仅当**至少一条**满足时可写入 style_number，否则为 null、勿猜：
   - 编号去空格后，**以 26 开头**（可含字母前缀，如 A26036-2 视为以 26 起头的款号段）
   - 或**以 # 结尾**（如 26018#、671008-010#、177#）
9. **即使出现在货号列**，也**不得**将 `E35101`、`35101` 写入 style_number（可写 remark 或适当时 fabric_code）
10. 同单多码时，仅将**符合第 8 条**的写入 style_number；勿用厂内码充当款号

只输出 JSON，不要输出其他内容。"""

MAX_IMAGE_DIM = 1600


def encode_image_data_uri(image_path: str) -> str:
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


EXTRACT_TEXT_PROMPT = """以下是 OCR 识别的服装工厂票据文本，请提取结构化数据。

OCR 可能有识别错误（如数字混淆、乱码），请根据上下文推断正确内容。

先判断票据类型，再提取数据：
- 如果是购买面料/辅料 → document_type = "delivery"
- 如果是委托加工服务（洗水、砂洗、印花、绣花等）→ document_type = "processing"

输出 JSON 格式，严格遵循以下 schema：
{
  "document_type": "delivery | processing",
  "document_title": "string (票据抬头/标题，如'销售码单','送货单','购销协议')",
  "delivery_note": {
    "supplier_name": "string or null (供应商/厂家名)",
    "note_number": "string or null (单号)",
    "date": "YYYY-MM-DD or null (开单日期)",
    "customer": "string or null (客户名，即我方公司名)"
  },
  "items": [
    {
      "row_number": 1,
      "material_type": "面料|相色|螺纹|印花|扣子|拉链|砂洗|洗水|织带|拉条|披肩|钉扣|其他",
      "material_name": "string or null (品名/面料名称/加工项目名)",
      "supplier": "string or null (厂家名，如果和表头不同)",
      "fabric_code": "string or null (面料款号/货号。加工单填null)",
      "fabric_code_is_handwritten": false,
      "style_number": "string or null (本厂服装款号：仅当编号以26开头或以#结尾时填入；E35101/35101等厂内货号不得填入，填null)",
      "color_code": "string or null (色号/颜色)",
      "unit_price": number or null (单价)",
      "quantity": number or null (数量)",
      "unit": "米|公斤|件|个|码|null (单位)",
      "total_amount": number or null (金额 = 单价 × 数量)",
      "remark": "string or null (备注)"
    }
  ],
  "total_amount": number or null (合计金额)",
  "confidence": "high" | "medium" | "low",
  "needs_review": ["需要人工复核的字段路径"],
  "raw_text_notes": "额外观察说明"
}

关键规则：
1. **document_type 必须先判断**：delivery（送货单）或 processing（加工单）
2. **送货单规则**：
   - fabric_code 是供应商的面料产品编号
   - 若款号只出现在摘要、表头、页脚、备注或手写区，而明细行无款号列：将识别到的、**且通过【款号 style_number 判定】**的编号写入**每一行** items 的 style_number（整单通常同一款号）；并在 raw_text_notes 中简要注明款号所在区域
3. **加工单规则**：
   - fabric_code 填 null（加工单无面料款号）
   - material_name 填加工项目描述（如"牛仔长裤 2位猫须+风磨+磨边"）
   - style_number 仅当**通过【款号 style_number 判定】**时填写，否则 null
   - material_type 填对应的加工类型（砂洗、洗水、印花等）
4. 金额统一为数字，去掉 ¥ 和逗号
5. 无法辨认的字段设为 null 并在 needs_review 中列出
6. 检查明细金额合计是否等于 total_amount，不等则标注 "total_mismatch"
7. 如果有多个明细行，按行号顺序全部提取
8. **【款号 style_number 判定】**：与 vision 版规则 8–10 相同：仅 **以 26 开头**或**以 # 结尾**可写 style_number；**不得**写 E35101、35101；同单多码只收符合前述条件的

只输出 JSON，不要输出其他内容。"""

# Lazy-loaded OCR engine singleton
_paddle_ocr_engine = None
_ocr_lock = __import__("threading").Lock()

VISION_CHECK_TEXT = "VISION42"
VISION_CHECK_PROMPT = f"""这是一张安装自检图片。请读取图片中的大字。
只输出 JSON，不要解释：
{{"can_see_image": true, "text": "你看到的文字"}}
如果你看不到图片，只输出：
{{"can_see_image": false, "text": null}}

图片里的文字应该是 {VISION_CHECK_TEXT}。"""


def _notify_progress(progress, filename: str, stage: str, message: str):
    if progress:
        progress(filename, stage, message)


def _create_paddle_ocr():
    from paddleocr import PaddleOCR
    try:
        return PaddleOCR(
            use_angle_cls=True,
            lang='ch',
            show_log=False,
        )
    except Exception as exc:
        if "Unknown argument" not in str(exc) and "show_log" not in str(exc):
            raise
        return PaddleOCR(
            lang='ch',
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=True,
        )


def _collect_ocr_lines(result, min_score: float = 0.3) -> list[str]:
    """Normalize PaddleOCR v2/v3 result shapes into recognized text lines."""
    lines: list[str] = []
    for page in result or []:
        if isinstance(page, dict) and "rec_texts" in page:
            scores = page.get("rec_scores") or []
            for idx, text in enumerate(page.get("rec_texts") or []):
                score = scores[idx] if idx < len(scores) else 1
                if text and score > min_score:
                    lines.append(str(text))
            continue

        if isinstance(page, list):
            for item in page:
                if (
                    isinstance(item, (list, tuple))
                    and len(item) > 1
                    and isinstance(item[1], (list, tuple))
                    and len(item[1]) > 1
                    and item[1][1] > min_score
                ):
                    lines.append(str(item[1][0]))
    return lines


def create_vision_check_image(path: str):
    """Create a tiny local image used to verify image-capable model routing."""
    if not HAS_PIL:
        raise RuntimeError("Pillow is required for vision self-check")
    img = Image.new("RGB", (640, 240), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle((20, 20, 620, 220), outline="black", width=4)
    draw.text((155, 92), VISION_CHECK_TEXT, fill="black")
    img.save(path, format="PNG")


def check_model_vision_support(base_url: str, token: str,
                               model: str = "openclaw/default",
                               timeout: int = 90) -> dict:
    """Return whether the current OpenClaw model route can actually see images."""
    with tempfile.TemporaryDirectory() as tmp:
        image_path = str(Path(tmp) / "vision-check.png")
        create_vision_check_image(image_path)
        try:
            raw = call_openclaw_chat(
                base_url, token, image_path, VISION_CHECK_PROMPT, model, timeout=timeout
            ).strip()
        except VisionNotSupportedError as exc:
            return {
                "vision_supported": False,
                "reason": "model_rejected_image",
                "raw_response": str(exc)[:500],
            }
        except (HTTPError, URLError, OSError, RuntimeError) as exc:
            return {
                "vision_supported": False,
                "reason": "gateway_error",
                "raw_response": str(exc)[:500],
            }

    if _is_blind_response(raw):
        return {
            "vision_supported": False,
            "reason": "model_cannot_see_image",
            "raw_response": raw[:500],
        }

    try:
        data = extract_json_from_text(raw)
    except json.JSONDecodeError:
        data = {}

    text = str(data.get("text") or raw).upper()
    can_see = bool(data.get("can_see_image")) or VISION_CHECK_TEXT in text
    return {
        "vision_supported": can_see and VISION_CHECK_TEXT in text,
        "reason": "ok" if can_see and VISION_CHECK_TEXT in text else "unexpected_response",
        "raw_response": raw[:500],
    }


def ocr_image(image_path: str) -> str:
    """Extract text from image via PaddleOCR. Returns text or empty string."""
    global _paddle_ocr_engine
    try:
        if _paddle_ocr_engine is None:
            with _ocr_lock:
                if _paddle_ocr_engine is None:
                    _paddle_ocr_engine = _create_paddle_ocr()
        if hasattr(_paddle_ocr_engine, "predict"):
            result = _paddle_ocr_engine.predict(image_path)
        else:
            result = _paddle_ocr_engine.ocr(image_path, cls=True)
        lines = _collect_ocr_lines(result)
        if lines:
            return "\n".join(lines)
    except Exception as e:
        print(f"  OCR failed: {e}", file=sys.stderr)
    return ""


def call_openclaw_chat(base_url: str, token: str, image_path: str,
                       prompt: str, model: str = "openclaw/default",
                       timeout: int = 300, ocr_text: str | None = None) -> str:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    if ocr_text is not None:
        # Text-only mode: send OCR text as plain string
        full_text = f"{prompt}\n\n--- OCR 识别文本 ---\n{ocr_text}"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": full_text}],
            "max_tokens": 4096,
        }
    else:
        # Vision mode: send image + prompt
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


def extract_json_from_text(text: str) -> dict:
    """Robustly extract JSON from model response, handling code fences and noise."""
    text = text.strip()
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip code fences: ```json ... ``` or ``` ... ```
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Find first { ... } blob
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


def extract_single(base_url: str, token: str, image_path: str,
                   model: str, retries: int = MAX_RETRIES,
                   prompt_extra: str = "", progress=None) -> dict:
    filename = os.path.basename(image_path)
    last_error = None
    vision_failed = False

    for attempt in range(1, retries + 2):  # 1 initial + retries
        try:
            _notify_progress(progress, filename, "vision_start", "正在识别这张票据。")
            prompt = EXTRACT_PROMPT + prompt_extra if prompt_extra else EXTRACT_PROMPT
            text = call_openclaw_chat(base_url, token, image_path,
                                      prompt, model).strip()
            data = extract_json_from_text(text)
            _check_total(data)
            needs_review = data.get("needs_review", [])
            review_status = "confirmed" if not needs_review else "pending"
            return {
                "filename": filename,
                "status": "success",
                "error": None,
                "data": data,
                "review_status": review_status,
            }
        except VisionNotSupportedError as e:
            print(f"  {filename}: model does not support images, falling back to OCR",
                  file=sys.stderr)
            _notify_progress(
                progress, filename, "ocr_fallback",
                "当前模型不支持图片，我先用本地 OCR 兜底，这张会慢一点。"
            )
            vision_failed = True
            break
        except (json.JSONDecodeError, KeyError) as e:
            last_error = f"JSON parse error: {e}"
            raw = text[:500] if "text" in dir() else ""
            # Check if model said it can't see the image (not a real vision model)
            if raw and _is_blind_response(raw):
                print(f"  {filename}: model cannot see images, falling back to OCR",
                      file=sys.stderr)
                _notify_progress(
                    progress, filename, "ocr_fallback",
                    "当前模型看不到图片，我先用本地 OCR 兜底，这张会慢一点。"
                )
                vision_failed = True
                break
            # Non-retryable: model returned garbage, retrying won't help
            if attempt > retries:
                return {
                    "filename": filename,
                    "status": "error",
                    "error": last_error,
                    "raw_response": raw,
                    "data": None,
                    "review_status": "pending",
                }
        except (URLError, OSError) as e:
            last_error = str(e)
            if attempt <= retries:
                import time
                print(f"  {filename}: attempt {attempt} failed ({e}), retrying...",
                      file=sys.stderr)
                time.sleep(RETRY_DELAY * attempt)
                continue
            return {
                "filename": filename,
                "status": "error",
                "error": f"Network error after {attempt} attempts: {e}",
                "data": None,
                "review_status": "pending",
            }

    if not vision_failed:
        return {
            "filename": filename,
            "status": "error",
            "error": last_error,
            "data": None,
            "review_status": "pending",
        }

    # ── OCR fallback path ──
    ocr_text = ocr_image(image_path)
    if not ocr_text:
        _notify_progress(progress, filename, "ocr_failed", "这张照片暂时没读清楚。")
        return {
            "filename": filename,
            "status": "error",
            "error": "模型不支持图片且 OCR 不可用。请安装: pip install paddleocr paddlepaddle",
            "data": None,
            "review_status": "pending",
        }

    try:
        _notify_progress(progress, filename, "ocr_done", "已读出票据文字，正在整理成表格。")
        prompt = EXTRACT_TEXT_PROMPT + prompt_extra if prompt_extra else EXTRACT_TEXT_PROMPT
        text = call_openclaw_chat(base_url, token, image_path,
                                  prompt, model, ocr_text=ocr_text).strip()
        data = extract_json_from_text(text)
        _check_total(data)
        # OCR results always need review
        data["extraction_method"] = "ocr_fallback"
        if data.get("confidence") == "high":
            data["confidence"] = "medium"
        needs_review = data.get("needs_review", [])
        needs_review.append("ocr_fallback")
        data["needs_review"] = needs_review
        return {
            "filename": filename,
            "status": "success",
            "error": None,
            "data": data,
            "review_status": "pending",  # always pending for OCR
        }
    except (json.JSONDecodeError, KeyError) as e:
        raw = text[:500] if "text" in dir() else ""
        return {
            "filename": filename,
            "status": "error",
            "error": f"OCR fallback JSON parse error: {e}",
            "raw_response": raw,
            "data": None,
            "review_status": "pending",
        }
    except (URLError, OSError) as e:
        return {
            "filename": filename,
            "status": "error",
            "error": f"OCR fallback network error: {e}",
            "data": None,
            "review_status": "pending",
        }


def _check_total(data: dict):
    items = data.get("items", [])
    total = data.get("total_amount")
    if not items or total is None:
        return
    item_sum = sum(it.get("total_amount", 0) or 0 for it in items)
    if abs(item_sum - total) > 0.01:
        needs_review = data.get("needs_review", [])
        if "total_mismatch" not in needs_review:
            needs_review.append("total_mismatch")
        data["needs_review"] = needs_review


def collect_images(directory: str) -> list[str]:
    path = Path(directory)
    if not path.is_dir():
        print(f"Error: {directory} is not a directory", file=sys.stderr)
        sys.exit(1)
    images = sorted(str(f) for f in path.iterdir() if f.suffix.lower() in IMAGE_EXTENSIONS)
    if not images:
        print(f"No images found in {directory}", file=sys.stderr)
        sys.exit(1)
    return images


def check_gateway(base_url: str, token: str):
    """Verify the gateway is reachable and chat completions endpoint is enabled."""
    try:
        req = Request(f"{base_url}/v1/models")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urlopen(req, timeout=10) as resp:
            content_type = resp.headers.get("Content-Type", "")
            body = resp.read().decode("utf-8", errors="replace")
            if "application/json" not in content_type:
                print(f"Error: Gateway at {base_url} returned HTML, not JSON.\n"
                      f"  The chat completions endpoint may not be enabled. Run:\n"
                      f"  openclaw config set gateway.http.endpoints.chatCompletions.enabled true\n"
                      f"  Then restart the gateway.",
                      file=sys.stderr)
                sys.exit(1)
            models = json.loads(body)
            if not models.get("data"):
                print(f"Warning: No models available at gateway", file=sys.stderr)
    except HTTPError as e:
        if e.code == 401:
            print(f"Error: Gateway authentication failed. Set --token or OPENCLAW_GATEWAY_TOKEN.",
                  file=sys.stderr)
        elif e.code == 404:
            print(f"Error: Gateway at {base_url} does not have /v1/models endpoint.\n"
                  f"  Enable with: openclaw config set gateway.http.endpoints.chatCompletions.enabled true",
                  file=sys.stderr)
        else:
            print(f"Error: Gateway returned HTTP {e.code}: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except URLError as e:
        print(f"Error: Cannot reach OpenClaw gateway at {base_url}\n"
              f"  Detail: {e}", file=sys.stderr)
        sys.exit(1)


def load_existing_results(path: str) -> dict | None:
    """Load existing results JSON for append mode."""
    if not path or not Path(path).is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def merge_results(existing: dict, new_results: list[dict], batch_id: str) -> dict:
    """Merge new results into existing batch, skipping duplicate filenames."""
    existing_filenames = {r["filename"] for r in existing.get("results", [])}
    added = []
    skipped = 0
    for r in new_results:
        if r["filename"] in existing_filenames:
            skipped += 1
        else:
            added.append(r)

    merged_results = existing.get("results", []) + added
    merged_results.sort(key=lambda r: r["filename"])

    confirmed = sum(1 for r in merged_results if r["review_status"] == "confirmed")
    pending = sum(1 for r in merged_results if r["review_status"] == "pending")
    errors = sum(1 for r in merged_results if r["status"] == "error")
    total_amount = sum(r["data"].get("total_amount", 0) or 0
                       for r in merged_results if r["status"] == "success" and r.get("data"))

    return {
        "batch_id": existing.get("batch_id", batch_id),
        "created_at": existing.get("created_at", datetime.now().isoformat()),
        "updated_at": datetime.now().isoformat(),
        "total_images": len(merged_results),
        "results": merged_results,
        "summary": {
            "total_invoices": len(merged_results),
            "confirmed": confirmed,
            "pending_review": pending,
            "errors": errors,
            "total_amount": total_amount,
        },
        "_append_info": {
            "added": len(added),
            "skipped_duplicates": skipped,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Extract delivery note data via OpenClaw gateway")
    parser.add_argument("input_dir", help="Directory containing invoice images")
    parser.add_argument("--output", "-o", help="Output JSON file path")
    parser.add_argument("--append", action="store_true",
                        help="Append to existing results JSON (skip duplicate filenames)")
    parser.add_argument("--gateway-host",
                        default=os.environ.get("OPENCLAW_GATEWAY_HOST", DEFAULT_GATEWAY_HOST))
    parser.add_argument("--gateway-port", type=int,
                        default=int(os.environ.get("OPENCLAW_GATEWAY_PORT", str(DEFAULT_GATEWAY_PORT))))
    parser.add_argument("--token", default=os.environ.get("OPENCLAW_GATEWAY_TOKEN", ""))
    parser.add_argument("--model", default="openclaw/default")
    parser.add_argument("--parallel", "-j", type=int, default=3)
    parser.add_argument("--retries", "-r", type=int, default=MAX_RETRIES,
                        help=f"Number of retries per image on failure (default: {MAX_RETRIES})")
    parser.add_argument("--templates-dir", type=str,
                        default=str(Path.home() / ".openclaw" / "skill-state" / "invoice-ocr-templates"),
                        help="Templates directory for supplier context injection")
    parser.add_argument("--no-template", action="store_true",
                        help="Disable template context injection")
    args = parser.parse_args()

    base_url = f"http://{args.gateway_host}:{args.gateway_port}"
    check_gateway(base_url, args.token)

    # Build supplier context from templates
    prompt_extra = ""
    templates_dir = Path(args.templates_dir)
    if not args.no_template:
        sys.path.insert(0, str(Path(__file__).parent))
        from templates import build_supplier_context, post_process_extraction
        prompt_extra = build_supplier_context(templates_dir)
        if prompt_extra:
            print(f"Loaded supplier context from templates.")
    else:
        sys.path.insert(0, str(Path(__file__).parent))
        from templates import post_process_extraction

    output_path = args.output or str(Path(args.input_dir) / "results.json")

    # In append mode, skip images already in existing results
    existing_filenames = set()
    if args.append:
        existing = load_existing_results(output_path)
        if existing:
            existing_filenames = {r["filename"] for r in existing.get("results", [])}
            print(f"Append mode: {len(existing_filenames)} existing results in {output_path}")

    all_images = collect_images(args.input_dir)
    images = [img for img in all_images
              if os.path.basename(img) not in existing_filenames]
    skipped_count = len(all_images) - len(images)

    if skipped_count:
        print(f"Skipped {skipped_count} already-processed images.")
    if not images:
        print("No new images to process.")
        return

    print(f"Processing {len(images)} new delivery notes via OpenClaw ({args.model})...")

    results = []
    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        futures = {executor.submit(extract_single, base_url, args.token,
                                   img, args.model, args.retries, prompt_extra): img
                   for img in images}
        for future in as_completed(futures):
            img = futures[future]
            try:
                result = future.result()
                # Programmatic post-processing: apply template corrections
                if result.get("status") == "success" and not args.no_template:
                    result = post_process_extraction(result, templates_dir)
                needs = len(result.get("data", {}).get("needs_review", [])) if result.get("data") else 0
                items = len(result.get("data", {}).get("items", [])) if result.get("data") else 0
                auto = " [auto-confirmed]" if result.get("auto_confirmed") else ""
                tmpl = f" [template: {result.get('template_matched')}]" if result.get("template_matched") else ""
                print(f"  {os.path.basename(img)}: {result['review_status']}{auto}{tmpl}"
                      f" ({items} items" + (f", {needs} to review" if needs else "") + ")")
                results.append(result)
            except Exception as e:
                print(f"  {os.path.basename(img)}: error - {e}", file=sys.stderr)
                results.append({
                    "filename": os.path.basename(img), "status": "error",
                    "error": str(e), "data": None, "review_status": "pending",
                })

    results.sort(key=lambda r: r["filename"])
    batch_id = Path(args.input_dir).name

    if args.append and existing_filenames:
        batch = merge_results(
            load_existing_results(output_path), results, batch_id)
        info = batch.get("_append_info", {})
        print(f"\nAppended {info.get('added', 0)} new, "
              f"skipped {info.get('skipped_duplicates', 0)} duplicates.")
    else:
        confirmed = sum(1 for r in results if r["review_status"] == "confirmed")
        pending = sum(1 for r in results if r["review_status"] == "pending")
        errors = sum(1 for r in results if r["status"] == "error")
        total_amount = sum(r["data"].get("total_amount", 0) or 0
                           for r in results if r["status"] == "success" and r.get("data"))
        batch = {
            "batch_id": batch_id,
            "created_at": datetime.now().isoformat(),
            "total_images": len(results),
            "results": results,
            "summary": {
                "total_invoices": len(results),
                "confirmed": confirmed,
                "pending_review": pending,
                "errors": errors,
                "total_amount": total_amount,
            },
        }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(batch, f, ensure_ascii=False, indent=2)

    summary = batch.get("summary", {})
    print(f"Total: {summary.get('total_invoices', 0)} invoices, "
          f"{summary.get('confirmed', 0)} confirmed, "
          f"{summary.get('pending_review', 0)} need review, "
          f"{summary.get('errors', 0)} errors. "
          f"Amount: ¥{summary.get('total_amount', 0):,.2f}")
    print(f"Results saved to: {output_path}")


if __name__ == "__main__":
    main()
