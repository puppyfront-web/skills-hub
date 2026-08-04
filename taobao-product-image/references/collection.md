# 套图编排协议详解

本文件给 agent 走"套图"路径时参考。SKILL.md 已给核心流程，本文件讲协议细节和 agent 应该怎么解析。

## 三阶段协议

```
plan → (AskUserQuestion) → dispatch → summary
```

每个阶段都是一次独立的 `python3 collection.py --phase <phase>` Bash 调用。**agent 自己不要并发**——dispatch 阶段的并发在 skill 层用 `ThreadPoolExecutor` 完成。

---

## Phase 1: plan

```bash
python3 <skill>/scripts/collection.py \
  --phase plan \
  --image <path_or_url> \
  [--apparel] \
  [--category <c>] \
  [--scene "..."] [--selling-points "..."] \
  [--brand "..."] [--model-desc "..."]
```

### stdout 格式（agent 要解析）

```
# 套图方案（非服饰）

| # | 类型 | label | 需要参数 |
|---|------|-------|---------|
| 1 | `white-bg` | 白底主图 | scene, selling_points |
| 2 | `scene` | 场景图 | scene, selling_points |
...

**输出目录**: `/abs/path/to/taobao-images/YYYY-MM-DD/HHMMSS`

_state=/abs/path/to/collection-state.json
_total=4

STATUS: {"status":"plan_complete","state_file":"...","total":4,"bundle":"non-apparel","missing_required_args":[]}
```

### agent 要做的

1. 从 stdout grep `STATUS:` 行，解析 JSON 拿到：
   - `state_file`（后续 dispatch/summary 必传）
   - `missing_required_args`（缺哪些参数）
2. 用 `AskUserQuestion` 让用户：
   - **勾选要哪几张**（multiSelect）— 不勾选默认全要
   - **补齐缺失参数**（如果 `missing_required_args` 非空）
3. 用户确认后 → 进入 dispatch

### 默认套图方案

**非服饰**（4 张）：
1. `white-bg` 白底主图
2. `scene` 场景图
3. `selling-point` 卖点图
4. `aplus` A+详情图

**服饰**（4 张，需 `--apparel --category`）：
1. `model-wear` 模特试穿
2. `multi-model` 多模特展示
3. `flat-lay` 平铺
4. `white-bg` 白底主图

---

## Phase 2: dispatch

```bash
python3 <skill>/scripts/collection.py \
  --phase dispatch \
  --state <state_file> \
  --selected id1,id2,id3 \
  [--max-workers 4]
```

### `--selected` 说明

- 逗号分隔的 shot id 列表（如 `white-bg,scene,selling-point`）
- 省略 = 跑全部
- agent 把用户在 AskUserQuestion 勾选的 id 拼进来

### 内部行为

- skill 层用 `ThreadPoolExecutor(max_workers=4)` 并发跑 `generate_mod.generate_one()`
- 每张图独立任务，失败不影响其他
- 每张完成立即落 `task-result-<id>.json` 片段

### stdout 格式

```
{"status":"dispatch_complete","total":4,"succeeded":3,"failed":1}
```

**注意**：dispatch 阶段**不渲染图**，stdout 只有一行 status JSON。这是正常的，agent 不要以为出错了。

### exit code

- 0：全部成功
- 1：有失败（但仍写 task-result 片段，可进入 summary 查看详情）

无论成功失败，都继续走 summary。

---

## Phase 3: summary

```bash
python3 <skill>/scripts/collection.py \
  --phase summary \
  --state <state_file>
```

### stdout 格式

```
# 套图生成完成

- 成功: **3** / 4
- 失败: 1

## 成功的图片

- 白底主图 (`white-bg`)
  ![白底主图](/abs/path/to/white-bg-213144.png)
- 场景图 (`scene`)
  ![场景图](/abs/path/to/scene-213144.png)
...

## 失败的图片

- A+详情图 (`aplus`): 内容被拒绝: ...
  - error_type: `ContentRejectedError`

**Manifest**: `/abs/path/to/collection-manifest.json`

STATUS: {"status":"summary_complete","manifest":"...","succeeded":3,"failed":1}
```

### agent 要做的

1. **整段 markdown 原样转发给用户**（包括 `![]()` 行！）
   - 前端 markdown 渲染器会根据 `![](abs_path)` 渲染图片
   - **禁止**把 `![]()` 行剥掉，否则图渲染不出来
2. 从 STATUS 行拿 `manifest` 路径，告诉用户后续可以基于 manifest 做合并/重渲染
3. 失败的图如实告知用户原因，建议改进 prompt 重跑单张

### Manifest 结构

```json
{
  "version": 1,
  "created_at": "2026-07-21T21:31:44",
  "image": "/path/to/input.jpg",
  "bundle": "non-apparel",
  "total": 4,
  "succeeded": 3,
  "failed": 1,
  "assets": [
    {"id": "white-bg", "type": "white-bg", "label": "白底主图",
     "src": "/abs/path/to/white-bg-213144.png", "slot": 0},
    ...
  ]
}
```

`assets[]` 按 slot 顺序排列，方便后续工具读取。

---

## 常见问题排查

### plan 阶段总说"缺参数"
- agent 没把 `--scene` / `--selling-points` / `--category` 传进来
- 看一下 `STATUS.missing_required_args` 列表，里面明确说哪些 shot 缺哪个参数

### dispatch 后 stdout 空白
- 正常！dispatch 只在 stderr 打进度，stdout 只有最后 1 行 status
- 不要以为脚本卡住了

### summary 找不到图
- 看一下 state 文件里的 `out_dir`，确认 `task-result-*.json` 在不在
- 不在 → dispatch 阶段没真正跑（可能 state_file 路径错）

### 想做自定义套图组合（不要默认 4 张）
- `--selected white-bg,aplus` 只跑这 2 张
- 或在 plan 后用 AskUserQuestion 让用户勾选
