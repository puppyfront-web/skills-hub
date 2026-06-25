import hashlib
import json
import re
import unicodedata


ALLOWED_FIELDS = {
    "note_number": "单据编号",
    "date": "单据日期",
    "customer": "收货客户",
    "supplier_name": "供应商或厂家",
    "style_number": "服装款号",
    "material_type": "面料或辅料类型",
    "material_name": "品名或物料名称",
    "fabric_code": "供应商货号或面料款号",
    "color_code": "色号或颜色",
    "unit_price": "单价",
    "quantity": "数量",
    "unit": "计量单位",
    "total_amount": "金额",
    "remark": "备注",
}

CANONICAL_HEADERS = {
    "单号": "note_number",
    "日期": "date",
    "客户": "customer",
    "面料厂家": "supplier_name",
    "款号": "style_number",
    "面料/辅料": "material_type",
    "品名": "material_name",
    "面料款号": "fabric_code",
    "色号/颜色": "color_code",
    "单价": "unit_price",
    "数量": "quantity",
    "单位": "unit",
    "总金额": "total_amount",
    "备注": "remark",
}


def normalize_header(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    return re.sub(r"\s+", " ", normalized)


def header_signature(headers: list[object]) -> str:
    normalized = [normalize_header(header) for header in headers]
    payload = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def exact_mappings(headers: list[object]) -> dict[int, dict]:
    mappings = {}
    for column_index, raw in enumerate(headers, 1):
        header = normalize_header(raw)
        target = CANONICAL_HEADERS.get(header)
        if target:
            mappings[column_index] = {
                "column_index": column_index,
                "header": header,
                "target_field": target,
                "confidence": 1.0,
                "reason": "标准字段精确匹配",
                "source": "exact",
            }
    return mappings


def validate_suggestions(
    headers: list[object],
    suggestions: list[dict],
    occupied: set[str],
) -> tuple[dict[int, dict], list[dict]]:
    normalized_headers = {
        index: normalize_header(header)
        for index, header in enumerate(headers, 1)
    }
    candidates = []
    pending = []

    for suggestion in suggestions:
        header = normalize_header(suggestion.get("header"))
        column_index = suggestion.get("column_index")
        target = suggestion.get("target_field")
        confidence = suggestion.get("confidence")

        if (
            not isinstance(column_index, int)
            or normalized_headers.get(column_index) != header
        ):
            pending.append({**suggestion, "reason_code": "unknown_column"})
            continue
        if target is not None and target not in ALLOWED_FIELDS:
            pending.append({**suggestion, "reason_code": "invalid_target"})
            continue
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            pending.append({**suggestion, "reason_code": "invalid_confidence"})
            continue
        candidates.append({**suggestion, "header": header, "source": "llm"})

    target_counts = {}
    for item in candidates:
        target = item["target_field"]
        if target is not None:
            target_counts[target] = target_counts.get(target, 0) + 1

    accepted = {}
    for item in candidates:
        target = item["target_field"]
        if target is not None and (
            target_counts[target] > 1 or target in occupied
        ):
            pending.append({**item, "reason_code": "target_conflict"})
        elif item["confidence"] < 0.90:
            pending.append({**item, "reason_code": "low_confidence"})
        else:
            accepted[item["column_index"]] = item

    return accepted, pending
