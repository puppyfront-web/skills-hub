---
name: taobao-product-image
description: 淘宝/天猫商品图与视频生成（非服饰 + 服饰 + 套图编排 + 图生视频）。当用户说"做淘宝图/做主图/做白底图/做场景图/做卖点图/做 A+ 图/做详情图/做模特图/做试穿图/换模特/做平铺图/做挂拍图/做橱窗图/做店铺首页大图/做套图/做商品视频/图生视频/让商品动起来"时使用。图像端基于 OpenAI gpt-image 系列（图生图、文字渲染）；视频端支持多后端可配（默认智谱 CogVideoX-Flash 免费，可切换 Kling / Wan / 任意自定义 API）。单张直出走 generate.py；套图走 collection.py 三阶段（plan→dispatch→summary）；视频走 video.py。
---

# 淘宝商品图/视频生成

本地技能，用 OpenAI gpt-image 系列（图生图）+ 可插拔视频后端（默认智谱 CogVideoX-Flash 免费）。后端全部可配置——用户可接入自己的 LLM/图像/视频 API 或 codex 这类工具。

## 何时触发

| 用户说 | 场景 |
|------|------|
| 做淘宝图/主图/白底图/场景图/卖点图/A+图/详情图 | 非服饰单张 |
| 做模特图/试穿图/换模特/多场景展示/平铺图/挂拍图 | 服饰单张 |
| 做橱窗图/店铺首页大图/活动大图/banner | 橱窗图（横幅，单一焦点） |
| 做套图/一套图/全套图 | 套图编排（plan→dispatch→summary） |
| 做商品视频/图生视频/让商品动起来 | 视频生成（默认智谱，可换后端） |

## 前置：首次使用配置

**必须配置 API Key 才能用**。两种方式二选一：

### 方式 A：环境变量（推荐）
```bash
# 图像（必填）
export OPENAI_API_KEY="sk-..."           # OpenAI 或任意 OpenAI 兼容代理
export OPENAI_BASE_URL="https://api.openai.com/v1"  # 默认；用代理时改
export OPENAI_IMAGE_MODEL="gpt-image-1-mini"        # 默认；便宜 $0.005/张
# 视频（用视频功能时必填）
export VIDEO_BACKEND="zhipu"             # 默认；可选 zhipu/custom/keling/wan
export ZHIPU_API_KEY="id.secret"         # 默认后端用
```

### 方式 B：配置文件
首次运行会自动生成默认配置文件于：
`~/.openclaw/skill-state/taobao-product-image/config.json`

编辑该文件填入 key 即可。

### 💡 复用已有 key
若 `/Users/tutu/ZCodeProject/codex-pet-system/.env` 里已有可用的 OpenAI 兼容代理配置（如 deepkey.top），可直接复用其 `OPENAI_API_KEY` + `OPENAI_BASE_URL`，省去额外注册。

### Key 获取地址
- **OpenAI**: https://platform.openai.com/api-keys
- **智谱（默认视频后端）**: https://bigmodel.cn/console/usercenter/apikeys （CogVideoX-Flash 免费）
- **可灵（可选）**: https://klingai.com （需自己实现 adapter 或用 custom 后端）
- **Wan 2.1（可选）**: https://dashscope.aliyun.com （需自己实现 adapter 或用 custom 后端）

### 自检连通
```bash
python3 <skill>/scripts/generate.py --self-test
python3 <skill>/scripts/_config.py        # 看当前已配置的 key/后端
python3 <skill>/scripts/video.py --list-backends  # 看视频后端
```

---

## 入口路由

| 条件 | 路径 | 操作 |
|------|------|------|
| 要单张特定类型的图 | **单张直出** | Read 对应 `references/types-*.md` → 调 `generate.py` |
| 要多张/多类型/套图 | **套图编排** | Read `references/collection.md` → 跑 plan → 用 `AskUserQuestion` 让用户选 → dispatch → summary |
| 要视频 | **图生视频** | Read `references/video.md` → 调 `video.py` |

---

## 类型路由表

### 非服饰（4 种）

| type | 中文名 | 接口 | 默认 size | 必填参数 |
|------|--------|------|----------|---------|
| `white-bg` | 白底主图 | `/images/edits` | 1024x1024 | `--image` |
| `scene` | 场景图 | `/images/edits` | 1024x1024 | `--image --scene` |
| `selling-point` | 卖点图 | `/images/edits` | 1024x1024 | `--image --selling-point` |
| `aplus` | A+详情图 | `/images/generations` | 1536x1024 | `--selling-point`（无需 image） |

### 服饰（3 种 × 6 品类）

