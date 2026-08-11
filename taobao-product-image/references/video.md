# 图生视频（CogVideoX）详解

本文件给 agent 处理"商品视频/图生视频"任务时参考。SKILL.md 已覆盖基本用法，本文件提供 prompt 技巧和模型选择。

## 三个模型怎么选

| model | 何时选 | 时长 | 价格 |
|-------|-------|------|------|
| `CogVideoX-Flash` | **默认**，大多数场景 | 5-10s | **免费** |
| `CogVideoX-2` | 高质量要求，电商广告片 | 5-10s | 按时长 |
| `CogVideoX-3` | 需要首尾帧控制 | 5-10s | 按时长 |

**默认推荐**：`CogVideoX-Flash`（免费且效果对电商展示足够）。

切换：`--model CogVideoX-2` 或 `--model CogVideoX-3`

---

## Prompt 技巧

CogVideoX 是图生视频，prompt 描述的是"镜头怎么动 / 商品怎么动"，不是"商品是什么"（商品已经在参考图里）。

### 好的 prompt 模板

**通用旋转展示**：
```
商品缓缓旋转 30 度，柔光扫过表面，揭示材质纹理
镜头：缓慢环绕，30 度旋转
```

**细节特写**：
```
镜头从远景推近至商品表面特写，凸显纹理细节
光影：从右上方有方向光，质感立体
```

**氛围演绎**：
```
商品静置于木质台面，环境光从左侧缓缓扫过，背景虚化
镜头：固定机位，仅有光影变化
```

**使用场景**（适合家居/数码）：
```
商品置于北欧客厅场景，阳光透过窗帘缓缓移动，光影在商品上流动
镜头：固定远景
```

**服饰动态**（适合服装）：
```
模特穿着本件服装缓缓走动，布料随动作自然摆动
镜头：跟随模特平移
```

### 默认 prompt（用户省略时）

```
Smooth cinematic product showcase: the product rotates slowly,
soft directional light sweeps across the surface revealing texture and details,
subtle depth-of-field shifts. Elegant, premium e-commerce video aesthetic.
Camera: gentle orbit, 30-degree rotation over the clip duration.
```

### 反例（要避免）

- ❌ "做一个酷炫的商品视频" — 太抽象
- ❌ "商品是蓝牙音箱，黑色，圆柱形" — 商品描述不需要（图里已有）
- ❌ "把视频做成抖音爆款风格" — 风格词对 CogVideoX 无效
- ❌ "时长 30 秒" — prompt 里写时长没用，用 `--duration` 参数

---

## 参数详解

### `--duration`（时长）
- 范围：5-10s（CogVideoX-Flash）/ 5-10s（CogVideoX-2/3）
- 默认 5s
- **建议**：电商展示 5-6s 足够，太长积分消耗大且容易出 bug

### `--size`（分辨率）
- `1920x1080`：默认横屏，适合淘宝详情页
- `1080x1920`：竖屏，适合抖音/小红书短视频
- `1280x720`：低成本试稿

### `--product-hint`
- 商品类别提示，让 prompt 更有针对性
- 如 `蓝牙音箱`、`连衣裙`、`口红`

### `--poll-timeout`
- 默认 600s（10 分钟）
- CogVideoX-Flash 通常 1-3 分钟，CogVideoX-2/3 可能 5-8 分钟
- 超时返回 TransientError，用户可重试

### `--poll-interval`
- 默认 10s
- 轮询频率，不需要改

---

## 失败处理

| 失败类型 | exit code | 处理 |
|---------|----------|------|
| ZHIPU_API_KEY 未配置 / 格式错误 | 2 | 引导用户配 key（见 SKILL.md） |
| 内容被拒（违规） | 3 | 改 prompt，去掉敏感词 |
| 网络超时 | 4 | 重试一次；仍失败告知用户晚点再试 |
| 轮询超时 | 4 | 增加 `--poll-timeout`，或换 Flash 模型 |

### 智谱 key 格式

智谱 key 格式是 `id.secret`（如 `abc123.xyz789`），JWT 签名需要这个结构。
- 如果用户报"格式错误" → 提示去 https://bigmodel.cn/console/usercenter/apikeys 重新复制完整 key
- 不要让用户自己拼 key

---

## 工作流建议（agent 视角）

用户说"给商品做个视频"时：

1. **确认参考图**：`--image` 是什么？如果用户只发了视频没发图，先抽帧（参考之前 `frame_3s.jpg` 的做法）
2. **确认场景**：问"视频用在哪儿"（淘宝详情页/抖音/小红书）→ 决定 `--size` 横竖
3. **写 prompt**：用上面的模板套，或让用户描述想要的镜头
4. **跑 Flash**：先用免费 Flash 跑一版看效果
5. **不满意再升**：升级到 CogVideoX-2 重跑
6. **解析输出**：`Saved: <abs_path>.mp4` → 在回复里贴 `<abs_path>` 路径，提醒用户去本地查看

### 一行命令示例

```bash
python3 ~/.agents/skills/taobao-product-image/scripts/video.py \
  --image ~/Downloads/product.jpg \
  --prompt "商品缓缓旋转 30 度，柔光扫过表面" \
  --model CogVideoX-Flash \
  --duration 5 \
  --size 1920x1080
```
