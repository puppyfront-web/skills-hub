#!/usr/bin/env python3
"""
Export delivery-note extraction results to a user-selected Excel workbook.

Layout (headers, column mapping, formulas) is driven entirely by the active
profile (see _profile.py / ../profiles/*.json), so the same exporter serves
any factory scenario. Supports append/update mode: preserves existing file,
appends new rows, detects price changes on same item from same supplier
(highlights red).
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _profile
from _profile import load_profile

try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
except ImportError:
    print("Error: openpyxl required. Install with: pip install openpyxl", file=sys.stderr)
    sys.exit(1)

# ── Styling (domain-neutral) ───────────────────────────────────────────

HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)
REVIEW_FILL = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
ERROR_FILL = PatternFill(start_color="FCE4EC", end_color="FCE4EC", fill_type="solid")
PRICE_ALERT_FILL = PatternFill(start_color="FF6B6B", end_color="FF6B6B", fill_type="solid")
CLEAR_FILL = PatternFill(fill_type=None)
THIN_BORDER = Border(
    left=Side(style="thin"), right=Side(style="thin"),
    top=Side(style="thin"), bottom=Side(style="thin"),
)
NUM_FMT_MONEY = '#,##0.00'
NUM_FMT_QTY = '#,##0.##'
NUM_FMT_INT = '#,##0'

DEFAULT_COL_WIDTHS = {
    "单号": 18, "日期": 12, "款号": 10, "描述": 18, "件数": 8,
    "面料/辅料": 10, "面料厂家": 18, "面料款号": 14, "色号/颜色": 14,
    "物料分类": 12, "品名": 16, "供应商": 18, "规格": 12,
    "单价": 10, "数量": 10, "单位": 8, "金额": 14, "总金额": 14,
    "单件金额": 12, "用料": 10, "备注": 28, "客户": 14,
    "面料": 10, "辅料": 10, "砂洗": 10, "加工": 10,
    "裁床": 10, "吊牌": 10, "包装": 10, "合计": 12,
}


def _fmt_for(formula_def: dict) -> str | None:
    fmt = formula_def.get("fmt")
    if fmt == "money":
        return NUM_FMT_MONEY
    if fmt == "qty":
        return NUM_FMT_QTY
    if fmt == "int":
        return NUM_FMT_INT
    return None


def _load_results(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_results(path: str) -> dict:
    return _load_results(path)


def build_remark(item: dict, group: dict) -> str:
    """Build remark string from item metadata."""
    parts = []
    if group.get("note_number"):
        parts.append(f"单号:{group['note_number']}")
    if item.get("fabric_code_is_handwritten"):
        parts.append("手写货号")
    remark = item.get("remark") or ""
    if remark:
        parts.append(remark)
    return "; ".join(parts) if parts else ""


def iter_invoice_groups(batch: dict):
    """Yield (result_dict, group_meta) for each invoice, including errors."""
    for result in batch.get("results", []):
        if result.get("review_status") == "skipped":
            continue
        if result.get("status") == "success" and result.get("data"):
            data = result["data"]
            delivery = data.get("delivery_note", {})
            yield result, {
                "filename": result.get("filename", ""),
                "supplier_name": delivery.get("supplier_name") or "",
                "note_number": delivery.get("note_number") or "",
                "date": delivery.get("date") or "",
                "customer": delivery.get("customer") or "",
                "items": data.get("items", []),
                "total_amount": data.get("total_amount"),
                "confidence": data.get("confidence", "medium"),
                "needs_review": data.get("needs_review", []),
                "review_status": result.get("review_status", "pending"),
                "raw_text_notes": data.get("raw_text_notes") or "",
                "is_error": False,
            }
        else:
            yield result, {
                "filename": result.get("filename", ""),
                "supplier_name": "",
                "note_number": "",
                "date": "",
                "customer": "",
                "items": [],
                "total_amount": None,
                "confidence": "low",
                "needs_review": [],
                "review_status": result.get("review_status", "pending"),
                "raw_text_notes": "",
                "is_error": True,
                "error": result.get("error") or "unknown error",
            }


def _set_cell(ws, row: int, col: int, value=None, fmt=None, fill=None):
    """Write a cell with border and optional format/fill."""
    c = ws.cell(row=row, column=col, value=value)
    c.border = THIN_BORDER
    if fmt:
        c.number_format = fmt
    if fill is not None:
        c.fill = fill
    return c


# ── Profile-driven layout ──────────────────────────────────────────────

def _main_headers(profile: dict) -> list[str]:
    return _profile.excel_main_headers(profile)


def _all_headers(profile: dict) -> list[str]:
    return _profile.all_excel_headers(profile)


def _num_main(profile: dict) -> int:
    return len(_main_headers(profile))


def _num_all(profile: dict) -> int:
    return len(_all_headers(profile))


def _setup_header(ws, profile: dict):
    """Write header row with styling."""
    headers = _all_headers(profile)
    for col, header in enumerate(headers, 1):
        c = ws.cell(row=1, column=col, value=header)
        c.fill = HEADER_FILL
        c.font = HEADER_FONT
        c.alignment = HEADER_ALIGNMENT
        c.border = THIN_BORDER


def _setup_columns(ws, profile: dict):
    """Set column widths, freeze panes, auto-filter."""
    headers = _all_headers(profile)
    for col, header in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(col)].width = DEFAULT_COL_WIDTHS.get(header, 12)
    ws.freeze_panes = "A2"


def _write_item_row(ws, r: int, group: dict, item: dict, profile: dict,
                    row_fill=None, price_alert: bool = False,
                    preserve_user_columns: bool = False):
    """Write a single item row starting at row r, driven by profile.column_map."""
    cmap = _profile.excel_column_map(profile)
    user_fill = set(_profile.excel_user_fill_columns(profile))
    formulas = {f.get("col"): f for f in _profile.excel_formulas(profile)}
    num_all = _num_all(profile)

    # 1. Field-value columns from column_map (json_key -> col)
    for key, col in cmap.items():
        if key == "note_number":
            value = group.get("note_number") or ""
        elif key == "date":
            value = group.get("date") or ""
        elif key == "supplier_name":
            value = item.get("supplier") or group["supplier_name"] or ""
        elif key == "remark":
            value = build_remark(item, group)
        else:
            value = item.get(key)
            if value is None:
                value = ""
        # number format for price/qty-like columns
        fmt = None
        if key == "unit_price":
            fmt = NUM_FMT_MONEY
            fill = PRICE_ALERT_FILL if price_alert else row_fill
            _set_cell(ws, r, col, value=item.get("unit_price"), fmt=fmt, fill=fill)
            continue
        if key == "quantity":
            fmt = NUM_FMT_QTY
        if key in ("total_amount",):
            fmt = NUM_FMT_MONEY
        _set_cell(ws, r, col, value=value or None, fmt=fmt, fill=row_fill)

    # 2. User-fill columns (e.g. 描述/件数) left blank on new rows
    if not preserve_user_columns:
        for col in user_fill:
            if col not in cmap.values():
                _set_cell(ws, r, col, fill=row_fill)

    # 3. Formula columns (may overlap a column_map col, e.g. total_amount)
    for fcol_letter, fdef in formulas.items():
        col_idx = _profile.col_letter_to_index(fcol_letter)
        expr = fdef.get("expr", "").replace("{r}", str(r))
        fmt = _fmt_for(fdef)
        needs = fdef.get("needs", [])
        fallback_field = fdef.get("fallback_field")
        # "__always__" means write the formula unconditionally (e.g. per-piece = M/E)
        if "__always__" in needs:
            _set_cell(ws, r, col_idx, value=expr, fmt=fmt, fill=row_fill)
            continue
        # Otherwise only when all needed item fields are present
        has_all = all(item.get(n) is not None for n in needs)
        if has_all:
            _set_cell(ws, r, col_idx, value=expr, fmt=fmt, fill=row_fill)
        elif fallback_field:
            fb = item.get(fallback_field)
            _set_cell(ws, r, col_idx, value=fb, fmt=fmt, fill=row_fill)

    # 4. Cost-breakdown columns (trailing, user fills)
    if not preserve_user_columns:
        cost_start = _num_main(profile) + 1
        for col in range(cost_start, num_all + 1):
            if col in cmap.values():
                continue
            if col in user_fill:
                continue
            # leave blank with border/fill
            _set_cell(ws, r, col, fill=row_fill)


# ── Price / note dedup indexes ─────────────────────────────────────────

def _supplier_col(profile: dict) -> int:
    return _profile.excel_column_map(profile).get("supplier_name", 0)


def _fabriccode_col(profile: dict) -> int:
    return _profile.excel_column_map(profile).get("fabric_code", 0)


def _unitprice_col(profile: dict) -> int:
    return _profile.excel_column_map(profile).get("unit_price", 0)


def _notenumber_col(profile: dict) -> int:
    return _profile.excel_column_map(profile).get("note_number", 1)


def _remark_col(profile: dict) -> int:
    return _profile.excel_column_map(profile).get("remark", 0)


def _build_price_index(ws, last_row: int, profile: dict) -> dict:
    """Build index of (supplier, item_code) -> unit_price from existing rows.

    Uses supplier + fabric_code columns if fabric_code exists in the profile;
    otherwise indexes by supplier only.
    """
    prices = {}
    s_col = _supplier_col(profile)
    f_col = _fabriccode_col(profile)
    p_col = _unitprice_col(profile)
    if not s_col or not p_col:
        return prices
    for row in range(2, last_row + 1):
        supplier = ws.cell(row=row, column=s_col).value
        unit_price = ws.cell(row=row, column=p_col).value
        code = ws.cell(row=row, column=f_col).value if f_col else None
        if not supplier:
            continue
        key = (str(supplier).strip(), str(code or "").strip())
        if key not in prices and unit_price is not None:
            try:
                prices[key] = float(unit_price)
            except (TypeError, ValueError):
                continue
    return prices


def _price_alert_for_item(price_index: dict, group: dict, item: dict,
                          profile: dict) -> tuple[bool, tuple | None]:
    s_col_keys = "supplier_name"
    f_col_keys = "fabric_code"
    supplier = item.get("supplier") or group["supplier_name"] or ""
    unit_price = item.get("unit_price")
    if not supplier or unit_price is None:
        return False, None
    fabric_code = str(item.get(f_col_keys) or "").strip()
    if not fabric_code:
        return False, None
    key = (str(supplier).strip(), fabric_code)
    prev = price_index.get(key)
    if prev is not None and abs(float(unit_price) - prev) > 0.005:
        return True, key
    return False, key


def _normalized_cell_value(value) -> str:
    return str(value or "").strip()


def _note_key(supplier, note_number):
    note = _normalized_cell_value(note_number)
    if not note:
        return None
    return ("note", f"{_normalized_cell_value(supplier)}|{note}")


def _item_key(supplier, fabric_code):
    return ("sf", f"{_normalized_cell_value(supplier)}|{_normalized_cell_value(fabric_code)}")


def _build_note_index(ws, last_row: int, profile: dict) -> dict:
    """Build index of note_number (or supplier+code) -> set of row numbers."""
    index = {}
    s_col = _supplier_col(profile)
    f_col = _fabriccode_col(profile)
    n_col = _notenumber_col(profile)
    for row in range(2, last_row + 1):
        note = ws.cell(row=row, column=n_col).value
        supplier = ws.cell(row=row, column=s_col).value if s_col else None
        fabric_code = ws.cell(row=row, column=f_col).value if f_col else None
        if note and str(note).strip():
            key = _note_key(supplier, note)
        else:
            key = _item_key(supplier, fabric_code)
        index.setdefault(key, set()).add(row)
    return index


def _is_invoice_entry_sheet(ws, profile: dict) -> bool:
    headers = [
        ws.cell(row=1, column=col).value
        for col in range(1, _num_main(profile) + 1)
    ]
    return headers == _main_headers(profile)


def _prepare_invoice_sheet(wb, profile: dict):
    """Find or create the sheet used for invoice rows without replacing user sheets."""
    sheet_name = _profile.excel_sheet_name(profile)
    if _is_invoice_entry_sheet(wb.active, profile):
        return wb.active, False

    for ws in wb.worksheets:
        if _is_invoice_entry_sheet(ws, profile):
            return ws, False

    if sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        if ws.max_row == 1 and ws.max_column == 1 and ws.cell(row=1, column=1).value is None:
            _setup_header(ws, profile)
            _setup_columns(ws, profile)
            return ws, True

    ws = wb.create_sheet(sheet_name)
    _setup_header(ws, profile)
    _setup_columns(ws, profile)
    return ws, True


def _write_error_row(ws, row_num: int, group: dict, profile: dict):
    num_all = _num_all(profile)
    for col in range(1, num_all + 1):
        _set_cell(ws, row_num, col, fill=ERROR_FILL)
    s_col = _supplier_col(profile) or 7
    r_col = _remark_col(profile) or _num_main(profile)
    # put failure marker near material-type area if present
    m_col = _profile.excel_column_map(profile).get("material_type", 6)
    ws.cell(row=row_num, column=m_col, value="[提取失败]")
    ws.cell(row=row_num, column=s_col, value=group.get("filename", ""))
    ws.cell(row=row_num, column=r_col, value=group.get("error", ""))
    for col in range(1, num_all + 1):
        ws.cell(row=row_num, column=col).fill = ERROR_FILL


def _write_empty_items_row(ws, row_num: int, group: dict, profile: dict, row_fill):
    num_all = _num_all(profile)
    for col in range(1, num_all + 1):
        _set_cell(ws, row_num, col, fill=row_fill)
    n_col = _notenumber_col(profile)
    d_col = _profile.excel_column_map(profile).get("date", 2)
    s_col = _supplier_col(profile) or 7
    r_col = _remark_col(profile) or _num_main(profile)
    ws.cell(row=row_num, column=n_col, value=group.get("note_number") or "")
    ws.cell(row=row_num, column=d_col, value=group.get("date") or "")
    ws.cell(row=row_num, column=s_col, value=group["supplier_name"] or group["filename"])
    ws.cell(row=row_num, column=r_col, value=f"[无明细] {group.get('raw_text_notes', '')}")


def export_xlsx_append(batch: dict, output_path: str, only_confirmed: bool = False,
                       profile: dict | None = None):
    """Append new rows to existing Excel file, preserving user edits."""
    if profile is None:
        profile = load_profile()
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    num_all = _num_all(profile)

    if output_file.exists():
        try:
            wb = load_workbook(output_path)
        except PermissionError:
            print(f"Error: Cannot open {output_path} (permission denied). "
                  f"Using a new file.", file=sys.stderr)
            output_path = str(Path(output_path).with_name(
                Path(output_path).stem + "_new" + Path(output_path).suffix))
            wb = Workbook()
            ws = wb.active
            ws.title = _profile.excel_sheet_name(profile)
            _setup_header(ws, profile)
            _setup_columns(ws, profile)
            last_row = 1
        else:
            ws, created_sheet = _prepare_invoice_sheet(wb, profile)
            last_row = ws.max_row
            while last_row > 1 and all(
                ws.cell(row=last_row, column=c).value is None
                for c in range(1, num_all + 1)
            ):
                last_row -= 1
            if created_sheet:
                last_row = 1
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = _profile.excel_sheet_name(profile)
        _setup_header(ws, profile)
        _setup_columns(ws, profile)
        last_row = 1

    price_index = _build_price_index(ws, last_row, profile)
    existing_notes = _build_note_index(ws, last_row, profile)

    row_num = last_row + 1
    new_rows = 0
    price_alerts = 0

    for result, group in iter_invoice_groups(batch):
        if only_confirmed and group["review_status"] != "confirmed":
            continue

        items = group["items"]
        has_review = bool(group.get("needs_review"))
        row_fill = REVIEW_FILL if has_review else CLEAR_FILL

        if group.get("is_error"):
            _write_error_row(ws, row_num, group, profile)
            row_num += 1
            new_rows += 1
            continue

        if not items:
            _write_empty_items_row(ws, row_num, group, profile, row_fill)
            row_num += 1
            new_rows += 1
            continue

        # Check if this note was already imported
        note_number = group.get("note_number") or ""
        note_key = _note_key(group["supplier_name"], note_number)
        existing_note_rows = sorted(existing_notes.get(note_key, [])) if note_key else []
        if existing_note_rows:
            group_rows = []
            for idx, item in enumerate(items):
                price_alert, price_key = _price_alert_for_item(price_index, group, item, profile)
                if price_alert:
                    price_alerts += 1
                if idx < len(existing_note_rows):
                    target_row = existing_note_rows[idx]
                    _write_item_row(ws, target_row, group, item, profile, row_fill,
                                    price_alert, preserve_user_columns=True)
                    group_rows.append(target_row)
                else:
                    _write_item_row(ws, row_num, group, item, profile, row_fill, price_alert)
                    group_rows.append(row_num)
                    row_num += 1
                    new_rows += 1
                if price_key:
                    price_index[price_key] = float(item.get("unit_price"))
            existing_notes[note_key] = set(group_rows)
            continue

        wrote_group = False
        group_rows = []
        for item in items:
            item_key = None
            if not note_key:
                supplier = str(item.get("supplier") or group["supplier_name"] or "").strip()
                fabric_code = str(item.get("fabric_code") or "").strip()
                item_key = _item_key(supplier, fabric_code)
                if item_key in existing_notes:
                    target_row = min(existing_notes[item_key])
                    price_alert, price_key = _price_alert_for_item(price_index, group, item, profile)
                    if price_alert:
                        price_alerts += 1
                    _write_item_row(ws, target_row, group, item, profile, row_fill,
                                    price_alert, preserve_user_columns=True)
                    if price_key:
                        price_index[price_key] = float(item.get("unit_price"))
                    continue

            price_alert, price_key = _price_alert_for_item(price_index, group, item, profile)
            if price_alert:
                price_alerts += 1
            _write_item_row(ws, row_num, group, item, profile, row_fill, price_alert)
            wrote_group = True
            group_rows.append(row_num)
            if item_key:
                existing_notes.setdefault(item_key, set()).add(row_num)
            if price_key:
                price_index[price_key] = float(item.get("unit_price"))
            row_num += 1
            new_rows += 1

        if note_key and wrote_group:
            existing_notes.setdefault(note_key, set()).update(group_rows)

    if last_row == 1:
        _setup_columns(ws, profile)

    final_row = row_num - 1
    if final_row >= 2:
        ws.auto_filter.ref = f"A1:{get_column_letter(num_all)}{final_row}"

    try:
        wb.save(output_path)
    except PermissionError:
        alt = str(Path(output_path).with_name(
            Path(output_path).stem + "_new" + Path(output_path).suffix))
        print(f"Warning: Cannot write to {output_path}, saving to {alt}", file=sys.stderr)
        wb.save(alt)
        output_path = alt
    return new_rows, price_alerts


def export_xlsx_full(batch: dict, output_path: str, only_confirmed: bool = False,
                     profile: dict | None = None):
    """Full regeneration — creates new file from scratch."""
    if profile is None:
        profile = load_profile()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    num_all = _num_all(profile)
    wb = Workbook()
    ws = wb.active
    ws.title = _profile.excel_sheet_name(profile)
    _setup_header(ws, profile)

    row_num = 2
    for result, group in iter_invoice_groups(batch):
        if only_confirmed and group["review_status"] != "confirmed":
            continue
        items = group["items"]
        has_review = bool(group.get("needs_review"))
        row_fill = REVIEW_FILL if has_review else CLEAR_FILL

        if group.get("is_error"):
            _write_error_row(ws, row_num, group, profile)
            row_num += 1
            continue
        if not items:
            _write_empty_items_row(ws, row_num, group, profile, row_fill)
            row_num += 1
            continue
        for item in items:
            _write_item_row(ws, row_num, group, item, profile, row_fill)
            row_num += 1

    _setup_columns(ws, profile)
    last_row = row_num - 1
    if last_row >= 2:
        ws.auto_filter.ref = f"A1:{get_column_letter(num_all)}{last_row}"
    wb.save(output_path)
    return last_row - 1, 0


def export_csv(batch: dict, output_path: str, only_confirmed: bool = False,
               profile: dict | None = None):
    """Export to CSV (formulas become computed values)."""
    if profile is None:
        profile = load_profile()
    headers = _all_headers(profile)
    cmap = _profile.excel_column_map(profile)
    num_main = _num_main(profile)
    cost_count = len(_profile.excel_cost_headers(profile))
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)

        for result, group in iter_invoice_groups(batch):
            if only_confirmed and group["review_status"] != "confirmed":
                continue

            if group.get("is_error"):
                row = [""] * len(headers)
                m_col = cmap.get("material_type", 6)
                s_col = _supplier_col(profile) or 7
                r_col = _remark_col(profile) or num_main
                row[m_col - 1] = "[提取失败]"
                row[s_col - 1] = group.get("filename", "")
                row[r_col - 1] = group.get("error", "")
                writer.writerow(row)
                continue

            for item in group["items"]:
                unit_price = item.get("unit_price")
                quantity = item.get("quantity")
                total = (
                    unit_price * quantity
                    if unit_price is not None and quantity is not None
                    else item.get("total_amount")
                )
                # Build row by header position
                row = [""] * len(headers)
                for key, col in cmap.items():
                    if col > len(row):
                        continue
                    if key == "note_number":
                        row[col - 1] = group.get("note_number", "")
                    elif key == "date":
                        row[col - 1] = group.get("date", "")
                    elif key == "supplier_name":
                        row[col - 1] = item.get("supplier") or group["supplier_name"]
                    elif key == "remark":
                        row[col - 1] = build_remark(item, group)
                    elif key == "total_amount":
                        row[col - 1] = total if total is not None else ""
                    else:
                        v = item.get(key)
                        row[col - 1] = v if v is not None else ""
                # formula columns -> computed
                for fdef in _profile.excel_formulas(profile):
                    fcol = _profile.col_letter_to_index(fdef.get("col", "Z"))
                    if fcol <= len(row) and fdef.get("needs") != ["__always__"]:
                        row[fcol - 1] = total if total is not None else ""
                writer.writerow(row + [""] * cost_count)

            if not group["items"]:
                row = [""] * len(headers)
                n_col = _notenumber_col(profile)
                s_col = _supplier_col(profile) or 7
                r_col = _remark_col(profile) or num_main
                row[n_col - 1] = group.get("note_number", "")
                row[s_col - 1] = group["supplier_name"] or group["filename"]
                row[r_col - 1] = f"[无明细] {group.get('raw_text_notes', '')}"
                writer.writerow(row)


def main():
    parser = argparse.ArgumentParser(description="Export delivery-note results to Excel/CSV")
    parser.add_argument("input_json", help="Results JSON file from extract.py")
    parser.add_argument("--output", "-o", help="Output file path (overrides preferences)")
    parser.add_argument("--format", "-f", choices=["xlsx", "csv"], default="xlsx")
    parser.add_argument("--mode", "-m", choices=["append", "full"], default="append",
                        help="Export mode: append (default, preserves existing file) or full (regenerate)")
    parser.add_argument("--only-confirmed", action="store_true",
                        help="Only export confirmed/reviewed invoices")
    parser.add_argument("--profile", default=None,
                        help="Profile id or path to a profile JSON")
    args = parser.parse_args()

    profile = _profile.resolve_profile(args)
    batch = _load_results(args.input_json)

    if args.output:
        output_path = args.output
    else:
        from templates import get_table_for_supplier
        output_path = None
        for result in batch.get("results", []):
            if result.get("status") == "success" and result.get("data"):
                supplier = result["data"].get("delivery_note", {}).get("supplier_name", "")
                if supplier:
                    pref = get_table_for_supplier(supplier)
                    if pref:
                        output_path = pref
                        break
        if not output_path:
            print(
                "Error: No output table specified. Pass --output with the target Excel/CSV file, "
                "or set a saved table preference first.",
                file=sys.stderr,
            )
            sys.exit(1)

    if args.format == "csv":
        export_csv(batch, output_path, args.only_confirmed, profile)
        rows = sum(1 for _, g in iter_invoice_groups(batch)
                   if not args.only_confirmed or g["review_status"] == "confirmed")
        print(f"Exported {rows} rows to {output_path}")
    elif args.mode == "append":
        new_rows, alerts = export_xlsx_append(batch, output_path, args.only_confirmed, profile)
        confirmed = batch.get("summary", {}).get("confirmed", 0)
        pending = batch.get("summary", {}).get("pending_review", 0)
        print(f"Appended {new_rows} rows to {output_path}")
        if alerts:
            print(f"  {alerts} price change(s) detected (highlighted red)")
        print(f"  {confirmed} confirmed, {pending} pending review")
    else:
        rows, _ = export_xlsx_full(batch, output_path, args.only_confirmed, profile)
        confirmed = batch.get("summary", {}).get("confirmed", 0)
        pending = batch.get("summary", {}).get("pending_review", 0)
        print(f"Exported {rows} rows to {output_path}")
        print(f"  {confirmed} confirmed, {pending} pending review")


if __name__ == "__main__":
    main()
