#!/usr/bin/env python3
"""
小学数学作业批改 Web 界面。

基于 Gradio，3 个 Tab：批改作业 / 学情查看 / 模型配置。
后端直接 import 技能函数（grade/report/bank），不走 subprocess。

启动：python3 grade.py --web
或：  python3 web.py
"""

import sys
import html as _html
from pathlib import Path

# 让同目录脚本可互相 import
sys.path.insert(0, str(Path(__file__).parent))

import grade
import report
import _config
from _gateway import resolve_gateway_config, check_vision_support
from _gateway import VisionNotSupportedError, VisionUnavailableError
from explain import error_type_label


# ---------------------------------------------------------------------------
# 模块级配置（启动时读一次）
# ---------------------------------------------------------------------------

def get_conn():
    """每次调用时读取最新配置（配置页可能改过）。"""
    return resolve_gateway_config()


def escape(text) -> str:
    """HTML 转义，None 返回空串。"""
    if text is None:
        return ""
    return _html.escape(str(text))


# ===========================================================================
# HTML 渲染
# ===========================================================================

# CSS 样式（内联到页面，确保单文件自包含）
_CSS = """
<style>
.hw-container { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; max-width: 900px; margin: 0 auto; }
.hw-summary-card { background: linear-gradient(135deg, #667eea, #764ba2); color: white; border-radius: 16px; padding: 24px; margin-bottom: 20px; box-shadow: 0 4px 12px rgba(102,126,234,0.3); }
.hw-summary-card h2 { margin: 0 0 12px 0; font-size: 20px; }
.hw-stats { display: flex; gap: 24px; flex-wrap: wrap; }
.hw-stat { text-align: center; }
.hw-stat .num { font-size: 32px; font-weight: bold; }
.hw-stat .label { font-size: 13px; opacity: 0.9; }
.hw-alert { border-radius: 12px; padding: 12px 16px; margin-bottom: 16px; font-size: 14px; }
.hw-alert-warn { background: #fff8e1; border-left: 4px solid #ffa000; color: #6d4c00; }
.hw-alert-info { background: #e3f2fd; border-left: 4px solid #1976d2; color: #0d47a1; }
.hw-qcard { background: white; border: 1px solid #e0e0e0; border-radius: 12px; padding: 16px 20px; margin-bottom: 12px; }
.hw-qcard.wrong { border-left: 4px solid #f44336; }
.hw-qcard.right { border-left: 4px solid #4caf50; }
.hw-qcard.undetermined { border-left: 4px solid #ff9800; background: #fffde7; }
.hw-qhead { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
.hw-badge { display: inline-block; width: 28px; height: 28px; line-height: 28px; text-align: center; border-radius: 50%; font-weight: bold; font-size: 16px; }
.hw-badge-right { background: #4caf50; color: white; }
.hw-badge-wrong { background: #f44336; color: white; }
.hw-badge-undetermined { background: #ff9800; color: white; }
.hw-qno { font-weight: bold; color: #333; font-size: 15px; }
.hw-qtype { font-size: 12px; color: #888; background: #f5f5f5; padding: 2px 8px; border-radius: 10px; }
.hw-qbody { margin-left: 40px; font-size: 14px; color: #333; line-height: 1.8; }
.hw-stem { font-weight: 500; margin-bottom: 4px; }
.hw-ansrow { display: flex; gap: 16px; margin-bottom: 4px; }
.hw-ans-label { color: #888; min-width: 80px; }
.hw-ans-val { color: #333; }
.hw-ans-val.wrong { color: #f44336; text-decoration: line-through; }
.hw-ans-val.correct { color: #4caf50; font-weight: bold; }
.hw-detail { background: #fafafa; border-radius: 8px; padding: 12px 16px; margin-top: 8px; font-size: 13px; }
.hw-detail-row { margin-bottom: 6px; }
.hw-detail-label { color: #888; font-weight: 500; }
.hw-kp { display: inline-block; background: #e3f2fd; color: #1565c0; padding: 2px 8px; border-radius: 10px; font-size: 12px; margin: 2px; }
.hw-source-badge { font-size: 11px; padding: 1px 6px; border-radius: 8px; }
.hw-source-bank { background: #e8f5e9; color: #2e7d32; }
.hw-source-ai { background: #e3f2fd; color: #1565c0; }
.hw-weak { background: #fff3e0; border: 1px solid #ffb74d; border-radius: 12px; padding: 16px; margin-top: 16px; }
.hw-weak-item { padding: 4px 0; color: #e65100; }
.hw-derived { background: #f3e5f5; border: 1px solid #ba68c8; border-radius: 8px; padding: 12px; margin-top: 8px; }
</style>
"""


