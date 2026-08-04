# Platform Access · 平台拓岗命令矩阵

本文件分为两部分：
1. **环境检查与配置**（对应 Step 2）— 各平台如何检测可用性、如何配置
2. **拓岗命令矩阵**（对应 Step 3）— 各平台如何拉岗位、字段解析、关键词过滤

---

## Part 1 · 环境检查与配置

### 1.1 一键检查（推荐）

```bash
bash job-seeker/scripts/check-env.sh
```

输出示例（实际以 doctor 为准）：

```
V2EX:       ✅ 可用（公开 API，零配置）
XiaoHongShu: ⚠️ 需配置（小红书 MCP 未注册）
LinkedIn:    ⚠️ 需配置（LinkedIn MCP 未注册）

配置指引：
  XiaoHongShu: docker run -d ... ; mcporter config add xiaohongshu ...
  LinkedIn:    pip install linkedin-scraper-mcp ; mcporter config add linkedin ...
```

### 1.2 手动检查（agent-reach doctor 原始输出）

agent-reach CLI 路径：`~/.local/bin/agent-reach`（pipx 安装，不在默认 PATH 时用绝对路径）。

```bash
~/.local/bin/agent-reach doctor
```

**关键识别词**（解析 doctor 输出）：

| 平台 | doctor 中状态关键字 |
|---|---|
| V2EX | `✅ V2EX 节点、主题与回复 — 公开 API 可用` |
| 小红书 | `配置后可用` 段落中的 `小红书笔记 — mcporter 已装但小红书 MCP 未配置` |
| LinkedIn | `配置后可用` 段落中的 `LinkedIn 职业社交 — mcporter 已装但 LinkedIn MCP 未配置` |

✅ = 可用；`[!]` = 部分可用（有警告）；`--` / `[X]` = 未配置或不可用。

### 1.3 平台配置步骤

#### V2EX（通常无需配置）

公开 JSON API，免登录、免密钥。若 `curl` 失败：
- 检查网络连通：`curl -sI https://www.v2ex.com/api/topics/hot.json`
- 大陆访问可能需要代理：`agent-reach configure proxy http://user:pass@ip:port`

#### LinkedIn（首次配置）

```bash
# 1. 安装上游 MCP server
pip install linkedin-scraper-mcp

# 2. 启动 server（默认监听 3000 端口）
# 按 https://github.com/stickerdaniel/linkedin-mcp-server 文档起服务

# 3. 注册到 mcporter
mcporter config add linkedin http://localhost:3000/mcp

# 4. 浏览器登录（linkedin-scraper-mcp 用本地 Chromium 持久化 session）
#    首次跑 server 时会弹浏览器，手动登录一次

# 5. 重新检查
bash job-seeker/scripts/check-env.sh
```

**注意事项**：
- agent-reach 的 `channels/linkedin.py` 是探测壳，**实际能调的工具以上游 MCP 暴露的为准**。配置后用 `mcporter list linkedin` 看真实工具清单。
- 若上游未提供 `search_jobs`，改用 jina.ai reader 读公开 JD 页（见 Part 2）。

#### 小红书（首次配置）

```bash
# 1. 启动小红书 MCP（docker，linux/amd64 镜像；ARM64 Mac 需自行从源码编译）
docker run -d --name xiaohongshu-mcp -p 18060:18060 --platform linux/amd64 xpzouying/xiaohongshu-mcp

# 2. 注册到 mcporter
mcporter config add xiaohongshu http://localhost:18060/mcp

# 3. 用 Chrome 插件 Cookie-Editor 导出小红书登录态，导入到 MCP
#    （详见 https://github.com/xpzouying/xiaohongshu-mcp）

# 4. 重新检查
bash job-seeker/scripts/check-env.sh
```

**注意事项**：小红书招聘内容稀少，多为兼职/外包/远程，**建议作为辅助数据源**，主战场放 V2EX 和 LinkedIn。

---

## Part 2 · 拓岗命令矩阵

### 2.1 V2EX（核心数据源，零依赖）

**限制**：V2EX 公开 API **没有搜索端点**，只能按节点拉 feed。招聘相关节点：`jobs`（酷工作）。

**拉取招聘节点 feed**：

```bash
curl -s "https://www.v2ex.com/api/topics/show.json?node_name=jobs" \
    -H "User-Agent: agent-reach/1.0" \
    -o /tmp/v2ex-jobs-raw.json
```