| type | 中文名 | 接口 | 必填参数 |
|------|--------|------|---------|
| `model-wear` | 模特试穿图 | `/images/edits` | `--image --category` |
| `multi-model` | 多模特展示图 | `/images/edits` | `--image --category` |
| `flat-lay` | 平铺/挂拍图 | `/images/edits` | `--image --category` |

品类 (`--category`): `upper` 上装 / `lower` 下装 / `dress` 连衣裙 / `outerwear` 外套 / `shoes` 鞋 / `hat` 帽

### 通用（横幅/营销）

| type | 中文名 | 接口 | 默认 size | 必填参数 | 用途 |
|------|--------|------|----------|---------|------|
| `banner` | 橱窗图 | 有 image 走 edits；无 image 走 generations | 1536x1024 | 无（slogan/selling-points 可选） | 店铺首页大图 / 活动横幅 |

**banner vs aplus 区别**：A+ 是详情页 hero（信息密集、多卖点卡片）；banner 是店铺首页/活动大图（单一焦点、大量留白、品牌感优先）。

### 视频后端（可插拔）

| backend | 默认模型 | 状态 |
|---------|---------|------|
| `zhipu`（默认） | `CogVideoX-Flash`（免费） | ✅ 已实现 |
| `custom` | 用户配置 | ✅ 已实现（OpenAI 兼容协议） |
| `keling` | `kling-v1` | ⚠️ 占位，需用户实现或改用 custom |
| `wan` | `wan2.1-i2v-plus` | ⚠️ 占位，需用户实现或改用 custom |

切换后端：CLI `--backend zhipu`、或 env `VIDEO_BACKEND=custom`、或 config.json 的 `video_backend` 字段。

---

## 🚨 强制交互协议（最高优先级，违反即故障）

**agent 不得凭视觉判断商品类别**。原因：图像识别错误会一路污染到 prompt（例如把"裤子"判成"玩偶"，整个套图都白生成）。

### 必须问用户的决策（不能用 agent 自己的判断代替）

| 决策 | 何时问 | 怎么问 |
|------|-------|-------|
| 服饰 vs 非服饰 | 任何套图 / 服饰类单张 | `AskUserQuestion` 给出"服饰 / 非服饰"二选一 |
| 服饰品类（category） | 服饰类生成 | `AskUserQuestion` 给出 upper/lower/dress/outerwear/shoes/hat |
| 商品名（product-hint） | 任何生成 | 直接问用户"这是什么商品？" |
| 场景描述（scene） | 场景图 | 提供几个候选场景 + 让用户补充 |
| 卖点文案（selling-points） | 卖点图 / A+ | 问用户的核心卖点，让用户填 |
| 模特描述（model-desc） | 模特试穿 | 提供默认"亚洲女模 25yo"，让用户确认或改 |

### collection.py plan 阶段的退出协议

plan 脚本在缺失关键参数时会**主动退出**，stdout 输出：
```
STATUS: {"status":"needs_user_input","reason":"apparel_not_specified",...}
```
或：
```
STATUS: {"status":"needs_user_input","reason":"shot_required_args_missing","missing":[...],...}
```

**agent 看到 `needs_user_input` 必须立即用 `AskUserQuestion` 问用户**，拿到答复后**重跑 plan**（带上新参数）。禁止：
- ❌ 自己填默认值继续往下走
- ❌ 凭印象填 `--product-hint`
- ❌ 凭印象判断 `--apparel`

---

## 工作流（单张直出）

### 步骤 1：校验输入 + 必要时问用户
- 本地图片路径 → 直接传给 `generate.py --image`
- HTTP(S) URL → 直接传给 `generate.py --image`（脚本自动 fetch）
- 空数组护栏：**`--image` 为空且 type != `aplus` 时立即停止**，向用户报错请求补充
- **服饰类（model-wear/multi-model/flat-lay）必须先问用户 `--category`**，没有就报错退出
- **`--product-hint` 必须由用户提供**，禁止 agent 凭视觉猜测

### 步骤 2：构造参数
- 服饰类必须提供 `--category`，否则脚本报错（这是兜底，agent 应该在步骤 1 就问好）
- 场景/卖点/A+ 类型缺省时会用脚本内置默认值（如默认"现代客厅"），但 agent 应**主动问用户**而非依赖默认

### 步骤 3：调用脚本
```bash
python3 <skill>/scripts/generate.py \
  --type <type> \
  --image <path_or_url> \
  [--scene "..."] [--selling-point "..."] [--brand "..."] \
  [--category <c>] [--model-desc "..."] [--variations "..."] [--style folded] \
  [--size 1024x1024] [--quality low] [--model gpt-image-1-mini] \
  [--product-hint "<用户告知的商品名>"]
```

**绝对路径铁律**：`<skill>` = `~/.agents/skills/taobao-product-image`，所有脚本调用用绝对路径。