def render_report_html(batch_report: dict) -> str:
    """把 batch_report 渲染成 HTML。

    三态判定视觉区分：
    - True → 绿色 ✓
    - False → 红色 ✗（展开错因+点评+解析）
    - None → 黄色 ❓（无法判定，单独提示老师看）
    """
    if not batch_report or batch_report.get("error"):
        err = batch_report.get("user_message", "批改遇到问题") if batch_report else "批改遇到问题"
        return f'<div class="hw-alert hw-alert-warn">⚠️ {escape(err)}</div>'

    s = batch_report.get("summary", {})
    student = batch_report.get("student_name") or "学生"
    src = batch_report.get("recognition_source", "vision")

    parts = [_CSS, '<div class="hw-container">']

    # === 摘要卡片 ===
    accuracy_pct = int(s.get("accuracy", 0) * 100)
    parts.append(f'''
    <div class="hw-summary-card">
        <h2>{escape(student)}的作业批改完成</h2>
        <div class="hw-stats">
            <div class="hw-stat"><div class="num">{s.get("total",0)}</div><div class="label">总题数</div></div>
            <div class="hw-stat"><div class="num">{s.get("correct",0)}</div><div class="label">✓ 做对</div></div>
            <div class="hw-stat"><div class="num">{s.get("wrong",0)}</div><div class="label">✗ 做错</div></div>
            <div class="hw-stat"><div class="num">{accuracy_pct}%</div><div class="label">正确率</div></div>
        </div>
    </div>''')

    # === OCR 兜底提示 ===
    if src == "ocr_fallback":
        parts.append(
            '<div class="hw-alert hw-alert-info">📋 本次用了本地 OCR 兜底识别，'
            '识别结果可能有少量误差，建议校对题号。</div>'
        )

    # === 无法判定提示 ===
    undetermined_count = s.get("undetermined", 0)
    if undetermined_count:
        parts.append(
            f'<div class="hw-alert hw-alert-warn">❓ 有 {undetermined_count} 题无法自动判定，'
            f'请在下方"无法判定"区查看并手动核对。</div>'
        )

    # === 需复核提示 ===
    need_review = s.get("need_review", 0)
    if need_review:
        parts.append(
            f'<div class="hw-alert hw-alert-info">⚠️ 有 {need_review} 题 AI 不太确定，'
            f'已用⚠️标记，建议重点看一下。</div>'
        )

    # === 逐题卡片 ===
    parts.append('<h3 style="margin-top:24px;color:#333;">📝 逐题结果</h3>')
    for q in batch_report.get("questions", []):
        parts.append(_render_question_card(q))

    # === 薄弱知识点 ===
    wps = batch_report.get("weak_points", [])
    if wps:
        parts.append('<div class="hw-weak"><h4>🎯 本次薄弱知识点</h4>')
        for wp in wps[:5]:
            rate = int(wp.get("wrong_rate", 0) * 100)
            parts.append(
                f'<div class="hw-weak-item">• {escape(wp["knowledge_point"])}：'
                f'错 {wp["wrong"]}/{wp["total"]}（错误率 {rate}%）</div>'
            )
        parts.append('</div>')

    parts.append('</div>')
    return "\n".join(parts)


