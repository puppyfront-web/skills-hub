---
name: invoice-ocr
description: "工厂票据/送货单录入助手。当用户发送送货单、出仓单、收据、加工单、码单等供应商单据照片，或说「录单/录到表格/帮我识别这批单」时使用——识别成结构化明细、批量待确认、自然语言纠偏、写进指定 Excel，同供应商认过一次后越用越准。通过 Profile 适配行业（内置通用工厂与服装面料厂，可自建），支持任意 OpenAI 兼容网关。"
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

面向**中小工厂**的通用票据/送货单录入助手。核心能力与行业无关：发图 → 识别 → 批量待确认 → 自然语言纠偏 → 写进指定 Excel，**越用越准**（同供应商、同抬头的单子认过一次就少问）。通过 **Profile** 适配不同行业场景。

## 新手介绍与使用指引

面向**第一次使用**本技能的用户，Agent **优先**在开场用简短、口语化的中文做下面几件事（不要一上来就进命令或技术词）：说清「能帮你做什么」→「你要准备什么」→「你大概会经历哪几步」→「有问题怎么跟我一句搞定」。老用户可跳过本段，直接按下方 Workflow 执行。

### 这技能是干什么的（给用户听的大白话）

- 你把**供应商开的送货单、出仓单、收据、加工单**等照片发来，系统会**认字、抽行、对金额**，按你指定的表头排好，**写进你指定的 Excel 表**里，少用手抄、少对一行行敲。
- **第一次**遇到某种「新样子」的单子，可能有几项要你**确认一下**或**说一句话纠正**；认过一次之后，**同厂、同抬头**的单子会**少问、快很多**（习惯记在用户本机，换电脑需重认）。

### 使用前要准备什么

- **照片**：手机拍清楚即可，尽量正着拍、不糊、不裁断金额行；**一批可以多张一起发**。
- **表**：想好要录进**哪张表**。你可以直接发 Excel，也可以说清楚文件名或路径。
- **网关**：需要一个 OpenAI 兼容的模型网关（能看图片的模型最佳）。默认读取环境变量 `OPENAI_BASE_URL` / `OPENAI_API_KEY`；若你装了 openclaw，也会自动探测其配置。

### 第一次使用建议顺序（分步指引）

1. **直接发**「我要录单」+ **票据照片**（可多张）。若没说录到哪，系统会**问一句**：要录到哪张表 → 你发 Excel 或说表名即可。
2. 等系统处理 → 会告诉你**录了几张、大概多少钱**；若有拿不准的，会**标黄/列在待确认里**，**一批给你看清**，不一张张打断。
3. 你有三种配合方式，任选方便的：**在表上改**、**发一句人话**（如「第二行数量是 300」「这家没有规格」）、说 **「全部确认」**；你说过的习惯会**记在这家厂上**，**下次同类型少问**。
4. 若**上一张改错了、事后才想起来**，补说一句「**上一家 XX 厂其实…**」也尽量说清 — 会**补记**，避免下次重犯（见 Core Rule 6）。

### 你多半不用搞懂的后台词

不要在对话里对用户讲：**JSON、API、模板 ID、路径、Profile、安装命令**；这些由 Agent 在需要时代劳。用户只需知道：**发图、表放哪、不对就一句话**。

### 当用户说「不会用 / 教我怎么用」时 Agent 怎么做

- **先**用本节「大白话」**不超过 5 句**做介绍，再**问一句**：「照片准备好了吗？要录到哪张表？」再进入 **Workflow**。
- 若用户**明显着急**，只发 **「三句话版」** 也行：
  ① 发清楚票据照片，多张一起也行；② 发目标表或说表名；③ 有不对就说一句，我记在这家厂，下次少问。
- 若是**刚安装完**，Agent 可先运行：

```bash
python3 {{SKILL_PATH}}/scripts/process.py --intro --assistant-summary-json
```

然后把 `ASSISTANT_SUMMARY_JSON` 里的 `user_message` 发给用户。不要把命令、JSON 或路径原样发给用户。

正式启用前，Agent 还应做一次当前模型是否能看图片的自检：

