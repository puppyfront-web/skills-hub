---
name: invoice-ocr
description: "Garment factory delivery note OCR for spreadsheet data entry. Template-based extraction from supplier delivery note photos. Automatically recognizes known suppliers for faster extraction; for new suppliers, presents structured field confirmation and saves as reusable template. Supports printed, handwritten, and mixed formats. Appends to the user-selected Excel table instead of assuming a fixed workbook name. Use when user sends 送货单, 面料, 辅料 photos or asks about invoice/receipt data entry."
metadata:
  {
    "openclaw":
      {
        "emoji": "🧾",
        "requires": { "bins": ["python3"], "pip": ["openpyxl", "Pillow", "paddleocr", "paddlepaddle"] },
      },
  }
---

# Invoice OCR（票据录入助手）

## 新手介绍与使用指引

面向**第一次使用**本技能的用户，Agent **优先**在开场用简短、口语化的中文做下面几件事（不要一上来就进命令或技术词）：说清「能帮你做什么」→「你要准备什么」→「你大概会经历哪几步」→「有问题怎么跟我一句搞定」。老用户可跳过本段，直接按下方 Workflow 执行。

### 这技能是干什么的（给用户听的大白话）

- 你把**面料/辅料厂、洗水/印花厂**开的**送货单、码单、购销单**等照片发来，系统会**认字、抽行、对金额**，并尽量按 **「单号、日期、款号、厂家、数量、单价…」** 排好，**写进你指定的 Excel 表**里，少用手抄、少对一行行敲。
- **第一次**遇到某种「新样子」的单子，可能有几项要你**确认一下**或**说一句话纠正**；认过一次之后，**同厂、同抬头**的单子会**少问、快很多**（习惯记在用户本机，换电脑需重认）。

### 使用前要准备什么

- **照片**：手机拍清楚即可，尽量正着拍、不糊、不裁断金额行；**一批可以多张一起发**。
- **表**：第一次没有任何表时，让用户**上传 Excel 模板或提供表头字段**；已经设置过表时，默认**复用最后一次使用的表**，只需告诉用户“继续录到上次那张表”。
- **心理预期**：**款号、件数、成本细拆** 有的表要**你自己补**或后面再填；识别主要帮你省**厂家、品名/货号、数价金额** 这些重复劳动。

### 第一次使用建议顺序（分步指引）

1. **直接发**「我要录面料单」+ **送货单照片**（可多张）。若没设置过目标表，系统只问一次：请上传 Excel 模板或提供表头字段；若已有表，直接复用最后一次使用的表。
2. 等系统处理 → 会告诉你**录了几张、大概多少钱**；若有拿不准的，会**标黄/列在待确认里**，**一批给你看清**，不一张张打断。
3. 你有三种配合方式，任选方便的：**在表上改**、**发一句人话**（如「品名那列就是面料款号」「这家没有色号」）、说 **「全部确认」**；你说过的习惯会**记在这家厂上**，**下次同类型少问**。
4. 若**上一张改错了、事后才想起来**，补说一句「**上一家 XX 厂其实…**」也尽量说清 — 会**补记**，避免下次重犯（见 Core Rule 6）。

### 你多半不用搞懂的后台词

不要在对话里对用户讲：**JSON、API、模板 ID、路径、安装命令**；这些由 Agent 在需要时代劳。用户只需知道：**发图、表放哪、不对就一句话**。

### 当用户说「不会用 / 教我怎么用」时 Agent 怎么做

- **先**用本节「大白话」**不超过 5 句**做介绍，再**问一句**：「照片准备好了吗？要录到哪张表？」再进入 **Workflow**。
- 若用户**明显着急**，只发 **「三句话版」** 也行：  
  ① 发清楚送货单照片，多张一起也行；② 发目标表或说表名；③ 有不对就说一句，我记在这家厂，下次少问。
- 若是**刚安装完**，Agent 可先运行：

```bash
python3 {{SKILL_PATH}}/scripts/process.py --intro --assistant-summary-json
```