**字段解析**（每个 topic 对象）：

| 字段 | 含义 | 示例 |
|---|---|---|
| `id` | 帖子 ID | `1229845` |
| `title` | 帖子标题 | `[base 深圳 10k-20k 14~16 薪] Agent 开发工程师` |
| `url` | 帖子 URL | `https://www.v2ex.com/t/1229845` |
| `content` | 正文（Markdown，含 JD 详情） | `## 工作职责\n1. 参与 AI Agent 系统的设计...` |
| `created` | 创建时间戳（秒） | `1785035322` |
| `last_touched` | 最后回复时间戳 | `1785044390` |
| `replies` | 回复数 | `12` |
| `member.username` | 发帖人 | `Uzor` |
| `node.title` | 节点中文名 | `酷工作` |

**关键词过滤策略**（本地，无 API 搜索）：

```bash
# 用 jq 过滤标题+正文含关键词的帖子（示例：Python 后端 + 远程）
jq '[.[] | select(
    (.title + " " + .content) | test("(?i)(python|django|flask|fastapi)")
    and (.title + " " + .content) | test("(?i)(远程|remote|线上)")
)]' /tmp/v2ex-jobs-raw.json
```

或交给 LLM 做语义过滤（推荐，因为 V2EX 标题格式不统一，正则易漏）。

**就业类型识别**（从标题/正文正则）：

| 类型 | 关键词模式 |
|---|---|
| 远程 | `远程\|remote\|线上\|work from anywhere\|wfh` |
| 兼职 | `兼职\|part-time\|part time\|外包\|freelance` |
| 实习 | `实习\|intern\|internship` |
| 全职 | 默认（无上述关键词即为全职） |

**拉取帖子详情**（含评论区，可选）：

```bash
curl -s "https://www.v2ex.com/api/topics/show.json?id=<TOPIC_ID>" \
    -H "User-Agent: agent-reach/1.0"

curl -s "https://www.v2ex.com/api/replies/show.json?topic_id=<TOPIC_ID>&p=1" \
    -H "User-Agent: agent-reach/1.0"
```

评论区常含「已投」「薪资范围真实吗」「公司文化如何」等线索，可辅助 Step 4 评分。

### 2.2 LinkedIn（需配置后可用）

**调用方式优先级**：

1. **首选**：上游 MCP 直接调（如果暴露了 job 搜索工具）

```bash
# 先看上游实际暴露什么工具
mcporter list linkedin

# 假设暴露了 search_jobs（占位示例，实际工具名以 mcporter list 为准）
mcporter call 'linkedin.search_jobs(keywords: "Python 后端", location: "深圳", limit: 20)'

# 假设暴露了 search_people（用于找 HR/招聘官）
mcporter call 'linkedin.search_people(keyword: "company role recruiter", limit: 10)'
```

2. **fallback**：jina.ai reader 读公开 JD 页

```bash
# 已知 JD URL 时（如用户贴了一个 LinkedIn 岗位链接）
curl -s "https://r.jina.ai/https://linkedin.com/jobs/view/<JOB_ID>"

# 或者通过 Google 搜索 LinkedIn 岗位（Exa 配置后）
mcporter call 'exa.web_search_exa(query: "site:linkedin.com/jobs python 北京", numResults: 10)'
```

**字段映射**（上游 MCP 返回结构不固定，按实际为准）：

统一映射到内部结构 `{来源: linkedin, 标题, URL, 公司, JD 摘要, 发布时间}`。字段缺失标 `[待补充]`。

**节流**：
- LinkedIn 反爬严，**每次会话最多调用 5 次** MCP 搜索；超过强制 sleep 60s。
- jina.ai reader 每分钟上限 10 次。
- 任何 `429` / `auth_wall` / `challenge` 立即停止，提示用户重新登录。

### 2.3 Hacker News "Ask HN: Who is hiring?" 月帖（🎯 远程岗位密度最高，强烈推荐）

每月 1 号发布，每个评论是一条招聘，HR 直接发布（无中介），格式规范（公司 | 岗位 | 地点 | 薪资），远程岗密度全网最高。

**三步拉取法**：

