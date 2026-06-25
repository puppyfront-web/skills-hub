# Invoice OCR 高度自动化录入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 首次设置一次目标表后，让用户只需发送单张或批量票据照片，系统自动识别、自动学习新供应商版式、自动写表，仅在异常时集中请求确认。

**Architecture:** 保留 `process.py` 作为批次编排入口，把表头映射和目标表校验提取到独立的 `table_schema.py`；`templates.py` 负责严格校验后的模板学习；`export.py` 负责按目标表表头写入、重复单号跳过、价格标红和写入统计。所有行为变更先由 `unittest` 失败用例定义，再做最小实现。

**Tech Stack:** Python 3、标准库 `unittest`、`openpyxl`、现有 OpenClaw Vision/OCR 调用。

---

## 文件结构

**新增：**

- `invoice-ocr/scripts/table_schema.py`：标准字段、中文别名、表头映射、Excel 校验和按表头创建工作簿。
- `invoice-ocr/tests/__init__.py`：测试包标记。
- `invoice-ocr/tests/test_table_schema.py`：目标表初始化和表头映射测试。
- `invoice-ocr/tests/test_templates.py`：自动录入判定、模板学习和纠偏持久化测试。
- `invoice-ocr/tests/test_export.py`：自定义表头写入、重复单号、批量部分成功和价格标红测试。
- `invoice-ocr/tests/test_process.py`：目标表复用、批次排序、结果摘要和异常聚合测试。
- `invoice-ocr/tests/test_skill_contract.py`：Skill 自动化工作流静态契约测试。

**修改：**

- `invoice-ocr/scripts/process.py`：目标表初始化命令、批次状态编排、顺序稳定的后处理、自动模板学习调用和结果统计。
- `invoice-ocr/scripts/templates.py`：目标表偏好校验、完整性校验、自动确认与模板保存。
- `invoice-ocr/scripts/export.py`：表头驱动写入、重复单号跳过、结构化导出统计；保留价格标红。
- `invoice-ocr/SKILL.md`：改为“首次设表，之后只发照片；正常自动，异常集中确认”。

## Task 1：建立测试框架和目标表表头模型

**Files:**

- Create: `invoice-ocr/tests/__init__.py`
- Create: `invoice-ocr/tests/test_table_schema.py`
- Create: `invoice-ocr/scripts/table_schema.py`

- [ ] **Step 1：写表头映射失败测试**

```python
# invoice-ocr/tests/test_table_schema.py
import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from table_schema import create_table, inspect_table, map_headers


class TableSchemaTests(unittest.TestCase):
    def test_maps_standard_and_alias_headers(self):
        mapping = map_headers(["单号", "供应商", "货号", "单价", "数量"])

        self.assertEqual(mapping["note_number"], 1)
        self.assertEqual(mapping["supplier_name"], 2)
        self.assertEqual(mapping["fabric_code"], 3)
        self.assertEqual(mapping["unit_price"], 4)
        self.assertEqual(mapping["quantity"], 5)

    def test_create_table_preserves_header_order_and_reports_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "采购表.xlsx"

            result = create_table(path, ["供应商", "货号", "单价", "自定义备注"])

            wb = load_workbook(path)
            ws = wb.active
            self.assertEqual(
                [ws.cell(1, col).value for col in range(1, 5)],
                ["供应商", "货号", "单价", "自定义备注"],
            )
            self.assertEqual(result.field_columns["supplier_name"], 1)
            self.assertEqual(result.field_columns["fabric_code"], 2)
            self.assertEqual(result.unmapped_headers, ["自定义备注"])

    def test_rejects_table_when_no_header_can_be_mapped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "无效表.xlsx"

            with self.assertRaisesRegex(ValueError, "至少需要一个可识别的表头"):
                create_table(path, ["甲", "乙"])

    def test_inspect_table_reads_active_sheet_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "采购表.xlsx"
            create_table(path, ["单号", "日期", "厂家", "数量"])

            result = inspect_table(path)

            self.assertEqual(result.sheet_name, "Sheet")
            self.assertEqual(result.field_columns["supplier_name"], 3)
```

- [ ] **Step 2：运行测试并确认 RED**

Run:

```bash
python3 -m unittest invoice-ocr.tests.test_table_schema -v
```

Expected: `ModuleNotFoundError: No module named 'table_schema'`。

- [ ] **Step 3：实现最小表头模型**