```bash
python3 {{SKILL_PATH}}/scripts/process.py --self-check --assistant-summary-json
```

- 如果返回 `status=ready`，说明当前模型通道能看图片，可以交给用户发票据照片。
- 如果返回 `status=vision_unavailable`，不要直接交给录入人员使用；先切换到支持图片的模型，或明确告知只能走本地 OCR 兜底（带多预处理重试，速度会慢、手写/模糊图仍可能要复核）。
- 不要把 `status`、命令或 JSON 原样发给用户；只把 `user_message` 转成自然中文。

---

## Profile 机制（适配不同行业）

本技能通过 **Profile** 适配不同工厂场景。Profile 是 `profiles/` 目录下的 JSON 文件，定义该场景的全部规则：物料分类枚举、单位、款号识别规则、字段标签、Excel 表头与公式、供应商后缀。**所有识别提示词、表头、字段标签都从 profile 动态生成**，代码本身不含任何行业硬编码。

### 内置 Profile

| Profile ID | 名称 | 适用场景 |
|---|---|---|
| `generic-factory`（**默认**） | 通用工厂录单 | 物料分类开放、表头精简、无款号规则、无成本分解列。任何行业都能直接用。 |
| `garment-fabric` | 服装面料厂 | 面料/辅料/加工送货单 → 面料成本核算表。含款号识别规则（以 26 开头 / 以 # 结尾）、13 项物料枚举、24 列成本核算表头（含单件金额/用料 + 面料辅料砂洗加工裁床吊牌包装合计）。 |

### 怎么切换 Profile

三种方式，优先级从高到低：

1. **命令行参数**：`--profile <id>`（如 `--profile garment-fabric`）
2. **环境变量**：`INVOICE_OCR_PROFILE=<id>`
3. **用户偏好**：preferences.json 里的 `active_profile` 字段
4. 都没设置 → 默认 `generic-factory`

查看已安装的 profile：

```bash
python3 {{SKILL_PATH}}/scripts/_profile.py list
python3 {{SKILL_PATH}}/scripts/_profile.py show garment-fabric
```

### 怎么自建 Profile（适配你的行业）

复制 `profiles/generic-factory.json` 改名（如 `profiles/hardware.json`），改这几个关键字段：

- `id` / `name` / `description`：标识
- `material_types`：物料分类枚举（如五金：`["紧固件","工具","型材","其他"]`；留 `["其他"]` 则开放不限制）
- `units`：常用单位
- `style_number_rule`：是否启用款号规则（`enabled: false` 即不注入任何款号判定）
- `fields.items`：物料行字段（json_key + 中文标签），决定识别哪些列
- `excel.headers` / `excel.column_map` / `excel.formulas`：Excel 表头与公式
- `supplier_suffixes`：供应商名后缀（用于别名匹配，如 `["有限公司","厂","经营部"]`）
- `vocab.review_examples`：纠偏示例话术（出现在待确认提示里）

完整字段说明见 `references/template-schema.md` 的「Profile Schema」章节。

---

> **命令约定**：本文档中 `{{SKILL_PATH}}` 代表本 skill 的安装目录（即 `invoice-ocr/` 所在路径），由运行时注入；Agent 执行命令时替换为实际路径即可。

## Core Rules

1. **全程中文交互** — 所有输出、提问、确认均用中文
2. **用户是工厂录入人员，不懂技术** — 不展示代码、路径、JSON、命令行。用 "正在处理..." "已完成" "需要你确认一下" 等自然语言
3. **不要重复问已确认的内容** — 模版一旦保存，后续同类票据自动处理
4. **批量展示问题，不要逐张打断** — 所有待确认的票据汇总后一起给用户看
5. **用户说 "确认" 或 "对的" 就够了** — 不要追问 "确定吗？"
6. **用户纠偏必收录** — 使用中对字段、编号、缺列、行内容等的**任何纠正**（含事后补说「上一单其实…」），都要合并进**该供应商模板**的纠偏记录，**不要**只改当次 Excel 不记；否则下一张同厂、同抬头的单子会旧错重犯。
7. **首次安装要自我介绍** — 安装后或用户说「不会用」时，先输出简短中文介绍。
8. **越用越聪明** — 用户反复强调或纠正的信息，要记录为学习事件；能抽象成长期规则的，沉淀为供应商习惯。

