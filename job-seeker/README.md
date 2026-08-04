# 求职助手 · Job Seeker Skill

> 给**软件工程师 / 产品经理 / 设计师**的求职 agent：拓岗 → 匹配评分 → 简历定制 → 半自动投递 → 追踪，一条龙。
> 在 ZCode 客户端内手动触发，覆盖全职/兼职/远程/实习。

## 这是什么

一个 ZCode Skill，把求职从「手动刷招聘网站」变成 agent 编排的自动化流程。你说一句话，它帮你：

1. **拓岗**：从 V2EX / Hacker News Who's Hiring / WeWorkRemotely / 电鸭社区 / 小红书 / LinkedIn 拉 100+ 远程岗位
2. **匹配评分**：六维模型（技能 / 年限 / 行业 / 地域 / 薪资 / 可信度）打分，分 A/B/C/D 档
3. **简历定制**：针对每个 A/B 档岗位重写措辞、突出匹配点、生成 PDF / DOCX
4. **半自动投递**：生成「收件人 + 主题 + 正文 + 附件」就位的 `.eml`，你双击即发
5. **追踪归档**：维护 `INDEX.md` 求职主索引 + 每日报告

## 适合谁

### ✅ 效果最好

- **软件工程师**：前端 / 后端 / 全栈 / AI 应用 / 移动 / DevOps / QA
- **产品经理**：ToB / ToC / 数据 / 增长 / AI 产品
- **设计师**：UI / UX / 视觉 / 产品设计师 / 交互

这三种岗位在 HN / WWR / RemoteOK / 电鸭 都有足够岗位密度。

### 🟡 效果有限（能用但岗位少）

- 运营 / 市场 / 内容 / 增长黑客 / 销售
- 这些在 HN 几乎没有，主要靠 LinkedIn / 小红书（需配置）

### ❌ 不建议使用（覆盖不到）

- 金融 / 法律 / 医疗临床 / 教育教学
- 传统行业（制造 / 建筑 / 能源 / 房地产）
- 这些走专业招聘渠道（猎聘 / 行业招聘网 / boss 直聘），本 skill 没覆盖

如果你是 🟡/❌ 类岗位，建议改 `config.yaml` 关键词试试，但预期要降低。

## 5 分钟快速上手

### 1. 安装 skill

如果你已经有 skills-hub 仓库：

```bash
git clone <skills-hub repo>  # 或你已经有了
```

ZCode 客户端会自动发现 `job-seeker/SKILL.md`。

### 2. 配置你的信息

```bash
cd job-seeker
cp config.example.yaml config.yaml
# 编辑 config.yaml，填你的：
#   - identity: 姓名 / 邮箱 / 电话 / GitHub
#   - target: 求职关键词 / 排除词 / 地域偏好
#   - resume_focus: 你的强项弱项
```

### 3. 准备母简历

把你的「母简历」（最全的版本）放到 skill 能找到的地方：

```bash
# 中文母简历（Markdown）
# /path/to/your/resume-base-zh.md
# 英文母简历（Markdown）
# /path/to/your/resume-base-en.md

# 然后在 config.yaml 里填路径
```

### 4. 在 ZCode 里触发

打开 ZCode，说一句话：

```
帮我找 React 前端岗，国内远程优先
```

或：

```
分析这份简历 + 这个 JD：[贴简历] [贴 JD]，告诉我够不够格，怎么改
```

Skill 会自动执行六步工作流。

## 工作流（6 步）

| 步 | 做什么 | 产出 |
|---|---|---|
| 0 | 配置检查（读 config.yaml） | - |
| 1 | 需求澄清（用配置默认值，本次可覆盖） | - |
| 2 | 环境前置检查（`scripts/check-env.sh`） | 平台可用性报告 |
| 3 | 拓岗（V2EX / HN / WWR / 电鸭） | 候选岗位 JSON |
| 4 | 六维匹配评分 | A/B/C/D 档清单 |
| 5 | 简历定制（调 pdf/docx skill） | 定制版 PDF/DOCX |
| 6 | 投递归档（生成 .eml） | INDEX.md 更新 |