```python
# invoice-ocr/scripts/table_schema.py
from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook, load_workbook


HEADER_ALIASES = {
    "单号": "note_number",
    "日期": "date",
    "款号": "style_number",
    "描述": "description",
    "件数": "piece_count",
    "面料/辅料": "material_type",
    "面料厂家": "supplier_name",
    "厂家": "supplier_name",
    "供应商": "supplier_name",
    "面料款号": "fabric_code",
    "货号": "fabric_code",
    "色号/颜色": "color_code",
    "色号": "color_code",
    "颜色": "color_code",
    "单价": "unit_price",
    "数量": "quantity",
    "单位": "unit",
    "总金额": "total_amount",
    "金额": "total_amount",
    "备注": "remark",
}


@dataclass(frozen=True)
class TableSchema:
    path: Path
    sheet_name: str
    headers: list[str]
    field_columns: dict[str, int]
    unmapped_headers: list[str]


def map_headers(headers: list[str]) -> dict[str, int]:
    return {
        field: index
        for index, header in enumerate(headers, 1)
        if (field := HEADER_ALIASES.get(str(header or "").strip()))
    }


def _build_schema(path: Path, sheet_name: str, headers: list[str]) -> TableSchema:
    field_columns = map_headers(headers)
    if not field_columns:
        raise ValueError("至少需要一个可识别的表头")
    unmapped = [
        header for header in headers
        if str(header or "").strip() not in HEADER_ALIASES
    ]
    return TableSchema(path, sheet_name, headers, field_columns, unmapped)


def create_table(path: str | Path, headers: list[str]) -> TableSchema:
    cleaned = [str(header).strip() for header in headers if str(header).strip()]
    schema = _build_schema(Path(path), "Sheet", cleaned)
    schema.path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.append(cleaned)
    wb.save(schema.path)
    return schema


def inspect_table(path: str | Path) -> TableSchema:
    table_path = Path(path)
    if not table_path.is_file():
        raise ValueError("目标表不存在")
    wb = load_workbook(table_path)
    ws = wb.active
    headers = [
        str(ws.cell(1, col).value or "").strip()
        for col in range(1, ws.max_column + 1)
    ]
    return _build_schema(table_path, ws.title, headers)
```

- [ ] **Step 4：运行测试并确认 GREEN**

Run:

```bash
python3 -m unittest invoice-ocr.tests.test_table_schema -v
```

Expected: 4 tests `OK`。

- [ ] **Step 5：提交**

```bash
git add invoice-ocr/scripts/table_schema.py invoice-ocr/tests/__init__.py invoice-ocr/tests/test_table_schema.py
git commit -m "feat(invoice-ocr): add target table schema"
```

## Task 2：实现目标表初始化和最后使用表复用

**Files:**

- Modify: `invoice-ocr/scripts/templates.py`
- Modify: `invoice-ocr/scripts/process.py`
- Create: `invoice-ocr/tests/test_process.py`

- [ ] **Step 1：写目标表状态失败测试**

```python
# invoice-ocr/tests/test_process.py
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import process
import templates
from table_schema import create_table


class TargetTableTests(unittest.TestCase):
    def test_resolve_target_table_requests_setup_when_missing(self):
        decision = process.resolve_target_table(None, None)

        self.assertEqual(decision["status"], "waiting_for_table")
        self.assertIsNone(decision["path"])

    def test_resolve_target_table_reuses_saved_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "采购表.xlsx"
            create_table(path, ["单号", "厂家", "货号", "单价", "数量"])

            decision = process.resolve_target_table(None, str(path))

            self.assertEqual(decision["status"], "ready")
            self.assertEqual(decision["path"], str(path))
            self.assertTrue(decision["reused"])

    def test_invalid_saved_table_is_not_reused(self):
        decision = process.resolve_target_table(None, "/not/found.xlsx")

        self.assertEqual(decision["status"], "waiting_for_table")

    def test_set_default_table_rejects_invalid_path(self):
        with self.assertRaisesRegex(ValueError, "目标表不存在"):
            templates.set_default_table("/not/found.xlsx")
```

- [ ] **Step 2：运行测试并确认 RED**

Run:

```bash
python3 -m unittest invoice-ocr.tests.test_process.TargetTableTests -v
```

Expected: `AttributeError` for `resolve_target_table`，并且无效路径测试失败。

- [ ] **Step 3：实现目标表解析和安全偏好保存**

在 `templates.py` 中让偏好只保存通过校验的路径：

```python
def set_default_table(table_path: str):
    from table_schema import inspect_table

    schema = inspect_table(table_path)
    prefs = load_preferences()
    prefs["default_table"] = str(schema.path)
    save_preferences(prefs)
```

在 `process.py` 中增加纯函数：

```python
def resolve_target_table(explicit: str | None, saved: str | None) -> dict:
    from table_schema import inspect_table

    candidate = explicit or saved
    if not candidate:
        return {"status": "waiting_for_table", "path": None, "reused": False}
    try:
        schema = inspect_table(candidate)
    except ValueError:
        return {"status": "waiting_for_table", "path": None, "reused": False}
    return {
        "status": "ready",
        "path": str(schema.path),
        "reused": explicit is None and saved is not None,
    }
```

增加 CLI 参数：

```python
parser.add_argument("--setup-table", help="Validate or create the target Excel table")
parser.add_argument("--headers", help="Comma-separated headers used with --setup-table")
```

处理规则：

```python
if args.setup_table:
    from table_schema import create_table, inspect_table

    headers = [
        value.strip() for value in (args.headers or "").split(",")
        if value.strip()
    ]
    schema = (
        create_table(args.setup_table, headers)
        if headers else inspect_table(args.setup_table)
    )
    set_default_table(str(schema.path))
    saved_table = str(schema.path)
```

如果同时提供 `input_dir`，继续原批次；如果没有 `input_dir`，输出 `table_ready` 摘要后退出。目标表决策必须发生在 `check_gateway()` 和 OCR 之前。

