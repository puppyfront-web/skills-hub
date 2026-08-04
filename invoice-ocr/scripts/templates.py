#!/usr/bin/env python3
"""
Template management for invoice OCR.
Manages supplier-specific extraction templates: list, show, delete, match, save, review, correct.
Includes programmatic field remapping and user preferences persistence.

Field labels, name maps, and review columns are derived from the active
profile (see _profile.py), so the same template engine serves any factory
scenario. Functions that previously read module-level garment constants now
accept an optional `profile` argument; a default profile is loaded when none
is supplied.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _profile
from _profile import load_profile, resolve_profile

TEMPLATES_DIR = _profile.STATE_DIR / _profile.TEMPLATES_DIRNAME
PREFS_PATH = _profile.STATE_DIR / _profile.PREFS_FILENAME

# Module-level default profile + derived helpers (kept for backward-compat
# imports; callers should prefer the profile-aware variants below).
_DEFAULT_PROFILE = load_profile()


def _p(profile=None):
    return profile if profile is not None else _DEFAULT_PROFILE


# Field definitions: (json_key, excel_column_name) — derived from profile.
def all_item_fields(profile=None) -> list[tuple[str, str]]:
    return _profile.item_fields(_p(profile))


def header_fields(profile=None) -> list[tuple[str, str]]:
    return _profile.header_fields(_p(profile))


ALL_ITEM_FIELDS = all_item_fields()  # backward-compat (garment-derived default is generic now)

HEADER_FIELDS = header_fields()


def field_labels(profile=None) -> dict[str, str]:
    return _profile.field_labels(_p(profile))


def field_name_map(profile=None) -> dict[str, str]:
    return _profile.field_name_map(_p(profile))


# Chinese label lookup for error messages (short labels, no column letter)
FIELD_LABELS = field_labels()

# Natural language field name mapping (Chinese → JSON field key)
FIELD_NAME_MAP = field_name_map()

# Common synonym aliases shared across profiles (mapped to json keys present
# in the profile). Lets corrections like 货号/规格 parse even if the profile
# uses a different primary label.
_FIELD_SYNONYMS = {
    "material_name": ["品名", "货名", "名称"],
    "fabric_code": ["货号", "编号", "面料款号"],
    "style_number": ["款号", "服装款号"],
    "color_code": ["色号", "颜色", "色号/颜色"],
    "material_type": ["面料", "辅料", "类型", "物料分类"],
    "spec": ["规格", "型号", "规格型号"],
    "unit_price": ["单价", "价格"],
    "quantity": ["数量", "件数"],
    "unit": ["单位"],
    "total_amount": ["金额", "总价", "总金额"],
}


def _synonym_map(profile=None) -> dict[str, str]:
    """Merge profile labels + common synonyms → json_key."""
    p = _p(profile)
    m = _profile.field_name_map(p)
    known_keys = set(k for k, _ in _profile.all_known_fields(p))
    for key, syns in _FIELD_SYNONYMS.items():
        if key not in known_keys:
            continue
        for syn in syns:
            m.setdefault(syn, key)
    return m


import re


def _field_display_name(field: str, profile=None) -> str:
    return _profile.field_display_name(_p(profile), field)


def _learning_summary_for_correction(correction: dict, profile=None) -> str | None:
    field = correction.get("field")
    actual = correction.get("actual_meaning")
    if not field or not actual:
        return None
    field_name = _field_display_name(field, profile)
    if actual == "none":
        return f"以后默认没有{field_name}"
    if field != actual:
        actual_name = _field_display_name(actual, profile)
        return f"以后把{field_name}按{actual_name}处理"
    return None


def _field_mapping_corrections(corrections: list[dict] | None) -> list[dict]:
    if not corrections:
        return []
    cleaned = []
    for correction in corrections:
        field = correction.get("field")
        actual = correction.get("actual_meaning")
        if not field or not actual:
            continue
        item = {
            "field": field,
            "actual_meaning": actual,
        }
        if correction.get("description"):
            item["description"] = correction["description"]
        cleaned.append(item)
    return cleaned


def build_learning_entries(corrections: list[dict] | None, profile=None) -> tuple[list[dict], list[dict]]:
    """Build append-only learning events and deduplicated long-term rules."""
    if not corrections:
        return [], []

    events = []
    rules = []
    now = datetime.now().isoformat()
    for correction in corrections:
        summary = _learning_summary_for_correction(correction, profile)
        source_text = correction.get("source_text") or correction.get("description") or ""
        if not summary:
            if source_text:
                events.append({
                    "source_text": source_text,
                    "action": correction.get("action", "note"),
                    "summary": source_text,
                    "created_at": now,
                })
            continue

        field = correction.get("field")
        actual = correction.get("actual_meaning")
        key = f"{field}->{actual}"
        events.append({
            "source_text": source_text or summary,
            "action": "learn_rule",
            "rule_key": key,
            "summary": summary,
            "created_at": now,
        })
        rules.append({
            "key": key,
            "summary": summary,
            "field": field,
            "actual_meaning": actual,
            "source_text": source_text or summary,
            "updated_at": now,
            "count": 1,
        })
    return events, rules


def _merge_learning_rules(existing: list[dict], new_rules: list[dict]) -> list[dict]:
    merged = {rule.get("key"): dict(rule) for rule in existing if rule.get("key")}
    for rule in new_rules:
        key = rule.get("key")
        if not key:
            continue
        if key in merged:
            old = merged[key]
            if (
                old.get("updated_at") == rule.get("updated_at")
                and old.get("source_text") == rule.get("source_text")
            ):
                continue
            old["summary"] = rule.get("summary", old.get("summary", ""))
            old["source_text"] = rule.get("source_text", old.get("source_text", ""))
            old["updated_at"] = rule.get("updated_at", old.get("updated_at"))
            old["count"] = int(old.get("count", 1) or 1) + 1
        else:
            merged[key] = dict(rule)
    return list(merged.values())


def _merge_learning_events(existing: list[dict], new_events: list[dict]) -> list[dict]:
    merged = []
    seen = set()
    for event in existing + new_events:
        key = (
            event.get("source_text", ""),
            event.get("summary", ""),
            event.get("created_at", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(event)
    return merged

# ── Natural Language Correction Parser ────────────────────────────────

def parse_natural_correction(text: str, supplier: str = "", profile=None) -> list[dict]:
    """Parse natural language correction into correction JSON objects.

    Examples:
        '品名就是面料款号' → [{"field":"material_name","actual_meaning":"fabric_code",...}]
        '无色号' → [{"field":"color_code","actual_meaning":"none",...}]
        '面料款号实际是款号' → [{"field":"fabric_code","actual_meaning":"style_number",...}]
        '确认' → [{"action":"confirm",...}]
        '第二行数量是300' → [{"action":"row_update","row_number":2,"field":"quantity","value":300}]
        '这张不要' → [{"action":"skip_invoice"}]
    """
    text = text.strip()
    corrections = []
    name_map = _synonym_map(profile)

    # "确认" or "对" means user-confirm current pending invoice/template.
    if text in ("确认", "对的", "正确", "对", "没问题"):
        return [{"action": "confirm", "supplier": supplier}]
    if text in ("全部确认", "都确认", "全都确认"):
        return [{"action": "confirm_all"}]
    if any(phrase in text for phrase in ("这张不要", "删掉这张", "这单不要", "跳过这张", "不用录这张")):
        return [{"action": "skip_invoice", "supplier": supplier}]

    row_update = _parse_row_update(text, profile)
    if row_update:
        row_update["supplier"] = supplier
        return [row_update]

    # Try "XX就是YY" / "XX实际是YY" / "XX是YY" patterns (most specific first)
    remap_patterns = [
        r"(\S+?)就是(\S+)",
        r"(\S+?)实际(?:就)?是(\S+)",
        r"(\S+?)是(\S+)",
    ]
    for pattern in remap_patterns:
        m = re.search(pattern, text)
        if m:
            src_label = m.group(1).strip()
            dst_label = m.group(2).strip()
            src_field = name_map.get(src_label)
            dst_field = name_map.get(dst_label)
            if src_field and dst_field and src_field != dst_field:
                corrections.append({
                    "field": src_field,
                    "actual_meaning": dst_field,
                    "description": f"{src_label}就是{dst_label}",
                })
            break

    # Try "无XX" / "没有XX" patterns
    none_patterns = [r"没有(\S+?)(?:字段)?$", r"无(\S+?)(?:字段)?$"]
    for pattern in none_patterns:
        m = re.search(pattern, text)
        if m:
            label = m.group(1).strip()
            field = name_map.get(label)
            if field:
                corrections.append({
                    "field": field,
                    "actual_meaning": "none",
                    "description": f"无{label}字段",
                })
            break

    # Try "XX不是YY" pattern (field maps to something else)
    m = re.search(r"(\S+?)不是(\S+)", text)
    if m and not corrections:
        src_label = m.group(1).strip()
        dst_label = m.group(2).strip()
        src_field = name_map.get(src_label)
        if src_field:
            corrections.append({
                "field": src_field,
                "actual_meaning": "none",
                "description": f"{src_label}不是{dst_label}",
            })

    return corrections


def _parse_chinese_row_number(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    digits = {
        "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9,
    }
    if text == "十":
        return 10
    if text.startswith("十") and len(text) == 2:
        tail = digits.get(text[1])
        return 10 + tail if tail else None
    if text.endswith("十") and len(text) == 2:
        head = digits.get(text[0])
        return head * 10 if head else None
    if "十" in text and len(text) == 3:
        head = digits.get(text[0])
        tail = digits.get(text[2])
        if head and tail:
            return head * 10 + tail
    return digits.get(text)


def _parse_scalar_value(value: str):
    clean = value.strip().strip("，,。 .")
    clean = clean.replace("¥", "").replace("￥", "").replace(",", "")
    try:
        num = float(clean)
    except ValueError:
        return clean
    return int(num) if num.is_integer() else num


def _parse_row_update(text: str, profile=None) -> dict | None:
    pattern = (
        r"第\s*([一二两三四五六七八九十\d]+)\s*行\s*"
        r"(.+?)(?:应该)?(?:填错了)?(?:是|为|改成|填成|填)\s*(.+)$"
    )
    m = re.search(pattern, text)
    if not m:
        return None
    row_number = _parse_chinese_row_number(m.group(1))
    field_label = m.group(2).strip()
    value = _parse_scalar_value(m.group(3))
    name_map = _synonym_map(profile)
    field = name_map.get(field_label)
    if not row_number or not field:
        return None
    return {
        "action": "row_update",
        "row_number": row_number,
        "field": field,
        "value": value,
        "description": f"第{row_number}行{field_label}改为{value}",
    }


# ── Preferences ──────────────────────────────────────────────────────

def load_preferences() -> dict:
    if PREFS_PATH.is_file():
        try:
            with open(PREFS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "version": 1,
        "default_table": None,
        "supplier_tables": {},
        "field_aliases": {},
        "updated_at": None,
    }


def save_preferences(prefs: dict):
    PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    prefs["updated_at"] = datetime.now().isoformat()
    with open(PREFS_PATH, "w", encoding="utf-8") as f:
        json.dump(prefs, f, ensure_ascii=False, indent=2)


def set_supplier_table(supplier: str, table_path: str):
    prefs = load_preferences()
    prefs["supplier_tables"][supplier] = table_path
    save_preferences(prefs)


def get_table_for_supplier(supplier: str) -> str | None:
    prefs = load_preferences()
    return prefs.get("supplier_tables", {}).get(supplier) or prefs.get("default_table")


def set_default_table(table_path: str):
    prefs = load_preferences()
    prefs["default_table"] = table_path
    save_preferences(prefs)


# ── Index & Storage ──────────────────────────────────────────────────

def load_index(templates_dir: Path) -> dict:
    index_path = templates_dir / "index.json"
    if index_path.is_file():
        with open(index_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"version": 1, "templates": {}, "updated_at": ""}


def save_index(index: dict, templates_dir: Path):
    templates_dir.mkdir(parents=True, exist_ok=True)
    index["updated_at"] = datetime.now().isoformat()
    with open(templates_dir / "index.json", "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def _supplier_id(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name).strip("-")


def _template_id(supplier_name: str, document_title: str) -> str:
    """Build template ID from supplier name + document title."""
    s = _supplier_id(supplier_name)
    t = _supplier_id(document_title)
    return f"{s}--{t}" if t else s


def load_template(template_id: str, templates_dir: Path) -> dict | None:
    path = templates_dir / f"{template_id}.json"
    if path.is_file():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


# ── Match ────────────────────────────────────────────────────────────

def match_template(supplier_name: str, templates_dir: Path,
                   document_title: str = "") -> dict | None:
    """Match template by supplier_name + document_title.

    Priority:
    1. Exact match on supplier + title
    2. Fallback: supplier only (for backward compat with old templates)
    """
    index = load_index(templates_dir)
    needle = supplier_name.lower().strip()
    title_lower = document_title.lower().strip()

    # Pass 1: match supplier + title
    if title_lower:
        for _sid, entry in index.get("templates", {}).items():
            entry_supplier = entry.get("supplier_name", "").lower().strip()
            entry_title = entry.get("document_title", "").lower().strip()
            if not entry_title:
                continue
            supplier_match = (needle in entry_supplier or entry_supplier in needle)
            title_match = (title_lower in entry_title or entry_title in title_lower)
            if supplier_match and title_match:
                return load_template(entry["file"].replace(".json", ""), templates_dir)

    # Pass 2: fallback to supplier-only match (old templates)
    for _sid, entry in index.get("templates", {}).items():
        for alias in entry.get("aliases", []):
            if needle in alias.lower() or alias.lower() in needle:
                return load_template(entry["file"].replace(".json", ""), templates_dir)
    return None


def match_template_by_id(supplier_name: str, templates_dir: Path,
                         document_title: str = "") -> str | None:
    """Return template ID matching supplier + title."""
    index = load_index(templates_dir)
    needle = supplier_name.lower().strip()
    title_lower = document_title.lower().strip()

    # Pass 1: supplier + title
    if title_lower:
        for sid, entry in index.get("templates", {}).items():
            entry_supplier = entry.get("supplier_name", "").lower().strip()
            entry_title = entry.get("document_title", "").lower().strip()
            if not entry_title:
                continue
            supplier_match = (needle in entry_supplier or entry_supplier in needle)
            title_match = (title_lower in entry_title or entry_title in title_lower)
            if supplier_match and title_match:
                return sid

    # Pass 2: supplier only
    for sid, entry in index.get("templates", {}).items():
        for alias in entry.get("aliases", []):
            if needle in alias.lower() or alias.lower() in needle:
                return sid
    return None


# ── Programmatic Field Remapping ─────────────────────────────────────

def apply_corrections_to_data(data: dict, template: dict, profile=None) -> dict:
    """Programmatically apply template field mapping corrections to extracted data.

    This is the key function that makes the pipeline model-agnostic.
    After the model extracts data using generic field names, we remap fields
    based on user-confirmed corrections stored in the template.

    Returns the modified data dict.
    """
    corrections = template.get("field_mapping_corrections", [])
    if not corrections:
        return data

    items = data.get("items", [])
    for c in corrections:
        field = c.get("field")
        actual = c.get("actual_meaning")
        if not field or not actual:
            continue

        if actual == "none":
            # Field doesn't exist for this supplier → clear it
            for item in items:
                item[field] = None
        elif field != actual:
            # Remap: move value from source field to target field
            for item in items:
                val = item.get(field)
                # Skip placeholder values that mean "not applicable"
                if val is not None and str(val).strip() not in ("/", "", "null", "None"):
                    item[actual] = val
                    item[field] = None
                    if field == "fabric_code":
                        item["fabric_code_is_handwritten"] = False

    return data


def _required_fields(doc_type: str, profile=None) -> list[str]:
    """Required item fields by document type, derived from profile.

    Heuristic: delivery wants material_type + numeric core; processing wants
    material_name + numeric core (since processing has no fabric_code). Falls
    back gracefully when the profile omits a field.
    """
    p = _p(profile)
    keys = {k for k, _ in _profile.item_fields(p)}
    core = [k for k in ("unit_price", "quantity", "total_amount") if k in keys]
    if doc_type == "processing":
        name_like = [k for k in ("material_name",) if k in keys]
        return [k for k in (name_like + core) if k]
    type_like = [k for k in ("material_type",) if k in keys]
    code_like = [k for k in ("fabric_code",) if k in keys]
    return [k for k in (type_like + code_like + core) if k]


def validate_extraction(data: dict, template: dict | None, profile=None) -> list[str]:
    """Check for missing required fields and issues. Returns list of issue strings."""
    issues = []
    items = data.get("items", [])
    labels = field_labels(profile)

    # Check total mismatch
    total = data.get("total_amount")
    if total is not None and items:
        item_sum = sum(it.get("total_amount", 0) or 0 for it in items)
        if abs(item_sum - total) > 0.01:
            issues.append(f"total_mismatch: 明细合计{item_sum}≠单据总额{total}")

    # Select required fields based on document type
    doc_type = None
    if template:
        doc_type = template.get("document_type")
    if not doc_type:
        doc_type = data.get("document_type", "delivery")
    required = _required_fields(doc_type, profile)

    # Check required fields per item (use Chinese labels)
    for idx, item in enumerate(items):
        for req in required:
            val = item.get(req)
            if val is None or val == "":
                label = labels.get(req, req)
                issues.append(f"第{idx+1}行 {label} 缺失")

    return issues


def post_process_extraction(result: dict, templates_dir: Path, profile=None) -> dict:
    """Post-process a single extraction result: apply template corrections,
    validate, and set review_status.

    Returns the modified result dict.
    """
    if result.get("status") != "success" or not result.get("data"):
        return result

    data = result["data"]
    supplier = data.get("delivery_note", {}).get("supplier_name", "")
    doc_title = data.get("document_title", "")
    template = match_template(supplier, templates_dir, doc_title) if supplier else None

    # Apply programmatic corrections if template exists
    if template:
        # Sync document_type from template to ensure consistent validation
        tmpl_doc_type = template.get("document_type")
        if tmpl_doc_type and data.get("document_type") != tmpl_doc_type:
            data["document_type"] = tmpl_doc_type
        data = apply_corrections_to_data(data, template, profile)
        result["data"] = data
        result["template_matched"] = template["supplier_name"]

    # Validate
    issues = validate_extraction(data, template, profile)
    data["needs_review"] = issues

    # Auto-confirm if template matched and no issues
    if template and not issues:
        result["review_status"] = "confirmed"
        result["auto_confirmed"] = True
    else:
        result["review_status"] = "pending"

    return result


# ── Build from Extraction ────────────────────────────────────────────

def build_template_from_extraction(result: dict, user_corrections: list | None = None,
                                  profile=None) -> dict:
    p = _p(profile)
    item_field_keys = [k for k, _ in _profile.item_fields(p)]
    data = result.get("data", {})
    delivery = data.get("delivery_note", {})
    items = data.get("items", [])
    supplier_name = delivery.get("supplier_name") or ""
    document_title = data.get("document_title", "")
    doc_type = data.get("document_type", "delivery")

    field_presence = {}
    for field_key in item_field_keys:
        present_count = sum(1 for it in items if it.get(field_key) is not None)
        field_presence[field_key] = {
            "present": present_count > 0,
            "rate": f"{present_count}/{len(items)}" if items else "0/0",
        }

    handwritten = []
    for it in items:
        if it.get("fabric_code_is_handwritten"):
            handwritten.append("fabric_code")
            break

    mat_types = list(set(it.get("material_type") for it in items if it.get("material_type")))
    units = [it.get("unit") for it in items if it.get("unit")]
    default_unit = max(set(units), key=units.count) if units else None
    format_desc = data.get("raw_text_notes") or ""
    learning_events, learned_rules = build_learning_entries(user_corrections, p)

    template = {
        "version": 1,
        "document_type": doc_type,
        "document_title": document_title,
        "supplier_name": supplier_name,
        "supplier_aliases": _build_aliases(supplier_name, p),
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "source_count": 1,
        "format_description": format_desc,
        "field_layout": {
            "note_number_pattern": None,
            "items_table_type": "columnar",
            "item_row_fields": {
                field_key: {
                    "present": field_presence[field_key]["present"],
                    "observed_rate": field_presence[field_key]["rate"],
                }
                for field_key in item_field_keys
            },
        },
        "extraction_hints": {
            "handwritten_fields": handwritten,
            "common_material_types": mat_types,
            "default_unit": default_unit,
            "notes": "",
        },
        "field_mapping_corrections": _field_mapping_corrections(user_corrections),
        "learning_events": learning_events,
        "learned_rules": learned_rules,
        "sample_extraction": {
            "filename": result.get("filename", ""),
            "note_number": delivery.get("note_number"),
            "items_count": len(items),
        },
    }
    return template


def _build_aliases(name: str, profile=None) -> list[str]:
    p = _p(profile)
    aliases = [name]
    if len(name) > 2:
        aliases.append(name[:2])
    suffixes = p.get("supplier_suffixes") or []
    for suffix in suffixes:
        if name.endswith(suffix) and len(name) > len(suffix):
            short = name[:-len(suffix)]
            if short not in aliases:
                aliases.append(short)
    return aliases


# ── Save ─────────────────────────────────────────────────────────────

def save_template(template: dict, templates_dir: Path):
    templates_dir.mkdir(parents=True, exist_ok=True)
    supplier_name = template["supplier_name"]
    doc_title = template.get("document_title", "")
    tid = _template_id(supplier_name, doc_title)
    template_path = templates_dir / f"{tid}.json"

    existing = load_template(tid, templates_dir)
    if existing:
        template["source_count"] = existing.get("source_count", 0) + 1
        template["created_at"] = existing.get("created_at", template["created_at"])
        all_aliases = list(set(existing.get("supplier_aliases", []) + template.get("supplier_aliases", [])))
        template["supplier_aliases"] = all_aliases
        old_corrections = existing.get("field_mapping_corrections", [])
        new_corrections = template.get("field_mapping_corrections", [])
        template["field_mapping_corrections"] = old_corrections + [
            c for c in new_corrections if c not in old_corrections
        ]
        template["learning_events"] = _merge_learning_events(
            existing.get("learning_events", []),
            template.get("learning_events", []),
        )
        template["learned_rules"] = _merge_learning_rules(
            existing.get("learned_rules", []),
            template.get("learned_rules", []),
        )

    with open(template_path, "w", encoding="utf-8") as f:
        json.dump(template, f, ensure_ascii=False, indent=2)

    index = load_index(templates_dir)
    index["templates"][tid] = {
        "supplier_name": supplier_name,
        "document_title": doc_title,
        "aliases": template.get("supplier_aliases", [supplier_name]),
        "file": f"{tid}.json",
    }
    save_index(index, templates_dir)
    return tid


def save_from_results(results_path: str, filename: str | None,
                      templates_dir: Path, corrections_json: str | None = None,
                      target_supplier: str | None = None, profile=None):
    p = _p(profile)
    corrections = json.loads(corrections_json) if corrections_json else None
    target_supplier_norm = target_supplier.lower().strip() if target_supplier else None

    with open(results_path, "r", encoding="utf-8") as f:
        batch = json.load(f)

    saved = []
    for result in batch.get("results", []):
        if result.get("status") != "success" or not result.get("data"):
            continue
        if filename and result.get("filename") != filename:
            continue

        supplier = result["data"].get("delivery_note", {}).get("supplier_name")
        doc_title = result["data"].get("document_title", "")
        if not supplier:
            continue
        if target_supplier_norm and supplier.lower().strip() != target_supplier_norm:
            continue

        existing_id = match_template_by_id(supplier, templates_dir, doc_title)
        if existing_id:
            if corrections:
                tmpl = load_template(existing_id, templates_dir)
                old_c = tmpl.get("field_mapping_corrections", [])
                cleaned = _field_mapping_corrections(corrections)
                tmpl["field_mapping_corrections"] = old_c + [
                    c for c in cleaned if c not in old_c
                ]
                events, rules = build_learning_entries(corrections, p)
                tmpl["learning_events"] = tmpl.get("learning_events", []) + events
                tmpl["learned_rules"] = _merge_learning_rules(
                    tmpl.get("learned_rules", []),
                    rules,
                )
                tmpl["updated_at"] = datetime.now().isoformat()
                save_template(tmpl, templates_dir)
                saved.append((supplier, existing_id, "updated"))
            else:
                print(f"  Template already exists for {supplier} [{doc_title}], skipping.")
            continue

        template = build_template_from_extraction(result, corrections, p)
        sid = save_template(template, templates_dir)
        saved.append((supplier, sid, "new"))

    if not saved:
        print("No templates saved.")
    else:
        for supplier, sid, action in saved:
            print(f"  {supplier} -> {sid} ({action})")


# ── Correct ──────────────────────────────────────────────────────────

def apply_corrections(supplier: str, corrections_json: str, templates_dir: Path,
                     document_title: str = "", profile=None):
    p = _p(profile)
    corrections = json.loads(corrections_json)
    sid = match_template_by_id(supplier, templates_dir, document_title)
    if not sid:
        print(f"No template found for '{supplier}' [{document_title}]")
        sys.exit(1)

    tmpl = load_template(sid, templates_dir)
    existing = tmpl.get("field_mapping_corrections", [])

    for c in _field_mapping_corrections(corrections):
        existing.append(c)
        field = c.get("field")
        actual = c.get("actual_meaning")
        if field and actual and field != actual:
            layout = tmpl.get("field_layout", {}).get("item_row_fields", {})
            if field in layout:
                layout[field]["mapped_to"] = actual
                layout[field]["notes"] = c.get("description", "")
            if actual in layout and actual not in ("unit_price", "quantity", "total_amount"):
                layout[actual]["present"] = True
                layout[actual]["source_field"] = field

    tmpl["field_mapping_corrections"] = existing
    events, rules = build_learning_entries(corrections, p)
    tmpl["learning_events"] = tmpl.get("learning_events", []) + events
    tmpl["learned_rules"] = _merge_learning_rules(tmpl.get("learned_rules", []), rules)
    tmpl["updated_at"] = datetime.now().isoformat()
    save_template(tmpl, templates_dir)
    print(f"Applied {len(corrections)} correction(s) to {supplier}:")
    for c in corrections:
        print(f"  {c.get('field')} -> {c.get('actual_meaning')}: {c.get('description', '')}")


# ── Review Formatting ────────────────────────────────────────────────

def format_extraction_review(result: dict, template: dict | None = None,
                             profile=None) -> str:
    """Generate comprehensive review for user confirmation.

    For NEW suppliers: shows data table + field-by-field confirmation checklist.
    For template-matched suppliers: shows data table + only issues.

    Column letters and field labels are derived from the active profile so the
    review aligns with the exported Excel sheet (previously it used a
    compressed, inconsistent scheme).
    """
    if result.get("status") == "error":
        return f"[ERROR] {result.get('filename', '')}: {result.get('error', 'unknown')}"

    p = _p(profile)
    labels = field_labels(p)
    cmap = _profile.excel_column_map(p)
    has_processing = bool((p.get("document_types") or {}).get("processing"))
    style_enabled = bool(p.get("style_number_rule", {}).get("enabled"))
    review_examples = (p.get("vocab") or {}).get("review_examples") or ["确认", "全部确认"]
    domain_noun = (p.get("vocab") or {}).get("domain_noun", "物料")

    data = result.get("data", {})
    delivery = data.get("delivery_note", {})
    items = data.get("items", [])
    supplier = delivery.get("supplier_name") or "未知供应商"
    note_number = delivery.get("note_number") or ""
    date = delivery.get("date") or ""
    needs_review = data.get("needs_review", [])
    sample = items[0] if items else {}

    lines = []
    # Header
    template_tag = f" [已有模版]" if template else " [新供应商]"
    lines.append(f"## {supplier}{template_tag}")
    lines.append(f"单号: {note_number}  日期: {date}")

    # ── Data table: Excel column-aligned format (from profile column_map) ──
    if items:
        # Build (col_letter, label, field_key) rows in column order.
        # Fields not mapped to a column (or mapped to a non-item key) are skipped.
        col_entries = []
        for key in [k for k, _ in _profile.item_fields(p)] + ["remark"]:
            col = cmap.get(key)
            if not col:
                continue
            # Prefer the positional header label (covers 备注/描述/件数)
            label = _profile.header_label_at(p, col) or labels.get(key, key)
            col_entries.append((
                _profile.col_index_to_letter(col),
                label,
                key,
            ))
        # supplier_name shown via the supplier column
        s_col = cmap.get("supplier_name")
        if s_col:
            s_label = _profile.header_label_at(p, s_col) or labels.get("supplier_name", "供应商")
            col_entries.append((_profile.col_index_to_letter(s_col), s_label, "supplier_name"))
        col_entries.sort(key=lambda c: c[0])

        if col_entries:
            lines.append("")
            hdr = " | ".join(f"{c[0]} {c[1]}" for c in col_entries)
            lines.append(f"| {hdr} |")
            lines.append("|" + "|".join(["---"] * len(col_entries)) + "|")
            for item in items:
                row_vals = []
                for _letter, _label, field_key in col_entries:
                    if field_key == "supplier_name":
                        val = supplier
                    else:
                        val = item.get(field_key)
                    row_vals.append("*(空)*" if val in (None, "", "/", "null") else str(val))
                lines.append("| " + " | ".join(row_vals) + " |")

    # ── For NEW suppliers: field-by-field confirmation checklist ──
    if not template and items:
        lines.append("")
        doc_type = data.get("document_type", "delivery")
        is_processing = has_processing and doc_type == "processing"
        section = "加工单逐项确认" if is_processing else "逐项确认"
        lines.append(f"**{section}（请逐条回复）：**")
        lines.append("")

        step = 1
        # Ask about each non-numeric item field that exists in the profile
        ask_keys = []
        for key, _ in _profile.item_fields(p):
            if key in ("unit_price", "quantity", "total_amount", "unit"):
                continue
            ask_keys.append(key)
        # For processing, material_name comes first
        if is_processing and "material_name" in ask_keys:
            ask_keys.remove("material_name")
            ask_keys.insert(0, "material_name")

        for key in ask_keys:
            label = labels.get(key, key)
            val = sample.get(key)
            col = cmap.get(key)
            col_txt = f"{_profile.col_index_to_letter(col)}列 " if col else ""
            if val and str(val).strip() not in ("", "/", "null"):
                lines.append(f"{step}. {col_txt}{label} = {val} → 确认正确？")
            else:
                lines.append(f"{step}. {col_txt}{label} = 缺失 → 此供应商有{label}吗？")
            step += 1

        # Amount verification
        total = data.get("total_amount")
        if total is not None and items:
            item_sum = sum(it.get("total_amount", 0) or 0 for it in items)
            diff = abs(item_sum - total)
            if diff <= 0.01:
                lines.append(f"{step}. 金额核对: 明细合计 ¥{item_sum:,.2f} = 单据总额 ¥{total:,.2f} ✓")
            else:
                lines.append(f"{step}. 金额核对: 明细合计 ¥{item_sum:,.2f} ≠ 单据总额 ¥{total:,.2f} [不一致]")
        else:
            lines.append(f"{step}. 金额核对: 无金额数据")

        lines.append("")
        ex = " ".join(f"{i+1}-{e}" for i, e in enumerate(["确认"] * min(step, 4)))
        lines.append(f"回复示例：'{ex}' 或 '全部确认'")
        if review_examples:
            lines.append(f"也可直接说：{' / '.join(review_examples[:3])}")

    # ── For template-matched suppliers: only show issues ──
    elif template:
        missing_fields = [i for i in needs_review if "缺失" in i]
        other_issues = [i for i in needs_review if "缺失" not in i]

        if missing_fields:
            lines.append("")
            lines.append("缺失: " + ", ".join(missing_fields))
            lines.append("如无此字段，回复 '无XX' 即可")

        if other_issues:
            lines.append("")
            for issue in other_issues:
                lines.append(f"[!] {issue}")

        if not missing_fields and not other_issues:
            lines.append("")
            lines.append("模版匹配，数据完整，自动确认。")

    return "\n".join(lines)


# ── Supplier Context for Prompt Injection ────────────────────────────

SUPPLIER_CONTEXT_BLOCK = """