详细规则见 `SKILL.md`，细则见 `references/*.md`。

## 文件结构

```
job-seeker/
├── SKILL.md                    # 主入口
├── README.md                   # 你正在看的
├── config.example.yaml         # 配置模板（复制为 config.yaml 改）
├── agents/openai.yaml          # ZCode client 元数据
├── references/
│   ├── platform-access.md      # 各平台拓岗命令矩阵
│   ├── jd-match-scoring.md     # 六维评分细则
│   ├── resume-workflow.md      # 简历四步映射
│   └── apply-playbook.md       # 投递 SOP + 节流
└── scripts/
    └── check-env.sh            # 平台可用性检查
```

**注意**：你的个人数据（INDEX.md、简历、eml）**不放在 skill 目录**，而是放在 skill 执行时的**工作目录的 `jobs/` 子目录**。这样：
- 升级 skill 不会覆盖你的数据
- 多人/多账户隔离（不同用户各跑各的 jobs/）

## 拓岗渠道覆盖

| 平台 | 状态 | 用法 | 命令 |
|---|---|---|---|
| **V2EX** | ✅ 零配置 | curl 公开 API | `curl https://www.v2ex.com/api/topics/show.json?node_name=jobs` |
| **Hacker News 月帖** | ✅ 零配置 | Firebase + Algolia API | 见 `references/platform-access.md` §2.3 |
| **WeWorkRemotely** | ✅ 零配置 | jina reader + 正则 | 见 §2.5 |
| **电鸭社区** | ✅ 零配置 | jina reader | 见 §2.4 |
| **小红书** | ⚠️ 需配置 | mcporter + cookie | 见 §2.7 |
| **LinkedIn** | ⚠️ 需配置 | linkedin-scraper-mcp | 见 §1.3 |
| **Reddit** | ❌ 反爬不可用 | - | 已知限制 |

## 能做什么 / 不能做什么

### ✅ 能做

- 拉 100+ 真实招聘帖并按你的关键词/地域过滤
- 对每条岗位做结构化匹配评分（含理由）
- 针对每个 A/B 档岗位定制简历 + 导出 ATS PDF
- 生成发件就绪的 `.eml`（双击 Mail.app 即发）
- 维护求职主索引 + 每日报告

### ❌ 不能做

- **不能代你点"发送"**：邮件必须你亲自发出（这是你的求职信誉，AI 不应代发）
- **不能绕过 CAPTCHA / 平台反爬**：Reddit 等被挡的渠道不强绕，遵守平台协议
- **不能伪造简历**：改写优化可以调整措辞和顺序，但严禁虚增经历/学历/年限
- **不能自动投递 boss 直聘**：未覆盖（反爬代价过高）

## 投递礼仪（重要）

- **不在邮件正文强调"我要远程/全职"** —— 这是面试阶段争取的，不是投递时声明的
- **不批量同质化投递** —— 每封邮件为该家单独定制
- **LinkedIn 全自动投递** —— 默认节流（日 ≤25、批 ≤5、间隔 60-180s），且明确告知平台协议风险

## 已知限制

1. **boss 直聘不覆盖**（反爬 + 协议风险）
2. **Reddit 渠道不可用**（反爬）
3. **LinkedIn 拓岗需要先配 linkedin-scraper-mcp**
4. **Phase 3（LinkedIn 自动投递模块）尚未实现**——当前只支持半自动（生成 .eml 你发）

## 升级路径

- **v0.1（当前）**：拓岗 + 评分 + 简历定制 + .eml 半自动投递
- v0.2：Phase 3 linkedin-apply 模块（全自动投递 + 登录态缓存）
- v0.3：定时调度（每周自动拓岗 + 报告）
- v0.4：面试准备 + 谈薪辅导

## 设计文档

- 设计规格：`../docs/superpowers/specs/2026-07-26-ai-job-expert-design.md`
- 实施计划：`../docs/superpowers/plans/2026-07-26-ai-job-expert.md`

## 反馈与贡献

发现问题或想加渠道，欢迎提 issue / PR。

---

*Skill 版本：v0.1 · 适用：软件工程师/产品经理/设计师 · 触发方式：手动*