- [ ] **Step 4：运行目标表测试**

Run:

```bash
python3 -m unittest invoice-ocr.tests.test_process.TargetTableTests -v
```

Expected: 4 tests `OK`。

- [ ] **Step 5：增加 CLI 集成测试**

在 `TargetTableTests` 中增加：

```python
    def test_main_does_not_check_gateway_without_target_table(self):
    with tempfile.TemporaryDirectory() as tmp:
        images = Path(tmp) / "images"
        images.mkdir()
        with (
            patch.object(sys, "argv", ["process.py", str(images), "--agent-mode"]),
            patch.object(process, "load_preferences", return_value={"default_table": None}),
            patch.object(process, "check_gateway") as check_gateway,
            patch("builtins.print") as print_mock,
        ):
            process.main()

        check_gateway.assert_not_called()
        payload = json.loads(print_mock.call_args.args[0])
        self.assertEqual(payload["status"], "need_table")

    def test_setup_table_continues_original_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            images = Path(tmp) / "images"
            images.mkdir()
            table = Path(tmp) / "采购表.xlsx"
            result = {
                "confirmed": [],
                "pending": [],
                "output_path": str(table),
                "confirmed_suppliers": [],
                "exported_pending": False,
                "learned": [],
            }
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "process.py",
                        str(images),
                        "--setup-table",
                        str(table),
                        "--headers",
                        "单号,供应商,货号,单价,数量",
                        "--agent-mode",
                    ],
                ),
                patch.object(process, "load_preferences", return_value={"default_table": None}),
                patch.object(process, "set_default_table"),
                patch.object(process, "process_batch", return_value=result) as process_batch,
            ):
                process.main()

            self.assertEqual(process_batch.call_args.args[1], str(table))
```

- [ ] **Step 6：运行集成测试并确认先 RED 后 GREEN**

Run:

```bash
python3 -m unittest invoice-ocr.tests.test_process.TargetTableTests.test_main_does_not_check_gateway_without_target_table -v
```

Expected before main-flow change: FAIL because gateway/config work occurs too early or status differs。完成最小调整后再次运行整个 `TargetTableTests`，Expected: 6 tests `OK`。

- [ ] **Step 7：提交**

```bash
git add invoice-ocr/scripts/process.py invoice-ocr/scripts/templates.py invoice-ocr/tests/test_process.py
git commit -m "feat(invoice-ocr): initialize and reuse target table"
```

## Task 3：严格校验新供应商并自动建立模板

**Files:**

- Modify: `invoice-ocr/scripts/templates.py`
- Modify: `invoice-ocr/scripts/process.py`
- Create: `invoice-ocr/tests/test_templates.py`

- [ ] **Step 1：写新供应商自动学习失败测试**

```python
# invoice-ocr/tests/test_templates.py
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from templates import (
    apply_corrections,
    auto_confirm_and_learn,
    build_template_from_extraction,
    load_index,
    load_template,
    save_template,
    validate_extraction,
)


def valid_result(filename="001.jpg"):
    return {
        "filename": filename,
        "status": "success",
        "review_status": "pending",
        "data": {
            "document_type": "delivery",
            "document_title": "销售码单",
            "delivery_note": {
                "supplier_name": "旺泰纺织",
                "note_number": "WT-001",
                "date": "2026-06-25",
            },
            "items": [{
                "row_number": 1,
                "material_type": "面料",
                "material_name": "全棉布",
                "fabric_code": "A100",
                "color_code": "黑色",
                "unit_price": 10.0,
                "quantity": 20.0,
                "unit": "米",
                "total_amount": 200.0,
            }],
            "total_amount": 200.0,
            "needs_review": [],
        },
    }


class TemplateAutomationTests(unittest.TestCase):
    def test_new_supplier_with_valid_data_is_confirmed_and_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            templates_dir = Path(tmp)
            result = valid_result()

            learned = auto_confirm_and_learn(result, templates_dir)

            self.assertEqual(learned["review_status"], "confirmed")
            self.assertTrue(learned["auto_confirmed"])
            self.assertTrue(learned["auto_template_created"])
            self.assertEqual(len(load_index(templates_dir)["templates"]), 1)

    def test_new_supplier_with_missing_price_stays_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            templates_dir = Path(tmp)
            result = valid_result()
            result["data"]["items"][0]["unit_price"] = None

            learned = auto_confirm_and_learn(result, templates_dir)

            self.assertEqual(learned["review_status"], "pending")
            self.assertEqual(load_index(templates_dir)["templates"], {})

    def test_missing_supplier_and_empty_items_are_validation_issues(self):
        data = valid_result()["data"]
        data["delivery_note"]["supplier_name"] = ""
        data["items"] = []

        issues = validate_extraction(data, None)

        self.assertIn("供应商缺失", issues)
        self.assertIn("没有识别到明细行", issues)

    def test_line_amount_mismatch_is_validation_issue(self):
        data = valid_result()["data"]
        data["items"][0]["total_amount"] = 199.0

        issues = validate_extraction(data, None)

        self.assertTrue(any("第1行 金额不一致" in issue for issue in issues))

    def test_existing_model_review_issue_is_preserved(self):
        data = valid_result()["data"]
        data["needs_review"] = ["fabric_code_unclear"]

        issues = validate_extraction(data, None)

        self.assertIn("fabric_code_unclear", issues)

    def test_user_correction_is_persisted_in_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            templates_dir = Path(tmp)
            result = valid_result()
            template_id = save_template(
                build_template_from_extraction(result),
                templates_dir,
            )

            apply_corrections(
                "旺泰纺织",
                json.dumps([{
                    "field": "color_code",
                    "actual_meaning": "none",
                    "source_text": "这家没有色号",
                }], ensure_ascii=False),
                templates_dir,
                "销售码单",
            )

            template = load_template(template_id, templates_dir)
            self.assertIn(
                {"field": "color_code", "actual_meaning": "none"},
                template["field_mapping_corrections"],
            )
```

