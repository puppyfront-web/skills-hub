#!/usr/bin/env python3
"""
批改结果卡片渲染器。

把 batch_report 渲染成两张图（用 Pillow）：
1. 总览卡（overview.png）—— 老师一眼看全貌，可转发家长群
2. 错题详情卡（wrong_details.png）—— 每道错题的题面/作答/错因/解析

设计原则（参考竞品小猿/作业帮）：
- 总览卡一张图看清：对错统计、进度条、错题速览、薄弱点
- 颜色：绿(对)/红(错)/黄(无法判定)，视觉化优先于文字
- 家长群版（share=true）：只留对错和鼓励语，不暴露错题细节
"""

import os
from pathlib import Path
from datetime import datetime

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


# ---------------------------------------------------------------------------
# 字体（macOS 中文字体，按优先级回退）
# ---------------------------------------------------------------------------

_FONT_PATHS = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    """加载指定大小的中文字体。"""
    for p in _FONT_PATHS:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# 颜色（参考竞品配色，温和不刺眼）
# ---------------------------------------------------------------------------

class C:
    """调色板。"""
    BG = (255, 255, 255)                # 卡片白底
    BG_HEADER = (102, 126, 234)         # 顶部紫色渐变（主色）
    BG_HEADER_END = (118, 75, 162)
    TEXT = (33, 33, 33)                 # 主文字深灰
    TEXT_LIGHT = (117, 117, 117)        # 次要文字
    TEXT_WHITE = (255, 255, 255)
    GREEN = (76, 175, 80)               # 对
    RED = (244, 67, 54)                 # 错
    YELLOW = (255, 152, 0)              # 无法判定
    BLUE = (25, 118, 210)               # 知识点标签
    BG_WRONG = (254, 242, 242)          # 错题卡淡红底
    BG_RIGHT = (243, 246, 244)          # 对题卡淡绿底
    BG_UNDETERMINED = (255, 251, 231)   # 无法判定淡黄底
    BG_DETAIL = (250, 250, 250)         # 详情灰底
    BORDER = (224, 224, 224)
    ORANGE_LIGHT = (255, 243, 224)      # 薄弱点淡橙底
    ORANGE = (230, 81, 0)


# ---------------------------------------------------------------------------
# 辅助：绘制圆角矩形、文字换行
# ---------------------------------------------------------------------------

def _rounded_rect(draw, xy, radius, fill=None, outline=None, width=1):
    """画圆角矩形（Pillow 老版本兼容）。"""
    try:
        draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)
    except AttributeError:
        # Pillow < 8.2 没有 rounded_rectangle
        x0, y0, x1, y1 = xy
        draw.rectangle([x0+radius, y0, x1-radius, y1], fill=fill)
        draw.rectangle([x0, y0+radius, x1, y1-radius], fill=fill)
        for cx, cy in [(x0+radius, y0+radius), (x1-radius, y0+radius),
                       (x0+radius, y1-radius), (x1-radius, y1-radius)]:
            draw.ellipse([cx-radius, cy-radius, cx+radius, cy+radius], fill=fill)


def _text_width(font, text):
    """测量文字宽度。"""
    try:
        return font.getlength(text)
    except AttributeError:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0]


def _wrap_text(text, font, max_width):
    """中文文字换行（按字符断行，中文不像英文有词边界）。"""
    if not text:
        return []
    lines = []
    current = ""
    for ch in str(text):
        if ch == "\n":
            lines.append(current)
            current = ""
            continue
        test = current + ch
        if _text_width(font, test) > max_width and current:
            lines.append(current)
            current = ch
        else:
            current = test
    if current:
        lines.append(current)
    return lines


# ---------------------------------------------------------------------------
# 总览卡
# ---------------------------------------------------------------------------

