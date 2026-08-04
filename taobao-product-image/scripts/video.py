#!/usr/bin/env python3
"""Image-to-video entry point. Pluggable backend (zhipu / custom / keling / wan).

Default backend is `zhipu` (CogVideoX-Flash is free). Override via:
  - config.json `video_backend` field
  - env var VIDEO_BACKEND
  - CLI flag --backend

Usage:
    python3 video.py --image /path/or/url --prompt "..." [options]
    python3 video.py --list-backends

Output: prints `Saved: <abs_path>` on success.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import _config  # noqa: E402
import _gateway  # noqa: E402
import _prompts  # noqa: E402
import _video_backends as vback  # noqa: E402


def _default_out_dir() -> Path:
    cwd = Path(os.environ.get("TAOBAO_IMG_OUT_ROOT", os.getcwd())).resolve()
    today = dt.datetime.now().strftime("%Y-%m-%d")
    ts = dt.datetime.now().strftime("%H%M%S")
    out = cwd / "taobao-images" / today / ts
    out.mkdir(parents=True, exist_ok=True)
    return out


def _image_to_b64(path_or_url: str) -> str:
    """Read image into base64 (most video backends expect base64 data URL)."""
    mime, fname, data = _gateway.encode_image(path_or_url)
    return base64.b64encode(data).decode("ascii")


def generate_video(
    image: str,
    prompt: Optional[str] = None,
    *,
    backend: Optional[str] = None,
    model: Optional[str] = None,
    duration: int = 5,
    size: str = "1920x1080",
    out_dir: Optional[Path] = None,
    poll_timeout: int = 600,
    poll_interval: int = 10,
    product_hint: Optional[str] = None,
    video_cfg_override: Optional[Dict[str, Any]] = None,
) -> Path:
    """Generate a video from an image. Returns abs path to saved mp4."""
    if not image:
        raise ValueError("--image 必填")

    cfg = _config.resolve_video(backend=backend, override=video_cfg_override)
    active_backend = cfg["backend"]
    adapter = vback.get_backend(active_backend)
    if model:
        cfg["default_video_model"] = model
    chosen_model = cfg.get("default_video_model") or "default"

    final_prompt = _prompts.build_video_prompt(prompt, product_hint)
    image_b64 = _image_to_b64(image)

    # Submit.
    t0 = time.time()
    print(
        f"[video] backend={active_backend} model={chosen_model} "
        f"duration={duration}s size={size}",
        file=sys.stderr,
    )
    task_id = adapter["submit"](
        cfg, image_b64, final_prompt, chosen_model, duration, size,
    )
    print(f"[video] submitted: task_id={task_id}", file=sys.stderr)

    # Poll.
    video_url = adapter["poll"](
        cfg, task_id, timeout_sec=poll_timeout, interval_sec=poll_interval,
    )
    elapsed = round(time.time() - t0, 1)
    print(f"[video] ready in {elapsed}s", file=sys.stderr)

    # Download.
    if out_dir is None:
        out_dir = _default_out_dir()
    else:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%H%M%S")
    safe_name = (chosen_model or active_backend).replace("/", "-")
    out_path = out_dir / f"{safe_name}-{ts}.mp4"
    adapter["download"](video_url, out_path)
    return out_path.resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="图生视频（多后端）")
    parser.add_argument("--image", help="商品参考图（本地路径或 URL）")
    parser.add_argument("--prompt", help="视频描述；省略则用默认 cinematic 镜头")
    parser.add_argument(
        "--backend",
        choices=list(vback.BACKEND_REGISTRY.keys()),
        help="视频后端（默认 zhipu；可改 custom 走用户自定义 API）",
    )
    parser.add_argument("--model", help="覆盖默认视频模型")
    parser.add_argument("--duration", type=int, default=5, help="时长（秒）")
    parser.add_argument("--size", default="1920x1080", help="分辨率，如 1920x1080 / 1280x720")
    parser.add_argument("--out-dir", help="输出目录")
    parser.add_argument("--poll-timeout", type=int, default=600)
    parser.add_argument("--poll-interval", type=int, default=10)
    parser.add_argument("--product-hint", dest="product_hint")
    parser.add_argument(
        "--list-backends", action="store_true",
        help="列出所有已注册的视频后端",
    )
    args = parser.parse_args()

    if args.list_backends:
        print("已注册视频后端：")
        for name, adapter in vback.BACKEND_REGISTRY.items():
            print(f"  - {name}: submit={adapter['submit'].__name__}")
        return 0

    if not args.image:
        parser.error("--image 必填（除非 --list-backends）")

    try:
        out_path = generate_video(
            args.image,
            args.prompt,
            backend=args.backend,
            model=args.model,
            duration=args.duration,
            size=args.size,
            out_dir=Path(args.out_dir) if args.out_dir else None,
            poll_timeout=args.poll_timeout,
            poll_interval=args.poll_interval,
            product_hint=args.product_hint,
        )
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
    except NotImplementedError as e:
        print(f"ERROR (backend not implemented): {e}")
        return 7
    except ValueError as e:
        print(f"ERROR (invalid args): {e}")
        return 5
    except Exception as e:
        print(f"ERROR (unknown): {e}")
        traceback.print_exc()
        return 6


if __name__ == "__main__":
    sys.exit(main())