- [ ] **Step 2：运行测试并确认 RED**

Run:

```bash
python3 -m unittest invoice-ocr.tests.test_templates -v
```

Expected: import failure for `auto_confirm_and_learn`，其他校验断言失败。

- [ ] **Step 3：最小扩展校验**

修改 `validate_extraction()`：

```python
def validate_extraction(data: dict, template: dict | None) -> list[str]:
    issues = list(data.get("needs_review") or [])
    delivery = data.get("delivery_note") or {}
    items = data.get("items") or []

    if not delivery.get("supplier_name"):
        issues.append("供应商缺失")
    if not items:
        issues.append("没有识别到明细行")

    total = data.get("total_amount")
    item_sum = 0.0
    for idx, item in enumerate(items):
        unit_price = item.get("unit_price")
        quantity = item.get("quantity")
        item_total = item.get("total_amount")
        if unit_price is not None and quantity is not None:
            calculated = float(unit_price) * float(quantity)
            if item_total is None:
                item["total_amount"] = calculated
                item_total = calculated
            elif abs(calculated - float(item_total)) > 0.01:
                issues.append(
                    f"第{idx + 1}行 金额不一致: {unit_price}×{quantity}≠{item_total}"
                )
        if item_total is not None:
            item_sum += float(item_total)

    if total is None and items and all(
        item.get("total_amount") is not None for item in items
    ):
        data["total_amount"] = item_sum
        total = item_sum
    if total is not None and items and abs(item_sum - float(total)) > 0.01:
        issues.append(f"total_mismatch: 明细合计{item_sum}≠单据总额{total}")

    doc_type = (
        template.get("document_type")
        if template and template.get("document_type")
        else data.get("document_type", "delivery")
    )
    required = (
        REQUIRED_FIELDS_PROCESSING
        if doc_type == "processing"
        else REQUIRED_FIELDS_DELIVERY
    )
    for idx, item in enumerate(items):
        for field in required:
            if item.get(field) is None or item.get(field) == "":
                issues.append(
                    f"第{idx + 1}行 {FIELD_LABELS.get(field, field)} 缺失"
                )

    return list(dict.fromkeys(issues))
```

- [ ] **Step 4：实现无异常新供应商自动学习**

```python
def auto_confirm_and_learn(result: dict, templates_dir: Path) -> dict:
    if result.get("status") != "success" or not result.get("data"):
        return result
    if result.get("review_status") == "confirmed":
        return result

    data = result["data"]
    supplier = data.get("delivery_note", {}).get("supplier_name", "")
    title = data.get("document_title", "")
    if match_template(supplier, templates_dir, title):
        return result
    if data.get("needs_review"):
        return result

    save_template(build_template_from_extraction(result), templates_dir)
    result["review_status"] = "confirmed"
    result["auto_confirmed"] = True
    result["auto_template_created"] = True
    return result
```

在 `process_batch()` 中先收集全部原始结果并按文件名排序，再串行执行：

```python
results.sort(key=lambda result: result["filename"])
processed = []
for result in results:
    if result.get("status") == "success":
        result = post_process_extraction(result, templates_dir)
        result = auto_confirm_and_learn(result, templates_dir)
    processed.append(result)
results = processed
```

从 worker 完成回调中移除 `post_process_extraction()`，避免模板索引被并行修改，并保证同批次同供应商按稳定顺序复用模板。

- [ ] **Step 5：运行模板测试并确认 GREEN**

Run:

```bash
python3 -m unittest invoice-ocr.tests.test_templates -v
```

Expected: 6 tests `OK`。

- [ ] **Step 6：增加同批次模板复用测试**

在 `process.py` 中提取并固定使用以下顺序后处理函数：

```python
def post_process_results(results: list[dict], templates_dir: Path) -> list[dict]:
    processed = []
    for result in sorted(results, key=lambda item: item["filename"]):
        if result.get("status") == "success":
            result = post_process_extraction(result, templates_dir)
            result = auto_confirm_and_learn(result, templates_dir)
        processed.append(result)
    return processed
```

在 `test_process.py` 增加：

