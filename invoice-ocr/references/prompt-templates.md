# Extraction Prompt & Supplier Context（Profile 驱动）

> 提取提示词不再是写死的常量。`scripts/extract.py` 的 `build_extract_prompt(profile, use_ocr=False)` 会根据当前 profile **动态拼装**提示词——物料枚举、单位、款号规则、字段 schema、票据类型判断全部来自 profile。本文档说明拼装逻辑与 garment-fabric profile 的产出示例。

## 提示词拼装流程

`build_extract_prompt(profile)` 依次拼入：

1. **票据类型判断开头**（`_doc_type_intro`）：从 `profile.document_types` 取每种类型的 `name` 与 `title_examples`，生成「送货单/加工单」判断依据。
2. **items schema**（`_items_schema_block`）：遍历 `profile.fields.items`，每个字段按 json_key 生成 schema 行。`material_type` 用 `profile.material_types` 拼枚举；`unit` 用 `profile.units`；`style_number` 在 `style_number_rule.enabled=false` 时不注入款号判定。
3. **规则段**（`_rules_block`）：送货单规则、加工单规则（仅当 profile 有 processing 类型）、金额/复核规则。最后，仅当 `style_number_rule.enabled=true` 时追加 `style_rule_text(profile)`。

款号规则文本（仅 garment-fabric 等启用 profile 生成）：

```
【款号 style_number 判定】 — 与货号/编号区分；仅当至少一条满足时可写入 style_number，否则为 null、勿猜：
   - 匹配正则 `^26`（去空格后判断）；或 匹配正则 `#$`（去空格后判断）
   - 即使出现在货号列，也不得将 `E35101`, `35101` 写入 style_number
   - 同单多码时，仅将符合上述条件的写入 style_number；勿用厂内码充当款号
```

generic-factory profile（`style_number_rule.enabled=false`）**完全不生成**这一段——通用工厂不预设款号规则。

## Supplier Context Block（模板感知提取）

当存在已保存的供应商模板时，`templates.py` 的 `build_supplier_context(templates_dir, profile)` 会拼接一段「已知供应商列表」追加到提示词末尾。每个供应商一行，包含：

- 票据类型（加工单类型会提示 `fabric_code` 填 null——仅当 profile 含 fabric_code 字段时）
- 有/无哪些字段（来自 `field_layout.item_row_fields`）
- 手写字段、默认单位
- 版式说明、补充说明（截断）
- 已学规则、字段映射纠偏

例：

```
已知供应商列表及其格式特征：
- 供应商A 《送货单》: 字段: material_type,fabric_code,unit_price,quantity,total_amount; 无独立字段: style_number; 默认单位: 米
- 供应商B 《加工单》: 类型:加工单(fabric_code填null); 字段: material_type,material_name,unit_price,quantity,total_amount

如果识别到上述任一供应商，请严格遵循对应的格式特征进行提取。如果未匹配任何已知供应商，按通用规则提取。
```

每个供应商约 200-300 token。`--no-template` 可禁用。

## Review Prompt

用户对存疑字段纠正后，重新提取时使用（保持相同 schema，更新 confidence/needs_review）。

## Output JSON Schema

batch results JSON 结构（与 profile 无关的通用结构）：

```json
{
  "batch_id": "batch_20260418_143000",
  "created_at": "2026-04-18T14:30:00",
  "total_images": 5,
  "results": [
    {
      "filename": "IMG_001.jpg",
      "status": "success",
      "error": null,
      "data": {
        "document_type": "delivery",
        "document_title": "送货单",
        "delivery_note": {
          "supplier_name": "供应商A",
          "note_number": "DN-2026-001",
          "date": "2026-04-15",
          "customer": null
        },
        "items": [
          {
            "row_number": 1,
            "material_type": "...",
            "material_name": "...",
            "...": "（字段集合由 profile.fields.items 决定）"
          }
        ],
        "total_amount": 1824.0,
        "confidence": "high",
        "needs_review": [],
        "raw_text_notes": "..."
      },
      "review_status": "confirmed"
    }
  ],
  "summary": {"total_invoices": 5, "confirmed": 3, "pending_review": 2, "total_amount": 45230.00}
}
```

`items[]` 的字段集合由当前 profile 的 `fields.items` 决定——generic profile 是 `material_type/material_name/spec/unit_price/quantity/unit/total_amount`；garment profile 还含 `fabric_code/style_number/color_code`。

## Excel 表头映射

完整说明以 **`SKILL.md` 中「Excel 列与字段映射」** 为准。表头由 profile 的 `excel.headers` 决定，列写入由 `excel.column_map` 驱动，公式列由 `excel.formulas` 配置。详见 `profile-schema.md`。