## 对用户怎么说（话术要点）

面向不懂技术的工厂用户，Agent 优先用下面说法，少讲概念、多讲结果：

1. **介绍「一种版式」** — 用「**哪家供应商 + 单子最上面印的大标题**」（例如「供应商A 的那张《送货单》」），不说「模板 ID」。用户理解成「这种厂、这种抬头」即可。
2. **第一次见某种样子** — 说「**我帮你把这种单子的习惯记下来，下次少问**」，不说「创建模板」「保存 schema」。
3. **同一批里同厂、抬头也相同** — 合并成**一轮**说明待确认点。
4. **能进表就先进表** — 说「**已写进表格，有几行标黄需要你改一下或跟我说一句**」。
5. **用户问「你记住了哪种」** — 用「**《抬头》· 厂家简称**」这种顺序念给他听。

## 聊天/Agent 助手体验

本技能按「聊天/Agent 里的录单助手」设计。用户可以完全不懂文件路径、命令行；Agent 负责把消息、图片和表格转成后台处理步骤。适用于飞书、微信、任意 IM 或纯 Agent 场景。

### 入口意图识别

以下任一情况都应进入本技能，不要先问技术问题：

- 用户发送送货单、出仓单、收据、加工单、码单等照片
- 用户说「录单」「帮我录这批单」「写到表格」「录到表里」
- 用户发送 Excel 并说「以后录到这个表」「追加到这个表」
- 用户只发照片但没有文字说明时，默认理解为「帮我识别并录单」

### 首轮回复

- 收到照片后，先回一句短话：**「收到，我先帮你认这批单。」**
- 如果没有默认表，只问一句：**「这批要录到哪张表？你可以直接把目标表发给我，或告诉我表名。」**
- 如果用户发了 Excel，默认把它当作目标表；回复：**「收到，以后先录到这张表。」**
- 如果已有默认表，直接处理；只说：**「继续录到上次那张表，我先处理。」**
- 不要把 `NO_TABLE_SPECIFIED`、`SAVED_TABLE`、路径、命令、JSON 原样发给用户。

### 处理期间回复

- 多张图片时，不逐张打断。只发一次：**「我先批量处理，等下把需要确认的一起发你。」**
- 识别失败不阻塞整批。成功的先写表，失败的最后汇总。
- 新供应商不要说「创建模板」，说：**「这家我第一次见，我会把这种单子的习惯记下来，下次少问。」**

### 结果回复

Agent 运行 `process.py` 时可加 `--assistant-summary-json`。脚本会输出一行：

```text
ASSISTANT_SUMMARY_JSON:{...}
```

Agent 应优先使用其中的 `user_message` 作为回复，并可把 `suggested_replies` 变成下一句提示。处理过程中还会输出进度事件 `ASSISTANT_PROGRESS_JSON:{...}`，其 `user_message` 可作为短提示。不要把 JSON 原文发给用户。

常见结果话术：

- 全部完成：**「已全部录入完成，共 X 张票据，总金额约 ¥XX，已保存到《表名》。」**
- 需要复核：**「已先写入表格，共 X 张票据，其中 Y 张需要你看一下。你可以直接回复‘确认’‘全部确认’，或者告诉我哪一列不对。」**
- 缺少目标表：**「这批要录到哪张表？请直接把目标表发给我，或告诉我表名。」**
- 全部失败：**「这批照片暂时没有识别成功。请重新拍清楚一点，尽量正着拍、不要裁掉金额行。」**

### 用户自然回复处理

用户常用短句，Agent 直接理解并执行。纠偏示例由当前 profile 的 `vocab.review_examples` 决定（generic 是「第二行数量是300」之类通用话；garment 额外有「无色号」「品名就是面料款号」等）。