然后把 `ASSISTANT_SUMMARY_JSON` 里的 `user_message` 发给用户。不要把命令、JSON 或路径原样发给用户。

正式启用前，Agent 还应做一次当前模型是否能看图片的自检：

```bash
python3 {{SKILL_PATH}}/scripts/process.py --self-check --assistant-summary-json
```

- 如果返回 `status=ready`，说明当前 OpenClaw 模型通道能看图片，可以交给用户发票据照片。
- 如果返回 `status=vision_unavailable`，不要直接交给票据人员使用；先切换到支持图片的模型，或明确告知只能走本地 OCR 兜底且速度会慢。
- 不要把 `status`、命令或 JSON 原样发给用户；只把 `user_message` 转成自然中文。

---

## Core Rules

1. **全程中文交互** — 所有输出、提问、确认均用中文
2. **用户是工厂工人，不懂技术** — 不展示代码、路径、JSON、命令行。用 "正在处理..." "已完成" "需要你确认一下" 等自然语言
3. **不要重复问已确认的内容** — 模版一旦保存，后续同类票据自动处理
4. **批量展示问题，不要逐张打断** — 所有待确认的票据汇总后一起给用户看
5. **用户说 "确认" 或 "对的" 就够了** — 不要追问 "确定吗？"
6. **用户纠偏必收录** — 使用中对字段、款号/货号、缺列、行内容等的**任何纠正**（含事后补说「上一单其实…」），都要合并进**该供应商模板**的纠偏记录（`--apply "Supplier:correction"`，或等价的 `templates correct` 写入），**不要**只改当次 Excel 不记；否则下一张同厂、同抬头的单子会旧错重犯。
7. **首次安装要自我介绍** — 安装后或用户说「不会用」时，先输出简短中文介绍：能做什么、需要用户准备什么、怎么纠错；不要直接丢命令或技术说明。
8. **越用越聪明** — 用户反复强调或纠正的信息，要记录为学习事件；能抽象成长期规则的，沉淀为供应商习惯，并在下一次识别提示中使用。
9. **目标表只确认一次** — 首次设置目标表后，优先使用已学习映射；没有学习记录时只对不确定表头集中确认一次，之后批量照片自动处理。

## 款号（style_number / Excel 列「款号」）识别规则

与「面料款号 / 货号（fabric_code）」区分：下面只决定 **本厂服装款号** 填不填、填什么。

1. **可写入 `style_number` 的编号**须满足以下**任一**条件（原样或去掉空格后判断）：
   - **以 `26` 开头**的编号（含字母+数字，只要数字主段以 26 开头即可，如 `A26036-2`、`26018#` 中的款号部分）
   - **以 `#` 结尾**的编号（如 `26018#`、`671008-010#`、`177#`）
2. **明确不算款号、不得写入 `style_number`**（即使出现在货号/明细列，也只能进 `remark` 或按场景进 `fabric_code`，**不**进款号列）：
   - `E35101`、`35101` 及仅为此类形态的内部货号/加工行码
3. 若单据上**同时**有符合 1 的款号与符合 2 的货号，**款号列只收符合 1 的**；不要把厂内短码与服装款号混填。
4. 无任何符合 1 的编号时，`style_number` 置为缺失（null），不要猜测。
5. 若**用户**明确纠正某号是否算款号、某列应映射为何字段，**以用户为准**，并**按「用户纠偏必收录」**写入模板；用户未表态时仍按上款 1–4。

## 对用户怎么说（话术要点）

面向不懂 OpenClaw、也不懂「模版」的工厂用户，Agent 优先用下面说法，少讲概念、多讲结果：

1. **介绍「一种版式」** — 用「**哪家供应商 + 单子最上面印的大标题**」（例如「旺泰的那张《销售码单》」），不说「模板 ID」「两个键」「document_title」。用户理解成「这种厂、这种抬头」即可。
2. **第一次见某种样子** — 说「**我帮你把这种单子的习惯记下来，下次少问**」，不说「创建模板」「保存 schema」。
3. **同一批里同厂、抬头也相同** — 合并成**一轮**说明待确认点，不说「第 1 张…第 2 张…」各问一遍。
4. **能进表就先进表** — 说「**已写进表格，有几行标黄需要你改一下或跟我说一句**」，引导在表上改或一句话纠偏；别提 JSON、路径、命令行（与 Core Rules 一致）。
5. **用户问「你记住了哪种」** — 用「**《抬头》· 厂家简称**」这种顺序念给他听，简短、好记。

