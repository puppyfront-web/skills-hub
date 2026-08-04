# Template & Profile Schema

## 供应商模板存储位置

`~/.invoice-ocr/invoice-ocr-templates/`（兼容回退：若旧路径 `~/.openclaw/skill-state/invoice-ocr-templates/` 已存在且含已学模板，则沿用旧路径，避免升级丢失）。

```
invoice-ocr-templates/
├── index.json              # supplier lookup index
├── <supplier_id>--<title>.json   # one file per supplier+title
└── ...
```

## Index File (`index.json`)

```json
{
  "version": 1,
  "templates": {
    "<template_id>": {
      "supplier_name": "供应商A",
      "document_title": "送货单",
      "aliases": ["供应商A", "供应商A《送货单》"],
      "file": "<template_id>.json"
    }
  },
  "updated_at": "2026-04-18T14:30:00"
}
```

## Template File (`<template_id>.json`)

```json
{
  "version": 1,
  "document_type": "delivery",
  "document_title": "送货单",
  "supplier_name": "供应商A",
  "supplier_aliases": ["供应商A"],
  "created_at": "2026-04-18T14:30:00",
  "updated_at": "2026-04-18T14:30:00",
  "source_count": 3,
  "format_description": "Standard printed delivery note, columnar layout.",
  "field_layout": {
    "note_number_pattern": null,
    "items_table_type": "columnar",
    "item_row_fields": {
      "material_type": { "present": true, "observed_rate": "2/2" },
      "fabric_code": { "present": false, "observed_rate": "0/2" }
    }
  },
  "extraction_hints": {
    "handwritten_fields": [],
    "common_material_types": [],
    "default_unit": null,
    "notes": ""
  },
  "field_mapping_corrections": [],
  "learning_events": [],
  "learned_rules": [],
  "sample_extraction": { "filename": "IMG_001.jpg", "note_number": "DN-001", "items_count": 2 }
}
```

### Field Descriptions

| Field | Purpose |
|-------|---------|
| `supplier_name` | Full supplier name from invoice |
| `supplier_aliases` | Searchable aliases (auto-generated; suffix stripping uses `profile.supplier_suffixes`) |
| `source_count` | Number of invoices used to build/refine template |
| `format_description` | Free-text layout description, injected into extraction prompt |
| `field_layout.item_row_fields` | Per-field presence tracking with observed_rate |
| `extraction_hints` | Supplier-specific quirks: handwritten fields, common material types, default unit |
| `field_mapping_corrections` | User corrections stored for future reference（每次口头纠偏若有可复用规则都应追加，不要只改当次表） |
| `learning_events` / `learned_rules` | 自我学习沉淀（事件 + 长期规则） |

### Template ID Generation

由 `supplier_name` + `document_title` 组合，非字母数字字符替换为 `-`。

---

## Profile Schema（`profiles/*.json`）

Profile 定义一个工厂场景的全部规则。详见 `SKILL.md` 的 Profile 机制章节。关键字段：

| 字段 | 说明 |
|------|------|
| `id` / `name` / `description` | 标识 |
| `document_types` | 票据类型：`delivery`（送货单）/ `processing`（加工单）各自的 `name` 与 `title_examples`。决定提示词的类型判断段。 |
| `material_types` | 物料分类枚举，注入 `material_type` schema。留 `["其他"]` 则开放不限制。 |
| `units` | 常用单位枚举，注入 `unit` schema。 |
| `style_number_rule` | `enabled`（是否注入款号判定）、`patterns`（正则数组，去空格后匹配任一即可写入 style_number）、`forbidden`（禁止写入 style_number 的编号）。 |
| `fields.delivery_note` / `fields.items` | `[["json_key","中文标签"], ...]`。决定识别哪些字段、标签、纠偏解析、校验。 |
| `excel.headers` | 主表完整表头（A 列起，有序数组）。 |
| `excel.column_map` | `json_key` → 1-based 列号。决定哪些字段写进哪列。 |
| `excel.user_fill_columns` | 由用户后续手填的列号（新行置空，更新已有行时保留）。 |
| `excel.cost_headers` | 成本分解列表，接在主表之后（无则空）。 |
| `excel.formulas` | 公式列：`[{col, expr, fmt, needs, fallback_field}]`。`expr` 里 `{r}` 替换为行号；`needs` 列出依赖字段（`["__always__"]` 表示无条件写）；依赖全缺失时写 `fallback_field`。 |
| `excel.sheet_name` | 工作表名（默认「票据录入」）。 |
| `supplier_suffixes` | 供应商名后缀（别名匹配用，如 `["有限公司","厂"]`）。 |
| `vocab.review_examples` | 纠偏示例话术（出现在待确认提示与 suggested_replies 里）。 |

完整范例见 `profiles/generic-factory.json`（默认/最简）与 `profiles/garment-fabric.json`（服装面料成本核算，完整款号规则与 24 列表头）。