| 用户说 | Agent 做 |
|--------|----------|
| 「确认」「对的」 | 确认当前待复核内容，保存这家单子的习惯 |
| 「全部确认」 | 确认本批所有待复核内容 |
| 「第二行数量是 300」 | 修正本批结果；若能抽象成规则，也写入模板 |
| 「这张不要」「删掉这张」 | 本批跳过该票据，不导出 |
| 「以后录到这张表」 | 保存目标表偏好 |
| 「换一张表」 | 让用户发目标表或说明表名/路径，保存新偏好 |
| 「品名就是货号」「无规格」等 | 按 profile 字段做映射/标记（仅当该字段在 profile 中存在时生效） |

### 自我学习与沉淀

本技能把用户纠正分成两层保存：

- **学习事件**：保留用户原话和发生时间，用于追溯。
- **长期规则**：能稳定复用的习惯沉淀到供应商模板，下次识别同厂、同抬头票据时自动带入提示。

处理原则：字段级纠正应沉淀为长期规则；行级数值纠正优先只修正本批结果，除非用户明确说「以后都这样」。用户纠错后回复要让人放心，例如：**「我记住了：以后默认没有规格。」** Agent 不要对用户说「learning_events」「learned_rules」等内部词。

## When to Trigger

**适用**（满足任一即触发）：
- 用户发送工厂/供应商的送货单、出仓单、收据、加工单、码单等照片
- 用户提到 送货单、出仓单、收据、加工单、码单、录单、录入表格
- 用户上传图片并要求从供应商单据中抽取表格/明细数据

**不适用**（避免误触发，这些场景请用专门的 skill）：
- 个人消费/差旅**报销小票**、餐饮发票 → 不属于工厂供应商往来单据
- 税务**发票/增值税专用发票（fapiao）**结构化抽取 → 用专门的发票 OCR 技能
- 通用"图片转文字"而无录表需求 → 直接用 OCR 工具即可，本技能价值在"识别+录表+越用越准"

## Workflow

### Step 1: User Sends Photos

**1a. Check dependencies** (first time only):
```bash
# 必需：Excel 读写 + 图像处理
pip install openpyxl Pillow 2>/dev/null || pip3 install openpyxl Pillow

# 可选：仅当模型不支持图片、需要本地 OCR 兜底时才装（体积较大）
pip install paddleocr paddlepaddle 2>/dev/null || pip3 install paddleocr paddlepaddle
```
> `openpyxl`/`Pillow` 是必装项；`paddleocr`/`paddlepaddle` 仅 Vision 不可用时走 OCR 兜底才需要，不装则这类图会报"OCR 不可用"。

**1b. Ask which table** (first time, or when no saved preference):
- "这批要录到哪张表？请直接发目标 Excel，或告诉我表名/路径。"
- User provides path → use it, save preference
- User sends an Excel file → use that file as the target table and save preference
- User specifies an existing file → append mode
- User asks to create a new table → ask for the desired table name first; do not invent a fixed filename
- **Subsequent uses**: use saved preference silently

**1c. Save and process（1 条命令搞定）:**
```bash
mkdir -p ~/invoice-ocr-work/batch_$(date +%Y%m%d)/
# Copy user's images there, then:
python3 {{SKILL_PATH}}/scripts/process.py ~/invoice-ocr-work/batch_YYYYMMDD/ -o <table_path> --agent-mode
```

`--agent-mode` 会自动：调用 Vision API 识别所有照片、匹配已有供应商模板 → 自动确认、标记新供应商和存疑字段、只输出一行干净 JSON、隐式启用 `--assistant-summary-json` 和 `--review-in-excel`、**自动检测网关配置**（优先 `OPENAI_BASE_URL`/`OPENAI_API_KEY`，回退 openclaw 配置）。

**指定 profile**：加 `--profile <id>`，例如 `--profile garment-fabric`。

**调节 OCR 兜底**（仅当模型不支持图片、走本地 OCR 时生效）：`--ocr-retries N`（低置信图额外多预处理重试次数，默认 3，设 0 禁用）、`--ocr-min-confidence F`（触发重试的平均置信阈值，默认 0.80）。详见下方「OCR 兜底机制」。

**交互模式**（终端直接操作）:
```bash
python3 {{SKILL_PATH}}/scripts/process.py ~/photos/ -o ~/表.xlsx --interactive
```