## 飞书私聊助手体验（第一版）

本技能优先按「飞书私聊里的录单助手」设计。用户可以完全不懂 OpenClaw、不懂文件路径、不懂命令行；Agent 负责把飞书消息、图片和表格转成后台处理步骤。

### 入口意图识别

以下任一情况都应进入本技能，不要先问技术问题：

- 用户发送送货单、码单、购销单、洗水单、印花单等照片
- 用户说「录面料单」「帮我录这批单」「写到表格」「面料核算」
- 用户发送 Excel 并说「以后录到这个表」「追加到这个表」
- 用户只发照片但没有文字说明时，默认理解为「帮我识别并录单」

### 飞书私聊首轮回复

- 收到照片后，先回一句短话：**「收到，我先帮你认这批单。」**
- 如果没有默认表，只问一句：**「这批要录到哪张表？你可以直接上传 Excel 模板或提供表头字段。」**
- 如果用户发了 Excel，默认把它当作目标表；回复：**「收到，以后先录到这张表。」**
- 如果已有默认表，直接处理；只说：**「继续录到上次那张表，我先处理。」** 这就是复用最后一次使用的表，不要重复追问。
- 不要把 `NO_TABLE_SPECIFIED`、`SAVED_TABLE`、路径、命令、JSON 原样发给用户。

### 处理期间回复

- 多张图片时，不逐张打断。只发一次：**「我先批量处理，等下把需要确认的一起发你。」**
- 识别失败不阻塞整批。成功的先写表，失败的最后汇总：**「其中 X 张没认清，请重新拍清楚一点发我。」**
- 新供应商不等于异常。只要供应商、明细、单价、数量、金额等关键数据完整，就自动保存这种单子的习惯；需要对用户说：**「这家我第一次见，我会把这种单子的习惯记下来，下次少问。」**

### 结果回复

Agent 运行 `process.py` 时可加 `--assistant-summary-json`。脚本会输出一行：

```text
ASSISTANT_SUMMARY_JSON:{...}
```

Agent 应优先使用其中的 `user_message` 作为飞书回复，并可把 `suggested_replies` 变成下一句提示。不要把整段 JSON 发给用户。

处理过程中脚本还会输出多行：

```text
ASSISTANT_PROGRESS_JSON:{...}
```

飞书/OpenClaw 侧应把这些进度事件的 `user_message` 作为短提示发给用户，例如「正在识别这张票据」「已处理 1/2 张票据」「正在写入表格」。这样用户不会在 OCR 或模型处理较慢时长时间无反馈。不要把进度 JSON 原文发给用户。

常见结果话术：

- 全部完成：**「已全部录入完成，共 X 张票据，总金额约 ¥XX，已保存到《表名》。」**
- 需要复核：**「识别完成，共 X 张票据，其中 Y 张需要你确认一下。待确认的问题我会一次列出来，你可以直接回复‘确认’‘全部确认’，或者告诉我哪一列不对。」**
- 缺少目标表：**「这批要录到哪张表？请直接把目标表发给我，或告诉我表名。」**
- 全部失败：**「这批照片暂时没有识别成功。请重新拍清楚一点，尽量正着拍、不要裁掉金额行。」**

### 用户自然回复处理

飞书里用户常用短句，Agent 直接理解并执行：

