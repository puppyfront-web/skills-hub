# 服饰类（3 种 × 6 品类）参数与 prompt 详解

本文件给 agent 处理服饰类商品图时参考。服饰对"还原度"要求高（颜色/版型不能变），prompt 模板做了品类分流。

## 通用约束

**所有服饰类型都强依赖参考图，且参考图必须能清晰展示服装的：**
- 颜色 / 图案
- 面料质感
- 剪裁 / 版型
- 关键细节（领口/袖型/扣子/拉链）

如果用户给的是模糊小图或只露出局部，建议提示用户补一张清晰全图。

---

## model-wear · 模特试穿图

**用途**：淘宝服饰主图，AI 模特穿上用户的衣服。

**接口**：`POST /v1/images/edits`

**默认 size**：`1024x1024`（上装/帽类用 `1024x1024`，连衣裙/外套/下装建议改 `1024x1536` 竖幅）

**必填**：
- `--image`：服装实物图
- `--category`：`upper` / `lower` / `dress` / `outerwear` / `shoes` / `hat`

**可选**：
- `--model-desc`：自定义模特描述，例如 `"Asian male, 30yo, athletic build, beard"`

### 各品类默认模特设定

| category | 默认模特 | 默认构图 |
|----------|---------|---------|
| `upper` 上装 | Asian female, 25yo, slim | 头到腰 |
| `lower` 下装 | Asian female, 25yo, slim | 全身 |
| `dress` 连衣裙 | Asian female, 25yo, slim | 全身 |
| `outerwear` 外套 | Asian female, 28yo, slim | 全身 |
| `shoes` 鞋 | female feet / mannequin | 下腿 |
| `hat` 帽 | Asian female, 25yo | 头肩 |

### 用 `--model-desc` 自定义

```bash
# 男性模特
--model-desc "Asian male, 30yo, athletic build, neutral expression"

# 中老年模特
--model-desc "Asian female, 45yo, mature, confident smile"

# 外国模特
--model-desc "Caucasian female, 25yo, blonde, slim"

# 模特 + 场景
--model-desc "Asian female, 25yo, slim, standing in modern office"
```

### 调优建议

- **颜色还原失败**：参考图曝光过曝/欠曝会导致颜色偏，先用其他工具校色
- **版型变形**：参考图最好是平铺图，挂拍图次之，模特图最次
- **图案还原**：复杂印花（小碎花、几何）偶尔会糊，可改 `--quality high`
- **鞋子**：默认是女模脚，要男鞋请用 `--model-desc "male feet, casual"`

---

## multi-model · 多模特/多场景

**用途**：服饰详情页，多角度展示同一件衣服。

**接口**：`POST /v1/images/edits`（一次生成 2x2 或 1x3 网格）

**默认 size**：`1024x1024`

**必填**：
- `--image`
- `--category`

**强烈推荐**：
- `--variations`：逗号分隔的变体描述

**变体示例**：

```bash
# 三种肤色模特
--variations "Asian female 25yo, Caucasian female 28yo, African female 26yo"

# 三个场景
--variations "studio gray background, street setting, cafe setting"

# 多年龄
--variations "young 20yo, middle 35yo, mature 50yo"
```

省略时默认：`Asian female 25yo in studio, Caucasian male 30yo in street, African female 28yo in cafe`

### 调优建议

- **网格串型**：偶尔变体间会互相串风格（如某个变体的衣服颜色被另一个污染）→ 改用 model-wear 跑 3 次单张
- **变体数量**：建议 3 个，超过 4 个排版会乱
- **模特一致性**：所有变体应保留同样的发型/姿态，差异只在肤色/场景

---

## flat-lay · 平铺/挂拍图

**用途**：服饰主图另一种风格（无模特），适合极简风、设计感强的品牌。

**接口**：`POST /v1/images/edits`

**默认 size**：`1024x1024`

**必填**：
- `--image`
- `--category`

**可选**：
- `--style`：`folded` / `hanging` / `steamed` / `laid`

### style 选项

| style | 效果 |
|-------|------|
| `folded`（默认） | 折叠摆放，略带角度 |
| `hanging` | 木质衣架挂拍，背景干净墙面 |
| `steamed` | 平铺烫平，无褶皱 |
| `laid` | 完全平铺，俯拍 |

### 调优建议

- **平铺适合**：T恤、卫衣、毛衣、裤子
- **挂拍适合**：外套、西装、风衣（垂感能体现版型）
- **steamed 适合**：丝绸、雪纺、棉麻（强调质感）
- **laid 适合**：床品、围巾、毯子等大件

---

## 品类专属技巧

### upper（上装）
- 参考图最好是平铺正面图
- 衣服上的图案（印花/刺绣）保持原样是关键
- 圆领/V领/翻领等领型细节要清晰

### lower（下装）
- 裤长（七分/九分/长裤）参考图要能看出来
- 牛仔裤的水洗纹路是卖点，建议 `--quality medium` 以上
- 短裙/短裤和长裤模特姿势不同，模板已分流

### dress（连衣裙）
- 默认全身竖构图，建议改 `--size 1024x1536`
- 长度（短/中/长）参考图要清晰
- 收腰/A字/直筒版型是关键

### outerwear（外套）
- 默认敞开穿，露出内搭
- 长款（风衣/大衣）建议竖构图
- 翻领/连帽/立领等领型细节

### shoes（鞋）
- 默认女模脚，男鞋用 `--model-desc "male feet"`
- 鞋面材质（皮/帆布/网面）影响质感呈现
- 运动鞋/正装鞋模板略有差异

### hat（帽）
- 默认头肩构图
- 帽型（棒球帽/渔夫帽/礼帽）模板已分流
- 男帽建议 `--model-desc "Asian male, 30yo"`

---

## 还原度调试技巧

如果生成结果总是和参考图差太多：

1. **换参考图**：找一张干净背景的平铺图，去掉复杂背景干扰
2. **加 `--quality high`**：高画质对细节还原更好
3. **明确品类**：`--category` 准确能让模板选对
4. **简化 `--model-desc`**：太多约束反而让模型纠结
5. **退而求其次**：实在还原不了，改用 flat-lay（无模特，变形风险低）
