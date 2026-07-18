# SkillHub 案例展示与可安装体验设计

## 目标

将当前 `skills-hub` 改造成一个以真实业务案例为入口、以 canonical skill 为交付单元的静态展示平台。访客可以理解每个工作流解决的问题、查看真实产物、确认验证状态，并获得可复制的安装方式。

## 范围

### MVP 包含

- 首页：平台定位、精选案例、工作流入口、质量状态说明。
- 工作流列表：按业务结果展示股票研究、OCR、跨境电商、B2B 情报、内容生产等工作流。
- 工作流详情：问题、输入、流程、输出、案例证据、组成 skill、安装入口。
- Skill 详情：能力、适用场景、依赖、兼容运行时、验证命令、限制。
- canonical manifest：统一描述公开 skill、工作流、证据和安装信息。
- 静态数据驱动：页面从本地 manifest 和内容文件渲染，不引入后端、账号或在线代码执行。
- 现有 `stitch-site` 改造成 SkillHub React/Vite 前端。

### MVP 不包含

- 用户上传、评论、评分、社区协作。
- 在线执行任意 skill。
- 支付、订阅和 marketplace。
- 将重复迁移版本、`dist/` 产物或中间文件作为公开资产。
- 对尚未完成端到端验证的能力作“可直接运行”的承诺。

## Canonical 资产规则

公开目录必须只引用 `skillhub-manifest.json` 中的条目。

1. 顶层独立 skill 目录优先作为 canonical 来源。
2. `stock-skills` 中重复的来源迁移版本合并为一套公开能力；`jq_xx`、趋势和策略类内容作为股票工作台的可选插件，不作为首页一级案例。
3. `cross-border-ecommerce-skills` 作为一个业务套件展示，子 skill 在工作流详情中展开。
4. `dist/`、迁移清单、重复哈希目录和中间产物不进入公开目录。
5. 报告 PDF、Excel、测试结果和示例输出属于案例证据，不属于独立 skill。
6. 每个公开条目必须声明验证状态：`verified`、`tested`、`beta` 或 `unverified`，并明确限制。

## 首批公开工作流

### 股票研究工作台

组合统一数据层、股票专家路由、行情、选股、技术分析、风控、回测和报告能力。案例重点是“一个研究问题如何经过合理的投资者工作流得到研究结论”。

### 送货单 OCR 自动录入

展示图片输入、字段识别、供应商模板学习、批量异常汇总和 Excel 写入。当前图片通道或本地 OCR 依赖尚未完整验证的部分必须标记为 beta 或限制，不得隐藏。

### 跨境电商运营

以选品、Listing、定价、库存、营销、客服和合规构成业务套件，展示多 skill 如何围绕一个业务目标协作。

### B2B 客户情报

展示公开网页检索、联系人和角色抽取、来源 URL、证据和置信度。页面必须保留公开来源和合规边界说明。

### 内容生产工作流

展示选题、大纲、正文、标题、封面文案和发布准备，作为低门槛体验入口。

## 信息架构

```text
首页
├── 精选案例
├── 工作流
├── Skills
└── 安装指南

工作流详情
├── 业务问题
├── 输入与输出
├── 工作流流程图
├── 真实产物
├── 组成 Skills
├── 安装方式
└── 验证状态与限制

Skill 详情
├── 能力说明
├── 适用场景
├── 依赖与运行时
├── 安装命令
├── 最小验证命令
├── 测试证据
└── 已知限制
```

## 数据模型

manifest 至少包含以下字段：

```json
{
  "id": "invoice-ocr",
  "name": "送货单 OCR 自动录入",
  "type": "skill",
  "category": "办公自动化",
  "source": "invoice-ocr",
  "runtime": ["OpenClaw", "Codex"],
  "status": "beta",
  "workflowIds": ["invoice-automation"],
  "evidence": {
    "tests": true,
    "realOutput": true,
    "e2eVerified": false
  },
  "install": {
    "sourcePath": "invoice-ocr",
    "verifyCommand": "python3 -m unittest discover -s invoice-ocr/tests -v"
  },
  "limitations": ["当前图片识别通道仍需在目标运行时验证"]
}
```

工作流条目额外包含 `skillIds`、`problem`、`inputs`、`outputs`、`steps`、`artifacts` 和 `caseSummary`。页面只消费 manifest 中存在且状态明确的内容。

## 页面与组件边界

- `content/`：manifest、工作流文案、skill 文案和案例证据引用。
- `components/`：Header、StatusBadge、WorkflowCard、SkillCard、EvidencePanel、InstallPanel、StepFlow。
- `pages/`：HomePage、WorkflowListPage、WorkflowDetailPage、SkillListPage、SkillDetailPage。
- `App.jsx`：只负责路由状态和页面组合，不承载具体业务数据。
- `styles.css`：沿用现有 Vite 项目基础，但移除 WarmNest 母婴电商语义。

## 安装体验

MVP 使用静态安装卡片：

1. 展示支持的运行时。
2. 展示依赖和环境变量。
3. 展示安装来源或相对路径。
4. 提供复制安装命令。
5. 提供最小验证命令。
6. 展示最近验证状态和已知限制。

安装按钮不得暗示平台可以在浏览器中直接执行本地 skill。

## 验证与测试

- manifest 测试：公开条目不能指向 `dist/`、迁移清单或重复来源；所有 workflow 引用必须存在。
- 页面测试：首页能进入工作流详情；详情页能展示证据、安装信息和状态；不存在的 ID 显示明确的空状态。
- 安装体验测试：安装命令和验证命令可复制，代码块内容与 manifest 一致。
- 构建验证：`npm test` 和 `npm run build` 必须通过。
- 静态 QA：检查首页、列表页和详情页在桌面和窄屏下没有溢出，链接和按钮具备可访问名称。

## 成功标准

访客在首页可以在 30 秒内理解平台定位，在 2 分钟内看懂一个真实案例，在 5 分钟内获得一个 skill 的安装和验证方式。平台不以技能数量作为核心指标，而以可理解性、证据完整度和安装可执行性作为第一阶段质量标准。