`--interactive` 会在终端逐项展示待确认票据，支持 `y`/回车确认、`n` 跳过、`字段=值` 纠正、`e` 编辑字段映射。

**1d. Handle errors:**
- **Gateway not running** (`Connection refused`): tell user "服务未启动，请稍后再试" and stop. Do NOT debug.
- **Extraction failed** on some images: continue with successful ones, report failures at end.
- **No images found**: tell user "未检测到图片，请直接发送票据照片".

### Step 2: Review Problems (Only If Any)

Present ALL pending invoices together as a batch. Example (列字母与实际导出的 Excel 列一致)：

```
需要确认以下 Y 张票据：

## 供应商A [新供应商]
单号: DN-2026-001  日期: 2026-07-30

| F 物料分类 | G 品名 | H 供应商 | I 单价 | J 数量 | K 单位 | L 金额 |
|---|---|---|---|---|---|---|
| ... | ... | ... | ... | ... | ... | ... |

缺失: 规格

逐项确认（请逐条回复）：
1. ...
2. 金额核对: 明细合计 ¥X = 单据总额 ¥X ✓
回复示例：'1-确认 2-确认' 或 '全部确认'
```

**Apply corrections（1 条命令搞定所有供应商）:**
```bash
# 单一供应商纠正:
python3 {{SKILL_PATH}}/scripts/process.py ~/invoice-ocr-work/batch_YYYYMMDD/ --apply "供应商A:确认" --agent-mode

# 多供应商一次性纠正:
python3 {{SKILL_PATH}}/scripts/process.py ~/invoice-ocr-work/batch_YYYYMMDD/ --apply "供应商A:确认 | 供应商B:无规格 | 全部确认" --agent-mode

# 全部确认:
python3 {{SKILL_PATH}}/scripts/process.py ~/invoice-ocr-work/batch_YYYYMMDD/ --apply "全部确认" --agent-mode
```

`--apply` 支持 `Supplier:correction | Supplier:correction` 格式，输出一行干净 JSON。

### Step 3: Deliver Excel

Send the Excel file with a short summary:
```
已录入完成！
  共 X 张票据，总金额 ¥X,XXX.XX
  已保存到: [用户指定的表名]
```

### New Photos Arriving Later

```bash
python3 {{SKILL_PATH}}/scripts/process.py ~/invoice-ocr-work/batch_YYYYMMDD/ --append --agent-mode
```

`--append` 跳过已处理图片，只提取新图。

## Excel 列与字段映射

Excel 表头由当前 profile 的 `excel.headers` + `excel.column_map` 决定，**不同 profile 表头不同**。脚本按 profile 的 `column_map`（json_key → 列号）写入，公式列由 `excel.formulas` 配置。

- **generic-factory**（11 列）：单号/日期/物料分类/品名/供应商/规格/单价/数量/单位/金额/备注。金额列为公式 `=单价×数量`。
- **garment-fabric**（24 列）：单号/日期/款号/描述/件数/面料/辅料/面料厂家/面料款号/色号/单价/数量/单位/总金额/单件金额/用料/备注 + 面料辅料砂洗加工裁床吊牌包装合计。

若用户**已有表**的表头与 profile 不一致：脚本会在该文件内新建一个 profile 命名的 sheet 并写入 profile 表头，而非强行套到用户表上。要让表头匹配你的业务，请自建 profile 改 `excel.headers`。

## Template Management

```bash
python3 {{SKILL_PATH}}/scripts/templates.py list              # 查看所有模版
python3 {{SKILL_PATH}}/scripts/templates.py show "供应商A"    # 查看模版详情
python3 {{SKILL_PATH}}/scripts/templates.py delete "供应商A"  # 删除模版
python3 {{SKILL_PATH}}/scripts/templates.py prefs             # 查看偏好设置
```

## 网关与状态目录