def _render_question_card(q: dict) -> str:
    """渲染单道题的卡片。"""
    ic = q.get("is_correct")
    qno = q.get("question_no", "?")
    qtype = q.get("type", "")
    stem = q.get("stem", "")
    sa = q.get("student_answer")
    ca = q.get("correct_answer")

    # 三态样式
    if ic is True:
        cls, badge_cls, badge_icon = "right", "hw-badge-right", "✓"
    elif ic is False:
        cls, badge_cls, badge_icon = "wrong", "hw-badge-wrong", "✗"
    else:
        cls, badge_cls, badge_icon = "undetermined", "hw-badge-undetermined", "?"

    sa_text = escape(sa) if sa else '<span style="color:#999">未作答</span>'
    ca_text = escape(ca) if ca else '<span style="color:#999">无法确定</span>'

    # 来源 badge
    source = q.get("source", "")
    src_badge = ""
    if source == "bank":
        src_badge = '<span class="hw-source-badge hw-source-bank">题库</span>'
    elif source == "ai":
        src_badge = '<span class="hw-source-badge hw-source-ai">AI</span>'
    if q.get("need_review"):
        src_badge += ' <span style="color:#f57c00;">⚠️建议复核</span>'

    # 题型中文
    type_map = {"calculation": "计算题", "fill_blank": "填空题", "choice": "选择题",
                "judge": "判断题", "application": "应用题"}
    type_cn = type_map.get(qtype, qtype)

    parts = [f'<div class="hw-qcard {cls}">']
    # 卡片头
    parts.append(f'''
    <div class="hw-qhead">
        <span class="hw-badge {badge_cls}">{badge_icon}</span>
        <span class="hw-qno">第{qno}题</span>
        <span class="hw-qtype">{type_cn}</span>
        {src_badge}
    </div>''')

    # 题面 + 答案
    parts.append('<div class="hw-qbody">')
    if stem:
        parts.append(f'<div class="hw-stem">{escape(stem)}</div>')
    parts.append(f'<div class="hw-ansrow"><span class="hw-ans-label">学生作答：</span>'
                 f'<span class="hw-ans-val {"wrong" if ic is False else ""}">{sa_text}</span></div>')
    parts.append(f'<div class="hw-ansrow"><span class="hw-ans-label">正确答案：</span>'
                 f'<span class="hw-ans-val {"correct" if ic is False else ""}">{ca_text}</span></div>')

    # 错题展开（错因+点评+解析）
    if ic is False:
        parts.append('<div class="hw-detail">')
        et = q.get("error_type")
        if et:
            parts.append(f'<div class="hw-detail-row"><span class="hw-detail-label">错因：</span>{escape(error_type_label(et))}</div>')
        kps = q.get("knowledge_points", [])
        if kps:
            kp_html = "".join(f'<span class="hw-kp">{escape(kp)}</span>' for kp in kps)
            parts.append(f'<div class="hw-detail-row"><span class="hw-detail-label">知识点：</span>{kp_html}</div>')
        if q.get("comment"):
            parts.append(f'<div class="hw-detail-row"><span class="hw-detail-label">💬 点评：</span>{escape(q["comment"])}</div>')
        if q.get("solution"):
            parts.append(f'<div class="hw-detail-row"><span class="hw-detail-label">📖 解析：</span>{escape(q["solution"])}</div>')
        parts.append('</div>')
    elif ic is None:
        # 无法判定
        parts.append('<div class="hw-detail"><span class="hw-detail-label">说明：</span>'
                     '这题 AI 没能确定标准答案（可能题面条件不全），请你手动核对。</div>')
    else:
        # 对的题，展示知识点（如有）
        kps = q.get("knowledge_points", [])
        if kps:
            kp_html = "".join(f'<span class="hw-kp">{escape(kp)}</span>' for kp in kps)
            parts.append(f'<div style="margin-top:4px;">{kp_html}</div>')

    parts.append('</div></div>')
    return "\n".join(parts)