| 用户说 | Agent 做 |
|--------|----------|
| 「确认」「对的」 | 确认当前待复核内容，保存这家单子的习惯 |
| 「全部确认」 | 确认本批所有待复核内容 |
| 「这家没有色号」「无色号」 | 记录该供应商无色号字段 |
| 「品名就是面料款号」 | 记录字段映射，后续同类单据自动修正 |
| 「面料款号是款号」 | 记录 `fabric_code` 应写到款号列 |
| 「第二行数量是 300」 | 修正本批结果；若能抽象成规则，也写入模板 |
| 「这张不要」「删掉这张」 | 本批跳过该票据，不导出 |
| 「以后录到这张表」 | 保存目标表偏好 |
| 「换一张表」 | 让用户发目标表或说明表名/路径，保存新偏好 |

### 飞书回复边界

- 不在飞书里展示命令、路径、JSON、Python 报错栈。
- 不要求用户理解「模板」「字段名」「review_status」等后台概念。
- 不连续追问多个问题；每次只问用户下一步必须回答的一件事。
- 用户已经确认过的供应商和版式，下次自动处理；只在异常或金额不一致时提醒。
- 用户纠错后，一定要回复：**「这条我记下来了，下次这家同类单子会按这个来。」**

### 自我学习与沉淀

本技能会把用户的纠正分成两层保存：

- **学习事件**：保留用户原话和发生时间，例如「这家没有色号」「品名就是面料款号」。这用于追溯用户反复强调了什么。
- **长期规则**：能稳定复用的习惯会沉淀到供应商模板，例如「以后默认没有色号」「以后把品名按面料款号处理」。下次识别同厂、同抬头票据时，会自动带入提示，减少重复确认。

处理原则：

- 字段级纠正（无色号、品名就是面料款号、面料款号是款号）应沉淀为长期规则。
- 行级数值纠正（第二行数量是 300）优先只修正本批结果；除非用户明确说「以后都这样」，否则不要强行变成长期规则。
- 用户纠错后的飞书回复要能让人放心，例如：**「我记住了：以后默认没有色号。」**
- Agent 不要对用户说「learning_events」「learned_rules」「prompt context」等内部词。

## When to Trigger

- User sends photos of delivery notes (送货单), invoices, or receipts from fabric/trimming suppliers
- User mentions 面料、辅料、送货单、面料成本、面料核算 data entry
- User uploads images and asks to extract table/tabular data from supplier documents

## Skill Updates

When updating/reinstalling this skill:
- **Templates** at `~/.openclaw/skill-state/invoice-ocr-templates/` are NOT inside the skill package — preserved
- **Preferences** at `~/.openclaw/skill-state/invoice-ocr-preferences.json` — preserved
- **Saved Excel tables** — preferences remember the user's table path, preserved
- **Ask the user** whether to keep existing templates before clearing them

## First Run Detection

When running `process.py` for the first time after install/update, it auto-detects:
- `EXISTING_TEMPLATES:N` — N supplier templates already saved
- `SAVED_TABLE:/path/to/file.xlsx` — previously used Excel table
- `NO_TABLE_SPECIFIED` — no saved table, must ask user

**Agent behavior:**
0. New install or user asks how to use → run `process.py --intro --assistant-summary-json`, send the intro `user_message`
1. `EXISTING_TEMPLATES` → tell user: "发现已有 N 个供应商模版，已保留"
2. `SAVED_TABLE` → use that path silently, tell user: "继续录入到 [表名]"
3. `NO_TABLE_SPECIFIED` → ask: "你要录入到哪个表格？"

## Workflow（优化版）

### Step 1: User Sends Photos

**1a. Check dependencies** (first time only):
```bash
pip install openpyxl Pillow 2>/dev/null || pip3 install openpyxl Pillow
```

**1b. Ask which table** (first time, or when no saved preference):
- "这批要录到哪张表？请直接上传 Excel 模板或提供表头字段。"
- User provides path → use it, save preference
- User sends an Excel file → use that file as the target table and save preference
- User specifies an existing file → append mode
- User asks to create a new table → ask for the desired table name or business purpose first; do not invent a fixed filename
- **Subsequent uses**: 复用最后一次使用的表，use saved preference silently