### 步骤 4：解析输出
成功：stdout 含 `Saved: <abs_path>` 一行 → 在对话回复正文里**追加**：
```markdown
- <类型中文名>
  ![<类型中文名>](<abs_path>)
```
失败：stdout 是 `ERROR: <message>` → 如实告知用户，按错误类型走护栏。

**禁止**：把 `Saved: [...]` 协议原文复述进对话回复（裸露路径会让用户看到原始协议字面）。

---

## 工作流（套图编排）

按用户选择走 3 阶段。**agent 自己不并发**——dispatch 阶段在 skill 层用 `ThreadPoolExecutor` 并发跑全部子任务。

### 阶段 1：plan

**必须先问用户**：服饰 vs 非服饰（不指定会退出），服饰还需要品类。

```bash
python3 <skill>/scripts/collection.py \
  --phase plan \
  --image <path_or_url> \
  --apparel | --no-apparel        # 二选一，必须显式指定
  [--category <c>]                 # 服饰必填
  [--scene "..."] [--selling-points "..."] [--brand "..."] [--model-desc "..."]
```

**stdout 解析**（agent 必须解析 STATUS 行）：

| status | 含义 | agent 行为 |
|--------|------|-----------|
| `plan_complete` | 所有参数齐全 | 进入 dispatch 阶段 |
| `needs_user_input` + `reason: apparel_not_specified` | 没指定服饰/非服饰 | 用 AskUserQuestion 问用户，重跑 plan 加 `--apparel` 或 `--no-apparel` |
| `needs_user_input` + `reason: apparel_category_missing` | 服饰但缺品类 | 用 AskUserQuestion 让用户选 category，重跑 plan 加 `--category` |
| `needs_user_input` + `reason: shot_required_args_missing` | 某些类型缺 scene/selling_points | 用 AskUserQuestion 让用户补齐，重跑 plan |

**禁止**：自己填默认值让 plan 顺利通过（必须由用户答复后才能往下走）。

### 阶段 2：dispatch
```bash
python3 <skill>/scripts/collection.py \
  --phase dispatch \
  --state <state_file> \
  --selected id1,id2,id3
```
- skill 层并发跑全部 `generate.py`，每张落 `task-result-<id>.json`
- 末尾打 1 行 status JSON（不渲染图，正常）
- 失败一张不影响其他

### 阶段 3：summary
```bash
python3 <skill>/scripts/collection.py \
  --phase summary \
  --state <state_file>
```
- stdout 打 markdown 明细（**每张成功图自带 `![](abs_path)` 内联引用**）
- 同时合并写 `collection-manifest.json`

**转发规则**：summary 的 markdown 整段原样转发给用户（包括 `![]()` 行，前端 markdown 渲染器据此渲染图）。**禁止**把 `![]()` 行剥掉，否则图渲染不出来。

---

## 工作流（图生视频）

```bash
python3 <skill>/scripts/video.py \
  --image <path_or_url> \
  [--prompt "..."] \
  [--model CogVideoX-Flash] \
  [--duration 5] [--size 1920x1080] \
  [--product-hint "..."]
```
- 内部：submit → 轮询（默认 10 分钟超时，10s 间隔）→ 下载 mp4
- 进度打到 stderr，成功时 stdout 是 `Saved: <abs_path>.mp4`
- CogVideoX-Flash **免费**，其他按时长计费

---

## 失败护栏（铁律）

| 失败类型 | 判断 | 处理 |
|----------|------|------|
| 参数非法 | `--type` / `--category` 取值不合法 | 提示用户用合法值，不重试 |
| 认证失败 | exit code 2 / 401 / 402 / 403 | 提示配置 key（指向上述配置章节），不重试 |
| 内容被拒 | exit code 3 / moderation / policy | 告知用户改 prompt，不重试 |
| 瞬时错误 | exit code 4 / 网络/超时/5xx | gateway 层已自动重试 1 次；仍失败如实告知 |
| 其它错误 | exit code 5/6 | 不重试，告知用户原始错误 |

**硬性护栏**：
- ❌ 禁止因生图失败改调其它 skill
- ❌ 禁止无上限重试
- ❌ 禁止把失败的返回当成功继续往下走
- ❌ 禁止 agent 自己并发跑 N 次 generate.py 来做"套图"（必须走 collection.py dispatch 阶段）

---

## 落盘路径

所有产物落到 `<cwd>/taobao-images/<YYYY-MM-DD>/<时间戳>/`：

| 产物 | 文件 |
|------|------|
| 单张图 | `<type>-<时间戳>.png` |
| 套图每张 | `<type>-<时间戳>.png` + `task-result-<id>.json` |
| 套图清单 | `collection-manifest.json` |
| 套图状态 | `collection-state.json` |
| 视频 | `<model>-<时间戳>.mp4` |