已知供应商列表及其格式特征：
{supplier_lines}

如果识别到上述任一供应商，请严格遵循对应的格式特征进行提取。如果未匹配任何已知供应商，按通用规则提取。"""


def build_supplier_context(templates_dir: Path, profile=None) -> str:
    """Build supplier context block for injection into extraction prompt."""
    p = _p(profile)
    proc_name = (p.get("document_types") or {}).get("processing", {}).get("name", "加工单")
    index = load_index(templates_dir)
    if not index.get("templates"):
        return ""

    supplier_lines = []
    for sid, entry in index["templates"].items():
        tmpl = load_template(sid, templates_dir)
        if not tmpl:
            continue
        hints = tmpl.get("extraction_hints", {})
        layout = tmpl.get("field_layout", {}).get("item_row_fields", {})
        corrections = tmpl.get("field_mapping_corrections", [])

        parts = []
        doc_type = tmpl.get("document_type", "delivery")
        if doc_type == "processing":
            # Generic hint: processing-type suppliers typically have no fabric_code
            if "fabric_code" in {k for k, _ in _profile.item_fields(p)}:
                parts.append(f"类型:{proc_name}(fabric_code填null)")
            else:
                parts.append(f"类型:{proc_name}")
        present_fields = [k for k, v in layout.items() if v.get("present")]
        missing_fields = [k for k, v in layout.items() if not v.get("present")]
        if present_fields:
            parts.append(f"字段: {','.join(present_fields)}")
        if missing_fields:
            parts.append(f"无独立字段: {','.join(missing_fields)}")
        hw = hints.get("handwritten_fields", [])
        if hw:
            parts.append(f"手写: {','.join(hw)}")
        if hints.get("default_unit"):
            parts.append(f"默认单位: {hints['default_unit']}")

        fd = (tmpl.get("format_description") or "").strip()
        if fd:
            if len(fd) > 1200:
                fd = fd[:1199] + "…"
            parts.append(f"版式说明:{fd}")
        notes_txt = (hints.get("notes") or "").strip()
        if notes_txt:
            if len(notes_txt) > 600:
                notes_txt = notes_txt[:599] + "…"
            parts.append(f"补充:{notes_txt}")

        for rule in tmpl.get("learned_rules", []):
            summary = (rule.get("summary") or "").strip()
            if summary:
                parts.append(f"用户确认:{summary}")

        for c in corrections:
            field = c.get("field")
            actual = c.get("actual_meaning")
            if not field or not actual:
                continue
            if actual == "none":
                parts.append(f"无{field}字段，填null")
            elif field != actual:
                parts.append(
                    f"输出规则: 单据中的'{field}'位置的内容必须写入输出JSON的'{actual}'字段"
                    f"，{field}字段填null"
                )

        title_str = f" [{tmpl['document_title']}]" if tmpl.get("document_title") else ""
        supplier_lines.append(f"- {tmpl['supplier_name']}{title_str}: " + "; ".join(parts))

    if not supplier_lines:
        return ""
    return SUPPLIER_CONTEXT_BLOCK.format(supplier_lines="\n".join(supplier_lines))


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Invoice OCR template management")
    parser.add_argument("--templates-dir", type=str, default=str(TEMPLATES_DIR))
    parser.add_argument("--profile", default=None,
                        help="Profile id or path to a profile JSON")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="List all saved templates")

    show_p = sub.add_parser("show", help="Show template detail")
    show_p.add_argument("supplier", help="Supplier name or alias")

    del_p = sub.add_parser("delete", help="Delete a template")
    del_p.add_argument("supplier", help="Supplier name or alias")

    match_p = sub.add_parser("match", help="Find matching template")
    match_p.add_argument("--supplier", required=True, help="Supplier name to match")

    save_p = sub.add_parser("save", help="Save template from extraction results")
    save_p.add_argument("--from-results", required=True, help="Results JSON file")
    save_p.add_argument("--filename", help="Only save from specific image filename")
    save_p.add_argument("--corrections", help="JSON array of field mapping corrections")

    correct_p = sub.add_parser("correct", help="Apply field mapping corrections to existing template")
    correct_p.add_argument("supplier", help="Supplier name")
    correct_p.add_argument("--corrections", required=True,
                           help='JSON array, e.g. \'[{"field":"material_name","actual_meaning":"color_code","description":"品名列实际是色号/颜色"}]\'')

    review_p = sub.add_parser("review", help="Generate review text for extraction")
    review_p.add_argument("results_json", help="Results JSON file")
    review_p.add_argument("--filename", help="Review specific image only")

    sub.add_parser("context", help="Output supplier context block for prompt injection")

    # Preferences commands
    prefs_p = sub.add_parser("prefs", help="Show/set user preferences")
    prefs_p.add_argument("--set-table", help="Set default table path")
    prefs_p.add_argument("--set-supplier-table", nargs=2, metavar=("SUPPLIER", "PATH"),
                         help="Set table path for a specific supplier")

    args = parser.parse_args()
    tdir = Path(args.templates_dir)
    profile = _profile.resolve_profile(args)

    if args.command == "list":
        index = load_index(tdir)
        templates = index.get("templates", {})
        if not templates:
            print("No templates saved yet.")
            return
        print(f"Saved templates ({len(templates)}):")
        for sid, entry in templates.items():
            tmpl = load_template(sid, tdir)
            count = tmpl.get("source_count", "?") if tmpl else "?"
            corrections = len(tmpl.get("field_mapping_corrections", [])) if tmpl else 0
            corr_str = f", {corrections} corrections" if corrections else ""
            title = (entry.get("document_title") or "").strip() or "（无标题）"
            print(
                f"  《{title}》· {entry['supplier_name']} ({sid}) — {count} samples{corr_str}"
            )

    elif args.command == "show":
        sid = match_template_by_id(args.supplier, tdir)
        if not sid:
            print(f"No template found for '{args.supplier}'")
            sys.exit(1)
        tmpl = load_template(sid, tdir)
        print(json.dumps(tmpl, ensure_ascii=False, indent=2))

    elif args.command == "delete":
        sid = match_template_by_id(args.supplier, tdir)
        if not sid:
            print(f"No template found for '{args.supplier}'")
            sys.exit(1)
        path = tdir / f"{sid}.json"
        path.unlink(missing_ok=True)
        index = load_index(tdir)
        index["templates"].pop(sid, None)
        save_index(index, tdir)
        print(f"Deleted template: {sid}")

    elif args.command == "match":
        tmpl = match_template(args.supplier, tdir)
        if tmpl:
            print(f"Matched: {tmpl['supplier_name']}")
            print(json.dumps(tmpl, ensure_ascii=False, indent=2))
        else:
            print(f"No match for '{args.supplier}'")
            sys.exit(1)

    elif args.command == "save":
        save_from_results(args.from_results, args.filename, tdir, args.corrections, profile=profile)

    elif args.command == "correct":
        apply_corrections(args.supplier, args.corrections, tdir, profile=profile)

    elif args.command == "review":
        with open(args.results_json, "r", encoding="utf-8") as f:
            batch = json.load(f)
        for result in batch.get("results", []):
            if args.filename and result.get("filename") != args.filename:
                continue
            if result.get("status") != "success":
                continue
            supplier = result.get("data", {}).get("delivery_note", {}).get("supplier_name", "")
            doc_title = result.get("data", {}).get("document_title", "")
            tmpl = match_template(supplier, tdir, doc_title) if supplier else None
            print(format_extraction_review(result, tmpl, profile))
            print()

    elif args.command == "context":
        ctx = build_supplier_context(tdir, profile)
        if ctx:
            print(ctx)
        else:
            print("(no templates loaded)")

    elif args.command == "prefs":
        if args.set_table:
            set_default_table(args.set_table)
            print(f"Default table set to: {args.set_table}")
        elif args.set_supplier_table:
            supplier, path = args.set_supplier_table
            set_supplier_table(supplier, path)
            print(f"Table for {supplier} set to: {path}")
        else:
            prefs = load_preferences()
            print(json.dumps(prefs, ensure_ascii=False, indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
