#!/usr/bin/env python3
"""
Invoice / delivery-note OCR extraction.

Sends supplier-note photos to an OpenAI-compatible chat-completions endpoint
and returns structured JSON. The extraction prompt is built dynamically from a
profile (see _profile.py / ../profiles/*.json) so the same engine serves any
factory scenario (garment fabric, generic factory, ...) without code changes.

Key flow:
    extract_single() -> vision call -> JSON parse -> total check
                     -> (on vision failure) local OCR fallback -> text prompt
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

sys.path.insert(0, str(Path(__file__).parent))
import _profile
from _profile import (
    item_fields, header_fields, field_labels, style_rule_text, load_profile,
)

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"}

DEFAULT_GATEWAY_HOST = "127.0.0.1"
DEFAULT_GATEWAY_PORT = 18789
DEFAULT_MODEL = os.environ.get(_profile.ENV_OPENAI_MODEL, "")
MAX_RETRIES = 2
RETRY_DELAY = 5  # seconds between retries

MAX_IMAGE_DIM = 1600

# OCR confidence thresholds (used by the multi-pass OCR fallback path).
OCR_DEFAULT_RETRIES = 3        # extra preprocessed variants tried when confidence is low
OCR_DEFAULT_MIN_CONFIDENCE = 0.80  # below this → trigger multi-pass retry
OCR_LOW_LINE_THRESHOLD = 0.70  # individual lines below this are flagged for review
OCR_RETRY_CONFIDENCE = 0.85    # mean confidence high enough to skip retry entirely



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


# ── Dynamic prompt builder ─────────────────────────────────────────────

def _material_enum_text(profile: dict) -> str:
    types = profile.get("material_types") or []
    if not types:
        return "字符串（按票据实际物料归类，无预设枚举）"
    return "|".join(types)


def _units_enum_text(profile: dict) -> str:
    units = profile.get("units") or []
    if not units:
        return "null (按票据实际单位)"
    return "|".join(units) + "|null"


def _doc_type_intro(profile: dict) -> str:
    """Build the document-type judgement intro from profile.document_types."""
    dtypes = profile.get("document_types") or {}
    delivery = dtypes.get("delivery", {})
    processing = dtypes.get("processing", {})
    d_name = delivery.get("name", "送货单")
    p_name = processing.get("name", "加工单")
    d_examples = "、".join(delivery.get("title_examples", [])) or d_name
    p_examples = "、".join(processing.get("title_examples", [])) or p_name
    lines = [
        f"这是工厂的票据录入场景。票据可能是以下类型之一：",
        f"- **{d_name}**：供应商发货的单据，抬头如「{d_examples}」，有品名、数量、单价等",
    ]
    if processing:
        lines.append(
            f"- **{p_name}**：外协/加工服务单据，抬头如「{p_examples}」，有加工项目、数量、加工费"
        )
    lines.append("")
    lines.append("判断依据：")
    lines.append(f"- 如果是采购物料/商品 → document_type = \"delivery\"")
    if processing:
        lines.append(f"- 如果是委托加工/外协服务 → document_type = \"processing\"")
        lines.append(f"- 抬头含加工/外协/委外等字样 → processing")
    return "\n".join(lines)


def _items_schema_block(profile: dict) -> str:
    """Build the items[] schema lines from profile.fields.items."""
    labels = field_labels(profile)
    lines = ["    {", '      "row_number": 1,']
    # Fixed semantic fields first (handwritten tracking is useful regardless)
    item_keys = [k for k, _ in item_fields(profile)]
    for key in item_keys:
        label = labels.get(key, key)
        if key == "material_type":
            lines.append(f'      "material_type": "{_material_enum_text(profile)}",')
        elif key == "material_name":
            lines.append(f'      "material_name": "string or null ({label}/项目名)",')
        elif key == "fabric_code":
            lines.append('      "fabric_code": "string or null (供应商货号/编号)",')
            lines.append('      "fabric_code_is_handwritten": true | false,')
        elif key == "style_number":
            rule = profile.get("style_number_rule", {})
            if rule.get("enabled"):
                lines.append(
                    '      "style_number": "string or null (本厂款号；仅符合 profile 款号规则时填入，否则填null)",'
                )
            else:
                lines.append(f'      "style_number": "string or null ({label})",')
        elif key in ("unit_price", "quantity", "total_amount"):
            lines.append(f'      "{key}": number or null ({label}"),')
        elif key == "unit":
            lines.append(f'      "unit": "{_units_enum_text(profile)} ({label})",')
        else:
            lines.append(f'      "{key}": "string or null ({label})",')
    lines.append('      "remark": "string or null (备注)"')
    lines.append("    }")
    return "\n".join(lines)


def _rules_block(profile: dict) -> str:
    """Build the numbered extraction rules, including style-number judgment."""
    dtypes = profile.get("document_types") or {}
    has_processing = bool(dtypes.get("processing"))
    labels = field_labels(profile)
    style_enabled = bool(profile.get("style_number_rule", {}).get("enabled"))

    lines = ["关键规则："]
    n = 1
    lines.append(f"{n}. **document_type 必须先判断**：delivery（{dtypes.get('delivery',{}).get('name','送货单')}）"
                 + (f" 或 processing（{dtypes.get('processing',{}).get('name','加工单')}）" if has_processing else ""))
    n += 1

    # Delivery rules
    lines.append(f"{n}. **送货单规则**：")
    lines.append("   - 手写内容与打印内容需区分，手写编号标记 fabric_code_is_handwritten: true")
    lines.append("   - 货名/品名后面紧跟的手写编号视为供应商货号(fabric_code)")
    if style_enabled:
        lines.append("   - 若款号只出现在摘要/表头/页脚/备注/手写区，明细行无款号列：将符合款号规则的编号写入每一行 items 的 style_number（整单通常同一款号），并在 raw_text_notes 注明区域")
    n += 1

    if has_processing:
        lines.append(f"{n}. **加工单规则**：")
        lines.append("   - fabric_code 填 null（加工单无供应商货号）")
        lines.append("   - material_name 填加工项目描述")
        lines.append("   - material_type 填对应加工类型")
        if style_enabled:
            lines.append("   - style_number 仅当符合款号规则时填写（常手写），否则 null")
        n += 1

    lines.append(f"{n}. 金额统一为数字，去掉 ¥ 和逗号")
    n += 1
    lines.append(f"{n}. 无法辨认的字段设为 null 并在 needs_review 中列出")
    n += 1
    lines.append(f"{n}. 检查明细金额合计是否等于 total_amount，不等则标注 \"total_mismatch\"")
    n += 1
    lines.append(f"{n}. 如果有多个明细行，按行号顺序全部提取")
    n += 1

    # Style-number judgment (only when enabled)
    style_text = style_rule_text(profile)
    if style_text:
        lines.append(f"{n}. {style_text}")
        n += 1

    return "\n".join(lines)


def build_extract_prompt(profile: dict, use_ocr: bool = False) -> str:
    """Build the extraction prompt for the given profile.

    use_ocr=True produces the text-mode variant (OCR fallback path).
    """
    domain = profile.get("vocab", {}).get("domain_noun", "物料")
    intro = _doc_type_intro(profile)
    items_schema = _items_schema_block(profile)
    rules = _rules_block(profile)
    title_examples = "、".join(
        ex for dt in (profile.get("document_types") or {}).values()
        for ex in dt.get("title_examples", [])
    ) or "送货单"

    if use_ocr:
        head = (
            f"以下是 OCR 识别的工厂票据文本，请提取结构化数据。\n\n"
            f"OCR 可能有识别错误（如数字混淆、乱码），请根据上下文推断正确内容。\n\n"
            f"先判断票据类型，再提取数据：\n{intro}\n"
        )
    else:
        head = (
            f"分析这张工厂的票据图片，先判断票据类型，再提取结构化数据。\n\n"
            f"【第一步：判断票据类型】\n{intro}\n"
        )

    middle = f"""