**1c. Save and process（1 条命令搞定）:**
```bash
# Save images to working dir
mkdir -p ~/invoice-ocr-work/batch_$(date +%Y%m%d)/
# Copy user's images there, then:
python3 {{SKILL_PATH}}/scripts/process.py ~/invoice-ocr-work/batch_YYYYMMDD/ -o <table_path> --agent-mode
```

`--agent-mode` 会自动：
- 调用 Vision API 识别所有照片
- 匹配已有供应商模板 → 自动确认
- 标记新供应商和存疑字段
- 只输出一行干净 JSON（无杂音 stdout）
- 隐式启用 `--assistant-summary-json`；不会默认把待确认票据写入 Excel，除非显式使用 `--review-in-excel`
- 使用目标表的已学习映射、标准字段精确匹配和受约束 LLM 表头映射写入用户表
- **自动检测 Gateway 配置**（从 `~/.openclaw/openclaw.json` 读取 token）

**交互模式**（终端直接操作）:
```bash
python3 {{SKILL_PATH}}/scripts/process.py ~/photos/ -o ~/表.xlsx --interactive
```

`--interactive` 会在终端逐项展示待确认票据，支持：
- `y` / 回车 → 确认并记住这家供应商
- `n` → 跳过
- `字段=值` → 纠正（如 `色号=无色号`、`面料款号=26232`）
- `e` → 编辑模式，输入字段映射（如 `品名=面料款号`）

适用：在终端直接操作的场景，无需 Agent 中转。

**1d. Handle errors:**
- **Gateway not running** (`Connection refused`): tell user "服务未启动，请稍后再试" and stop. Do NOT debug.
- **Extraction failed** on some images: continue with successful ones, report failures at end: "其中 X 张识别失败，请重新拍照发送"
- **No images found**: tell user "未检测到图片，请直接发送送货单照片"

**1e. Tell user the result:**
- Agent 解析 `--agent-mode` 输出 → 自然语言展示给用户
- 全部自动确认: "已全部录入完成，共 X 张票据，总金额 ¥XX"
- 部分需确认: "共 X 张票据，其中 Y 张需要你看一下" → go to Step 2

### Step 2: Review Problems (Only If Any)

Present ALL pending invoices together as a batch. Example:

```
需要确认以下 Y 张票据：

## 鑫雨特种印花厂 [新供应商]
单号: 0000071  日期: 2026-03-08

| 面料/辅料 | 面料款号 | 色号/颜色 | 单价 | 数量 | 单位 | 金额 |
|----------|---------|----------|------|------|------|------|
| 印花 | 26232 | 缺失 | 1.5 | 301 | 件 | 451.5 |

缺失: 色号/颜色

## 湖州彩达印花 [新供应商]
...
```

**User response handling:**

| User says | Agent does |
|-----------|-----------|
| "确认" | Save template, confirm this supplier |
| "全部确认" | Confirm all pending suppliers at once |
| "品名就是面料款号" | Record field mapping, save template |
| "无色号" / "没有色号" | Mark field as non-existent for this supplier |
| "面料款号是款号" | Remap fabric_code → style_number |
| "第2行数量应该是300" | Manually fix the value in results.json |
| "这张不要了" | Skip this invoice, don't export it |
| "重新识别" | Re-extract this image |
| "这个提取错了" | Ask which field is wrong |

**Apply corrections（1 条命令搞定所有供应商）:**

```bash
# 单一供应商纠正:
python3 {{SKILL_PATH}}/scripts/process.py ~/invoice-ocr-work/batch_YYYYMMDD/ --apply "鑫雨特种印花厂:无色号" --agent-mode

# 多供应商一次性纠正:
python3 {{SKILL_PATH}}/scripts/process.py ~/invoice-ocr-work/batch_YYYYMMDD/ --apply "旺泰:确认 | 宇博:无色号 | 全部确认" --agent-mode

# 全部确认:
python3 {{SKILL_PATH}}/scripts/process.py ~/invoice-ocr-work/batch_YYYYMMDD/ --apply "全部确认" --agent-mode
```

`--apply` 会自动：
- 如果还没处理过照片，先处理再应用纠正
- 如果已有 results.json，直接应用纠正并导出
- 支持 `Supplier:correction | Supplier:correction` 格式
- 输出一行干净 JSON，Agent 直接解析展示给用户