def render_overview_card(batch_report: dict, output_path: str,
                         share: bool = False) -> str:
    """渲染总览卡（主卡）。

    share=True 生成家长群版（只留对错和鼓励语，不暴露错题细节）。
    返回图片路径。
    """
    if not HAS_PIL:
        raise RuntimeError("Pillow 未安装，无法生成卡片")

    s = batch_report.get("summary", {})
    student = batch_report.get("student_name") or "学生"
    date = (batch_report.get("date") or datetime.now().isoformat())[:10]
    total = s.get("total", 0)
    correct = s.get("correct", 0)
    wrong = s.get("wrong", 0)
    undetermined = s.get("undetermined", 0)
    accuracy = int(s.get("accuracy", 0) * 100)
    src = batch_report.get("recognition_source", "vision")
    questions = batch_report.get("questions", [])
    wrong_qs = [q for q in questions if q.get("is_correct") is False]

    # 卡片尺寸
    W = 800
    # 家长群版更简洁，错题详情不显示，高度更小
    if share:
        H = 480
    else:
        # 高度根据错题数动态调整
        H = 560 + max(0, len(wrong_qs) - 2) * 110
        if not wrong_qs:
            H = 460  # 全对场景更紧凑
        H += 80 if batch_report.get("weak_points") else 0
        if undetermined:
            H += 60

    img = Image.new("RGB", (W, H), C.BG)
    draw = ImageDraw.Draw(img)

    y = 0

    # === 顶部紫色横幅 ===
    banner_h = 140
    # 用渐变模拟（画多条线）
    for i in range(banner_h):
        ratio = i / banner_h
        r = int(C.BG_HEADER[0] * (1-ratio) + C.BG_HEADER_END[0] * ratio)
        g = int(C.BG_HEADER[1] * (1-ratio) + C.BG_HEADER_END[1] * ratio)
        b = int(C.BG_HEADER[2] * (1-ratio) + C.BG_HEADER_END[2] * ratio)
        draw.line([(0, i), (W, i)], fill=(r, g, b))

    # 标题
    f_title = _font(28)
    f_sub = _font(16)
    draw.text((40, 32), "📐 数学作业批改", font=f_title, fill=C.TEXT_WHITE)
    draw.text((40, 75), f"{student}  ·  {date}", font=f_sub, fill=(220, 220, 240))
    if src == "ocr_fallback":
        draw.text((40, 100), "(OCR 兜底识别，建议校对)", font=_font(13),
                  fill=(255, 235, 130))

    y = banner_h + 20

    # === 统计区（大数字 + 加大标签）===
    f_big = _font(48)
    f_num_label = _font(16)  # 从14加大到16

    stats = [
        (str(total), "总题数", C.TEXT),
        (str(correct), "做对", C.GREEN),
        (str(wrong), "做错", C.RED),
        (f"{accuracy}%", "正确率", C.BLUE),
    ]
    col_w = (W - 80) // 4
    for i, (num, label, color) in enumerate(stats):
        cx = 40 + col_w * i + col_w // 2
        # 数字居中
        nw = _text_width(f_big, num)
        draw.text((cx - nw//2, y), num, font=f_big, fill=color)
        # 标签（加大、加粗效果用深色）
        lw = _text_width(f_num_label, label)
        draw.text((cx - lw//2, y + 62), label, font=f_num_label, fill=C.TEXT)

    y += 105

    # === 进度条 ===
    if total > 0:
        bar_x, bar_y = 40, y
        bar_w = W - 80
        bar_h = 16
        # 背景
        _rounded_rect(draw, (bar_x, bar_y, bar_x+bar_w, bar_y+bar_h), 8, fill=(235, 238, 245))
        # 绿色填充（对的）
        green_w = int(bar_w * correct / total)
        if green_w > 0:
            _rounded_rect(draw, (bar_x, bar_y, bar_x+green_w, bar_y+bar_h), 8, fill=C.GREEN)
        # 黄色填充（无法判定）
        yellow_w = int(bar_w * undetermined / total)
        if yellow_w > 0:
            _rounded_rect(draw, (bar_x+green_w, bar_y, bar_x+green_w+yellow_w, bar_y+bar_h),
                          8, fill=C.YELLOW)
        y += bar_h + 20

    # === 家长群版到此为止，加鼓励语 ===
    if share:
        # 鼓励语
        if accuracy >= 90:
            msg = "🌟 表现很棒，继续保持！"
        elif accuracy >= 70:
            msg = "💪 整体不错，注意错题哦～"
        else:
            msg = "📚 加油！多练错题会更好"
        f_msg = _font(20)
        mw = _text_width(f_msg, msg)
        draw.text((W//2 - mw//2, y + 20), msg, font=f_msg, fill=C.TEXT)
        # 底部小字
        f_foot = _font(12)
        foot = "由 📐 数学作业批改助手 生成"
        fw = _text_width(f_foot, foot)
        draw.text((W//2 - fw//2, H - 40), foot, font=f_foot, fill=C.TEXT_LIGHT)
        img.save(output_path, "PNG", optimize=True)
        return output_path

    # === 错题速览（只列错题，不展开详情）===
    if wrong_qs:
        f_sec = _font(18)
        draw.text((40, y), f"❌ 错题速览（{len(wrong_qs)}道）", font=f_sec, fill=C.TEXT)
        y += 36

        f_q = _font(14)
        f_ans = _font(13)
        card_w = W - 80
        for q in wrong_qs[:5]:  # 最多展示5道
            qno = q.get("question_no", "?")
            stem = (q.get("stem") or "")[:30]
            sa = (q.get("student_answer") or "未作答")
            ca = q.get("correct_answer") or "?"
            # 错题卡背景
            card_h = 64
            _rounded_rect(draw, (40, y, 40+card_w, y+card_h), 10,
                          fill=C.BG_WRONG)
            # 左侧红色竖条
            draw.rectangle([40, y, 44, y+card_h], fill=C.RED)
            # 题号 + 题面
            draw.text((58, y+8), f"第{qno}题  {stem}", font=f_q, fill=C.TEXT)
            # 答案对照
            ans_text = f"学生：{sa}  →  正确：{ca}"
            draw.text((58, y+32), ans_text, font=f_ans, fill=C.TEXT_LIGHT)
            y += card_h + 10
        if len(wrong_qs) > 5:
            draw.text((40, y), f"...还有 {len(wrong_qs)-5} 道错题", font=f_ans, fill=C.TEXT_LIGHT)
            y += 24

    # === 无法判定提示（放在错题之后，不分割统计区）===
    if undetermined:
        f_warn = _font(14)
        warn_h = 44
        _rounded_rect(draw, (40, y, W-40, y+warn_h), 8, fill=C.BG_UNDETERMINED)
        draw.text((52, y+13), f"❓ {undetermined} 题无法自动判定，建议老师手动核对",
                  font=f_warn, fill=C.ORANGE)
        y += warn_h + 16

    # === 全对场景的鼓励（放在最后，不抢统计区视觉）===
    if not wrong_qs and undetermined == 0:
        f_perfect = _font(22)
        msg = "🎉 全部做对，太棒了！"
        mw = _text_width(f_perfect, msg)
        draw.text((W//2 - mw//2, y + 10), msg, font=f_perfect, fill=C.GREEN)
        y += 50

    # === 薄弱知识点 ===
    wps = batch_report.get("weak_points", [])
    if wps:
        y += 10
        f_sec = _font(16)
        draw.text((40, y), "🎯 薄弱知识点", font=f_sec, fill=C.TEXT)
        y += 30
        weak_h = 36 + len(wps[:3]) * 24
        _rounded_rect(draw, (40, y, W-40, y+weak_h), 10, fill=C.ORANGE_LIGHT)
        f_wp = _font(13)
        for i, wp in enumerate(wps[:3]):
            rate = int(wp.get("wrong_rate", 0) * 100)
            text = f"• {wp['knowledge_point']}：错 {wp['wrong']}/{wp['total']}（{rate}%）"
            draw.text((52, y+10+i*24), text, font=f_wp, fill=C.ORANGE)
        y += weak_h + 16

    # === 底部 ===
    y += 10
    f_foot = _font(12)
    foot = "📐 数学作业批改助手  ·  发送「出同类题」生成练习"
    fw = _text_width(f_foot, foot)
    draw.text((W//2 - fw//2, H - 36), foot, font=f_foot, fill=C.TEXT_LIGHT)

    img.save(output_path, "PNG", optimize=True)
    return output_path


# ---------------------------------------------------------------------------
# 错题详情卡
# ---------------------------------------------------------------------------

def render_wrong_details_card(batch_report: dict, output_path: str) -> str:
    """渲染错题详情卡：每道错题的题面/作答/错因/点评/解析。"""
    if not HAS_PIL:
        raise RuntimeError("Pillow 未安装")

    questions = batch_report.get("questions", [])
    wrong_qs = [q for q in questions if q.get("is_correct") is False]
    undetermined_qs = [q for q in questions if q.get("is_correct") is None]
    show_qs = wrong_qs + undetermined_qs

    if not show_qs:
        # 没有错题，返回总览卡
        return render_overview_card(batch_report, output_path)

    W = 800
    # 动态高度：每道错题约 240-320 像素
    H = 100  # 顶部
    for q in show_qs:
        comment_lines = len(_wrap_text(q.get("comment") or "", _font(13), W - 120))
        solution_lines = len(_wrap_text(q.get("solution") or "", _font(13), W - 120))
        H += 100 + comment_lines * 20 + solution_lines * 20 + 20
    H += 40  # 底部

    img = Image.new("RGB", (W, H), C.BG)
    draw = ImageDraw.Draw(img)

    # 顶部
    f_title = _font(24)
    draw.text((40, 36), "📝 错题详情与解析", font=f_title, fill=C.TEXT)
    f_sub = _font(14)
    student = batch_report.get("student_name") or "学生"
    draw.text((40, 70), f"{student}的作业  ·  共 {len(show_qs)} 道错题/无法判定",
              font=f_sub, fill=C.TEXT_LIGHT)

    y = 110
    f_q = _font(16)
    f_label = _font(13)
    f_body = _font(13)
    card_w = W - 80

    for idx, q in enumerate(show_qs):
        is_undetermined = q.get("is_correct") is None
        qno = q.get("question_no", "?")
        stem = q.get("stem") or ""
        sa = q.get("student_answer") or "未作答"
        ca = q.get("correct_answer") or "无法确定"

        # 卡片背景色
        bg = C.BG_UNDETERMINED if is_undetermined else C.BG_WRONG
        border = C.YELLOW if is_undetermined else C.RED

        # 先画卡片框（高度待定，先画头部，详情动态往下）
        card_top = y
        # 题面行
        _rounded_rect(draw, (40, y, 40+card_w, y+44), 10, fill=bg)
        draw.rectangle([40, y, 44, y+44], fill=border)
        prefix = "❓" if is_undetermined else "❌"
        draw.text((58, y+12), f"{prefix} 第{qno}题  {stem[:35]}", font=f_q, fill=C.TEXT)
        y += 52

        # 详情区
        detail_lines = [
            ("学生作答", str(sa), C.RED if not is_undetermined else C.TEXT),
            ("正确答案", str(ca), C.GREEN if not is_undetermined else C.TEXT),
        ]
        # 错因（仅错题）
        if not is_undetermined and q.get("error_type"):
            from explain import error_type_label
            detail_lines.append(("错因", error_type_label(q["error_type"]), C.ORANGE))
        # 知识点
        kps = q.get("knowledge_points") or []
        if kps:
            detail_lines.append(("知识点", "、".join(kps[:3]), C.BLUE))

        for label, val, color in detail_lines:
            draw.text((58, y), label, font=f_label, fill=C.TEXT_LIGHT)
            draw.text((160, y), val, font=f_body, fill=color)
            y += 22

        # 点评（错题才有）
        if q.get("comment") and not is_undetermined:
            y += 4
            draw.text((58, y), "💬 点评", font=f_label, fill=C.TEXT_LIGHT)
            y += 20
            for line in _wrap_text(q["comment"], f_body, W - 120):
                draw.text((58, y), line, font=f_body, fill=C.TEXT)
                y += 20

        # 解析
        if q.get("solution"):
            y += 4
            draw.text((58, y), "📖 解析", font=f_label, fill=C.TEXT_LIGHT)
            y += 20
            sol_lines = _wrap_text(q["solution"], f_body, W - 120)
            # 解析最多 6 行，超出省略
            for line in sol_lines[:6]:
                draw.text((58, y), line, font=f_body, fill=C.TEXT)
                y += 20
            if len(sol_lines) > 6:
                draw.text((58, y), "...", font=f_body, fill=C.TEXT_LIGHT)
                y += 20

        # 无法判定的说明
        if is_undetermined:
            y += 4
            for line in _wrap_text("AI 未能确定标准答案（可能题面条件不全），请老师手动核对。",
                                   f_body, W - 120):
                draw.text((58, y), line, font=f_body, fill=C.ORANGE)
                y += 20

        y += 20  # 卡片间距

    img.save(output_path, "PNG", optimize=True)
    return output_path


# ---------------------------------------------------------------------------
# 衍生题卡片
# ---------------------------------------------------------------------------

def render_derived_card(derived: dict, output_path: str) -> str:
    """渲染衍生练习题卡片。"""
    if not HAS_PIL:
        raise RuntimeError("Pillow 未安装")

    qs = derived.get("derived_questions", [])
    if not qs:
        # 错误卡
        W, H = 600, 200
        img = Image.new("RGB", (W, H), C.BG)
        draw = ImageDraw.Draw(img)
        err = derived.get("user_message") or derived.get("_error") or "未能生成练习题"
        for line in _wrap_text("⚠️ " + err, _font(16), W - 80):
            draw.text((40, 80), line, font=_font(16), fill=C.RED)
            break
        img.save(output_path, "PNG")
        return output_path

    W = 800
    source_stem = (derived.get("source_stem") or "")[:30]
    H = 120  # 顶部
    for q in qs:
        opts = q.get("options") or []
        H += 80 + len(opts) * 22 + 60
    H += 40

    img = Image.new("RGB", (W, H), C.BG)
    draw = ImageDraw.Draw(img)

    # 顶部
    f_title = _font(22)
    draw.text((40, 32), "📝 同类练习", font=f_title, fill=C.TEXT)
    f_sub = _font(13)
    draw.text((40, 68), f"针对：{source_stem}", font=f_sub, fill=C.TEXT_LIGHT)

    y = 110
    f_q = _font(15)
    f_opt = _font(14)
    f_ans = _font(14)
    f_exp = _font(12)

    for i, q in enumerate(qs, 1):
        stem = q.get("stem") or ""
        opts = q.get("options") or []
        ans = q.get("answer") or ""
        exp = q.get("explanation") or ""

        # 卡片背景（淡紫）
        card_h = 50 + len(opts) * 24 + 30
        if exp:
            exp_lines = _wrap_text(exp, f_exp, W - 120)
            card_h += 20 + len(exp_lines) * 18

        _rounded_rect(draw, (40, y, W-40, y+card_h), 10, fill=(243, 233, 245))
        draw.rectangle([40, y, 44, y+card_h], fill=(156, 39, 176))

        # 题目
        draw.text((58, y+12), f"练习{i}：{stem}", font=f_q, fill=C.TEXT)
        y += 42

        # 选项
        for opt in opts:
            draw.text((72, y), opt, font=f_opt, fill=C.TEXT)
            y += 24

        # 答案
        y += 6
        draw.text((58, y), f"✓ 答案：{ans}", font=f_ans, fill=C.GREEN)
        y += 24

        # 解析
        if exp:
            for line in exp_lines:
                draw.text((58, y), line, font=f_exp, fill=C.TEXT_LIGHT)
                y += 18
            y += 6

        y += 16

    img.save(output_path, "PNG", optimize=True)
    return output_path


# ---------------------------------------------------------------------------
# 学情卡片
# ---------------------------------------------------------------------------

def render_student_card(summary: dict, output_path: str) -> str:
    """渲染学生学情卡片。"""
    if not HAS_PIL:
        raise RuntimeError("Pillow 未安装")

    if not summary or not summary.get("found"):
        W, H = 600, 200
        img = Image.new("RGB", (W, H), C.BG)
        draw = ImageDraw.Draw(img)
        name = summary.get("name", "") if summary else ""
        draw.text((40, 80), f"还没有 {name} 的学情记录", font=_font(18), fill=C.TEXT_LIGHT)
        img.save(output_path, "PNG")
        return output_path

    name = summary.get("name", "")
    total = summary.get("total_questions", 0)
    correct = summary.get("correct", 0)
    wrong = summary.get("wrong", 0)
    acc = int(summary.get("accuracy", 0) * 100)
    wps = summary.get("weak_points", [])
    recent = summary.get("recent_wrong", [])

    W = 800
    H = 420 + len(wps[:5]) * 28 + min(len(recent[-5:]), 5) * 28 + 40

    img = Image.new("RGB", (W, H), C.BG)
    draw = ImageDraw.Draw(img)

    # 顶部紫色
    banner_h = 110
    for i in range(banner_h):
        ratio = i / banner_h
        r = int(C.BG_HEADER[0] * (1-ratio) + C.BG_HEADER_END[0] * ratio)
        g = int(C.BG_HEADER[1] * (1-ratio) + C.BG_HEADER_END[1] * ratio)
        b = int(C.BG_HEADER[2] * (1-ratio) + C.BG_HEADER_END[2] * ratio)
        draw.line([(0, i), (W, i)], fill=(r, g, b))

    f_title = _font(26)
    f_sub = _font(15)
    draw.text((40, 28), f"📊 {name} 的学情", font=f_title, fill=C.TEXT_WHITE)
    draw.text((40, 65), f"累计答题分析  ·  截至 {(datetime.now().date())}", font=f_sub, fill=(220, 220, 240))

    y = banner_h + 24

    # 统计
    f_big = _font(40)
    f_label = _font(13)
    stats = [
        (str(total), "累计答题", C.TEXT),
        (str(correct), "做对", C.GREEN),
        (str(wrong), "做错", C.RED),
        (f"{acc}%", "正确率", C.BLUE),
    ]
    col_w = (W - 80) // 4
    for i, (num, label, color) in enumerate(stats):
        cx = 40 + col_w * i + col_w // 2
        nw = _text_width(f_big, num)
        draw.text((cx - nw//2, y), num, font=f_big, fill=color)
        lw = _text_width(f_label, label)
        draw.text((cx - lw//2, y + 52), label, font=f_label, fill=C.TEXT_LIGHT)
    y += 90

    # 薄弱知识点
    if wps:
        f_sec = _font(16)
        draw.text((40, y), "🎯 需重点关注的薄弱点", font=f_sec, fill=C.TEXT)
        y += 28
        weak_h = 30 + len(wps[:5]) * 28
        _rounded_rect(draw, (40, y, W-40, y+weak_h), 10, fill=C.ORANGE_LIGHT)
        f_wp = _font(13)
        for i, wp in enumerate(wps[:5]):
            rate = int(wp.get("wrong_rate", 0) * 100)
            text = f"• {wp['knowledge_point']}：错 {wp['wrong']}/{wp['total']}（{rate}%）"
            for line in _wrap_text(text, f_wp, W - 120):
                draw.text((52, y+10+i*28), line, font=f_wp, fill=C.ORANGE)
                break
        y += weak_h + 16

    # 近期错题
    if recent:
        y += 10
        f_sec = _font(16)
        draw.text((40, y), "📝 近期错题", font=f_sec, fill=C.TEXT)
        y += 28
        f_r = _font(13)
        for r in recent[-5:]:
            stem = (r.get("stem") or "")[:35]
            kp = "、".join(r.get("knowledge_points") or [])[:20]
            date = (r.get("date") or "")[:10]
            text = f"• [{date}] {stem}  ({kp})"
            for line in _wrap_text(text, f_r, W - 120):
                draw.text((52, y), line, font=f_r, fill=C.TEXT)
                y += 22
                break
            y += 6

    img.save(output_path, "PNG", optimize=True)
    return output_path


# ---------------------------------------------------------------------------
# 原图批注：在学生作业图上画红勾/红叉/黄问号
# ---------------------------------------------------------------------------

def _draw_check(draw, cx, cy, size, color, width=6):
    """画一个 ✓ 勾。cx,cy 是中心，size 是大小。"""
    # 勾的两段折线：右下到中下，中下到右上
    p1 = (cx - size*0.4, cy + size*0.1)
    p2 = (cx - size*0.1, cy + size*0.4)
    p3 = (cx + size*0.5, cy - size*0.3)
    draw.line([p1, p2], fill=color, width=width)
    draw.line([p2, p3], fill=color, width=width)


def _draw_cross(draw, cx, cy, size, color, width=6):
    """画一个 ✗ 叉。"""
    s = size * 0.5
    draw.line([(cx-s, cy-s), (cx+s, cy+s)], fill=color, width=width)
    draw.line([(cx-s, cy+s), (cx+s, cy-s)], fill=color, width=width)


def _draw_question(draw, cx, cy, size, color, width=6):
    """画一个 ? 问号（简化为圆圈+一点）。"""
    r = size * 0.45
    # 画圆圈
    draw.ellipse([cx-r, cy-r, cx+r, cy+r], outline=color, width=width)


def annotate_homework_image(image_path: str, graded_questions: list,
                            output_path: str,
                            mark_size_ratio: float = 0.06) -> str:
    """在学生作业图上画批注符号。

    graded_questions: 已批改的题目列表，每题需有 bbox（归一化坐标 [x1,y1,x2,y2]）和 is_correct。
    - is_correct=True → 绿色 ✓
    - is_correct=False → 红色 ✗ + 题号
    - is_correct=None → 黄色 ? （无法判定）
    - 无 bbox 的题跳过

    mark_size_ratio: 批注符号大小占题框的比例（默认 0.06，即题目框短边的 6%）。
    返回输出路径。
    """
    if not HAS_PIL:
        raise RuntimeError("Pillow 未安装")

    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    W, H = img.size
    # 符号字体（题号）
    f_no = _font(max(20, int(min(W, H) * 0.025)))

    annotated_count = 0
    for q in graded_questions:
        bbox = q.get("bbox")
        ic = q.get("is_correct")
        if not bbox or len(bbox) < 4:
            continue
        # 归一化坐标 → 像素
        x1, y1 = bbox[0] * W, bbox[1] * H
        x2, y2 = bbox[2] * W, bbox[3] * H
        box_w, box_h = x2 - x1, y2 - y1
        # 符号大小：基于题目框尺寸
        size = min(box_w, box_h) * 1.2 + min(W, H) * mark_size_ratio

        # 颜色和符号
        if ic is True:
            color, draw_fn = C.GREEN, _draw_check
        elif ic is False:
            color, draw_fn = C.RED, _draw_cross
        else:  # None
            color, draw_fn = C.YELLOW, _draw_question

        # 符号位置：放在题目框的右上角（不遮挡题面和作答）
        cx = x2 + size * 0.3
        cy = y1 + size * 0.3
        # 如果超出右边界，改放左上角
        if cx + size > W - 10:
            cx = x1 - size * 0.3
        # 如果还是超出，放题框内右上角
        if cx - size < 10:
            cx = x2 - size * 0.6
            cy = y1 + size * 0.5

        draw_fn(draw, cx, cy, size, color)

        # 错题/无法判定的，标注题号（方便老师对应）
        if ic is not True:
            qno = str(q.get("question_no", ""))
            no_text = f"第{qno}题"
            # 题号放在符号下方
            try:
                tw = _text_width(f_no, no_text)
                draw.text((cx - tw//2, cy + size*0.5 + 2), no_text,
                          font=f_no, fill=color)
            except Exception:
                pass

        annotated_count += 1

    img.save(output_path, "JPEG", quality=90)
    return output_path


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import argparse
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))

    parser = argparse.ArgumentParser(description="生成批改卡片图")
    parser.add_argument("input", nargs="?", help="batch_report JSON 文件路径（默认用 last-batch.json）")
    parser.add_argument("--type", choices=["overview", "details", "derived", "student", "annotate"],
                        default="overview", help="卡片类型")
    parser.add_argument("--share", action="store_true", help="生成家长群版（总览卡）")
    parser.add_argument("--image", default=None, help="annotate 类型：原图路径（默认用报告里的 image_path）")
    parser.add_argument("-o", "--output", default=None, help="输出路径")
    args = parser.parse_args()

    if not HAS_PIL:
        print("❌ 需要 Pillow：pip install Pillow", file=sys.stderr)
        sys.exit(1)

    # 默认用 last-batch.json
    if args.input:
        data = json.loads(Path(args.input).read_text())
    else:
        last = Path.home() / ".openclaw" / "skill-state" / "homework-grader-math" / "last-batch.json"
        data = json.loads(last.read_text())

    out = args.output or f"/tmp/card_{args.type}.png"

    if args.type == "overview":
        render_overview_card(data, out, share=args.share)
    elif args.type == "details":
        render_wrong_details_card(data, out)
    elif args.type == "derived":
        render_derived_card(data, out)
    elif args.type == "student":
        render_student_card(data, out)
    elif args.type == "annotate":
        # 在原图上画批注
        img_path = args.image or data.get("image_path")
        if not img_path or not Path(img_path).is_file():
            print("❌ annotate 需要原图，请用 --image 指定", file=sys.stderr)
            sys.exit(1)
        annotate_homework_image(img_path, data.get("questions", []), out)

    print(f"✅ 卡片已生成：{out}")
