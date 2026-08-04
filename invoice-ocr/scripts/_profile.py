#!/usr/bin/env python3
"""
Profile loading and access layer for invoice-ocr.

A "profile" is a JSON file in ../profiles/ that defines the rules for one
factory scenario: material enum, units, style-number rules, field labels,
Excel headers/formulas, supplier suffixes, vocab. All other scripts read
these rules from a profile dict instead of hardcoding garment-fabric values.

Resolution order for the active profile id:
    --profile arg > INVOICE_OCR_PROFILE env > preferences.active_profile > "generic-factory"

The garment-fabric profile preserves the original behaviour byte-for-byte;
generic-factory is the open, industry-neutral default.
"""

import json
import os
import re
from pathlib import Path

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"
DEFAULT_PROFILE_ID = "generic-factory"

# Backward-compat: if the user previously learned templates under the old
# openclaw path, keep reading from there so their saved supplier templates
# are not lost on upgrade.
_LEGACY_STATE_DIR = Path.home() / ".openclaw" / "skill-state"
STATE_DIR = Path.home() / ".invoice-ocr"
if _LEGACY_STATE_DIR.is_dir() and not STATE_DIR.is_dir():
    STATE_DIR = _LEGACY_STATE_DIR
TEMPLATES_DIRNAME = "invoice-ocr-templates"
PREFS_FILENAME = "invoice-ocr-preferences.json"

# Legacy openclaw env names kept readable for backward compatibility;
# standard OpenAI env names take priority in the gateway resolver (process.py).
ENV_OPENAI_BASE_URL = "OPENAI_BASE_URL"
ENV_OPENAI_API_KEY = "OPENAI_API_KEY"
ENV_OPENAI_MODEL = "OPENAI_MODEL"
ENV_GATEWAY_HOST = "OPENAI_GATEWAY_HOST"   # new neutral name
ENV_GATEWAY_PORT = "OPENAI_GATEWAY_PORT"
ENV_GATEWAY_TOKEN = "OPENAI_GATEWAY_TOKEN"
LEGACY_ENV_HOST = "OPENCLAW_GATEWAY_HOST"
LEGACY_ENV_PORT = "OPENCLAW_GATEWAY_PORT"
LEGACY_ENV_TOKEN = "OPENCLAW_GATEWAY_TOKEN"


def _deep_copy(obj):
    return json.loads(json.dumps(obj))