```python
def test_same_supplier_reuses_template_within_batch(self):
    from test_templates import valid_result

    with tempfile.TemporaryDirectory() as tmp:
        templates_dir = Path(tmp)
        second = valid_result("002.jpg")
        second["data"]["delivery_note"]["note_number"] = "WT-002"

        processed = process.post_process_results(
            [second, valid_result("001.jpg")],
            templates_dir,
        )

        self.assertTrue(processed[0]["auto_template_created"])
        self.assertEqual(processed[1]["template_matched"], "旺泰纺织")
        self.assertEqual(
            [item["review_status"] for item in processed],
            ["confirmed", "confirmed"],
        )
```

`process_batch()` 必须调用 `post_process_results()`，测试不模拟网络。

- [ ] **Step 7：运行测试并提交**

Run:

```bash
python3 -m unittest invoice-ocr.tests.test_templates invoice-ocr.tests.test_process -v
```

Expected: all tests `OK`。

```bash
git add invoice-ocr/scripts/templates.py invoice-ocr/scripts/process.py invoice-ocr/tests/test_templates.py invoice-ocr/tests/test_process.py
git commit -m "feat(invoice-ocr): auto learn valid supplier templates"
```

## Task 4：按用户表头写入并保留价格标红

**Files:**

- Modify: `invoice-ocr/scripts/table_schema.py`
- Modify: `invoice-ocr/scripts/export.py`
- Create: `invoice-ocr/tests/test_export.py`

- [ ] **Step 1：写自定义表头写入失败测试**

```python
# invoice-ocr/tests/test_export.py
import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from export import PRICE_ALERT_FILL, export_xlsx_append
from table_schema import create_table


def batch(note_number="DN-001", price=10.0):
    return {
        "batch_id": "batch",
        "results": [{
            "filename": f"{note_number}.jpg",
            "status": "success",
            "review_status": "confirmed",
            "data": {
                "delivery_note": {
                    "supplier_name": "旺泰纺织",
                    "note_number": note_number,
                    "date": "2026-06-25",
                },
                "items": [{
                    "material_type": "面料",
                    "fabric_code": "A100",
                    "color_code": "黑色",
                    "unit_price": price,
                    "quantity": 20,
                    "unit": "米",
                    "total_amount": price * 20,
                }],
                "total_amount": price * 20,
                "needs_review": [],
            },
        }],
    }


class ExportAutomationTests(unittest.TestCase):
    def test_writes_into_custom_header_columns_without_creating_standard_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "采购表.xlsx"
            create_table(path, ["供应商", "货号", "数量", "单价", "备注"])

            stats = export_xlsx_append(batch(), str(path))

            wb = load_workbook(path)
            ws = wb.active
            self.assertEqual(wb.sheetnames, ["Sheet"])
            self.assertEqual(ws.cell(2, 1).value, "旺泰纺织")
            self.assertEqual(ws.cell(2, 2).value, "A100")
            self.assertEqual(ws.cell(2, 3).value, 20)
            self.assertEqual(ws.cell(2, 4).value, 10.0)
            self.assertEqual(stats.written_rows, 1)

    def test_price_change_stays_red_with_alias_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "采购表.xlsx"
            create_table(path, ["单号", "供应商", "货号", "单价", "数量"])
            export_xlsx_append(batch("DN-001", 10.0), str(path))

            stats = export_xlsx_append(batch("DN-002", 11.0), str(path))

            ws = load_workbook(path).active
            self.assertEqual(stats.price_alerts, 1)
            self.assertEqual(
                ws.cell(3, 4).fill.start_color.rgb,
                PRICE_ALERT_FILL.start_color.rgb,
            )
```

- [ ] **Step 2：运行测试并确认 RED**

Run:

```bash
python3 -m unittest invoice-ocr.tests.test_export.ExportAutomationTests.test_writes_into_custom_header_columns_without_creating_standard_sheet invoice-ocr.tests.test_export.ExportAutomationTests.test_price_change_stays_red_with_alias_headers -v
```

Expected: 当前实现创建“票据录入”工作表，返回值也没有 `written_rows` 属性。

- [ ] **Step 3：增加结构化导出结果**

在 `export.py` 增加：

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class ExportStats:
    written_rows: int
    price_alerts: int
    skipped_duplicates: int
    output_path: str
```

所有 `export_xlsx_append()` 调用方改为读取字段，不再解包二元 tuple。

- [ ] **Step 4：实现表头驱动的值映射**

在 `table_schema.py` 增加：

```python
def row_values(group: dict, item: dict) -> dict[str, object]:
    unit_price = item.get("unit_price")
    quantity = item.get("quantity")
    total = item.get("total_amount")
    if unit_price is not None and quantity is not None:
        total = float(unit_price) * float(quantity)
    return {
        "note_number": group.get("note_number") or "",
        "date": group.get("date") or "",
        "style_number": item.get("style_number") or "",
        "description": "",
        "piece_count": "",
        "material_type": item.get("material_type") or "",
        "supplier_name": item.get("supplier") or group.get("supplier_name") or "",
        "fabric_code": item.get("fabric_code") or "",
        "color_code": item.get("color_code") or "",
        "unit_price": unit_price,
        "quantity": quantity,
        "unit": item.get("unit") or "",
        "total_amount": total,
        "remark": item.get("remark") or "",
    }