def render_student_html(summary: dict) -> str:
    """渲染学情汇总 HTML。"""
    if not summary or not summary.get("found"):
        name = summary.get("name", "") if summary else ""
        return f'<div class="hw-alert hw-alert-warn">还没有 {escape(name)} 的学情记录。先批改一次作业吧。</div>'

    parts = [_CSS, '<div class="hw-container">']
    name = summary.get("name", "")
    total = summary.get("total_questions", 0)
    correct = summary.get("correct", 0)
    wrong = summary.get("wrong", 0)
    acc = int(summary.get("accuracy", 0) * 100)

    # 概览卡片
    parts.append(f'''
    <div class="hw-summary-card">
        <h2>{escape(name)} 的学情汇总</h2>
        <div class="hw-stats">
            <div class="hw-stat"><div class="num">{total}</div><div class="label">累计答题</div></div>
            <div class="hw-stat"><div class="num">{correct}</div><div class="label">✓ 做对</div></div>
            <div class="hw-stat"><div class="num">{wrong}</div><div class="label">✗ 做错</div></div>
            <div class="hw-stat"><div class="num">{acc}%</div><div class="label">正确率</div></div>
        </div>
    </div>''')

    # 薄弱知识点
    wps = summary.get("weak_points", [])
    if wps:
        parts.append('<div class="hw-weak"><h4>🎯 需重点关注的薄弱知识点</h4>')
        for wp in wps:
            rate = int(wp.get("wrong_rate", 0) * 100)
            parts.append(
                f'<div class="hw-weak-item">• {escape(wp["knowledge_point"])}：'
                f'错 {wp["wrong"]}/{wp["total"]}（错误率 {rate}%）</div>'
            )
        parts.append('</div>')
    else:
        parts.append('<div class="hw-alert hw-alert-info">暂无明显的薄弱知识点，继续加油～</div>')

    # 近期错题
    recent = summary.get("recent_wrong", [])
    if recent:
        parts.append('<h3 style="margin-top:24px;color:#333;">📝 近期错题</h3>')
        for r in recent[-10:]:
            stem = escape((r.get("stem") or "")[:50])
            kp = "、".join(r.get("knowledge_points", []))
            date = (r.get("date") or "")[:10]
            et = error_type_label(r.get("error_type", ""))
            parts.append(
                f'<div class="hw-qcard wrong"><div class="hw-qhead">'
                f'<span class="hw-badge hw-badge-wrong">✗</span>'
                f'<span class="hw-qno">{escape(stem)}</span></div>'
                f'<div class="hw-qbody">'
                f'<div class="hw-ansrow"><span class="hw-ans-label">日期：</span>{date}</div>'
                f'<div class="hw-ansrow"><span class="hw-ans-label">错因：</span>{escape(et)}</div>'
                f'<div class="hw-ansrow"><span class="hw-ans-label">知识点：</span>{escape(kp)}</div>'
                f'</div></div>'
            )

    parts.append('</div>')
    return "\n".join(parts)


def render_derived_html(derived: dict) -> str:
    """渲染衍生练习题 HTML。"""
    qs = derived.get("derived_questions", [])
    # 优先显示友好提示（题号不存在/未批改等业务错误）
    if derived.get("user_message") and not qs:
        return f'<div class="hw-alert hw-alert-warn">⚠️ {escape(derived["user_message"])}</div>'
    if not qs:
        err = derived.get("_error", "未能生成练习题（模型可能未响应，可重试）")
        return f'<div class="hw-alert hw-alert-warn">⚠️ {escape(err)}</div>'

    parts = [_CSS, '<div class="hw-container">']
    source_stem = derived.get("source_stem", "")
    source_qno = derived.get("source_question_no", "")
    source_correct = derived.get("source_is_correct")
    # 判断是错题还是对的题
    if source_correct is False:
        label = f"错题「第{source_qno}题」"
    else:
        label = f"第{source_qno}题"
    parts.append(
        f'<div class="hw-alert hw-alert-info">📝 针对{label}（{escape(source_stem[:40])}）'
        f'出了 {len(qs)} 道同类练习：</div>'
    )
    for i, q in enumerate(qs, 1):
        parts.append(f'<div class="hw-derived">')
        parts.append(f'<div style="font-weight:500;margin-bottom:6px;">练习{i}：{escape(q.get("stem",""))}</div>')
        opts = q.get("options")
        if opts:
            parts.append('<div style="margin-left:16px;">')
            for opt in opts:
                parts.append(f'<div>{escape(opt)}</div>')
            parts.append('</div>')
        parts.append(f'<div style="margin-top:6px;color:#4caf50;font-weight:500;">答案：{escape(q.get("answer",""))}</div>')
        if q.get("explanation"):
            parts.append(f'<div style="color:#666;font-size:13px;margin-top:4px;">解析：{escape(q["explanation"])}</div>')
        parts.append('</div>')
    parts.append('</div>')
    return "\n".join(parts)


# ===========================================================================
# Gradio 界面
# ===========================================================================

# ---------------------------------------------------------------------------
# Handler 函数（模块级，便于独立测试）
# ---------------------------------------------------------------------------