```bash
# 1. jina 搜 HN Algolia 找月帖 story ID（每月不同）
curl -s "https://r.jina.ai/https://hn.algolia.com/?q=Ask+HN+Who+is+hiring+(<月份>+%202026)&typ=story" \
    -H "User-Agent: agent-reach/1.0"
# 例如 2026-07 月帖 story_id = 48747976

# 2. Firebase 拿 story 元数据（含 kids: 所有评论 ID 数组）
curl -s "https://hacker-news.firebaseio.com/v0/item/<story_id>.json"

# 3. Algolia 批量拉评论内容（每页 50，分页拉全部）
for p in 0 1 2 3 4 5 6 7 8; do
  curl -s "https://hn.algolia.com/api/v1/search?tags=comment,story_<story_id>&hitsPerPage=50&page=$p" \
    -o hn-page-$p.json
done
jq -c '.hits[] | {author, text: .comment_text, id: .objectID}' hn-page-*.json > hn-all-comments.jsonl
```

**字段映射**（每条评论即一个岗位）：
- `text`（原文）→ 解析首行 `[公司] | [岗位] | [地点] | [薪资]`
- `objectID` → 拼接 URL `https://news.ycombinator.com/item?id=<objectID>`
- `author` → HR 用户名

**关键词过滤**（jq 或 python）：
```bash
python3 -c "
import json, re
KW = re.compile(r'(?i)\b(' + '|'.join(re.escape(k) for k in config['target']['job_keywords']) + r')\b')
# 排除关键词
EXCLUDE = re.compile(r'(?i)\b(' + '|'.join(re.escape(k) for k in config['target']['exclude_keywords']) + r')\b')
# 排除地域
EXCLUDE_REGION = re.compile(r'(?i)\b(' + '|'.join(re.escape(r) for r in config['target']['exclude_regions']) + r')\b')

# 关键词预设示例（不同岗位族）：
#   技术岗:    ['AI', 'LLM', 'React', 'TypeScript', 'frontend', 'full-stack', ...]
#   产品经理:  ['product manager', 'PM', '产品经理', 'Head of Product', 'AI 产品', ...]
#   设计师:    ['UI designer', 'UX designer', 'product designer', '视觉设计', '交互设计', ...]
#   运营/市场: ['运营', 'growth', 'marketing', '内容运营', 'community manager', ...] （岗位较少）
# 完整预设见 config.example.yaml
# 排除求职帖（含 I'm a / my resume）
SEEKER = re.compile(r'(?i)\b(I am a|I.m a|my resume|hire me|available for)\b')
for line in open('hn-all-comments.jsonl'):
    c = json.loads(line)
    text = c.get('text','')
    if KW.search(text) and not SEEKER.search(text[:300]):
        print(c['objectID'], text[:200].replace(chr(10),' '))
"
```

**地理筛选（对中国求职者关键）**：
- ✅ 可投：`Worldwide` / `Global` / `Remote (Global)` / `UTC+8` / `CET` / `Asia` / `Anywhere`
- ⚠️ 需问：`Remote (US)` / `Remote (North America)` —— 投时主动问 "open to international contractor?"
- ❌ 不投：`US only (no visa sponsorship)` / `Onsite SF/NYC`

**节流**：HN Algolia 无强反爬，但建议 `sleep 0.3` 避免被限速。

**重要**：HN 月帖发布后**前 3 天密度最高**，每月 1-3 号优先拓岗。

---

### 2.4 电鸭社区 eleduck.com（国内远程第一站）

国内远程岗位聚合，含全职远程、外包零活、兼职。Flutter/小程序/前端岗密度高。

**拉取列表**：
```bash
# 远程招聘分类（categories/5）
curl -s "https://r.jina.ai/https://eleduck.com/categories/5" \
    -H "User-Agent: agent-reach/1.0" -o eleduck-list.md

# 按标签过滤（AI工程师 / 全职远程）
curl -s "https://r.jina.ai/https://eleduck.com/jobs-channel?recruitment_type=jd&tags=0-0-0-162" \
    -H "User-Agent: agent-reach/1.0"
```

**拉取帖子详情**：
```bash
# 从列表解析出帖子 slug（如 W9fmVR），拉详情
curl -s "https://r.jina.ai/https://eleduck.com/posts/<slug>" \
    -H "User-Agent: agent-reach/1.0"
```

**特点**：
- 国内外包/创业项目为主，薪资面议多
- 含**短期项目**（1-3 个月）、**众包接单群**（适合副业现金流）
- HR 多为微信/手机联系（PII），不要把联系方式写入仓库