```

在 `export.py` 中：

1. 使用 `inspect_table(output_path)` 选择活动工作表和字段列。
2. 对每一行调用 `row_values()`。
3. 只写映射字段；未映射自定义列保持空白。
4. `unit_price` 对应列在价格变化时使用 `PRICE_ALERT_FILL`。
5. `_build_price_index()` 使用 schema 中的 `supplier_name`、`fabric_code`、`unit_price` 列，不再写死 G/H/J。
6. `_build_note_index()` 使用 schema 中的 `supplier_name`、`note_number`、`fabric_code` 列，不再写死 A/G/H。
7. 标准 A-P 表继续支持公式列和用户列保护；自定义表使用值写入，不自动增加新工作表。

- [ ] **Step 5：运行自定义写入和价格测试**

Run:

```bash
python3 -m unittest invoice-ocr.tests.test_export -v
```

Expected: 当前已有测试全部 `OK`。

- [ ] **Step 6：提交**

```bash
git add invoice-ocr/scripts/table_schema.py invoice-ocr/scripts/export.py invoice-ocr/tests/test_export.py
git commit -m "feat(invoice-ocr): export using target table headers"
```

## Task 5：重复单号改为跳过并补齐批量统计

**Files:**

- Modify: `invoice-ocr/scripts/export.py`
- Modify: `invoice-ocr/scripts/process.py`
- Modify: `invoice-ocr/tests/test_export.py`
- Modify: `invoice-ocr/tests/test_process.py`

- [ ] **Step 1：写重复跳过失败测试**

在 `test_export.py` 增加：

```python
def test_duplicate_note_is_skipped_without_updating_existing_row(self):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "采购表.xlsx"
        create_table(path, ["单号", "供应商", "货号", "单价", "数量"])
        export_xlsx_append(batch("DN-001", 10.0), str(path))

        stats = export_xlsx_append(batch("DN-001", 99.0), str(path))

        ws = load_workbook(path).active
        self.assertEqual(ws.max_row, 2)
        self.assertEqual(ws.cell(2, 4).value, 10.0)
        self.assertEqual(stats.written_rows, 0)
        self.assertEqual(stats.skipped_duplicates, 1)
```

- [ ] **Step 2：运行并确认 RED**

Run:

```bash
python3 -m unittest invoice-ocr.tests.test_export.ExportAutomationTests.test_duplicate_note_is_skipped_without_updating_existing_row -v
```

Expected: 当前代码把旧单价更新为 `99.0`。

- [ ] **Step 3：实现重复单号整单跳过**

在 `row_num`、`new_rows`、`price_alerts` 初始化位置增加：

```python
skipped_duplicates = 0
```

然后用下面代码替换当前 `existing_note_rows` 更新分支：

```python
    note_number = group.get("note_number") or ""
    note_key = _note_key(group["supplier_name"], note_number)
    if note_key and note_key in existing_notes:
        skipped_duplicates += 1
        continue
```

删除“重复单号时按明细更新已有行”的分支。没有单号的票据保留现有供应商 + 面料款号行为，不计入重复单号统计。

返回：

```python
return ExportStats(
    written_rows=new_rows,
    price_alerts=price_alerts,
    skipped_duplicates=skipped_duplicates,
    output_path=output_path,
)
```

- [ ] **Step 4：写批量部分成功和顺序测试**

在 `test_process.py` 增加：

```python
class BatchResultTests(unittest.TestCase):
    def test_post_processing_uses_filename_order(self):
        results = [
            {"filename": "002.jpg", "status": "error", "review_status": "pending"},
            {"filename": "001.jpg", "status": "error", "review_status": "pending"},
        ]

        ordered = process.post_process_results(results, Path("/unused"))

        self.assertEqual([item["filename"] for item in ordered], ["001.jpg", "002.jpg"])

    def test_summary_reports_written_and_duplicate_counts(self):
        result = {
            "confirmed": [],
            "pending": [],
            "output_path": "/tmp/采购表.xlsx",
            "export_stats": {
                "written_rows": 3,
                "price_alerts": 0,
                "skipped_duplicates": 1,
                "output_path": "/tmp/采购表.xlsx",
            },
        }

        summary = process.build_assistant_summary(result)

        self.assertEqual(summary["counts"]["written_rows"], 3)
        self.assertEqual(summary["counts"]["skipped_duplicates"], 1)
        self.assertIn("1 张单号已录入过", summary["user_message"])
```

- [ ] **Step 5：运行并确认 RED**

Run:

```bash
python3 -m unittest invoice-ocr.tests.test_process.BatchResultTests -v
```

Expected: 缺少统计字段，摘要文本不包含重复信息。

- [ ] **Step 6：传播导出统计并更新摘要**

`_categorize()` 保存 `ExportStats`：

```python
stats = export_xlsx_append(export_batch, output)
result_view["export_stats"] = {
    "written_rows": stats.written_rows,
    "price_alerts": stats.price_alerts,
    "skipped_duplicates": stats.skipped_duplicates,
    "output_path": stats.output_path,
}
```

`build_assistant_summary()` 把字段加入 `counts`，并在正常或部分成功文本末尾增加：

```python
if skipped_duplicates:
    message += (
        f" 其中 {skipped_duplicates} 张单号已录入过，"
        "本次已跳过，没有重复写入。"
    )
