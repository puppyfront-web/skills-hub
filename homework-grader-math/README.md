# 小学数学作业批改 Agent（homework-grader-math）

面向**个人小学数学教师**的 AI 批改助教：拍照上传 → 智能批改 → 错题解析 → 同类题衍生 → 多周期学情诊断。

## 核心能力

| 能力 | 说明 |
|------|------|
| **拍照批改** | 视觉模型识别题面+作答，自动判定对错（计算题/填空/选择/判断/应用题） |
| **三态判定** | ✓ 对 / ✗ 错 / ❓ 无法判定（不强行判错，标老师复核） |
| **题库+AI 混合** | 题库命中（confidence=1.0）优先，未命中走 AI 解题 |
| **OCR 兜底** | 视觉失败时自动降级（PaddleOCR + LLM 结构化 / 离线规则） |
| **错题解析** | 错因分类（粗心/概念/计算）+ 鼓励式点评 + 分步骤解析 |
| **同类题衍生** | 针对错题生成同知识点练习，干扰项来自常见错误 |
| **批量批改** | 多学生同一套题，共享题面+答案缓存，各自批改 |
| **原图批注** | 在作业图上画红勾/红叉/黄问号，像老师真实批改 |
| **卡片图** | 生成可转发的总览卡/错题详情卡/家长群版（PNG） |
| **学情累积** | 按学生/知识点聚合，可查历史 + 薄弱点 |
| **Web 界面** | 4 个 Tab：批改 / 批量批改 / 学情 / 配置 |

## 快速开始

### 1. 安装依赖

```bash
pip install Pillow gradio paddleocr paddlepaddle
```

### 2. 配置模型服务

需要一个支持视觉理解的 OpenAI 兼容 API（如 GPT-4o、GLM-4V、通义千问 VL 等）：

```bash
python3 scripts/grade.py --setup \
  --base-url "https://api.openai.com" \
  --api-key "sk-你的key" \
  --model "gpt-4o"
```

### 3. 启动 Web 界面（推荐）

```bash
python3 scripts/grade.py --web
# 浏览器打开 http://127.0.0.1:7860
```

### 4. 命令行用法

```bash
# 单图批改 + 卡片 + 原图批注
python3 scripts/grade.py 作业.png --grade --render-card --annotate --student 小明

# 批量批改（多学生同一套题）
python3 scripts/grade.py --batch 小明.png 小红.png --names 小明,小红 --annotate

# 查学情
python3 scripts/grade.py --student-summary 小明
```

## 目录结构

```
homework-grader-math/
├── SKILL.md                   # 技能入口（触发条件 + 工作流 + 话术）
├── README.md                  # 本文件
├── config.example.json        # 配置示例（实际配置在 .state/config.json）
├── .gitignore                 # 排除 .state/ 等运行时数据
├── references/                # 参考文档
│   ├── grading-rules.md       # 批改规则
│   ├── knowledge-map.md       # 人教版小学数学知识点体系
│   └── prompt-templates.md    # 提示词设计
├── scripts/                   # 代码
│   ├── _config.py             # 配置管理
│   ├── _gateway.py            # 模型服务调用
│   ├── ocr.py                 # 视觉识别
│   ├── ocr_fallback.py        # OCR 兜底
│   ├── bank.py                # 题库 + AI解题 + 衍生出题
│   ├── compare.py             # 答案等价归一 + 对错判定
│   ├── explain.py             # 错因分类 + 点评 + 解析
│   ├── report.py              # 批改报告 + 学情累积
│   ├── card_renderer.py       # 卡片图渲染 + 原图批注
│   ├── grade.py               # 主入口
│   └── web.py                 # Web 界面
└── .state/                    # 运行时数据（gitignored）
    ├── config.json            # 模型服务配置（含 API key，不进 git）
    ├── question-bank.json     # 题库
    ├── study-records.json     # 学情累积
    ├── last-batch.json        # 上次批改缓存
    └── cards/                 # 生成的卡片图
```

## 状态目录解析

`_config.py` 按以下优先级查找状态目录：
1. 环境变量 `HWM_STATE_DIR`
2. 代码旁边的 `.state/`（自包含模式，**默认**）
3. `~/.openclaw/skill-state/homework-grader-math/`（兼容旧部署）

## 技术栈

- Python 3.10+
- Pillow（卡片图、原图批注）
- PaddleOCR（OCR 兜底）
- Gradio（Web 界面）
- OpenAI 兼容 API（视觉+文本，用户自选）

## 边界与限制

- ✅ 计算/填空/选择/判断/应用题（仅判最终答案）
- ✅ 小学数学（人教版知识点体系）
- ❌ 图形/几何题（需识别图形）
- ❌ 应用题步骤分判定
- ❌ 多教材版本（首期人教版）