def list_profiles() -> list[dict]:
    """List all profile files in PROFILES_DIR as {id, name, description}."""
    out = []
    if not PROFILES_DIR.is_dir():
        return out
    for p in sorted(PROFILES_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append({
            "id": data.get("id", p.stem),
            "name": data.get("name", data.get("id", p.stem)),
            "description": data.get("description", ""),
            "path": str(p),
        })
    return out


def load_profile(profile_id_or_path: str | None = None) -> dict:
    """Load a profile by id (looked up in PROFILES_DIR) or by file path.

    Falls back to DEFAULT_PROFILE_ID on any failure, after warning.
    """
    if profile_id_or_path:
        # Explicit file path
        p = Path(profile_id_or_path)
        if p.is_file():
            try:
                return _finish_load(json.loads(p.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError) as e:
                print(f"Warning: failed to load profile '{profile_id_or_path}': {e}; "
                      f"falling back to {DEFAULT_PROFILE_ID}", flush=True)
        # Look up by id in PROFILES_DIR
        candidate = PROFILES_DIR / f"{profile_id_or_path}.json"
        if candidate.is_file():
            try:
                return _finish_load(json.loads(candidate.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError) as e:
                print(f"Warning: failed to load profile '{profile_id_or_path}': {e}; "
                      f"falling back to {DEFAULT_PROFILE_ID}", flush=True)

    default = PROFILES_DIR / f"{DEFAULT_PROFILE_ID}.json"
    try:
        return _finish_load(json.loads(default.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError) as e:
        raise RuntimeError(f"Cannot load default profile {default}: {e}")


def _finish_load(profile: dict) -> dict:
    """Validate and normalize a loaded profile dict."""
    profile.setdefault("id", DEFAULT_PROFILE_ID)
    profile.setdefault("name", profile["id"])
    profile.setdefault("document_types", {})
    profile.setdefault("material_types", [])
    profile.setdefault("units", [])
    profile.setdefault("style_number_rule", {"enabled": False, "patterns": [], "forbidden": []})
    profile.setdefault("fields", {"delivery_note": [], "items": []})
    profile.setdefault("excel", {})
    profile.setdefault("supplier_suffixes", [])
    profile.setdefault("vocab", {})
    profile.setdefault("description", "")
    profile["excel"].setdefault("column_map", {})
    profile["excel"].setdefault("user_fill_columns", [])
    profile["excel"].setdefault("cost_headers", [])
    profile["excel"].setdefault("formulas", [])
    profile["excel"].setdefault("sheet_name", "票据录入")
    return profile


def resolve_profile(args=None) -> dict:
    """Resolve the active profile from --profile arg > env > prefs > default."""
    profile_id = None
    if args is not None:
        profile_id = getattr(args, "profile", None)
    if not profile_id:
        profile_id = os.environ.get("INVOICE_OCR_PROFILE")
    if not profile_id:
        profile_id = _prefs_active_profile()
    return load_profile(profile_id)


def _prefs_active_profile() -> str | None:
    try:
        prefs_path = STATE_DIR / PREFS_FILENAME
        if prefs_path.is_file():
            return json.loads(prefs_path.read_text(encoding="utf-8")).get("active_profile")
    except (json.JSONDecodeError, OSError):
        pass
    return None


# ── Field helpers (built from profile.fields) ──────────────────────────

def item_fields(profile: dict) -> list[tuple[str, str]]:
    """Return [(json_key, label), ...] for item-row fields."""
    return [(pair[0], pair[1]) for pair in profile.get("fields", {}).get("items", [])]


def header_fields(profile: dict) -> list[tuple[str, str]]:
    """Return [(json_key, label), ...] for delivery-note header fields."""
    return [(pair[0], pair[1]) for pair in profile.get("fields", {}).get("delivery_note", [])]


def all_known_fields(profile: dict) -> list[tuple[str, str]]:
    """item fields + header fields (header first)."""
    return header_fields(profile) + item_fields(profile)


def field_labels(profile: dict) -> dict[str, str]:
    """json_key -> Chinese label, from both item and header fields."""
    return {k: v for k, v in all_known_fields(profile)}


def field_name_map(profile: dict) -> dict[str, str]:
    """Chinese label -> json_key, for natural-language correction parsing.

    Includes both the profile's labels and a few common synonyms mapped to
    each label's json_key (so '货号'/'规格' etc. still parse generically).
    """
    m: dict[str, str] = {}
    for key, label in all_known_fields(profile):
        m[label] = key
        if key not in m:
            m[key] = key
    return m


def field_display_name(profile: dict, field: str) -> str:
    """Short display name for a json_key (falls back to labels, then key)."""
    labels = field_labels(profile)
    return labels.get(field, field)


# ── Style-number rule text (injected into prompt only when enabled) ────

def style_rule_text(profile: dict) -> str:
    """Render the style_number judgment rules for the prompt, or '' if disabled."""
    rule = profile.get("style_number_rule", {}) or {}
    if not rule.get("enabled"):
        return ""
    patterns = [p for p in rule.get("patterns", []) if p]
    forbidden = [f for f in rule.get("forbidden", []) if f]
    if not patterns:
        return ""

    parts = [
        "【款号 style_number 判定】 — 与货号/编号区分；仅当至少一条满足时可写入 style_number，否则为 null、勿猜："
    ]
    descs = []
    for pat in patterns:
        descs.append(f"匹配正则 `{pat}`（去空格后判断）")
    parts.append("   - " + "；或 ".join(descs))
    if forbidden:
        parts.append(
            f"   - 即使出现在货号列，也不得将 {', '.join(f'`{f}`' for f in forbidden)} 写入 style_number（可写 remark 或适当时 fabric_code/货号）"
        )
    parts.append("   - 同单多码时，仅将符合上述条件的写入 style_number；勿用厂内码充当款号")
    return "\n".join(parts)


# ── Excel column helpers ───────────────────────────────────────────────

def excel_sheet_name(profile: dict) -> str:
    return profile.get("excel", {}).get("sheet_name", "票据录入")


def excel_column_map(profile: dict) -> dict:
    """json_key -> 1-based column number (keys starting with '_' are ignored)."""
    raw = profile.get("excel", {}).get("column_map", {}) or {}
    return {k: int(v) for k, v in raw.items()
            if not str(k).startswith("_") and isinstance(v, int)}


def excel_formulas(profile: dict) -> list[dict]:
    return profile.get("excel", {}).get("formulas", []) or []


def excel_cost_headers(profile: dict) -> list[str]:
    return profile.get("excel", {}).get("cost_headers", []) or []


def excel_user_fill_columns(profile: dict) -> list[int]:
    return [int(c) for c in profile.get("excel", {}).get("user_fill_columns", []) or []]


def col_letter_to_index(letter: str) -> int:
    """'A'->1, 'J'->10, 'AA'->27."""
    letter = letter.strip().upper()
    n = 0
    for ch in letter:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n


def col_index_to_letter(idx: int) -> str:
    """1->'A', 27->'AA'."""
    s = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        s = chr(ord("A") + rem) + s
    return s


def excel_main_headers(profile: dict) -> list[str]:
    """Explicit ordered main-table headers (A1, B1, ...). Falls back to
    deriving from column_map + field labels if the profile omits `headers`."""
    headers = profile.get("excel", {}).get("headers")
    if headers:
        return list(headers)
    cmap = excel_column_map(profile)
    max_col = max(cmap.values()) if cmap else 0
    out = [""] * max_col
    labels = field_labels(profile)
    for key, col in cmap.items():
        out[col - 1] = labels.get(key, key)
    return out


def header_label_at(profile: dict, col_index: int) -> str:
    """Return the header label for a 1-based column index, from excel.headers
    (covers positional columns like 备注/描述/件数 that aren't field-mapped)."""
    headers = excel_main_headers(profile)
    if 1 <= col_index <= len(headers):
        return headers[col_index - 1]
    return ""


def all_excel_headers(profile: dict) -> list[str]:
    """Ordered list of header strings for the whole sheet (main + cost)."""
    return excel_main_headers(profile) + excel_cost_headers(profile)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Inspect invoice-ocr profiles")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("list", help="List available profiles")
    show = sub.add_parser("show", help="Show a profile")
    show.add_argument("profile_id", nargs="?", default=DEFAULT_PROFILE_ID)
    args = parser.parse_args()
    if args.cmd == "list":
        for p in list_profiles():
            default = " (default)" if p["id"] == DEFAULT_PROFILE_ID else ""
            print(f"  {p['id']}{default}: {p['name']}")
            if p["description"]:
                print(f"      {p['description']}")
    elif args.cmd == "show":
        print(json.dumps(load_profile(args.profile_id), ensure_ascii=False, indent=2))
    else:
        parser.print_help()