def handle_grade(image, student_name, progress=None):
    """批改作业的 handler。progress 可选（Gradio 注入或测试时 None）。

    返回 (html_report, wrong_nos_str, overview_card_path, details_card_path)。
    批改失败时卡片路径为 None。
    """
    if not image:
        return ('<div class="hw-alert hw-alert-warn">⚠️ 请先上传作业图片</div>',
                "", None, None)

    if progress:
        progress(0.1, desc="识别中...")
    base, key, model = get_conn()
    try:
        if progress:
            progress(0.3, desc="批改中（可能需要几十秒）...")
        batch_report = grade.grade_homework(image, student_name or "", base, key, model)
        if progress:
            progress(0.9, desc="生成报告和卡片...")
        html_out = render_report_html(batch_report)
        # 生成卡片图
        overview_path, details_path = _generate_cards(batch_report)
        if progress:
            progress(1.0, desc="完成")
        # 把错题题号提取出来，供衍生按钮用
        wrong_nos = [str(q["question_no"]) for q in batch_report.get("questions", [])
                     if q.get("is_correct") is False]
        return html_out, "、".join(wrong_nos), overview_path, details_path
    except VisionNotSupportedError:
        return ('<div class="hw-alert hw-alert-warn">⚠️ 当前模型看不了图。请在「模型配置」页换一个支持图片识别的模型（如 GPT-4o、GLM-4V 等多模态模型）。</div>',
                "", None, None)
    except VisionUnavailableError as e:
        return (f'<div class="hw-alert hw-alert-warn">⚠️ 识别不稳定（{escape(str(e)[:100])}）。可重试，或把题目打字发给我用文本批改。</div>',
                "", None, None)
    except Exception as e:
        return (f'<div class="hw-alert hw-alert-warn">⚠️ 批改失败：{escape(str(e)[:150])}。请重试或换张清楚的图。</div>',
                "", None, None)


def _generate_cards(batch_report: dict):
    """生成总览卡和错题详情卡，返回 (overview_path, details_path)。失败返回 (None, None)。"""
    try:
        import card_renderer
        from pathlib import Path
        card_dir = Path.home() / ".openclaw" / "skill-state" / "homework-grader-math" / "cards"
        card_dir.mkdir(parents=True, exist_ok=True)
        batch_id = batch_report.get("batch_id", "batch")
        overview_path = str(card_dir / f"{batch_id}_overview.png")
        details_path = str(card_dir / f"{batch_id}_details.png")
        card_renderer.render_overview_card(batch_report, overview_path)
        card_renderer.render_wrong_details_card(batch_report, details_path)
        return overview_path, details_path
    except Exception:
        return None, None


def _annotate_image(image_path, batch_report):
    """在原图上画批注，返回批注图路径。失败返回 None。"""
    try:
        import card_renderer
        from pathlib import Path
        card_dir = Path.home() / ".openclaw" / "skill-state" / "homework-grader-math" / "cards"
        card_dir.mkdir(parents=True, exist_ok=True)
        batch_id = batch_report.get("batch_id", "batch")
        out_path = str(card_dir / f"{batch_id}_annotated.jpg")
        card_renderer.annotate_homework_image(
            image_path, batch_report.get("questions", []), out_path,
        )
        return out_path
    except Exception:
        return None