- **状态目录**：`~/.invoice-ocr/`（模板在 `invoice-ocr-templates/` 子目录，偏好文件 `invoice-ocr-preferences.json`）。**兼容回退**：若检测到旧路径 `~/.openclaw/skill-state/` 已存在且含已学模板，会沿用旧路径，避免升级丢失已学习惯。
- **网关配置**优先级：`--token`/`OPENAI_API_KEY`（标准）> `OPENAI_GATEWAY_TOKEN` > 旧 `OPENCLAW_GATEWAY_TOKEN` > openclaw 配置文件探测。`OPENAI_BASE_URL` 若设置会自动解析 host/port。

## Edge Cases

- **用户纠偏**：凡纠正须入 `field_mapping_corrections`，与 Core Rule 6 相同；**事后补说**也要补录一次。
- **手写编号**：自动检测并标记 `fabric_code_is_handwritten`；模板会记住哪些供应商用了手写字段。
- **批量处理**：已有模板的供应商先自动确认；问题汇总到最后一起给用户看，不逐张打断。
- **价格变动**：同供应商 + 同货号但单价不同 → Excel 中标红提示。
- **追加模式**：`--append` 把新票据追加到已有 Excel，不覆盖旧行。
- **非票据照片**：识别失败 → 报"识别失败" → 提示用户重发正确照片。
- **重复票据**：检测到相同单号 → 自动跳过。
- **模型不支持图片**：自动走本地 OCR（PaddleOCR）兜底，带多预处理重试 + 行级投票（见下方「OCR 兜底机制」）。OCR 结果按置信分级标复核，不再一律整张标黄。
- **Profile 字段不存在**：用户说的纠偏若涉及 profile 没有的字段（如 generic profile 下说「无色号」），不会被解析——这是预期行为，因为该行业无此字段。要支持更多字段，扩展 profile 的 `fields.items`。

## OCR 兜底机制（多预处理 + 行级投票 + 置信区间）

当模型不支持图片（或看不到图）时，自动走本地 PaddleOCR 兜底。为应对手写/模糊/拍照倾斜，兜底路径做了三层增强，**不再是一律整张标黄**：

1. **行级置信保留**：每行 OCR 文本都带置信度分数（兼容 PaddleOCR v2/v3 两种返回结构）。
2. **多预处理重试**：清晰图（平均置信 ≥ 0.85）只 OCR 一次直接返回（**零额外开销**）；低置信图自动用灰度/锐化/对比度增强/二值化等多个预处理变体各 OCR 一次（最多 `--ocr-retries` 次，默认 3）。
3. **行级投票**：多轮结果按归一化文本分组——多轮一致的行置信被提升（稳定=可靠），仅单轮出现的噪声行被压低。

**置信分级**（决定标不标复核）：

| 平均置信 | 处理 | 复核标记 |
|---|---|---|
| ≥ 0.85 | 保持模型自评置信 | 仅标 `ocr_fallback` |
| 0.70 – 0.85 | 降为 medium | 标 `ocr_fallback` |
| < 0.70 | 降为 low | 标 `ocr_fallback` + **列出具体低置信行**（如「OCR 第3处低置信(52%): 数量」）进 `needs_review` |

低置信行在喂给抽字段模型时还会加 `[?]` 前缀，提示结合上下文与金额逻辑推断。这样**只有真正不可靠的行才需要人看**，清晰的 OCR 结果不必整张重核。

**相关 CLI 参数**（不进 profile）：

```bash
--ocr-retries 3            # 低置信时额外尝试的预处理变体数（0=禁用多预处理，退回单次OCR）
--ocr-min-confidence 0.80  # 低于此平均置信才触发多预处理重试
```

## Skill Updates

When updating/reinstalling this skill:
- **Templates** 状态目录**不在** skill 包内 — 保留（旧 openclaw 路径自动兼容）
- **Preferences** — 保留
- **Saved Excel tables** — 偏好里记住的表路径，保留
- 升级前**询问用户**是否保留已有模板

## First Run Detection

When running `process.py` for the first time after install/update, it auto-detects:
- `EXISTING_TEMPLATES:N` — N supplier templates already saved
- `SAVED_TABLE:/path/to/file.xlsx` — previously used Excel table
- `NO_TABLE_SPECIFIED` — no saved table, must ask user
