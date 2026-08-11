# 淘宝商品图/视频生成 Agent（taobao-product-image）

面向**淘宝/天猫电商运营**的 AI 出图出视频助手：商品参考图 → 白底主图 / 场景图 / 卖点图 / A+ 详情图 / 橱窗图 / 服饰模特图 / 商品视频。

linkfox-aigc-imagegen-product 的开源平替：后端全可配置，用户可接入自己的 LLM/图像/视频 API 或 codex 这类工具。

## 核心能力

| 能力 | 说明 |
|------|------|
| **非服饰 4 类** | 白底主图 / 场景图 / 卖点图 / A+ 详情图 |
| **服饰 3 类 × 6 品类** | 模特试穿 / 多模特展示 / 平铺挂拍；品类覆盖上装·下装·连衣裙·外套·鞋·帽 |
| **橱窗图（banner）** | 店铺首页大图 / 活动横幅，单一焦点 + 留白；有参考图走 edits，无参考图走 generations |
| **套图编排** | 一次 plan 出 5-6 张方案 → 用户勾选 → 并发 dispatch → summary 汇总 |
| **图生视频** | 多后端可插拔：默认智谱 CogVideoX-Flash（**免费**），可切 custom / keling / wan |
| **强制交互协议** | 商品类别/品类必须问用户，agent 禁止凭视觉猜测（避免误判污染） |
| **纯 stdlib** | 全部 Python 标准库（urllib + hmac + base64），不依赖 requests/openai SDK |
| **多 key 配置** | 环境变量 / 配置文件 / CLI flag 三层优先级，方便复用已有 key |

## 快速开始

### 1. 配置 API Key（首次必做）

**方式 A：环境变量（推荐）**

```bash
# 图像（必填）——OpenAI 或任意 OpenAI 兼容代理
export OPENAI_API_KEY="sk-..."
export OPENAI_BASE_URL="https://api.openai.com/v1"   # 用代理时改这里
export OPENAI_IMAGE_MODEL="gpt-image-1-mini"         # 默认；便宜 $0.005/张

# 视频（用视频功能时必填）
export VIDEO_BACKEND="zhipu"                          # 默认；可选 zhipu/custom/keling/wan
export ZHIPU_API_KEY="id.secret"                      # CogVideoX-Flash 免费
```

**方式 B：配置文件**

首次运行自动生成默认配置于 `~/.openclaw/skill-state/taobao-product-image/config.json`，直接编辑填 key 即可。

**复用已有 key**：若你已有 OpenAI 兼容代理的配置（如 deepkey.top），把 `OPENAI_API_KEY` + `OPENAI_BASE_URL` 直接拿来用即可。

### 2. 自检连通

```bash
python3 scripts/generate.py --self-test     # 验证图像后端
python3 scripts/_config.py                  # 查看当前已配置的 key/后端
python3 scripts/video.py --list-backends    # 查看视频后端
```

### 3. 命令行用法

```bash
# 单张白底主图（非服饰）
python3 scripts/generate.py --type white-bg --image ~/Downloads/product.jpg

# 服饰模特试穿（必须指定品类）
python3 scripts/generate.py --type model-wear \
  --image ~/Downloads/tshirt.jpg --category upper \
  --model-desc "Asian male, 28yo, athletic build"

# 橱窗图（有参考图走 edits）
python3 scripts/generate.py --type banner \
  --image ~/Downloads/product.jpg \
  --slogan "夏日新风尚" --brand "某品牌" --product-hint "连衣裙"

# 套图编排（3 阶段）
python3 scripts/collection.py --phase plan \
  --image ~/Downloads/product.jpg \
  --apparel --category lower \
  --selling-points "轻盈透气, 显瘦版型" --slogan "自在随心"
# ↑ STATUS 行返回 needs_user_input 时 agent 要用 AskUserQuestion 问用户
python3 scripts/collection.py --phase dispatch --state <state_file> --max-workers 2
python3 scripts/collection.py --phase summary  --state <state_file>

# 图生视频（免费 Flash）
python3 scripts/video.py \
  --image ~/Downloads/product.jpg \
  --prompt "商品缓缓旋转 30 度，柔光扫过表面" \
  --model CogVideoX-Flash

# 切换视频后端（走用户自配置的 API）
python3 scripts/video.py --image ~/Downloads/product.jpg \
  --backend custom --model "your-custom-video-model"
```

## 类型路由表

| type | 中文名 | 接口 | 默认 size | 必填参数 |
|------|--------|------|----------|---------|
| `white-bg` | 白底主图 | `/images/edits` | 1024x1024 | `--image` |
| `scene` | 场景图 | `/images/edits` | 1024x1024 | `--image --scene` |
| `selling-point` | 卖点图 | `/images/edits` | 1024x1024 | `--image --selling-point` |
| `aplus` | A+ 详情图 | `/images/generations` | 1536x1024 | `--selling-point`（无需 image） |
| `model-wear` | 模特试穿图 | `/images/edits` | 1024x1024 | `--image --category` |
| `multi-model` | 多模特展示图 | `/images/edits` | 1024x1024 | `--image --category` |
| `flat-lay` | 平铺/挂拍图 | `/images/edits` | 1024x1024 | `--image --category` |
| `banner` | 橱窗图 | edits 或 generations | 1536x1024 | 无（slogan/selling-points 可选） |

**服饰品类**（`--category`）：`upper` 上装 / `lower` 下装 / `dress` 连衣裙 / `outerwear` 外套 / `shoes` 鞋 / `hat` 帽

## 视频后端（可插拔）

