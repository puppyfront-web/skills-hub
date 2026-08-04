# Resume Workflow · 简历四步映射

简历侧四个子步骤（分析 / 改写 / 生成 / 导出）分别做什么、用什么 skill、输入输出是什么。

**核心原则**：本 skill 不自己实现 PDF/DOCX 渲染，**全部交给 `document-skills:pdf` 和 `document-skills:docx`**。本 skill 只负责：内容推理、关键词匹配、结构建议、调用指路。

---

## 1. 四步总览

| 步骤 | 做什么 | 用什么 | 输出 |
|---|---|---|---|
| **分析+诊断** | 读简历 → 强项/弱项/匹配度/缺什么 | 本 skill 内 LLM 推理（不调外部） | Markdown 诊断报告 |
| **改写优化** | 针对 JD 重写措辞、突出匹配点 | 本 skill 内 LLM 推理 → `pdf` skill | 定制版 Markdown + PDF |
| **生成新简历** | 从经历信息生成全新结构化简历 | `docx` skill（scenes/resume.md 模板） | DOCX |
| **导出** | 转成投递/编辑格式 | `pdf` skill（ATS/创意/学术模板） / `docx` skill | PDF / DOCX |

---

## 2. Step A · 分析+诊断

**触发**：用户粘贴简历 + 目标 JD，说「分析简历」「这家公司我够格吗」「简历哪里要改」。

**输入**：
- 用户简历（任何格式：Markdown / 纯文本 / PDF 文本抽取 / 口述经历）
- 目标岗位 JD（链接或文本）

**做什么**（本 skill 内 LLM 推理）：

1. 抽取简历关键信息：技能清单、工作经历、项目、教育、年限。
2. 抽取 JD 关键信号：必备技能、加分技能、年限、行业、职责、考核指标。
3. 交叉比对，输出诊断报告：

```markdown
# 📊 简历诊断 · {用户简称} × {岗位简称}

## 整体匹配度
> 总分 {x.x}/5 · 档位 {A/B/C/D} · {一句话结论}

## 强项（应突出）
- ✅ {强项 1：JD 要求的技能 + 用户简历对应证据}
- ✅ {强项 2}

## 弱项（应补/回避）
- ⚠️ {弱项 1：JD 要求但简历缺失，建议补法}
- ⚠️ {弱项 2}

## 关键词缺口
JD 含但简历未出现的词：
- {关键词 1}（建议植入位置：__）
- {关键词 2}

## JD 红旗（如有）
- ⚠️ {JD 中的可疑信号}

## 改写建议（下一步）
1. {优先级 1 改写动作}
2. {优先级 2}
```

**不做什么**：
- 不直接重写简历（那是 Step B 的事，且要用户确认）。
- 不编造用户没写的经历（缺什么标 `[简历未体现]`）。

---

## 3. Step B · 改写优化

**触发**：用户说「改简历」「按这个 JD 改」「定制版」。

**输入**：
- 原简历（Markdown 优先；其他格式先转 Markdown）
- 目标 JD（来自 Step 4 的档案卡）
- Step A 的诊断报告

**做什么**（本 skill 内 LLM 推理）：

1. **结构重组**：把与 JD 最相关的经历提到前面，弱化无关经历。
2. **措辞优化**：用 JD 的术语替换简历的同义词（如 JD 说「高并发」，简历写「大流量」→ 改成「高并发大流量」）。
3. **关键词植入**：把 JD 必备技能自然融入项目描述（**严禁**虚构，只调整已有经历的措辞）。
4. **量化强化**：把模糊描述改成带数字（如「优化了性能」→「QPS 从 500 提升到 3000」）—— 数字必须来自用户原简历或口述，不可编造。
5. **裁剪到 1 页**：投递用简历默认 1 页，移除与 JD 无关的内容。

**输出**：定制版 Markdown 简历。然后**调用 `document-skills:pdf`** 导出（见 Step D）。

**改写红线**：
- ❌ 不可虚增工作年限（如把 2 年写成 4 年）。
- ❌ 不可伪造职位/职级（如「工程师」改「高级工程师」）。
- ❌ 不可编造未参与的项目或未取得的成果。
- ❌ 不可伪造学历/证书/GPA。
- ✅ 可以调顺序、调措辞、合并同类项、强调可迁移技能。

---

## 4. Step C · 生成新简历

**触发**：用户没简历，或简历太旧要重做，说「生成简历」「从头写一份」。

**输入**：
- 用户的经历信息（口述 / 资料 / LinkedIn 主页文本）
- 目标岗位（决定简历侧重）

**做什么**：**调用 `document-skills:docx` skill**，走 `scenes/resume.md` 模板。

**调用方式**（在 SKILL.md 编排里写自然语言指路）：