**用户纠偏怎么收录**（与 Core Rule 6 一致）：
- 用户用自然话说的内容（如「品名就是面料款号」「这厂没有色号」「第 1 行款号填错了是 26082」中可解析为**字段级**的，走 `--apply`；**逐行改数**若现有解析器不支持，Agent 在 `results.json` 里改对后再 `--apply`，同时尽量把**可复用的规则**也写进 `field_mapping_corrections`（例如「这厂货号列=面料款号」这类一句话）。
- 向用户可说一句：**「你刚才说的我记在这家厂的习惯里了，下回同类少问」**（不提路径、JSON）。

### Step 3: Deliver Excel

Send the Excel file with a short summary:
```
已录入完成！
  共 X 张票据，总金额 ¥X,XXX.XX
  已保存到: [用户指定的表名]
  款号/描述/件数 列需要你补充填写
```

### New Photos Arriving Later

When user sends more photos after a previous batch:

```bash
# Add new images to existing working dir, then:
python3 {{SKILL_PATH}}/scripts/process.py ~/invoice-ocr-work/batch_YYYYMMDD/ --append --agent-mode
```

The `--append` flag skips already-processed images, only extracts new ones.

## 导入表表头与识别字段映射

本技能默认导出列（`export.py` 中 `MAIN_HEADERS` + `COST_HEADERS`）与 JSON 提取结果对应关系如下。目标文件名由用户决定，可能是采购、入库、成本、加工费或其他业务表；**向用户展示识别结果时，优先按列 A→P 顺序、用表头中文名对照**，便于用户逐列粘贴或核对导入。

### 主表（A–P）：表头 ↔ `data` 路径

| 列 | 表头 | JSON 来源 | 说明 |
|----|------|-----------|------|
| A | 单号 | `delivery_note.note_number` | 整单共用；无单号时可为空 |
| B | 日期 | `delivery_note.date` | `YYYY-MM-DD`；整单共用 |
| C | 款号 | `items[].style_number` | **本厂服装款号**，规则见上文「款号识别规则」；与 H 列区分 |
| D | 描述 | *通常留空，用户补* | 加工单可把 `items[].material_name`（加工项目）**建议**填于此列，便于车间看；脚本默认不写入 D |
| E | 件数 | *用户补* | 用于 N、O 列公式；识别结果不自动填 |
| F | 面料/辅料 | `items[].material_type` | 送货单为面料/辅料类型；加工单多为砂洗、洗水、印花等 |
| G | 面料厂家 | `delivery_note.supplier_name` 或 `items[].supplier` | 优先行内 `supplier`，否则抬头厂家名 |
| H | 面料款号 | `items[].fabric_code` | 供应商面料/辅料货号；**加工单一般为空** |
| I | 色号/颜色 | `items[].color_code` | |
| J | 单价 | `items[].unit_price` | |
| K | 数量 | `items[].quantity` | |
| L | 单位 | `items[].unit` | 米、公斤、件、码等 |
| M | 总金额 | 公式或 `items[].total_amount` | 有单价+数量时导出为 `=J*K`；否则写金额 |
| N | 单件金额 | 公式 `=M/E` | 依赖 E 列件数 |
| O | 用料 | 公式 `=K/E` | 依赖 E 列件数 |
| P | 备注 | `items[].remark` + 系统缀（如手写货号提示等） | `build_remark()` 合并 |

**一行一明细**：`items` 里每一行导出一行 Excel；A、B 多行可重复为同一单号/日期。

### 成本分解（Q–X）

| 列 | 表头 | 说明 |
|----|------|------|
| Q–R… | 面料、辅料、砂洗、加工、裁床、吊牌、包装、合计 | 导出时空白，**用户/财务**填，识别阶段不自动写 |

### Agent 展示话术（便于对照表头）

