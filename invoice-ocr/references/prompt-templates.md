# Prompt Templates for Invoice OCR (Fabric Cost Accounting)

与运行时完全一致的业务规则以仓库内 `SKILL.md` 及 `scripts/extract.py` 中的提示词为准。以下为补充说明。

## 款号（style_number）识别规则（本厂服装款号）

- **可写入 `style_number`** 仅当编号满足**之一**：① 以 `26` 开头；② 以 `#` 结尾（含如 `A26036-2`、`26018#`、`177#` 等符合上述条件者）。
- **不写入 `style_number`**：`E35101`、`35101` 等厂内/加工行货号；可写入 `remark` 或视场景写入 `fabric_code`，**不**进 Excel「款号」列。
- 无符合上条的编号时 `style_number` 为 `null`，勿猜测。

## Extraction Prompt (送货单 → 用户指定表格)

> **注意**：下面这段为历史/备用模板；**线上提取以 `scripts/extract.py` 中 `EXTRACT_PROMPT` / `EXTRACT_TEXT_PROMPT` 为准**（已内嵌款号规则）。

Used for all fabric/supply delivery note images. The model handles both printed and handwritten content.

```
分析这张面料/辅料送货单图片，提取所有结构化数据。

这是服装工厂的票据录入场景。送货单来自面料/辅料供应商。

输出 JSON 格式，严格遵循以下 schema：
{
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
      "material_name": "string or null (品名/面料名称)",
      "supplier": "string or null (厂家名，如果和表头不同)",
      "fabric_code": "string or null (面料款号/货号，注意货名后面可能手写追加了货号)",
      "fabric_code_is_handwritten": true | false,
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
1. 手写内容与打印内容需区分。手写的货号要标记 fabric_code_is_handwritten: true
2. 货名/品名后面紧跟的手写编号必须视为该面料款号(fabric_code)
3. material_type 要尽量归类到上述枚举值中
4. 金额统一为数字，去掉 ¥ 和逗号
5. 无法辨认的字段设为 null 并在 needs_review 中列出
6. 检查明细金额合计是否等于 total_amount，不等则标注 "total_mismatch"
7. 如果有多个明细行，按行号顺序全部提取

只输出 JSON，不要输出其他内容。
```

## Supplier Context Block (Template-Aware Extraction)

When supplier templates exist, this block is appended to the extraction prompt automatically:

```
已知供应商列表及其格式特征：
- 旺泰纺织: 字段: material_type,material_name,color_code,unit_price,quantity,unit,total_amount; 无独立字段: fabric_code; 默认单位: 米
- 宇博布行: 字段: material_type,fabric_code,color_code,unit_price,quantity,unit,total_amount; 手写: fabric_code
- 汇丰水洗、砂洗厂: 字段: material_type,fabric_code,unit_price,quantity,unit,total_amount; 默认单位: 件

如果识别到上述任一供应商，请严格遵循对应的格式特征进行提取。如果未匹配任何已知供应商，按通用规则提取。
```

This is built dynamically by `templates.py build_supplier_context()` from saved templates. Each supplier line includes:
- Present fields (from `field_layout.item_row_fields`)
- Missing fields (fields that don't exist in this supplier's format)
- Handwritten field indicators
- Default unit

The block adds ~200-300 tokens per supplier. Disable with `--no-template` flag.

## Review Prompt (for re-processing flagged fields)

When a user corrects or provides additional context for a flagged field:

```
基于用户反馈重新提取这张送货单的数据。

原始提取结果：
{previous_result}

用户反馈：
{user_feedback}

请根据用户反馈修正提取结果，保持相同的 JSON schema 格式。
更新 confidence 为 "high"，并从 needs_review 中移除已确认的字段。
```

## Output JSON Schema

The batch results JSON structure from extract.py:

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
        "delivery_note": {
          "supplier_name": "宇博布行",
          "note_number": "DN-2026-001",
          "date": "2026-04-15",
          "customer": null
        },
        "items": [
          {
            "row_number": 1,
            "material_type": "面料",
            "material_name": "全棉弹力布",
            "supplier": null,
            "fabric_code": "386#",
            "fabric_code_is_handwritten": true,
            "color_code": "蓝色",
            "unit_price": 12.0,
            "quantity": 152.0,
            "unit": "米",
            "total_amount": 1824.0,
            "remark": null
          }
        ],
        "total_amount": 1824.0,
        "confidence": "high",
        "needs_review": [],
        "raw_text_notes": "送货单为打印+手写混合格式"
      },
      "review_status": "confirmed"
    }
  ],
  "summary": {
    "total_invoices": 5,
    "confirmed": 3,
    "pending_review": 2,
    "total_amount": 45230.00
  }
}
```

## Excel Template Mapping (默认导出列)

完整说明（**表头与 JSON 一一对应、向用户按列展示顺序**、自定义表头如何映射）以 **`SKILL.md` 中「导入表表头与识别字段映射」** 为准。此处为速查表（24 列，主表 A–P + 成本 Q–X）。

| Excel Column | Header    | Source Field                        |
|--------------|-----------|-------------------------------------|
| A            | 单号      | delivery_note.note_number           |
| B            | 日期      | delivery_note.date                  |
| C            | 款号      | items[].style_number                 |
| D            | 描述      | *(user；加工单可对应 material_name)* |
| E            | 件数      | *(user)*                            |
| F            | 面料/辅料 | items[].material_type               |
| G            | 面料厂家  | items[].supplier / delivery_note.supplier_name |
| H            | 面料款号  | items[].fabric_code                 |
| I            | 色号/颜色 | items[].color_code                  |
| J            | 单价      | items[].unit_price                  |
| K            | 数量      | items[].quantity                    |
| L            | 单位      | items[].unit                        |
| M            | 总金额    | =J*K 或 items[].total_amount         |
| N            | 单件金额  | =M/E (formula)                      |
| O            | 用料      | =K/E (formula)                      |
| P            | 备注      | items[].remark + metadata           |
| Q–X          | 面料/辅料/砂洗/加工/裁床/吊牌/包装/合计 | *(user)*  |
