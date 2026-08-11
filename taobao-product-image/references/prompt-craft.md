# 商品图 Prompt 工艺规则

跨类型的 prompt 设计方法论。当 7 类内置模板不够用、需要手写 `--scene` / `--selling-point` / 定制 prompt 时，先读这里。规则按"投入产出比"排序，越靠前越常用。

---

## 1. 编辑接口的不变量保护（最重要）

图生图（`/images/edits`）的本质是"保留 + 改变"。prompt 里必须**同时**写清这两半，模型才知道什么能动什么不能动。这是商品图最常出问题的环节。

### 反例（只说改什么，没说留什么）
> 换成粉色背景，加上 Hello Kitty 元素

模型会把整张图重画，商品可能变形、变色、错位。

### 正例（保留 + 改变 分开声明）
> 改变：背景换成淡粉色墙面，增加 Hello Kitty 蝴蝶结暗纹和 Kitty 公仔道具。
> 保留：所有商品的摆放位置、数量、相对关系、原始颜色（除牙刷和牙膏需改为粉色外）不得改变。商品形状、文字、Logo 必须完整保留。

### 不变量措辞清单（按需挑选组合）

| 不变量 | 措辞 |
|--------|------|
| 整体构图 | `Preserve the original composition and arrangement exactly` |
| 商品身份 | `Keep all products identical: same shape, same count, same relative position` |
| 颜色 | `Do NOT change the color of [X]; keep their original colors exactly as in the input` |
| 局部改色 | `ONLY recolor [X] into pink; do NOT change the color of any other item` |
| 文字/Logo | `Preserve all text, labels, and brand logos exactly; do not redesign or move them` |
| 比例 | `Keep the original aspect ratio and framing` |

**铁律**：任何 `scene` / `selling-point` / 模特图生成，prompt 末尾都要有一段"保留什么"的声明。`_prompts.py` 的模板已内置，但用户手写 `--scene` 时要提醒带上。

---

## 2. 结构化 prompt（JSON / config 风格）

当场景复杂、有多个相互作用的子系统（环境/材质/灯光/道具/动作）时，**JSON schema 比散文更可控**。模型能更稳定地分配"细节预算"。

适用场景：高端产品渲染、食品爆炸图、带粒子和动效的卖点图。**不适合**白底主图（白底要的就是简洁）。

### 模板

```text
/* PRODUCT_RENDER_CONFIG: <商品名>
   VERSION: 1.0.0
   AESTHETIC: <调性，如 Premium Commercial Photography> */
{
  "GLOBAL_SETTINGS": {
    "aspect_ratio": "1:1 square",          // 或 "2:3 vertical" / "3:2 landscape"
    "style": "hyper-realistic commercial photography",
    "render_flags": ["8K_UHD", "sharp_foreground", "editorial_finish"]
  },
  "ENVIRONMENT": {
    "background": "<具体背景>",
    "lighting": "<光源类型 + 方向 + 色温>",
    "atmosphere": ["<氛围元素1>", "<氛围元素2>"]
  },
  "CORE_ASSETS": {
    "primary_subject": "<主商品>",
    "materials": ["<材质1>", "<材质2>"],
    "composition": "<构图：居中 / 对角 / 三分>"
  },
  "DETAIL_SYSTEMS": [
    { "object": "<装饰元素>", "state": "<状态>" }
  ],
  "OUTPUT": {
    "mood": "<情绪>",
    "avoid": ["<要避免的廉价感>"]
  }
}
```

### 关键规则
- key 描述**视觉子系统**，不是代码实现（用 `lighting` 不用 `param_exposure`）。
- value 是**具体视觉约束**，不是空泛赞美（`"directional softbox, upper-left, warm 3200K"` 而非 `"nice lighting"`）。
- `render_flags` 用于输出级约束：`8K_UHD` / `sharp_foreground` / `micro_texture` / `editorial_finish` / `no_CGI_tell`。
- JSON 不必机器可解析，加注释帮模型理解，但保持整洁可读。

---

## 3. 画布 / 比例 / 布局 放在主体之前

强 prompt 会**先分配空间，再描述物体**。否则模型把细节预算花在物体上，布局全靠即兴发挥。

### 开头句式（按类型选）
- 白底/场景：`Square 1:1 e-commerce main image.` → 再写商品
- A+/橱窗：`Wide 3:2 detail-page banner, hero on left, 3 callout cards on right.` → 再写内容
- 卖点图：`Square 1:1 marketing image, product occupies 60-70%, text overlay top-right.` → 再写文案
- 服饰：`Vertical 2:3 full-body fashion shot.` → 再写模特和衣服