- 用「**表头名：值**」按 **单号 → 日期 → 款号 → 面料/辅料 → 厂家 → 面料款号 → 色号 → 单价 → 数量 → 单位 → 金额/备注** 顺序说或列表，**不要**只堆 JSON 字段英文名。
- 若用户要「和表格列对齐」：可给**一行一列**的短表，**列名与上表「表头」列完全一致**。

### 通用目标表适配

本技能支持通用目标表适配，但仍面向服装工厂的送货单、码单、采购/入库/成本类表，不声明支持任意行业票据 Schema。

- 已学习映射优先：相同表头签名再次出现时直接复用，不重复调用 LLM。
- 标准字段精确匹配其次：如「单号」「日期」「单价」「数量」等标准表头可自动识别。
- 其余未知表头由 LLM 在白名单字段内推断；高置信度且无冲突时自动保存，低置信度或目标字段冲突时集中让用户确认一次。
- 用户纠正列含义后，写入已学习映射；下次同一张目标表或同样表头顺序直接按学习结果写入。
- 新供应商不等于异常：字段完整、金额校验通过时自动建供应商模板；只有缺关键字段、金额不一致、低置信度表头等情况才进入批次末尾确认。
- 价格提醒规则保持不变：same supplier + fabric_code 出现不同 unit_price 时 highlighted red in Excel；这一步只在写 Excel 时做，不阻塞正常录入。

### Excel 列名速查（与上表同）

| Col | Header    | Source                          |
|-----|-----------|---------------------------------|
| A   | 单号      | note_number                     |
| B   | 日期      | date                            |
| C   | 款号      | style_number                    |
| D   | 描述      | *(user / 建议 material_name 加工单)* |
| E   | 件数      | *(user)*                        |
| F   | 面料/辅料 | material_type                   |
| G   | 面料厂家  | supplier_name / supplier         |
| H   | 面料款号  | fabric_code                     |
| I   | 色号/颜色 | color_code                      |
| J   | 单价      | unit_price                      |
| K   | 数量      | quantity                        |
| L   | 单位      | unit                            |
| M   | 总金额    | =J*K 或 total_amount            |
| N   | 单件金额  | =M/E                            |
| O   | 用料      | =K/E                            |
| P   | 备注      | remark + 元数据                 |
| Q-X | 成本分解  | *(user)*                        |

## Template Management

```bash
python3 {{SKILL_PATH}}/scripts/templates.py list              # 查看所有模版
python3 {{SKILL_PATH}}/scripts/templates.py show "旺泰纺织"   # 查看模版详情
python3 {{SKILL_PATH}}/scripts/templates.py delete "旺泰纺织" # 删除模版
python3 {{SKILL_PATH}}/scripts/templates.py prefs              # 查看偏好设置
```

## Edge Cases

- **款号 vs 面料款号**: 默认按上文「款号（style_number）识别规则」；若某厂「货号」应对应服装款号，由用户确认时纠偏并记入模版。`E35101` / `35101` 不视为款号。
- **用户纠偏**: 凡纠正须入 `field_mapping_corrections`（或等价 `templates correct`），与 Core Rule 6 相同；**事后补说**也要补录一次，避免下批重复问。
- **Handwritten codes**: detected and flagged. Templates remember which suppliers use handwritten fields.
- **Batch processing**: known templates auto-confirmed first; problems collected and shown together at end.
- **Price changes**: same supplier + fabric_code with different unit_price → highlighted red in Excel.
- **Append mode**: `--append` adds new invoices to existing Excel without overwriting.
- **Non-invoice photos**: extraction will fail → reported as "识别失败" → ask user to resend correct photos.
- **Duplicate invoices**: same note_number detected → skipped automatically.
- **Multiple suppliers in one response**: process all suppliers' corrections at once with `--apply "旺泰:确认 | 宇博:无色号" --agent-mode`.
- **User changes table**: if user says "录入到另一个表", ask for the new path, save as new preference.
- **Model does not support images**: automatically falls back to OCR text extraction (RapidOCR). OCR results are always marked for review since OCR may introduce errors.
- **OCR not installed**: if `paddleocr` is not available, extraction fails with a clear message asking to install it.