def handle_grade_batch(files, student_names_text, progress=None):
    """批量批改多学生同一套题的 handler。

    files: list[filepath]，多张作业图
    student_names_text: 学生名文本，按行或逗号分隔（数量需与文件数一致）
    返回 (summary_html, annotated_gallery, cards_gallery)
    """
    if not files:
        return ('<div class="hw-alert hw-alert-warn">⚠️ 请先上传作业图片</div>',
                [], [])
    # 解析学生名
    names = [n.strip() for n in student_names_text.replace("，", ",").replace("\n", ",").split(",") if n.strip()] if student_names_text else []
    if len(names) != len(files):
        # 不匹配时按文件名兜底
        from pathlib import Path
        names = [Path(f).stem if isinstance(f, str) else f"学生{i+1}" for i, f in enumerate(files)]
        if not student_names_text:
            pass  # 用文件名
        else:
            return (f'<div class="hw-alert hw-alert-warn">⚠️ 学生名数量({len(names)})与图片数量({len(files)})不一致，请检查</div>',
                    [], [])

    if progress:
        progress(0.05, desc="识别第一份作业，建立题面模板...")

    base, key, model = get_conn()

    def progress_cb(stage, detail):
        if progress:
            progress(0.1 + 0.7 * (names and stage.startswith("批改") or 0.1),
                     desc=f"{stage} {detail}")

    try:
        # 调批量批改
        reports = grade.grade_multi_students(
            list(files), names, base, key, model,
            progress_callback=progress_cb,
        )
    except Exception as e:
        return (f'<div class="hw-alert hw-alert-warn">⚠️ 批量批改失败：{escape(str(e)[:200])}</div>',
                [], [])

    if progress:
        progress(0.85, desc="生成批注图和卡片...")

    # 生成每个学生的批注图和总览卡
    annotated_paths = []
    card_paths = []
    for r in reports:
        img_path = r.get("image_path")
        if img_path:
            ann = _annotate_image(img_path, r)
            if ann:
                annotated_paths.append(ann)
        ov, _dt = _generate_cards(r)
        if ov:
            card_paths.append(ov)

    if progress:
        progress(1.0, desc="完成")

    # 汇总 HTML
    parts = [_CSS, '<div class="hw-container">',
             f'<div class="hw-alert hw-alert-info">✅ 批量批改完成，共 {len(reports)} 位学生</div>']
    parts.append('<table style="width:100%;border-collapse:collapse;font-size:14px;">')
    parts.append('<tr style="background:#f5f5f5;"><th style="padding:8px;border:1px solid #ddd;">学生</th>'
                 '<th style="padding:8px;border:1px solid #ddd;">总题</th>'
                 '<th style="padding:8px;border:1px solid #ddd;">对</th>'
                 '<th style="padding:8px;border:1px solid #ddd;">错</th>'
                 '<th style="padding:8px;border:1px solid #ddd;">正确率</th></tr>')
    for r in reports:
        s = r.get("summary", {})
        name = r.get("student_name", "")
        acc = int(s.get("accuracy", 0) * 100)
        # 错了的行标红
        row_color = 'style="background:#fff8e1;"' if s.get("wrong", 0) > 0 else ""
        parts.append(
            f'<tr {row_color}><td style="padding:8px;border:1px solid #ddd;text-align:center;">{escape(name)}</td>'
            f'<td style="padding:8px;border:1px solid #ddd;text-align:center;">{s.get("total",0)}</td>'
            f'<td style="padding:8px;border:1px solid #ddd;text-align:center;color:#4caf50;">{s.get("correct",0)}</td>'
            f'<td style="padding:8px;border:1px solid #ddd;text-align:center;color:#f44336;">{s.get("wrong",0)}</td>'
            f'<td style="padding:8px;border:1px solid #ddd;text-align:center;"><b>{acc}%</b></td></tr>'
        )
    parts.append('</table>')
    parts.append('<div style="color:#888;font-size:13px;margin-top:12px;">'
                 '下方「批注后的作业图」展示了每位学生的作业原图（画了红勾/红叉），'
                 '「总览卡片」可单独下载转发。</div>')
    parts.append('</div>')

    return "\n".join(parts), annotated_paths, card_paths


def handle_derive(q_no, progress=None):
    """出同类题的 handler。"""
    if not q_no:
        return '<div class="hw-alert hw-alert-warn">⚠️ 请先输入要出题的错题题号</div>'
    try:
        q_no_int = int(q_no)
    except (ValueError, TypeError):
        return '<div class="hw-alert hw-alert-warn">⚠️ 题号必须是数字</div>'

    if progress:
        progress(0.3, desc="生成同类练习中...")
    base, key, model = get_conn()
    try:
        result = grade.derive_for_question(q_no_int, 2, base, key, model)
        if progress:
            progress(1.0, desc="完成")
        return render_derived_html(result)
    except Exception as e:
        return f'<div class="hw-alert hw-alert-warn">⚠️ 出题失败：{escape(str(e)[:150])}</div>'


def handle_student(name):
    """查学情的 handler。"""
    if not name:
        return '<div class="hw-alert hw-alert-warn">⚠️ 请选择或输入学生姓名</div>'
    summary = report.get_student_summary(name)
    return render_student_html(summary)


def refresh_students():
    """刷新学生下拉列表。返回 gr.update 或（测试时）纯列表。"""
    records = report.load_records()
    students = sorted(records.get("students", {}).keys())
    try:
        import gradio as gr
        return gr.update(choices=students, value=students[0] if students else None)
    except ImportError:
        return students


def handle_config(url, key_input, model_name):
    """保存配置的 handler。返回 (消息, 状态文本)。"""
    if not url or not key_input or not model_name:
        return "⚠️ 请填写完整", _config.config_status_text()
    _config.update_config(base_url=url, api_key=key_input, model=model_name)
    return "✅ 配置已保存", _config.config_status_text()


def get_status_badge():
    """检查视觉能力，返回 (ok, model)。"""
    base, key, model = get_conn()
    ok = check_vision_support(base, key, model)
    return ok, model


