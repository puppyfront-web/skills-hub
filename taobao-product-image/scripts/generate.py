#!/usr/bin/env python3
"""Single-image generation entry point for taobao-product-image skill.

Usage:
    python3 generate.py --type white-bg --image /path/or/url [options]
    python3 generate.py --self-test

Output:
    On success: prints `Saved: <abs_path>` (single line protocol).
    On failure: prints `ERROR: <message>` and exits non-zero.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

# Allow running as a script: add this dir to sys.path for sibling imports.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import _config  # noqa: E402
import _gateway  # noqa: E402
import _prompts  # noqa: E402


def _default_out_dir() -> Path:
    """Resolve output dir: $CWD/taobao-images/YYYY-MM-DD/."""
    cwd = Path(os.environ.get("TAOBAO_IMG_OUT_ROOT", os.getcwd())).resolve()
    today = dt.datetime.now().strftime("%Y-%m-%d")
    ts = dt.datetime.now().strftime("%H%M%S")
    out = cwd / "taobao-images" / today / ts
    out.mkdir(parents=True, exist_ok=True)
    return out


def _file_extension(image_type: str) -> str:
    return "png"  # OpenAI returns PNG


def generate_one(
    image_type: str,
    image: str,
    *,
    out_dir: Optional[Path] = None,
    model: Optional[str] = None,
    size: Optional[str] = None,
    quality: str = "low",
    scene_desc: Optional[str] = None,
    selling_points: Optional[str] = None,
    product_hint: Optional[str] = None,
    brand: Optional[str] = None,
    category: Optional[str] = None,
    model_desc: Optional[str] = None,
    variations: Optional[str] = None,
    style: Optional[str] = None,
    slogan: Optional[str] = None,
    style_refs: Optional[list] = None,
    style_ref_desc: Optional[str] = None,
    openai_cfg_override: Optional[Dict[str, Any]] = None,
) -> Path:
    """Generate a single image. Returns absolute path to saved file.

    style_refs: optional list of extra reference image paths/URLs (style/lighting).
        Passed to the edits endpoint as additional image fields. Ignored for
        text-to-image types (aplus / banner-without-image).
    style_ref_desc: natural-language description of what to borrow from the
        style ref (e.g. "warm creamy tone, soft softbox"). Injected into prompt.

    Raises on failure — caller should catch and report.
    """
    # Validate type.
    if image_type not in _prompts.ALL_IMAGE_TYPES:
        raise ValueError(
            f"未知 type: {image_type}. 允许: {list(_prompts.ALL_IMAGE_TYPES)}"
        )

    # Validate apparel category required.
    if image_type in _prompts.APPAREL_TYPES and not category:
        raise ValueError(
            f"{image_type} 需要 --category 参数 "
            f"(one of: {list(_prompts.APPAREL_CATEGORIES)})"
        )

    # Resolve config.
    cfg = _config.resolve_openai(openai_cfg_override)
    if model:
        cfg["image_model"] = model

    # Resolve size.
    if not size:
        size = _prompts.TYPE_SIZES[image_type]

    # Banner & A+ both support two modes: with reference image → edits; without → generations.
    # A+ traditionally was text-only, but edits mode (using the white-bg main image as
    # reference) yields higher product fidelity and dodges proxy limits on generations.
    has_reference_image = bool(image)

    # Build prompt.
    prompt = _prompts.build_prompt(
        image_type,
        scene_desc=scene_desc,
        selling_points=selling_points,
        product_hint=product_hint,
        brand=brand,
        category=category,
        model_desc=model_desc,
        variations=variations,
        style=style,
        slogan=slogan,
        has_reference_image=has_reference_image,
        style_ref=style_ref_desc,
    )

    # Resolve output dir + filename.
    if out_dir is None:
        out_dir = _default_out_dir()
    else:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

    ts = dt.datetime.now().strftime("%H%M%S")
    ext = _file_extension(image_type)
    out_path = out_dir / f"{image_type}-{ts}.{ext}"

    # Dispatch: generations (text-to-image) when no reference image.
    # edits (image-to-image) when a reference image is provided.
    # A+ 详情图：有 image → edits（基于白底图，商品还原度高，且绕开代理对 generations 的限制）
    #            无 image → generations（纯文生图，兜底）
    use_generations = not image
    if use_generations:
        # Style refs are meaningless for pure text-to-image; warn if provided.
        if style_refs:
            # Not an error — just ignore, since caller may pass through uniformly.
            pass
        img_bytes = _gateway.call_openai_generations(
            cfg, prompt, cfg["image_model"], size=size, quality=quality
        )
    else:
        # Empty-input guard (hardcoded rule from linkfox lineage).
        if not image:
            raise ValueError(
                "imageUrls 为空 — 必须提供 --image (本地路径或公开 URL)"
            )
        img_bytes = _gateway.call_openai_edits(
            cfg, image, prompt, cfg["image_model"],
            size=size, quality=quality, extra_images=style_refs,
        )

    out_path.write_bytes(img_bytes)
    return out_path.resolve()


def self_test() -> int:
    """Smoke-test OpenAI connectivity with the cheapest possible call."""
    print("Running self-test...")
    try:
        cfg = _config.resolve_openai()
    except RuntimeError as e:
        print(f"FAIL: {e}")
        return 1

    print(f"  base_url:    {cfg['base_url']}")
    print(f"  api_key:     {cfg['api_key'][:8]}...{cfg['api_key'][-4:]}")
    print(f"  image_model: {cfg['image_model']}")

    out_dir = _default_out_dir()
    try:
        out = generate_one(
            "aplus",  # text-to-image, doesn't need a reference image
            image="",
            selling_points="自检测试, hello world",
            out_dir=out_dir,
            quality="low",
        )
        print(f"PASS: generated test image -> {out}")
        return 0
    except _gateway.AuthError as e:
        print(f"FAIL (auth): {e}")
        print(f"  body: {e.body[:300]}")
        return 2
    except _gateway.ContentRejectedError as e:
        print(f"FAIL (content rejected): {e}")
        print(f"  body: {e.body[:300]}")
        return 3
    except Exception as e:
        print(f"FAIL (unknown): {e}")
        traceback.print_exc()
        return 4


def main() -> int:
    parser = argparse.ArgumentParser(description="单张商品图生成入口")
    parser.add_argument("--self-test", action="store_true", help="自检 OpenAI 连通性")
    parser.add_argument("--type", choices=_prompts.ALL_IMAGE_TYPES, help="图类型")
    parser.add_argument("--image", help="商品参考图（本地路径或 URL，A+ 类型可省略）")
    parser.add_argument("--out-dir", help="输出目录（默认 $CWD/taobao-images/YYYY-MM-DD/时间戳/）")
    parser.add_argument("--model", help="覆盖默认 image_model")
    parser.add_argument("--size", help="覆盖默认尺寸，如 1024x1024")
    parser.add_argument("--quality", default="low", choices=["low", "medium", "high"])
    # Non-apparel params.
    parser.add_argument("--scene", help="场景图：场景描述")
    parser.add_argument("--selling-point", dest="selling_points", help="卖点图/A+：卖点文案")
    parser.add_argument(
        "--product-hint",
        help="商品类别提示（如'蓝牙音箱'/'连衣裙'）。必须由用户提供，禁止 agent 凭视觉猜测填默认值。",
    )
    parser.add_argument("--brand", help="A+ / 橱窗图：品牌名")
    # Apparel params.
    parser.add_argument("--category", choices=_prompts.APPAREL_CATEGORIES, help="服饰品类")
    parser.add_argument("--model-desc", help="模特试穿：自定义模特描述")
    parser.add_argument("--variations", help="多模特：变体描述")
    parser.add_argument("--style", help="平铺：folded/hanging/steamed/laid")
    # Banner (橱窗图) params.
    parser.add_argument(
        "--slogan",
        help="橱窗图：主标语（2-4 字最佳），省略则用 selling-point 或留空",
    )
    # Style reference params (multi-image edit support).
    parser.add_argument(
        "--style-ref",
        action="append",
        dest="style_refs",
        default=[],
        help="风格/灯光参考图（本地路径或 URL，可重复）。附加到 edits 接口作为额外参考图",
    )
    parser.add_argument(
        "--style-ref-desc",
        dest="style_ref_desc",
        help="风格参考描述：要从参考图借用的视觉调性，如 'warm creamy tone, soft softbox'",
    )
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    if not args.type:
        parser.error("--type 必填（除非 --self-test）")

    # Empty-input guard.
    # aplus + banner can be text-only (no image) → generations; all others need image.
    needs_image = args.type not in ("aplus", "banner")
    if needs_image and not args.image:
        parser.error(f"--image 必填（type={args.type} 需要参考图；aplus/banner 可无图走纯文生图）")

    try:
        out_path = generate_one(
            args.type,
            args.image or "",
            out_dir=Path(args.out_dir) if args.out_dir else None,
            model=args.model,
            size=args.size,
            quality=args.quality,
            scene_desc=args.scene,
            selling_points=args.selling_points,
            product_hint=args.product_hint,
            brand=args.brand,
            category=args.category,
            model_desc=args.model_desc,
            variations=args.variations,
            style=args.style,
            slogan=args.slogan,
            style_refs=args.style_refs or None,
            style_ref_desc=args.style_ref_desc,
        )
        # Skill-internal protocol line. Agent parses this for abs path.
        print(f"Saved: {out_path}")
        return 0
    except _gateway.AuthError as e:
        print(f"ERROR (auth {e.status}): {e}")
        print(f"  body: {e.body[:300]}")
        return 2
    except _gateway.ContentRejectedError as e:
        print(f"ERROR (content rejected {e.status}): {e}")
        print(f"  body: {e.body[:300]}")
        return 3
    except _gateway.TransientError as e:
        print(f"ERROR (transient): {e}")
        return 4
    except ValueError as e:
        print(f"ERROR (invalid args): {e}")
        return 5
    except Exception as e:
        print(f"ERROR (unknown): {e}")
        traceback.print_exc()
        return 6


if __name__ == "__main__":
    sys.exit(main())