```
调用 document-skills:docx skill，按 scenes/resume.md 模板生成简历 DOCX。
输入：{用户经历结构化数据} + {目标岗位}
模板选择：见下表
输出：{resume-<公司简称>-<岗位>-<YYYYMMDD>.docx}
```

**模板选择**（docx skill 的三套模板，按岗位类型选）：

| 模板 | 布局 | 适用岗位 |
|---|---|---|
| **A** | 左侧栏 + 右主体 | 通用 / 技术岗（推荐默认） |
| **B** | 深色顶 banner + 单栏 | 内容多 / 资深 |
| **C** | 蓝侧栏 + 竖线标题 | 国际 / 双语 / 外企 |

**默认**：模板 A。除非用户明说要「外企 / 国际化 / 双语」走 C，或「资深 / 多内容」走 B。

---

## 5. Step D · 导出 PDF / DOCX

**PDF 投递格式**：简历投递主流是 PDF（防止 HR 改格式）。**调用 `document-skills:pdf` skill**。

**调用方式**：

```
调用 document-skills:pdf skill 生成投递用 PDF 简历。
输入：{定制版 Markdown 简历}
模板路由：见下表
输出：{resume-<公司简称>-<岗位>-<YYYYMMDD>.pdf}，落到 jobs/resumes/
```

**模板路由**（pdf skill 的三套产线，按岗位性质选）：

| 模板 | brief 路径 | 引擎 | 适用岗位 |
|---|---|---|---|
| **ATS-safe / 企业求职**（默认） | `briefs/report.md` §Resume | ReportLab（Python） | 国内 HR 系统 / 大厂 / ATS 筛选 |
| **创意 / 设计** | `briefs/creative.md` | Playwright + HTML/CSS | 设计师 / 创意岗 / 前端 |
| **学术 / 带出版物** | `briefs/academic.md` + `references/resume-altacv.tex` | Tectonic/LaTeX | 研究员 / 博士 / 论文密集型 |

**默认**：`report.md` §Resume（ATS-safe ReportLab），覆盖 90% 投递场景。

**硬约束**（pdf skill 自身规则）：
- 目标 1 页（除非用户要详细版）。
- margin 1.5cm，正文 10–10.5pt，**最小字号 9pt 硬底线**。
- 分节用横线（HRFlowable），项目符号 `•`。
- 页面填充率 ≥ 85%（自适应调 spacing）。
- 字体：Times New Roman / SimHei（中英混排）。

**DOCX 编辑格式**：HR 偶尔要求 DOCX（便于编辑批注）。走 `document-skills:docx`。

---

## 6. 简历版本管理

**命名约定**：

```
jobs/resumes/
├── resume-base.md                          # 基础版（用户的"母简历"）
├── resume-acme-be-20260726.pdf             # Acme 后端 定制版 PDF
├── resume-acme-be-20260726.md              # 对应 Markdown 源
├── resume-globex-pm-20260727.docx          # Globex 产品 定制版 DOCX
└── resume-stark-ai-20260728.pdf            # Stark AI 定制版 PDF
```

**规则**：
- 每个目标公司/岗位一份定制版，**不覆盖母简历**。
- 定制版的 Markdown 源也保留（方便再改）。
- 母简历（`resume-base.md`）由用户首次提供，本 skill 后续基于它定制。
- 每份简历在 `jobs/INDEX.md` 对应行的「简历版本」列记录文件名。

---

## 7. 调用 skill 的标准话术

为减少歧义，本 skill 在编排时用以下固定话术指路：

**生成 ATS 投递 PDF**：
> 调用 `document-skills:pdf`，按 `briefs/report.md` §Resume（ReportLab ATS）生成 PDF，输入为 {path} 的 Markdown 简历，输出到 `jobs/resumes/resume-<...>.pdf`。

**生成创意岗 PDF**：
> 调用 `document-skills:pdf`，按 `briefs/creative.md`（Playwright HTML/CSS）生成 PDF。

**生成学术 PDF**：
> 调用 `document-skills:pdf`，按 `briefs/academic.md` 走 AltaCV LaTeX 模板，参考 `references/resume-altacv.tex`。

**生成 DOCX**：
> 调用 `document-skills:docx`，按 `scenes/resume.md` 模板 {A/B/C} 生成 DOCX，输出到 `jobs/resumes/resume-<...>.docx`。

---

## 8. 不做的事

- ❌ 不自己用 ReportLab / LaTeX / docx-js 写渲染代码（pdf/docx skill 已实现）。
- ❌ 不伪造简历内容（见 Step B 改写红线）。
- ❌ 不生成「万能简历」（每份都要针对 JD 定制，否则违反 SKILL.md 硬约束第 3 条）。
- ❌ 不做封面、目录、致谢（ATS 友好性要求）。