**红旗**：电鸭偶有"用户举报涉海外灰产"警示帖，命中即跳过。

---

### 2.5 WeWorkRemotely（成熟远程招聘站）

成熟公司为主（Stripe/Coinbase/Twilio 等大厂），结构化好。

**拉取分类页**：
```bash
for cat in front-end-programming full-stack-programming back-end-programming; do
  curl -s "https://r.jina.ai/https://weworkremotely.com/categories/remote-${cat}-jobs" \
    -H "User-Agent: agent-reach/1.0" -o wwr-${cat}.txt
done

# 提取岗位（链接文本含完整信息：标题+公司+地区+时间）
grep -oE '\[[^]]{5,150}\]\(https://weworkremotely\.com/remote-jobs/[^)]+\)' wwr-*.txt | sort -u
```

**字段映射**：链接文本格式 `岗位名 Nd 公司名 地点 Top 100 Full-Time Anywhere`，正则切分。

**限制**：WWR 不提供 JSON API，只能 HTML 解析；jina reader 是当前最可靠方式。

---

### 2.6 Reddit（⚠️ 反爬不可用，已知限制）

Reddit 的 `.json` 端点对未认证请求返回 HTML 而非 JSON（即使 old.reddit.com 也被挡）。
- `r/remotework`、`r/programminghire`、`r/remotejs` 当前无法用 agent-reach 拉取。
- **未来若 agent-reach 配置 Reddit MCP 或 proxy**，可恢复使用。
- 不投入工程绕过，遵守平台协议。

---

### 2.7 小红书（需配置后可用）

**搜索招聘笔记**：

```bash
# 关键词组合：城市 + 岗位 + 招聘词
mcporter call 'xiaohongshu.search_feeds(keyword: "深圳 招聘 Python 远程")' \
    | agent-reach format xhs
```

**`agent-reach format xhs` 输出字段**（清洗后）：

| 字段 | 含义 |
|---|---|
| `title` / `desc` | 笔记标题 / 描述（含 JD） |
| `user.nickname` | 发布者（招聘方） |
| `time` | 发布时间 |
| `liked_count` / `collected_count` / `comment_count` | 互动数（用于判断热度） |
| `tags[]` | 标签（常含岗位/城市/经验年限） |
| `comments[]` | 评论区（常含「已投」「薪资？」等线索） |

**注意**：小红书 JD 多为招聘号引流，**邮箱/微信常在评论区或私信**，公开展示信息不全。Step 4 评分时「平台可信度」维度对小红书天然打折。

**节流**：与 LinkedIn 同理，每次会话 ≤ 5 次搜索。

---

## Part 3 · 统一候选列表数据结构

三个平台拓岗完成后，合并成统一格式（用于 Step 4 评分）：

```json
[
  {
    "source": "v2ex | linkedin | xiaohongshu",
    "id": "原始平台帖子/笔记 ID",
    "title": "标题",
    "url": "URL",
    "company": "公司名（解析不出则 null）",
    "location": "地点",
    "employment_type": "full_time | part_time | remote | intern",
    "salary_range": "原始薪资字符串",
    "jd_summary": "JD 摘要（前 500 字）",
    "jd_full_path": "/tmp/job-seeker-jd-<id>.md（完整 JD 落盘路径）",
    "posted_at": "ISO8601 时间",
    "raw": { /* 原始字段，供后续复算 */ }
  }
]
```

落盘到 `/tmp/job-seeker-candidates-<YYYYMMDD>.json`，传给 Step 4。

---

## Part 4 · 常见问题

**Q: V2EX 标题格式不统一，怎么解析薪资/地点/类型？**
A: V2EX 帖子标题常带 `[base 城市 薪资]` 这种方括号前缀，但格式不严格。建议用 LLM 做结构化抽取（给 LLM 原始 title + content，让它输出 `{company, location, employment_type, salary_range}` JSON），不要硬写正则。

**Q: LinkedIn 上游 MCP 实际暴露什么工具？**
A: 不确定。`agent-reach` 文档假设有 `search_jobs`，但代码里没实现。**配置后必须先跑 `mcporter list linkedin` 看真实工具清单**，再决定调用方式。若工具不足，回退 jina.ai reader。

**Q: 小红书 JD 经常信息不全怎么办？**
A: 把它当作「线索」而不是「成品」。Step 4 评分时直接打 C 档（备选），告知用户「小红书招聘号，需主动联系拿完整 JD」。