可用 `TAOBAO_IMG_OUT_ROOT` 环境变量改输出根目录。

---

## 共享输入参数

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `--image` | string | 必填（非 A+） | 商品参考图，本地路径或 URL |
| `--model` | string | `gpt-image-1-mini` | OpenAI 图像模型 |
| `--size` | string | 随类型 | 如 `1024x1024` / `1536x1024` |
| `--quality` | enum | `low` | `low` / `medium` / `high` |
| `--out-dir` | path | 自动 | 输出目录 |

完整参数说明：`python3 <skill>/scripts/generate.py --help`

---

## 已知局限

- 白底图用通用 prompt 处理，异形 / 透明 / 反光类产品的边缘可能不及 linkfox（其做了结构判定）。
- 服饰模特图依赖模型对品类与剪裁的理解，复杂款式（如不规则下摆、特殊袖型）可能失真。
- A+ 详情图基于文字渲染（gpt-image-1 的强项），但中文小字偶尔会糊，建议卖点文案精简。
- 多模特场景图是单次生成 2x2/1x3 网格，复杂布局可能串型；要稳就改成单张直出多次。
- 视频生成是异步任务，CogVideoX-Flash 通常 1-3 分钟，CogVideoX-2/3 可能 5+ 分钟。

---

## 不适用

- **图片 OCR / 商品识别** → 走 `qianwen-image-generation` 或 `linkfox-multimodal-recognize-image`（先识别商品后再来本 skill 生成）
- **纯图片编辑**（去水印、抠图、换背景为指定图） → 走 `linkfox-aigc-imagegen` 或 GPT-4 vision
- **AI 换脸到具体某人** → 合规风险，本 skill 不做
- **文生视频**（无参考图） → 本 skill 只做图生视频；纯文生视频请走其他工具

---

## 示例（参考）

```bash
# 1. 白底主图（非服饰）
python3 ~/.agents/skills/taobao-product-image/scripts/generate.py \
  --type white-bg \
  --image ~/Downloads/product.jpg

# 2. 场景图（带场景描述）
python3 ~/.agents/skills/taobao-product-image/scripts/generate.py \
  --type scene \
  --image ~/Downloads/product.jpg \
  --scene "北欧风客厅，木质茶几，暖光" \
  --product-hint "蓝牙音箱"

# 3. 服饰模特试穿
python3 ~/.agents/skills/taobao-product-image/scripts/generate.py \
  --type model-wear \
  --image ~/Downloads/tshirt.jpg \
  --category upper \
  --model-desc "Asian male model, 28 years old, athletic build"

# 4. A+ 详情图（无需参考图）
python3 ~/.agents/skills/taobao-product-image/scripts/generate.py \
  --type aplus \
  --selling-points "超大容量, 长续航, 快充" \
  --brand "某品牌"

# 5. 橱窗图（带参考图 → edits 模式，留白 + 单一焦点）
python3 ~/.agents/skills/taobao-product-image/scripts/generate.py \
  --type banner \
  --image ~/Downloads/product.jpg \
  --slogan "夏日新风尚" \
  --brand "某品牌" \
  --product-hint "连衣裙"

# 6. 橱窗图（无参考图 → generations 模式，纯文生图）
python3 ~/.agents/skills/taobao-product-image/scripts/generate.py \
  --type banner \
  --slogan "新品上市" \
  --product-hint "蓝牙音箱"

# 7. 非服饰套图（5 张：含 banner）
python3 ~/.agents/skills/taobao-product-image/scripts/collection.py \
  --phase plan \
  --image ~/Downloads/product.jpg \
  --no-apparel \
  --scene "北欧客厅" --selling-points "蓝牙5.0, 长续航" \
  --slogan "原音重现" --brand "某品牌"

# 8. 服饰套图（6 张：含 aplus + banner）
python3 ~/.agents/skills/taobao-product-image/scripts/collection.py \
  --phase plan \
  --image ~/Downloads/tshirt.jpg \
  --apparel --category upper \
  --selling-points "纯棉透气, 显瘦版型" \
  --slogan "自在随心"

# 9. 图生视频（免费 Flash，默认 zhipu 后端）
python3 ~/.agents/skills/taobao-product-image/scripts/video.py \
  --image ~/Downloads/product.jpg \
  --prompt "商品缓缓旋转 30 度，柔光扫过表面" \
  --model CogVideoX-Flash

# 10. 图生视频（切换 custom 后端，走用户自配置的 OpenAI 兼容 API）
python3 ~/.agents/skills/taobao-product-image/scripts/video.py \
  --image ~/Downloads/product.jpg \
  --backend custom \
  --model "your-custom-video-model" \
  --prompt "..."
```