| backend | 默认模型 | 状态 | 切换方式 |
|---------|---------|------|---------|
| `zhipu`（默认） | `CogVideoX-Flash`（**免费**） | ✅ 已实现 | 默认 |
| `custom` | 用户配置 | ✅ 已实现（OpenAI 兼容） | `--backend custom` |
| `keling` | `kling-v1` | ⚠️ 占位（用 custom 代替） | `--backend keling` |
| `wan` | `wan2.1-i2v-plus` | ⚠️ 占位（用 custom 代替） | `--backend wan` |

接入自己的视频 API（codex 这种工具或自建服务）时，选 `custom` 后端，在 config.json 的 `custom` 块填：
```json
{
  "video_backend": "custom",
  "custom": {
    "api_key": "...",
    "base_url": "https://your-api.com",
    "default_video_model": "your-model",
    "submit_path": "/videos/generations",
    "poll_path_template": "/async-result/{task_id}",
    "auth_scheme": "bearer"
  }
}
```
`auth_scheme` 三选一：`bearer`（普通 Bearer token）/ `jwt_zhipu`（智谱 id.secret 格式）/ `x-api-key`。不用改代码。

## 目录结构

```
taobao-product-image/
├── SKILL.md                       # 入口：触发关键词 + 三大路由 + 强制交互协议
├── README.md                      # 本文件
├── scripts/
│   ├── _config.py                 # 多 key 优先级链（CLI > env > config.json > 默认）
│   ├── _gateway.py                # urllib + multipart + JWT 纯 stdlib 封装
│   ├── _prompts.py                # 8 种图类型 prompt 构造器
│   ├── _video_backends.py         # 视频后端 adapter 注册表（zhipu/custom/keling/wan）
│   ├── generate.py                # 单张图主入口（含 --self-test）
│   ├── collection.py              # 套图编排（plan→dispatch→summary）
│   └── video.py                   # 图生视频入口（多后端可配）
└── references/
    ├── types-nonapparel.md        # 白底/场景/卖点/A+ 参数与调优
    ├── types-apparel.md           # 模特/多模特/平铺 × 6 品类
    ├── video.md                   # CogVideoX 参数与 prompt 技巧
    ├── collection.md              # 套图三阶段协议详解
    └── api.md                     # API 契约 + 错误码 + 自检
```

## 强制交互协议（重要）

agent **禁止凭视觉判断商品类别**——图像识别错误会一路污染到 prompt（例如把"裤子"判成"玩偶"，整个套图都白生成）。

`collection.py --phase plan` 在缺失关键参数时会主动退出，输出：
```
STATUS: {"status":"needs_user_input","reason":"apparel_not_specified",...}
STATUS: {"status":"needs_user_input","reason":"apparel_category_missing",...}
STATUS: {"status":"needs_user_input","reason":"shot_required_args_missing",...}
```

agent 看到 `needs_user_input` 必须用 `AskUserQuestion` 问用户，拿到答复后**重跑 plan**。禁止自己填默认值。

## 落盘路径

所有产物落到 `<cwd>/taobao-images/<YYYY-MM-DD>/<时间戳>/`：

| 产物 | 文件 |
|------|------|
| 单张图 | `<type>-<时间戳>.png` |
| 套图每张 | `<type>-<时间戳>.png` + `task-result-<id>.json` |
| 套图清单 | `collection-manifest.json` |
| 套图状态 | `collection-state.json` |
| 视频 | `<model>-<时间戳>.mp4` |

用 `TAOBAO_IMG_OUT_ROOT` 环境变量改输出根目录。

## 兼容性

- **Python**：3.9+（避免 `str | None` 联合类型语法）
- **Pillow**：可选。有则压缩长边 ≤1568 + 重编码 JPEG q87；无则用原始 bytes
- **外部包**：0 个必须依赖（urllib + hmac + base64 全是 stdlib）

## Key 获取地址

| 服务 | 地址 | 备注 |
|------|------|------|
| OpenAI | https://platform.openai.com/api-keys | gpt-image-1-mini $0.005/张起 |
| 智谱（默认视频后端） | https://bigmodel.cn/console/usercenter/apikeys | CogVideoX-Flash **免费** |
| 可灵（可选） | https://klingai.com | 需自己实现 adapter 或用 custom 后端 |
| Wan 2.1（可选） | https://dashscope.aliyun.com | 需自己实现 adapter 或用 custom 后端 |

## 已知局限

- 白底图用通用 prompt 处理，异形/透明/反光类产品的边缘可能不及 linkfox
- 服饰模特图依赖模型对品类与剪裁的理解，复杂款式（不规则下摆、特殊袖型）可能失真
- A+ 详情图基于文字渲染，中文小字偶尔会糊，建议卖点文案精简（每词 4-8 字）
- 多模特场景图是单次生成 2x2/1x3 网格，复杂布局可能串型；要稳就改成单张直出多次
- 视频生成是异步任务，CogVideoX-Flash 通常 1-3 分钟，CogVideoX-2/3 可能 5+ 分钟
- 部分 OpenAI 兼容代理对高并有限流，套图 dispatch 建议 `--max-workers 2`

## 不适用

- **图片 OCR / 商品识别** → 走 `qianwen-image-generation` 或 `linkfox-multimodal-recognize-image`（先识别商品后再来本 skill 生成）
- **纯图片编辑**（去水印、抠图、换背景为指定图） → 走 `linkfox-aigc-imagegen` 或 GPT-4 vision
- **AI 换脸到具体某人** → 合规风险，本 skill 不做
- **文生视频**（无参考图） → 本 skill 只做图生视频；纯文生视频请走其他工具