【第二步：按类型提取数据】
输出 JSON 格式，严格遵循以下 schema：
{{
  "document_type": "delivery{" | processing" if (profile.get("document_types") or {}).get("processing") else ""}",
  "document_title": "string (票据抬头/标题，如{title_examples})",
  "delivery_note": {{
    "supplier_name": "string or null (供应商/厂家名)",
    "note_number": "string or null (单号)",
    "date": "YYYY-MM-DD or null (开单日期)",
    "customer": "string or null (客户名，即我方公司名)"
  }},
  "items": [
{items_schema}
  ],
  "total_amount": "number or null (合计金额)",
  "confidence": "high" | "medium" | "low",
  "needs_review": ["需要人工复核的字段路径"],
  "raw_text_notes": "额外观察说明"
}}

"""
    tail = rules + "\n\n只输出 JSON，不要输出其他内容。"
    return head + middle + tail


# Lazy-loaded OCR engine singleton
_paddle_ocr_engine = None
_ocr_lock = __import__("threading").Lock()

# ── OCR data structures ────────────────────────────────────────────────

class OcrLine:
    """A single recognized text line with its confidence and bounding box."""
    __slots__ = ("text", "score", "bbox")

    def __init__(self, text: str, score: float, bbox=None):
        self.text = text
        self.score = float(score)
        self.bbox = bbox

    @property
    def norm(self) -> str:
        """Normalized text used for grouping/voting across OCR rounds."""
        import unicodedata
        t = unicodedata.normalize("NFKC", self.text).strip()
        # collapse internal whitespace; lowercase latin for grouping
        import re as _re
        t = _re.sub(r"\s+", "", t)
        return t.lower()


class OcrResult:
    """One OCR pass: recognized lines + aggregate confidence + method tag."""
    __slots__ = ("lines", "method")

    def __init__(self, lines: list[OcrLine], method: str = "paddle"):
        self.lines = lines
        self.method = method

    @property
    def mean_confidence(self) -> float:
        if not self.lines:
            return 0.0
        return sum(l.score for l in self.lines) / len(self.lines)

    @property
    def text(self) -> str:
        return "\n".join(l.text for l in self.lines)

    @property
    def low_confidence_lines(self) -> list[OcrLine]:
        return [l for l in self.lines if l.score < OCR_LOW_LINE_THRESHOLD]


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


def _collect_ocr_lines_with_score(result, min_score: float = 0.3) -> list[OcrLine]:
    """Normalize PaddleOCR v2/v3 result shapes into OcrLine list (keeping score).

    Handles both return shapes:
      v2: [[bbox, (text, score)], ...]
      v3: {rec_texts: [...], rec_scores: [...], dt_polys: [...]}
    Lines below min_score are dropped.
    """
    lines: list[OcrLine] = []
    for page in result or []:
        # PaddleOCR v3 dict shape
        if isinstance(page, dict) and "rec_texts" in page:
            scores = page.get("rec_scores") or []
            polys = page.get("dt_polys") or []
            for idx, text in enumerate(page.get("rec_texts") or []):
                score = scores[idx] if idx < len(scores) else 1.0
                bbox = polys[idx] if idx < len(polys) else None
                if text and score > min_score:
                    lines.append(OcrLine(str(text), score, bbox))
            continue

        # PaddleOCR v2 list shape: [[bbox, (text, score)], ...]
        if isinstance(page, list):
            for item in page:
                if (
                    isinstance(item, (list, tuple))
                    and len(item) > 1
                    and isinstance(item[1], (list, tuple))
                    and len(item[1]) > 1
                    and item[1][1] > min_score
                ):
                    bbox = item[0] if isinstance(item[0], (list, tuple)) else None
                    lines.append(OcrLine(str(item[1][0]), item[1][1], bbox))
    return lines


def _collect_ocr_lines(result, min_score: float = 0.3) -> list[str]:
    """Backward-compat: return only the text strings (drops score)."""
    return [l.text for l in _collect_ocr_lines_with_score(result, min_score)]



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
                               model: str = "",
                               timeout: int = 90) -> dict:
    """Return whether the current model route can actually see images."""
    with tempfile.TemporaryDirectory() as tmp:
        image_path = str(Path(tmp) / "vision-check.png")
        create_vision_check_image(image_path)
        try:
            raw = call_chat(
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


def _ocr_once(image_path: str) -> OcrResult:
    """Run PaddleOCR once on an image path, returning an OcrResult."""
    global _paddle_ocr_engine
    if _paddle_ocr_engine is None:
        with _ocr_lock:
            if _paddle_ocr_engine is None:
                _paddle_ocr_engine = _create_paddle_ocr()
    if hasattr(_paddle_ocr_engine, "predict"):
        result = _paddle_ocr_engine.predict(image_path)
    else:
        result = _paddle_ocr_engine.ocr(image_path, cls=True)
    return OcrResult(_collect_ocr_lines_with_score(result))


def _preprocess_variants(image_path: str) -> list[tuple[str, str]]:
    """Generate preprocessed image variants for multi-pass OCR.

    Returns list of (variant_name, temp_path). 'original' is always included.
    Each variant is written to a temp file. PIL is required; if unavailable,
    only the original (passed through) is returned.
    """
    import tempfile
    variants: list[tuple[str, str]] = []
    if not HAS_PIL:
        variants.append(("original", image_path))
        return variants

    try:
        img = Image.open(image_path)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
    except Exception as e:
        print(f"  OCR preprocess: cannot open image, using original: {e}", file=sys.stderr)
        variants.append(("original", image_path))
        return variants

    tmpdir = tempfile.mkdtemp(prefix="ocr_pp_")

    def _save(name: str, pil_img) -> str:
        path = os.path.join(tmpdir, f"{name}.jpg")
        pil_img.save(path, format="JPEG", quality=92)
        return path

    # original (re-saved for consistency)
    try:
        variants.append(("original", _save("original", img)))
    except Exception:
        variants.append(("original", image_path))

    try:
        from PIL import ImageFilter, ImageOps, ImageEnhance
        # grayscale
        try:
            variants.append(("grayscale", _save("grayscale", ImageOps.grayscale(img))))
        except Exception:
            pass
        # sharpen
        try:
            variants.append(("sharpen", _save("sharpen", img.filter(ImageFilter.SHARPEN))))
        except Exception:
            pass
        # contrast enhance
        try:
            variants.append(("contrast", _save("contrast", ImageEnhance.Contrast(img).enhance(1.6))))
        except Exception:
            pass
        # binarize (adaptive-ish: grayscale + autocontrast + point threshold)
        try:
            gray = ImageOps.grayscale(img)
            gray = ImageOps.autocontrast(gray)
            # simple threshold at midpoint
            variants.append(("binarize", _save("binarize", gray.point(lambda x: 255 if x > 140 else 0))))
        except Exception:
            pass
    except Exception as e:
        print(f"  OCR preprocess: some variants skipped: {e}", file=sys.stderr)

    return variants


def _vote_lines(rounds: list[OcrResult]) -> OcrResult:
    """Merge multiple OCR rounds via line-level voting.

    Groups lines by normalized text. A line that appears in multiple rounds
    (stable) gets boosted confidence (average of agreeing rounds); a line seen
    in only one round keeps its raw confidence but is considered less reliable.
    Returns a single merged OcrResult preserving top-to-bottom order from the
    first round that contained each line.
    """
    if not rounds:
        return OcrResult([], "vote")
    if len(rounds) == 1:
        return rounds[0]

    # group by norm: {norm: {"text": original_text, "scores": [], "first_index": int, "rounds": set}}
    groups: dict[str, dict] = {}
    order: list[str] = []  # norm keys in first-seen order
    global_idx = 0
    for r_idx, res in enumerate(rounds):
        for line in res.lines:
            key = line.norm
            if not key:
                continue
            if key not in groups:
                groups[key] = {
                    "text": line.text,
                    "scores": [],
                    "first_global_idx": global_idx,
                    "rounds": set(),
                }
                order.append(key)
            groups[key]["scores"].append(line.score)
            groups[key]["rounds"].add(r_idx)
            # keep the longest non-empty text representation seen
            if len(line.text) > len(groups[key]["text"]):
                groups[key]["text"] = line.text
            global_idx += 1

    merged: list[OcrLine] = []
    for key in order:
        g = groups[key]
        n_rounds = len(g["rounds"])
        avg_score = sum(g["scores"]) / len(g["scores"]) if g["scores"] else 0.0
        # Boost: lines seen in multiple rounds are more trustworthy.
        # A line seen in k of R rounds gets confidence weighted by agreement.
        R = len(rounds)
        agreement = n_rounds / R
        # final score: blend raw avg with agreement bonus
        final_score = min(1.0, avg_score * 0.6 + agreement * 0.4)
        merged.append(OcrLine(g["text"], final_score))

    merged.sort(key=lambda l: 0)  # preserve insertion order (stable)
    return OcrResult(merged, "vote")


def ocr_image_with_confidence(image_path: str,
                              retries: int = OCR_DEFAULT_RETRIES,
                              min_confidence: float = OCR_DEFAULT_MIN_CONFIDENCE) -> OcrResult:
    """OCR with multi-pass retry + line-level voting when confidence is low.

    Fast path: if the first (original) pass already has mean_confidence >=
    OCR_RETRY_CONFIDENCE, return immediately (most clear photos stop here).
    Otherwise, run additional preprocessed variants and vote across rounds.
    """
    try:
        first = _ocr_once(image_path)
    except Exception as e:
        print(f"  OCR failed: {e}", file=sys.stderr)
        return OcrResult([], "paddle")

    if not first.lines:
        return first
    # Fast path: clear image, no retry needed
    if first.mean_confidence >= OCR_RETRY_CONFIDENCE or retries <= 0:
        return first

    # Slow path: low confidence → multi-preprocess retry
    rounds = [first]
    variants = _preprocess_variants(image_path)
    # skip 'original' (already done) and limit to `retries` extra variants
    extra = [v for v in variants if v[0] != "original"][:retries]
    for name, vpath in extra:
        try:
            res = _ocr_once(vpath)
            if res.lines:
                rounds.append(res)
        except Exception as e:
            print(f"  OCR variant '{name}' failed: {e}", file=sys.stderr)

    if len(rounds) == 1:
        return first
    return _vote_lines(rounds)


def ocr_image(image_path: str) -> str:
    """Backward-compat: OCR and return plain text string."""
    return ocr_image_with_confidence(image_path).text



def call_chat(base_url: str, token: str, image_path: str,
              prompt: str, model: str = "",
              timeout: int = 300, ocr_text: str | None = None) -> str:
    """Call an OpenAI-compatible chat-completions endpoint.

    Renamed from call_openclaw_chat — the call is a standard
    /v1/chat/completions request usable by any compatible gateway.
    """
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload: dict = {"model": model, "max_tokens": 4096}
    if ocr_text is not None:
        # Text-only mode: send OCR text as plain string
        full_text = f"{prompt}\n\n--- OCR 识别文本 ---\n{ocr_text}"
        payload["messages"] = [{"role": "user", "content": full_text}]
    else:
        # Vision mode: send image + prompt
        data_uri = encode_image_data_uri(image_path)
        payload["messages"] = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_uri}},
                {"type": "text", "text": prompt},
            ],
        }]

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
                   prompt_extra: str = "", progress=None,
                   profile: dict | None = None,
                   ocr_retries: int = OCR_DEFAULT_RETRIES,
                   ocr_min_confidence: float = OCR_DEFAULT_MIN_CONFIDENCE) -> dict:
    if profile is None:
        profile = load_profile()
    vision_prompt = build_extract_prompt(profile, use_ocr=False)
    ocr_prompt = build_extract_prompt(profile, use_ocr=True)

    filename = os.path.basename(image_path)
    last_error = None
    vision_failed = False

    for attempt in range(1, retries + 2):  # 1 initial + retries
        try:
            _notify_progress(progress, filename, "vision_start", "正在识别这张票据。")
            prompt = vision_prompt + prompt_extra if prompt_extra else vision_prompt
            text = call_chat(base_url, token, image_path, prompt, model).strip()
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
            if raw and _is_blind_response(raw):
                print(f"  {filename}: model cannot see images, falling back to OCR",
                      file=sys.stderr)
                _notify_progress(
                    progress, filename, "ocr_fallback",
                    "当前模型看不到图片，我先用本地 OCR 兜底，这张会慢一点。"
                )
                vision_failed = True
                break
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

    # ── OCR fallback path (multi-pass + line-level voting) ──
    ocr_result = ocr_image_with_confidence(image_path, ocr_retries, ocr_min_confidence)
    if not ocr_result.lines:
        _notify_progress(progress, filename, "ocr_failed", "这张照片暂时没读清楚。")
        return {
            "filename": filename,
            "status": "error",
            "error": "模型不支持图片且 OCR 不可用。请安装: pip install paddleocr paddlepaddle",
            "data": None,
            "review_status": "pending",
        }

    try:
        mean_conf = ocr_result.mean_confidence
        low_lines = ocr_result.low_confidence_lines
        _notify_progress(progress, filename, "ocr_done",
                         f"已读出票据文字（平均置信 {mean_conf:.0%}），正在整理成表格。")
        # Build OCR text with per-line confidence hints for the LLM:
        # low-confidence lines are prefixed with [?] so the model knows to be
        # cautious and infer from context.
        ocr_lines_rendered = []
        for line in ocr_result.lines:
            marker = "[?] " if line.score < OCR_LOW_LINE_THRESHOLD else ""
            ocr_lines_rendered.append(f"{marker}{line.text}  (置信{line.score:.0%})")
        ocr_text = "\n".join(ocr_lines_rendered)
        if low_lines:
            ocr_text += (
                "\n\n注意：带 [?] 的行置信度较低，可能存在识别错误（数字混淆/乱码），"
                "请结合上下文与金额逻辑推断正确内容，并在 needs_review 中标注存疑字段。"
            )

        prompt = ocr_prompt + prompt_extra if prompt_extra else ocr_prompt
        text = call_chat(base_url, token, image_path, prompt, model, ocr_text=ocr_text).strip()
        data = extract_json_from_text(text)
        _check_total(data)
        data["extraction_method"] = "ocr_fallback"
        data["ocr_mean_confidence"] = round(mean_conf, 3)

        needs_review = data.get("needs_review", [])
        needs_review.append("ocr_fallback")

        # Confidence grading based on OCR mean confidence (no longer force-medium).
        # ≥0.85: keep model's own confidence; just flag ocr_fallback.
        # 0.70–0.85: cap at medium.
        # <0.70: force low + list specific low-confidence lines for review.
        if mean_conf >= OCR_RETRY_CONFIDENCE:
            pass  # keep model's confidence
        elif mean_conf >= 0.70:
            if data.get("confidence") == "high":
                data["confidence"] = "medium"
        else:
            data["confidence"] = "low"
            for idx, line in enumerate(low_lines, 1):
                preview = line.text[:20].replace("\n", " ")
                needs_review.append(f"OCR 第{idx}处低置信({line.score:.0%}): {preview}")

        data["needs_review"] = needs_review
        # OCR results with decent confidence need review only for flagged lines;
        # very low confidence stays pending unconditionally.
        review_status = "pending" if (mean_conf < OCR_RETRY_CONFIDENCE or low_lines) else "pending"
        return {
            "filename": filename,
            "status": "success",
            "error": None,
            "data": data,
            "review_status": review_status,  # OCR results always await review
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
    """Verify the gateway is reachable and exposes /v1/models."""
    try:
        req = Request(f"{base_url}/v1/models")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urlopen(req, timeout=10) as resp:
            content_type = resp.headers.get("Content-Type", "")
            body = resp.read().decode("utf-8", errors="replace")
            if "application/json" not in content_type:
                print(f"Error: Endpoint at {base_url} returned HTML, not JSON.\n"
                      f"  Confirm the chat-completions gateway is enabled and reachable.",
                      file=sys.stderr)
                sys.exit(1)
            models = json.loads(body)
            if not models.get("data"):
                print(f"Warning: No models available at gateway", file=sys.stderr)
    except HTTPError as e:
        if e.code == 401:
            print(f"Error: Authentication failed. Set --token or {ENV_TOKEN_HINT}.",
                  file=sys.stderr)
        elif e.code == 404:
            print(f"Error: {base_url} does not have /v1/models endpoint.\n"
                  f"  Confirm the OpenAI-compatible gateway is running.",
                  file=sys.stderr)
        else:
            print(f"Error: Endpoint returned HTTP {e.code}: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except URLError as e:
        print(f"Error: Cannot reach gateway at {base_url}\n  Detail: {e}", file=sys.stderr)
        sys.exit(1)


ENV_TOKEN_HINT = "OPENAI_API_KEY or OPENAI_GATEWAY_TOKEN"


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
    parser = argparse.ArgumentParser(
        description="Extract delivery-note data via an OpenAI-compatible gateway")
    parser.add_argument("input_dir", help="Directory containing invoice images")
    parser.add_argument("--output", "-o", help="Output JSON file path")
    parser.add_argument("--append", action="store_true",
                        help="Append to existing results JSON (skip duplicate filenames)")
    parser.add_argument("--profile", default=None,
                        help="Profile id (e.g. generic-factory, garment-fabric) or path to a profile JSON")
    parser.add_argument("--gateway-host",
                        default=os.environ.get("OPENAI_GATEWAY_HOST",
                                    os.environ.get(_profile.LEGACY_ENV_HOST, DEFAULT_GATEWAY_HOST)))
    parser.add_argument("--gateway-port", type=int,
                        default=int(os.environ.get("OPENAI_GATEWAY_PORT",
                                    os.environ.get(_profile.LEGACY_ENV_PORT, str(DEFAULT_GATEWAY_PORT)))))
    parser.add_argument("--token", default=os.environ.get("OPENAI_GATEWAY_TOKEN",
                                                os.environ.get(_profile.LEGACY_ENV_TOKEN,
                                                os.environ.get(_profile.ENV_OPENAI_API_KEY, ""))))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--parallel", "-j", type=int, default=3)
    parser.add_argument("--retries", "-r", type=int, default=MAX_RETRIES,
                        help=f"Number of retries per image on failure (default: {MAX_RETRIES})")
    parser.add_argument("--ocr-retries", type=int, default=OCR_DEFAULT_RETRIES,
                        help=f"Extra preprocessed OCR variants to try when confidence is low (default: {OCR_DEFAULT_RETRIES})")
    parser.add_argument("--ocr-min-confidence", type=float, default=OCR_DEFAULT_MIN_CONFIDENCE,
                        help=f"Mean OCR confidence below which multi-pass retry triggers (default: {OCR_DEFAULT_MIN_CONFIDENCE})")
    parser.add_argument("--templates-dir", type=str,
                        default=str(_profile.STATE_DIR / _profile.TEMPLATES_DIRNAME),
                        help="Templates directory for supplier context injection")
    parser.add_argument("--no-template", action="store_true",
                        help="Disable template context injection")
    args = parser.parse_args()

    profile = _profile.resolve_profile(args)
    base_url = f"http://{args.gateway_host}:{args.gateway_port}"
    check_gateway(base_url, args.token)

    # Build supplier context from templates
    prompt_extra = ""
    templates_dir = Path(args.templates_dir)
    if not args.no_template:
        from templates import build_supplier_context, post_process_extraction
        prompt_extra = build_supplier_context(templates_dir, profile)
        if prompt_extra:
            print(f"Loaded supplier context from templates.")
    else:
        from templates import post_process_extraction

    output_path = args.output or str(Path(args.input_dir) / "results.json")

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

    print(f"Processing {len(images)} delivery notes via gateway ({args.model or 'default model'})...")

    results = []
    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        futures = {executor.submit(extract_single, base_url, args.token,
                                   img, args.model, args.retries, prompt_extra,
                                   None, profile,
                                   args.ocr_retries, args.ocr_min_confidence): img
                   for img in images}
        for future in as_completed(futures):
            img = futures[future]
            try:
                result = future.result()
                if result.get("status") == "success" and not args.no_template:
                    result = post_process_extraction(result, templates_dir, profile)
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
        batch = merge_results(load_existing_results(output_path), results, batch_id)
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
