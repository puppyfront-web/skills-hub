#!/usr/bin/env python3
"""Collection (套图) orchestration: plan -> dispatch -> summary.

Three phases, mirroring linkfox's protocol but simplified:

  --phase plan      Read image, propose candidate set(s), print markdown table,
                    write collection-state.json. Agent then asks the user which
                    shots to keep.

  --phase dispatch  Read state, spawn ThreadPoolExecutor to generate each shot
                    concurrently. Each shot writes task-result-<id>.json.
                    Skill-layer concurrency (agent does NOT spawn parallel Bash).

  --phase summary   Read task-results, print markdown summary with inline
                    ![](abs_path) refs + write collection-manifest.json.

State file is passed via --state <path>. All artifacts live next to it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import _config  # noqa: E402
import _gateway  # noqa: E402
import _prompts  # noqa: E402
from _prompts import APPAREL_CATEGORIES, CATEGORY_LABELS  # noqa: E402
import generate as generate_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Preset bundles — what shots a "standard collection" contains
# ---------------------------------------------------------------------------

PRESET_NON_APPAREL = [
    {"id": "white-bg", "type": "white-bg", "label": "白底主图", "required_args": []},
    {"id": "scene", "type": "scene", "label": "场景图", "required_args": ["scene"]},
    {"id": "selling-point", "type": "selling-point", "label": "卖点图",
     "required_args": ["selling_points"]},
    {"id": "aplus", "type": "aplus", "label": "A+详情图",
     "required_args": ["selling_points"]},
    {"id": "banner", "type": "banner", "label": "橱窗图",
     "required_args": []},  # slogan/selling-points optional
]

PRESET_APPAREL = [
    {"id": "white-bg", "type": "white-bg", "label": "白底主图", "required_args": []},
    {"id": "model-wear", "type": "model-wear", "label": "模特试穿图",
     "required_args": ["category"]},
    {"id": "multi-model", "type": "multi-model", "label": "多模特展示图",
     "required_args": ["category"]},
    {"id": "flat-lay", "type": "flat-lay", "label": "平铺图",
     "required_args": ["category"]},
    {"id": "aplus", "type": "aplus", "label": "A+详情图",
     "required_args": ["selling_points"]},  # NEW: apparel needs detail page too
    {"id": "banner", "type": "banner", "label": "橱窗图",
     "required_args": []},
]


# ---------------------------------------------------------------------------
# State I/O
# ---------------------------------------------------------------------------

def _new_state_file(image: str, out_root: Optional[str] = None) -> Path:
    cwd = Path(out_root or os.environ.get("TAOBAO_IMG_OUT_ROOT", os.getcwd())).resolve()
    today = dt.datetime.now().strftime("%Y-%m-%d")
    ts = dt.datetime.now().strftime("%H%M%S")
    state_dir = cwd / "taobao-images" / today / ts
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "collection-state.json"


def _load_state(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_state(path: Path, state: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Phase: plan
# ---------------------------------------------------------------------------

def phase_plan(
    image: str,
    *,
    apparel: Optional[bool] = None,
    category: Optional[str] = None,
    scene: Optional[str] = None,
    selling_points: Optional[str] = None,
    model_desc: Optional[str] = None,
    brand: Optional[str] = None,
    slogan: Optional[str] = None,
    style_refs: Optional[List[str]] = None,
    style_ref_desc: Optional[str] = None,
    out_root: Optional[str] = None,
) -> int:
    """Propose shots + write state. Returns 0 on success.

    CRITICAL: `apparel` must be explicitly True or False. Agent must NOT
    guess based on visual inspection of the image — it must ASK the user.
    Same for `category` (required when apparel=True) and other optional args
    (scene/selling_points): when shot needs them and they're missing, this
    function exits early with STATUS=needs_user_input so the agent can ask.
    """
    # Hard rule: apparel must be explicit. None means agent hasn't asked yet.
    if apparel is None:
        status = {
            "status": "needs_user_input",
            "reason": "apparel_not_specified",
            "message": (
                "必须先确认是服饰还是非服饰。Agent 禁止凭视觉判断，必须用 AskUserQuestion 问用户：\n"
                '  - "做服饰套图" → 重跑加 --apparel\n'
                '  - "做非服饰套图" → 重跑加 --no-apparel'
            ),
        }
        print(f"STATUS: {json.dumps(status, ensure_ascii=False)}")
        return 5

    # Apparel requires category; non-apparel rejects category.
    if apparel and not category:
        status = {
            "status": "needs_user_input",
            "reason": "apparel_category_missing",
            "message": (
                "服饰套图需要品类。Agent 必须用 AskUserQuestion 问用户，从 "
                f"{list(APPAREL_CATEGORIES)} 中选一个，然后重跑加 --category <值>。"
            ),
        }
        print(f"STATUS: {json.dumps(status, ensure_ascii=False)}")
        return 5
    if not apparel and category:
        print("ERROR: 非服饰套图不接受 --category 参数")
        return 5

    preset = PRESET_APPAREL if apparel else PRESET_NON_APPAREL
    state_path = _new_state_file(image, out_root)
    out_dir = state_path.parent

    # Populate shot specs with shared user-provided args.
    shared_args = {
        "scene": scene,
        "selling_points": selling_points,
        "category": category,
        "model_desc": model_desc,
        "brand": brand,
        "slogan": slogan,
        "style_refs": style_refs,
        "style_ref_desc": style_ref_desc,
    }

    shots: List[Dict[str, Any]] = []
    for item in preset:
        spec = {
            "id": item["id"],
            "type": item["type"],
            "label": item["label"],
            "image": image,
            "args": {k: v for k, v in shared_args.items() if v},
        }
        shots.append(spec)

    state: Dict[str, Any] = {
        "version": 1,
        "created_at": dt.datetime.now().isoformat(),
        "image": image,
        "apparel": apparel,
        "category": category,
        "out_dir": str(out_dir),
        "shots": shots,
        "phase": "plan",
    }
    _save_state(state_path, state)

    # Print markdown table for agent to surface to user.
    bundle = "服饰" if apparel else "非服饰"
    print(f"# 套图方案（{bundle}）\n")
    print(f"| # | 类型 | label | 需要参数 |")
    print(f"|---|------|-------|---------|")
    for i, s in enumerate(shots, 1):
        req = ", ".join(s["args"].keys()) if s["args"] else "—"
        print(f"| {i} | `{s['type']}` | {s['label']} | {req} |")

    # Status line for agent to parse.
    print(f"\n**输出目录**: `{out_dir}`")
    print(f"\n_state={state_path}")
    print(f"_total={len(shots)}")

    # Hard rule: if any shot is missing a required arg, exit early so agent
    # asks the user. DO NOT silently fall back to default values — that's
    # how visual-guess errors propagate downstream.
    missing = _check_missing_args(shots)
    if missing:
        missing_summary = ", ".join(
            f"{m['id']}: --{m['missing'].replace('_', '-')}" for m in missing
        )
        status = {
            "status": "needs_user_input",
            "reason": "shot_required_args_missing",
            "missing": missing,
            "message": (
                f"以下类型缺少必要参数：{missing_summary}。"
                "Agent 必须用 AskUserQuestion 问用户补齐，然后重跑 plan。"
                "禁止 agent 自己填默认值或猜测。"
            ),
        }
        print(f"\nSTATUS: {json.dumps(status, ensure_ascii=False)}")
        return 5

    status = {
        "status": "plan_complete",
        "state_file": str(state_path),
        "total": len(shots),
        "bundle": "apparel" if apparel else "non-apparel",
        "missing_required_args": [],
    }
    print(f"\nSTATUS: {json.dumps(status, ensure_ascii=False)}")
    return 0


def _check_missing_args(shots: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """For each shot, identify args the user still needs to supply."""
    missing = []
    for s in shots:
        t = s["type"]
        if t == "scene" and not s["args"].get("scene"):
            missing.append({"id": s["id"], "type": t, "missing": "scene"})
        if t in ("selling-point", "aplus") and not s["args"].get("selling_points"):
            missing.append({"id": s["id"], "type": t, "missing": "selling_points"})
        if t in ("model-wear", "multi-model", "flat-lay") and not s["args"].get("category"):
            missing.append({"id": s["id"], "type": t, "missing": "category"})
    return missing


# ---------------------------------------------------------------------------
# Phase: dispatch
# ---------------------------------------------------------------------------

def phase_dispatch(
    state_path: Path,
    selected_ids: Optional[List[str]] = None,
    overrides: Optional[Dict[str, Dict[str, Any]]] = None,
    max_workers: int = 4,
) -> int:
    """Run selected shots concurrently. Writes task-result-<id>.json per shot."""
    state = _load_state(state_path)
    out_dir = Path(state["out_dir"])
    overrides = overrides or {}

    # Filter to selected shots (or all if none specified).
    shots = state["shots"]
    if selected_ids:
        sel = set(selected_ids)
        shots = [s for s in shots if s["id"] in sel]

    state["phase"] = "dispatch"
    state["dispatch_started_at"] = dt.datetime.now().isoformat()
    state["selected"] = [s["id"] for s in shots]
    _save_state(state_path, state)

    def _run_shot(shot: Dict[str, Any]) -> Dict[str, Any]:
        shot_id = shot["id"]
        args = dict(shot.get("args", {}))
        # Apply per-shot overrides from user.
        args.update(overrides.get(shot_id, {}))
        shot_start = time.time()
        try:
            out_path = generate_mod.generate_one(
                shot["type"],
                shot["image"],
                out_dir=out_dir,
                scene_desc=args.get("scene"),
                selling_points=args.get("selling_points"),
                category=args.get("category"),
                model_desc=args.get("model_desc"),
                brand=args.get("brand"),
                product_hint=args.get("product_hint"),
                variations=args.get("variations"),
                style=args.get("style"),
                slogan=args.get("slogan"),
                style_refs=args.get("style_refs"),
                style_ref_desc=args.get("style_ref_desc"),
            )
            return {
                "id": shot_id,
                "type": shot["type"],
                "label": shot["label"],
                "status": "success",
                "abs_path": str(out_path),
                "elapsed_sec": round(time.time() - shot_start, 1),
            }
        except Exception as e:
            return {
                "id": shot_id,
                "type": shot["type"],
                "label": shot["label"],
                "status": "failed",
                "error": str(e),
                "error_type": type(e).__name__,
                "elapsed_sec": round(time.time() - shot_start, 1),
            }

    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_run_shot, s): s for s in shots}
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            if r["status"] != "success":
                errors.append(r)
            # Write per-shot fragment.
            frag_path = out_dir / f"task-result-{r['id']}.json"
            with frag_path.open("w", encoding="utf-8") as f:
                json.dump(r, f, ensure_ascii=False, indent=2)

    # Sort results by original shot order for predictable summary.
    order = {s["id"]: i for i, s in enumerate(state["shots"])}
    results.sort(key=lambda r: order.get(r["id"], 999))

    state["phase"] = "dispatch_complete"
    state["dispatch_completed_at"] = dt.datetime.now().isoformat()
    state["results"] = results
    _save_state(state_path, state)

    # Final status line (no markdown here — summary phase renders).
    status = {
        "status": "dispatch_complete",
        "total": len(results),
        "succeeded": sum(1 for r in results if r["status"] == "success"),
        "failed": sum(1 for r in results if r["status"] != "success"),
    }
    print(json.dumps(status, ensure_ascii=False))
    return 0 if not errors else 1  # agent still proceeds to summary


# ---------------------------------------------------------------------------
# Phase: summary
# ---------------------------------------------------------------------------

def phase_summary(state_path: Path) -> int:
    """Print markdown summary with inline images + write manifest."""
    state = _load_state(state_path)
    results = state.get("results", [])
    if not results:
        # Maybe dispatch wrote per-shot fragments but state wasn't updated.
        out_dir = Path(state["out_dir"])
        results = []
        for s in state["shots"]:
            frag = out_dir / f"task-result-{s['id']}.json"
            if frag.exists():
                with frag.open("r", encoding="utf-8") as f:
                    results.append(json.load(f))
        order = {s["id"]: i for i, s in enumerate(state["shots"])}
        results.sort(key=lambda r: order.get(r["id"], 999))

    succeeded = [r for r in results if r.get("status") == "success"]
    failed = [r for r in results if r.get("status") != "success"]

    print(f"# 套图生成完成\n")
    print(f"- 成功: **{len(succeeded)}** / {len(results)}")
    if failed:
        print(f"- 失败: {len(failed)}")
    print()

    if succeeded:
        print("## 成功的图片\n")
        for r in succeeded:
            print(f"- {r['label']} (`{r['type']}`)")
            print(f"  ![{r['label']}]({r['abs_path']})")
        print()

    if failed:
        print("## 失败的图片\n")
        for r in failed:
            print(f"- {r['label']} (`{r['type']}`): {r.get('error', 'unknown')}")
            if r.get("error_type"):
                print(f"  - error_type: `{r['error_type']}`")
        print()

    # Write manifest.
    out_dir = Path(state["out_dir"])
    manifest = {
        "version": 1,
        "created_at": dt.datetime.now().isoformat(),
        "image": state["image"],
        "bundle": "apparel" if state.get("apparel") else "non-apparel",
        "total": len(results),
        "succeeded": len(succeeded),
        "failed": len(failed),
        "assets": [
            {
                "id": r["id"],
                "type": r["type"],
                "label": r["label"],
                "src": r.get("abs_path"),
                "slot": i,
            }
            for i, r in enumerate(succeeded)
        ],
    }
    manifest_path = out_dir / "collection-manifest.json"
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"**Manifest**: `{manifest_path}`")
    status = {
        "status": "summary_complete",
        "manifest": str(manifest_path),
        "succeeded": len(succeeded),
        "failed": len(failed),
    }
    print(f"\nSTATUS: {json.dumps(status, ensure_ascii=False)}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="套图编排（plan/dispatch/summary）")
    parser.add_argument("--phase", required=True,
                        choices=["plan", "dispatch", "summary"])
    parser.add_argument("--state", help="state file 路径（plan 阶段可省略，自动新建）")
    parser.add_argument("--image", help="商品参考图（plan 阶段必填）")
    # Three-state apparel: must be explicitly True or False, not guessed.
    #   --apparel        → True
    #   --no-apparel     → False
    #   (neither passed) → None → phase_plan exits asking the agent to ask user
    parser.add_argument(
        "--apparel", action="store_true", default=None,
        help="服饰套图（必须显式指定；不指定会退出让 agent 问用户）",
    )
    parser.add_argument(
        "--no-apparel", dest="apparel", action="store_false",
        help="非服饰套图",
    )
    parser.add_argument("--category", choices=_prompts.APPAREL_CATEGORIES)
    parser.add_argument("--scene")
    parser.add_argument("--selling-points", dest="selling_points")
    parser.add_argument("--model-desc", dest="model_desc")
    parser.add_argument("--brand")
    parser.add_argument("--slogan", help="橱窗图：主标语（2-4 字）")
    parser.add_argument(
        "--style-ref", action="append", dest="style_refs", default=[],
        help="风格/灯光参考图（可重复），附加到 edits 接口作为额外参考图",
    )
    parser.add_argument(
        "--style-ref-desc", dest="style_ref_desc",
        help="风格参考描述：要从参考图借用的视觉调性",
    )
    parser.add_argument("--out-root", dest="out_root")
    parser.add_argument("--selected", help="dispatch 阶段：逗号分隔的 shot id 列表")
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()

    if args.phase == "plan":
        if not args.image:
            parser.error("--phase plan 需要 --image")
        return phase_plan(
            args.image,
            apparel=args.apparel,
            category=args.category,
            scene=args.scene,
            selling_points=args.selling_points,
            model_desc=args.model_desc,
            brand=args.brand,
            slogan=args.slogan,
            style_refs=args.style_refs or None,
            style_ref_desc=args.style_ref_desc,
            out_root=args.out_root,
        )

    if args.phase == "dispatch":
        if not args.state:
            parser.error("--phase dispatch 需要 --state")
        selected = args.selected.split(",") if args.selected else None
        return phase_dispatch(
            Path(args.state),
            selected_ids=selected,
            max_workers=args.max_workers,
        )

    if args.phase == "summary":
        if not args.state:
            parser.error("--phase summary 需要 --state")
        return phase_summary(Path(args.state))

    return 0


if __name__ == "__main__":
    sys.exit(main())