def build_ui():
    import gradio as gr

    # 先获取状态
    vision_ok, current_model = get_status_badge()
    status_icon = "✅" if vision_ok else "⚠️"
    status_text = f"视觉可用" if vision_ok else "视觉不可用（可用OCR兜底）"
    import gradio as gr

    with gr.Blocks(title="小学数学作业批改") as demo:

        # 顶部标题 + 状态
        gr.Markdown(f"""
        # 📐 小学数学作业批改助手
        <span style="font-size:14px;color:#666;">
        状态：{status_icon} {status_text} ｜ 模型：{escape(current_model)}
        </span>
        """)

        with gr.Tabs():
            # === Tab 1: 批改作业 ===
            with gr.Tab("📷 批改作业"):
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("### 上传作业")
                        img_input = gr.Image(type="filepath", label="作业图片",
                                             sources=["upload", "clipboard"])
                        student_input = gr.Textbox(label="学生姓名（可选）",
                                                   placeholder="如：小明")
                        grade_btn = gr.Button("🚀 开始批改", variant="primary")

                    with gr.Column(scale=2):
                        report_out = gr.HTML(
                            value='<div style="text-align:center;color:#999;padding:40px;">'
                                  '上传作业图片后点击「开始批改」</div>'
                            )
                        # 卡片图区（批改后显示，可下载/分享）
                        with gr.Group(visible=False) as card_group:
                            gr.Markdown("### 📸 批改卡片（可下载或转发家长群）")
                            with gr.Row():
                                overview_card = gr.Image(label="总览卡", type="filepath",
                                                          interactive=False)
                                details_card = gr.Image(label="错题详情卡", type="filepath",
                                                         interactive=False)
                        # 衍生题操作区（批改后显示）
                        with gr.Group(visible=False) as derive_group:
                            gr.Markdown("### 📝 出同类练习")
                            gr.Markdown('<small>选一道错题生成同类练习，或点「生成一套」覆盖所有错题。</small>')
                            with gr.Row():
                                qno_dropdown = gr.Dropdown(label="选择错题", choices=[], scale=3)
                                derive_one_btn = gr.Button("出这道题的同类题", scale=2)
                            derive_all_btn = gr.Button("📋 生成一套同类练习（覆盖所有错题）",
                                                       variant="secondary")
                        derive_out = gr.HTML(value="")

                # 状态：存错题题号列表
                wrong_nos_state = gr.State([])

                # 批改后：渲染报告 + 显示卡片 + 显示衍生区 + 填充下拉框
                def on_grade(image, student):
                    html_out, wrong_nos_str, ov_card, dt_card = handle_grade(image, student)
                    wrong_nos = [n for n in wrong_nos_str.split("、") if n] if wrong_nos_str else []
                    has_wrong = bool(wrong_nos)
                    has_card = ov_card is not None
                    return (
                        html_out,  # 报告
                        wrong_nos,  # 状态
                        gr.update(visible=has_card),  # 卡片区显隐
                        ov_card or None,  # 总览卡
                        dt_card or None,  # 详情卡
                        gr.update(visible=has_wrong),  # 衍生区显隐
                        gr.update(choices=[f"第{n}题" for n in wrong_nos],
                                  value=f"第{wrong_nos[0]}题" if wrong_nos else None,
                                  visible=has_wrong),  # 下拉框
                        "" if has_wrong else '<div style="color:#999;padding:12px;">本次没有错题，棒！🎉</div>',
                    )

                grade_btn.click(
                    on_grade,
                    inputs=[img_input, student_input],
                    outputs=[report_out, wrong_nos_state, card_group,
                             overview_card, details_card,
                             derive_group, qno_dropdown, derive_out],
                )

                # 单题出题
                def on_derive_one(selected, wrong_nos):
                    if not selected:
                        return '<div class="hw-alert hw-alert-warn">⚠️ 请先选择一道错题</div>'
                    # "第3题" → 3
                    import re
                    m = re.search(r'\d+', selected)
                    if not m:
                        return '<div class="hw-alert hw-alert-warn">⚠️ 无法识别题号</div>'
                    return handle_derive(m.group())

                derive_one_btn.click(on_derive_one,
                                     inputs=[qno_dropdown, wrong_nos_state],
                                     outputs=[derive_out])

                # 整套出题
                def on_derive_all(wrong_nos):
                    if not wrong_nos:
                        return '<div class="hw-alert hw-alert-warn">⚠️ 没有错题，无需出题</div>'
                    parts = ['<div class="hw-container"><h3>📋 同类练习套卷（覆盖所有错题）</h3>']
                    for qno in wrong_nos:
                        parts.append(f'<h4 style="color:#333;margin-top:20px;">第{qno}题的同类练习</h4>')
                        parts.append(handle_derive(qno))
                    parts.append('</div>')
                    return "\n".join(parts)

                derive_all_btn.click(on_derive_all, inputs=[wrong_nos_state], outputs=[derive_out])

            # === Tab 2: 批量批改（同一套题）===
            with gr.Tab("📚 批量批改"):
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("### 上传多个学生作业")
                        gr.Markdown('<small>同一套题，多张学生作业图。第一张作为题面模板，后续学生共享答案。</small>')
                        batch_files = gr.File(label="多个学生的作业图",
                                              file_count="multiple",
                                              file_types=["image"])
                        batch_names = gr.Textbox(
                            label="学生姓名（按行或逗号分隔，顺序对应图片）",
                            placeholder="小明\n小红\n小刚\n或：小明,小红,小刚\n（留空则用文件名）",
                            lines=4,
                        )
                        batch_btn = gr.Button("🚀 批量批改", variant="primary")
                        gr.Markdown('<small>⚠️ 批量批改较慢（每位学生约30秒-2分钟），请耐心等待。</small>')

                    with gr.Column(scale=2):
                        batch_summary = gr.HTML(
                            value='<div style="text-align:center;color:#999;padding:40px;">'
                                  '上传多张作业图后点击「批量批改」</div>'
                        )
                        gr.Markdown("### 📝 批注后的作业图（红勾/红叉）")
                        batch_annotated = gr.Gallery(label="批注图", columns=2,
                                                      height=400)
                        gr.Markdown("### 📸 总览卡片")
                        batch_cards = gr.Gallery(label="总览卡", columns=2, height=400)

                batch_btn.click(
                    handle_grade_batch,
                    inputs=[batch_files, batch_names],
                    outputs=[batch_summary, batch_annotated, batch_cards],
                )

            # === Tab 3: 学情查看 ===
            with gr.Tab("📊 学情查看"):
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("### 选择学生")
                        records = report.load_records()
                        student_names = sorted(records.get("students", {}).keys())
                        student_dd = gr.Dropdown(
                            choices=student_names,
                            value=student_names[0] if student_names else None,
                            label="学生姓名",
                            allow_custom_value=True,
                        )
                        query_btn = gr.Button("🔍 查看学情", variant="primary")
                        refresh_btn = gr.Button("🔄 刷新学生列表")
                    with gr.Column(scale=2):
                        student_out = gr.HTML(
                            value='<div style="text-align:center;color:#999;padding:40px;">'
                                  '选择学生后点击「查看学情」</div>'
                            )

                query_btn.click(handle_student, inputs=[student_dd], outputs=[student_out])
                refresh_btn.click(refresh_students, outputs=[student_dd])

            # === Tab 4: 模型配置 ===
            with gr.Tab("⚙️ 模型配置"):
                conn = _config.load_config()
                gr.Markdown("### 模型服务配置\n配置支持视觉理解的 OpenAI 兼容模型（如 GPT-4o、GLM-4V、通义千问 VL 等）。")
                with gr.Row():
                    url_input = gr.Textbox(label="服务地址 (base_url)", value=conn.get("base_url", ""),
                                           placeholder="https://api.openai.com")
                    model_input = gr.Textbox(label="模型名", value=conn.get("model", ""),
                                             placeholder="gpt-4o")
                key_input = gr.Textbox(label="API Key", value=conn.get("api_key", ""),
                                       type="password", placeholder="sk-...")
                save_btn = gr.Button("💾 保存配置", variant="primary")
                config_msg = gr.Markdown("")
                config_status = gr.Markdown(_config.config_status_text().replace("\n", "\n\n"))

                def on_save(url, key_v, model_v):
                    msg, status = handle_config(url, key_v, model_v)
                    return msg, status.replace("\n", "\n\n")

                save_btn.click(on_save,
                               inputs=[url_input, key_input, model_input],
                               outputs=[config_msg, config_status])

    return demo


def launch(host: str = "127.0.0.1", port: int = 7860):
    """启动 Web 服务。"""
    demo = build_ui()
    print(f"\n📐 小学数学作业批改 Web 界面")
    print(f"   地址: http://{host}:{port}")
    print(f"   按 Ctrl+C 停止\n")
    demo.launch(server_name=host, server_port=port, inbrowser=True,
                theme=__import__("gradio").themes.Soft(),
                css="footer {display:none}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Web 界面")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()
    launch(args.host, args.port)