---

## 4. 场景密度 > 形容词

具体场景元素比堆形容词有效。模型对"豪华的、高端的、精致的"这类词理解模糊，但对"大理石台面、晨光斜射、单朵白茶花"理解精准。

### 反例
> 高端精致的浴室场景，豪华氛围

### 正例
> Travertine bathroom counter beside a frosted window, a minimal glass serum bottle, ceramic tray, folded linen towel, single dewy white camellia. Morning side light, soft shadows, cream/warm-stone palette.

**句式**：`<具体物体列表> + <光源> + <调色板>`。物体尽量名词化、可数化。

---

## 5. 文字必须用引号

gpt-image 系列能渲染文字，但必须**把要显示的字面量用引号包起来**，否则会乱码或臆造。

### 卖点图 / A+ 文案写法
```
Overlay these exact selling-point phrases, rendered in bold modern Chinese sans-serif:
"超大容量" / "长续航 30 天" / "TYPE-C 快充"
```

规则：
- 每个字符串用 `"…"` 包裹。
- 多段文案用 `/` 或 bullet 分隔。
- 中文逐字保留，不要改写。
- 如果文字必须可读，加 `crisp, legible, no garbled characters`；如果只是装饰，明说 `decorative only, may be abstract`。

---

## 6. 多参考图（风格 / 灯光参考）

当一个商品需要"借"另一张图的风格/灯光/调色时，用 `--style-ref`（可重复）传入额外的参考图。主图（`--image`）定义**商品本身**，风格参考图定义**视觉调性**。

### prompt 里如何呼应风格参考
```
Match the lighting style, color grading, and shadow softness of the style reference image.
Apply that same [warm creamy tone / soft frontal softbox / low-saturation palette] to this product.
Do NOT copy any objects or products from the style reference — only borrow its photographic style.
```

### 注意事项
- 风格参考图**只借调性**，不能把里面的商品复制过来。
- 主图的商品身份永远以 `--image` 为准。
- 后端是否支持多图取决于模型：OpenAI 原生 gpt-image 支持；部分代理（如 deepkey.top）可能只取第一张。不支持时会自动降级为单图 + 文字描述风格。

---

## 7. 相机与拍摄语境（解锁真实感）

写真级电商图应在 prompt 里加入**相机语境**，模型会据此模拟景深、焦段、胶片质感。

可借用句式：
- `Shot on 85mm portrait lens, f/2.8, shallow depth of field`（服饰/美妆特写）
- `50mm prime, deep depth of field, tack-sharp product`（白底/场景）
- `Medium-format Hasselblad, rich creamy texture`（高端静物）
- `35mm lens, environmental context softly blurred`（生活场景）
- `Portra 400 film grain`（复古/文艺调性）

光线上配 `softbox key light from front-upper-left, subtle fill, gentle rim light` 比单纯说 `soft lighting` 精准得多。

---

## 8. 否定约束用于"强先验"

`Do NOT ...` 不是必须，但当模型有错误倾向时，明确的否定能压制。常见场景：

| 场景 | 否定句 |
|------|--------|
| 怕商品变形 | `Do NOT alter the product's shape, proportions, or details` |
| 怕乱加 Logo | `Do NOT add any brand logos, watermarks, or fake text` |
| 怕廉价 CGI 感 | `Avoid plastic CGI look, no overdone shine` |
| 怕背景杂乱 | `No clutter, no distracting props, generous negative space` |
| 怕多人物串型 | `Do NOT merge or swap the models across panels` |

否定要**具体**（针对已知的失败模式），不要泛泛地写"不要难看"。

---

## 9. 多面板 / 套图一致性

套图（multi-model 网格、详情页多卡片）的关键不是"图多"，而是**跨面板的一致性**。

写法：
```
Arrange as a clean 2x2 grid, each panel in its own setting.
CRITICAL: the [garment / product] must be visually identical across all panels
(same color, same pattern, same fabric, same cut). Do NOT merge or swap items.
```

服装类多模特图尤其要强调"同一件衣服在所有镜头里完全一致"。

---

## 10. 套图编排时的 prompt 独立性

collection.py 的 dispatch 阶段会并发跑多个 generate.py。每个类型的 prompt 是**独立**构造的，不共享上下文。这意味着：
- 用户在 plan 阶段提供的 `--scene` / `--selling-points` 会被分发到所有需要的类型。
- 如果想让某张图用特殊 prompt，应在 plan 后用 `overrides`（见 collection.py 的 `_run_shot`）。
- 不要指望"先生成的白底图影响后生成的场景图"——它们之间无状态传递。