```

- [ ] **Step 7：运行相关测试并提交**

Run:

```bash
python3 -m unittest invoice-ocr.tests.test_export invoice-ocr.tests.test_process -v
```

Expected: all tests `OK`。

```bash
git add invoice-ocr/scripts/export.py invoice-ocr/scripts/process.py invoice-ocr/tests/test_export.py invoice-ocr/tests/test_process.py
git commit -m "feat(invoice-ocr): skip duplicate notes and report batch stats"
```

## Task 6：正常票据自动写入，异常票据集中确认

**Files:**

- Modify: `invoice-ocr/scripts/process.py`
- Modify: `invoice-ocr/tests/test_process.py`

- [ ] **Step 1：写自动模式导出边界失败测试**

在 `test_process.py` 增加：

```python
class AutomationBoundaryTests(unittest.TestCase):
    def test_agent_mode_does_not_export_pending_rows(self):
        batch = {
            "batch_id": "batch",
            "results": [
                {"filename": "ok.jpg", "status": "success", "review_status": "confirmed", "data": {"delivery_note": {}, "items": []}},
                {"filename": "bad.jpg", "status": "success", "review_status": "pending", "data": {"delivery_note": {}, "items": [], "needs_review": ["数量缺失"]}},
            ],
        }
        with patch.object(process, "export_xlsx_append") as export:
            from export import ExportStats
            export.return_value = ExportStats(0, 0, 0, "/tmp/out.xlsx")

            result = process._categorize(
                batch,
                Path("/tmp/templates"),
                "/tmp/out.xlsx",
                export_pending=False,
            )

        exported = export.call_args.args[0]["results"]
        self.assertEqual([item["filename"] for item in exported], ["ok.jpg"])
        self.assertEqual([item["filename"] for item in result["pending"]], ["bad.jpg"])

    def test_review_summary_lists_all_pending_in_one_response(self):
        pending = [
            {
                "filename": "001.jpg",
                "status": "success",
                "review_status": "pending",
                "data": {
                    "delivery_note": {"supplier_name": "甲厂", "note_number": "A1"},
                    "items": [],
                    "needs_review": ["数量缺失"],
                },
            },
            {
                "filename": "002.jpg",
                "status": "success",
                "review_status": "pending",
                "data": {
                    "delivery_note": {"supplier_name": "乙厂", "note_number": "B1"},
                    "items": [],
                    "needs_review": ["单价缺失"],
                },
            },
        ]

        text = process.build_pending_review(pending)

        self.assertIn("甲厂", text)
        self.assertIn("数量缺失", text)
        self.assertIn("乙厂", text)
        self.assertIn("单价缺失", text)
```

- [ ] **Step 2：运行并确认 RED**

Run:

```bash
python3 -m unittest invoice-ocr.tests.test_process.AutomationBoundaryTests -v
```

Expected: `build_pending_review` 不存在，agent-mode 当前隐式设置 `review_in_excel=True`。

- [ ] **Step 3：调整自动模式**

在 `main()` 中移除：

```python
args.review_in_excel = True
```

保留显式 `--review-in-excel` 作为人工复核模式。默认 `_categorize()` 只导出 confirmed 结果。

增加纯格式化函数：

```python
def build_pending_review(pending: list[dict]) -> str:
    sections = []
    for result in pending:
        data = result.get("data") or {}
        delivery = data.get("delivery_note") or {}
        supplier = delivery.get("supplier_name") or "未知供应商"
        note = delivery.get("note_number") or result.get("filename") or "未知票据"
        issues = data.get("needs_review") or [result.get("error") or "识别失败"]
        sections.append(
            f"{supplier}（{note}）：{'；'.join(str(issue) for issue in issues)}"
        )
    return "\n".join(sections)
```

`build_assistant_summary()` 在 `needs_review` 状态返回一个消息，正文包含所有异常及“确认/纠正/重新拍照”的可执行提示。

- [ ] **Step 4：运行测试并提交**

Run:

```bash
python3 -m unittest invoice-ocr.tests.test_process -v
```

Expected: all tests `OK`。

```bash
git add invoice-ocr/scripts/process.py invoice-ocr/tests/test_process.py
git commit -m "feat(invoice-ocr): confirm only exceptional invoices"
```

## Task 7：更新 Skill 工作流并进行 Skill TDD

**Files:**

- Modify: `invoice-ocr/SKILL.md`
- Create: `invoice-ocr/tests/test_skill_contract.py`

- [ ] **Step 1：写 Skill 契约失败测试**

```python
# invoice-ocr/tests/test_skill_contract.py
import unittest
from pathlib import Path


SKILL_PATH = Path(__file__).resolve().parents[1] / "SKILL.md"


class SkillContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SKILL_PATH.read_text(encoding="utf-8")

    def test_requires_one_time_table_setup_then_last_table_reuse(self):
        self.assertIn("没有目标表", self.text)
        self.assertIn("上传 Excel 模板或提供表头字段", self.text)
        self.assertIn("复用最后一次使用的表", self.text)

    def test_new_supplier_is_not_automatically_a_review_case(self):
        self.assertIn("新供应商不等于异常", self.text)
        self.assertIn("自动建立", self.text)

    def test_batch_exceptions_are_reported_together(self):
        self.assertIn("批次结束后统一", self.text)
        self.assertIn("不逐张打断", self.text)

    def test_price_rule_remains_unchanged(self):
        self.assertIn("same supplier + fabric_code", self.text)
        self.assertIn("highlighted red in Excel", self.text)
```

- [ ] **Step 2：运行静态契约并确认 RED**

Run:

```bash
python3 -m unittest invoice-ocr.tests.test_skill_contract -v
```

Expected: 新自动化措辞相关断言失败。

- [ ] **Step 3：运行无新 Skill 约束的压力场景并记录失败**

使用独立子代理，只给下面场景，不给更新后的 `SKILL.md`：

```text
用户一次发送 20 张票据，其中 18 张完整，2 张缺单价；5 家供应商第一次出现；
系统已有最后使用的 Excel。你会先问用户什么，哪些票据会写入，何时创建供应商模板？
```

记录基线是否出现以下任一失败：

- 因为有新供应商而先请求确认。
- 逐张处理或逐张询问。
- 在已有默认表时仍等待用户确认目标表。
- 把异常票据与正常票据一起无条件写入。

- [ ] **Step 4：最小更新 SKILL.md**

修改核心规则和 Workflow，明确：

```markdown
1. 没有目标表：只请求上传 Excel 模板或提供表头字段；完成后自动继续原批次。
2. 已有目标表：告知“继续录入到《表名》”后立即处理，不等待回复。
3. 新供应商不等于异常：字段完整、金额校验通过且无存疑字段时，自动建立该版式习惯并录入。
4. 已有模板：自动套用并录入。
5. 单张和批量统一处理；正常票据先录入，异常在批次结束后统一展示。
6. 用户纠偏继续写回模板。
7. 价格规则保持现状：same supplier + fabric_code with different unit_price → highlighted red in Excel。
```

删除或改写“第一次遇到新样子默认需要确认”“新供应商标记为待确认”等冲突文本。保留用户自然语言纠偏和现有字段业务规则。

- [ ] **Step 5：运行 Skill 契约并确认 GREEN**

Run:

```bash
python3 -m unittest invoice-ocr.tests.test_skill_contract -v
```

Expected: 4 tests `OK`。

- [ ] **Step 6：使用更新后的 Skill 重跑同一压力场景**

Expected:

- 直接复用最后使用表。
- 18 张正常票据自动写入。
- 5 个新供应商中校验通过的自动建模板。
- 2 张缺单价票据在批次末尾一次性请求确认。

- [ ] **Step 7：提交**

```bash
git add invoice-ocr/SKILL.md invoice-ocr/tests/test_skill_contract.py
git commit -m "docs(invoice-ocr): make photo intake fully automatic"
```

## Task 8：全量验证和最终清理

**Files:**

- Modify only if verification finds defects in files already changed.

- [ ] **Step 1：运行完整单元测试**

Run:

```bash
python3 -m unittest discover -s invoice-ocr/tests -v
```

Expected: all tests `OK`，无 traceback、warning 或跳过。

- [ ] **Step 2：运行语法编译检查**

Run:

```bash
python3 -m compileall -q invoice-ocr/scripts invoice-ocr/tests
```

Expected: exit code `0`，无输出。

- [ ] **Step 3：运行目标表初始化 smoke test**

Run:

```bash
tmpdir="$(mktemp -d)"
python3 invoice-ocr/scripts/process.py \
  --setup-table "$tmpdir/采购表.xlsx" \
  --headers "单号,日期,供应商,货号,单价,数量,单位,金额,备注" \
  --agent-mode
```

Expected JSON:

```json
{"status":"table_ready","user_message":"已设置录入表《采购表.xlsx》，以后会默认继续录入到这张表。"}
```

- [ ] **Step 4：检查完整需求覆盖**

逐项确认：

- 无表时不启动 OCR，只请求表模板或表头。
- 有表时复用最后使用表，不等待确认。
- 单张和批量共用同一流水线。
- 新供应商正常票据自动建模板。
- 异常票据不进入默认写表集合。
- 异常批量汇总。
- 重复单号跳过。
- 用户纠偏持久化。
- 价格变化仍只在 Excel 标红。

- [ ] **Step 5：检查代码质量**

Run:

```bash
rg -n "except Exception|\\bAny\\b|#.*(obvious|assign|set value)" invoice-ocr/scripts invoice-ocr/tests
```

Expected: 没有本次新增的大面积 `except Exception`、类型绕过或低价值注释。现有必要异常捕获不在本次无关重构范围内。

- [ ] **Step 6：检查工作区差异**

Run:

```bash
git diff --check
git status --short
```

Expected: `git diff --check` 无输出；状态仅包含本计划范围内尚未提交的文件。

- [ ] **Step 7：最终提交**

如果验证阶段有必要修复：

```bash
git add invoice-ocr
git commit -m "test(invoice-ocr): verify automated invoice workflow"
```

如果没有新修改，不创建空提交。
