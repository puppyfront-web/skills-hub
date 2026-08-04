"""Configuration resolver for taobao-product-image skill.

Priority (highest → lowest):
    1. Explicit argument (caller-provided dict)
    2. Environment variables (OPENAI_*, ZHIPU_*)
    3. Per-skill config file at ~/.openclaw/skill-state/taobao-product-image/config.json
    4. Built-in DEFAULT_CONFIG

Only Python stdlib. Compatible with Python 3.9+ (no `str | None` syntax).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

SKILL_NAME = "taobao-product-image"
STATE_DIR = Path(os.environ.get(
    "TAOBAO_IMG_STATE_DIR",
    str(Path.home() / ".openclaw" / "skill-state" / SKILL_NAME),
))
CONFIG_PATH = STATE_DIR / "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "openai": {
        # Default to official OpenAI; users on OpenAI-compatible proxies
        # (e.g. deepkey.top) should override base_url + api_key.
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        # gpt-image-1-mini is ~$0.005/image, cheap default.
        # Alternatives: gpt-image-1 (higher quality, more expensive).
        "image_model": "gpt-image-1-mini",
    },
    # Video backends — pick one via `video_backend` below.
    # Each backend has its own config block; only the active one is used.
    "video_backend": "zhipu",  # "zhipu" | "keling" | "wan" | "custom"
    "zhipu": {
        "api_key": "",
        # CogVideoX-Flash is free on Zhipu; fallback to paid -2 / -3.
        "default_video_model": "CogVideoX-Flash",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
    },
    # Placeholder for future / user-defined video backends.
    # Fill in to enable. Schema mirrors what the adapter expects.
    "keling": {
        "api_key": "",
        "base_url": "https://api.klingai.com/v1",
        "default_video_model": "kling-v1",
    },
    "wan": {
        # Aliyun DashScope Wan 2.1 image-to-video.
        "api_key": "",
        "base_url": "https://dashscope.aliyuncs.com/api/v1",
        "default_video_model": "wan2.1-i2v-plus",
    },
    "custom": {
        # Generic video backend for any OpenAI-style or custom API.
        # The custom adapter must implement the same protocol (see _video_backends.py).
        # For simple cases, set base_url + api_key + submit_path + poll_path_template
        # and the generic adapter will handle them.
        "api_key": "",
        "base_url": "",
        "default_video_model": "",
        "submit_path": "/videos/generations",
        "poll_path_template": "/async-result/{task_id}",
        "auth_scheme": "bearer",  # "bearer" | "jwt_zhipu" | "x-api-key"
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge override into base (override wins)."""
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _read_config_file() -> Dict[str, Any]:
    """Read config.json, returning {} if missing or invalid."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def ensure_config_file() -> Path:
    """Create the default config.json on disk if absent.

    First-run helper: writes DEFAULT_CONFIG to CONFIG_PATH so the user
    has a concrete file to edit. Returns the path.
    """
    if not STATE_DIR.exists():
        STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
    return CONFIG_PATH


def resolve_openai(override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resolve OpenAI config. Returns dict with base_url, api_key, image_model.

    Raises RuntimeError if no api_key is resolvable.
    """
    file_cfg = _read_config_file().get("openai", {})
    env_cfg = {
        "base_url": os.environ.get("OPENAI_BASE_URL"),
        "api_key": os.environ.get("OPENAI_API_KEY"),
        "image_model": os.environ.get("OPENAI_IMAGE_MODEL"),
    }
    env_cfg = {k: v for k, v in env_cfg.items() if v}

    merged = _deep_merge(
        DEFAULT_CONFIG["openai"],
        _deep_merge(file_cfg, env_cfg),
    )
    if override:
        merged = _deep_merge(merged, override)

    if not merged.get("api_key"):
        raise RuntimeError(
            "OPENAI_API_KEY 未配置。请通过以下任一方式设置：\n"
            f"  1. 编辑 {CONFIG_PATH}\n"
            "  2. 设置环境变量 OPENAI_API_KEY (和可选的 OPENAI_BASE_URL / OPENAI_IMAGE_MODEL)\n"
            "  3. 如使用 OpenAI 兼容代理（如 deepkey.top），同时设置 base_url\n"
            "获取官方 key: https://platform.openai.com/api-keys"
        )

    # Normalize: strip trailing slash from base_url so callers can safely do f"{base}/images/edits"
    merged["base_url"] = merged["base_url"].rstrip("/")
    return merged


def resolve_zhipu(override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resolve Zhipu (智谱) config. Returns dict with api_key, base_url, default_video_model.

    Raises RuntimeError if no api_key is resolvable.
    """
    file_cfg = _read_config_file().get("zhipu", {})
    env_cfg = {
        "api_key": os.environ.get("ZHIPU_API_KEY"),
        "default_video_model": os.environ.get("ZHIPU_VIDEO_MODEL"),
    }
    env_cfg = {k: v for k, v in env_cfg.items() if v}

    merged = _deep_merge(
        DEFAULT_CONFIG["zhipu"],
        _deep_merge(file_cfg, env_cfg),
    )
    if override:
        merged = _deep_merge(merged, override)

    if not merged.get("api_key"):
        raise RuntimeError(
            "ZHIPU_API_KEY 未配置。请通过以下任一方式设置：\n"
            f"  1. 编辑 {CONFIG_PATH}\n"
            "  2. 设置环境变量 ZHIPU_API_KEY\n"
            "获取 key: https://bigmodel.cn/console/usercenter/apikeys\n"
            "CogVideoX-Flash 免费可用。"
        )

    merged["base_url"] = merged["base_url"].rstrip("/")
    return merged


# Env var name template for each video backend.
_VIDEO_BACKEND_ENV = {
    "zhipu": ("ZHIPU_API_KEY", "ZHIPU_VIDEO_MODEL"),
    "keling": ("KELING_API_KEY", "KELING_VIDEO_MODEL"),
    "wan": ("DASHSCOPE_API_KEY", "WAN_VIDEO_MODEL"),
    "custom": ("CUSTOM_VIDEO_API_KEY", "CUSTOM_VIDEO_MODEL"),
}


def resolve_video(
    backend: Optional[str] = None,
    override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve the active video backend's config.

    Returns dict with: backend, api_key, base_url, default_video_model, plus
    any backend-specific fields (e.g. auth_scheme, paths for custom).

    Picks backend from (priority): override arg > config file `video_backend` > default "zhipu".
    Reads key/model from env vars specific to the backend.

    Raises RuntimeError if no api_key is resolvable for the active backend.
    """
    file_cfg_all = _read_config_file()
    file_video_backend = file_cfg_all.get("video_backend")
    env_video_backend = os.environ.get("VIDEO_BACKEND")

    chosen = backend or env_video_backend or file_video_backend or "zhipu"
    if chosen not in _VIDEO_BACKEND_ENV:
        raise RuntimeError(
            f"未知 video_backend: {chosen}. 允许: {list(_VIDEO_BACKEND_ENV)}"
        )

    default_block = DEFAULT_CONFIG.get(chosen, {})
    file_block = file_cfg_all.get(chosen, {})

    env_key_name, env_model_name = _VIDEO_BACKEND_ENV[chosen]
    env_block = {
        "api_key": os.environ.get(env_key_name),
        "default_video_model": os.environ.get(env_model_name),
    }
    env_block = {k: v for k, v in env_block.items() if v}

    merged = _deep_merge(
        default_block,
        _deep_merge(file_block, env_block),
    )
    if override:
        merged = _deep_merge(merged, override)

    if not merged.get("api_key"):
        raise RuntimeError(
            f"视频后端 '{chosen}' 的 API key 未配置。请通过以下任一方式设置：\n"
            f"  1. 编辑 {CONFIG_PATH} 的 '{chosen}' 块 + 'video_backend' 字段\n"
            f"  2. 设置环境变量 {env_key_name}"
            + (f" (和 {env_model_name})" if env_model_name else "")
        )

    if merged.get("base_url"):
        merged["base_url"] = merged["base_url"].rstrip("/")
    merged["backend"] = chosen
    return merged


def get_config_path() -> Path:
    """Return resolved config.json path (creates default if missing)."""
    ensure_config_file()
    return CONFIG_PATH


if __name__ == "__main__":
    # `python3 _config.py` prints the current resolved config (api_keys masked).
    ensure_config_file()
    print(f"Config file: {CONFIG_PATH}")
    try:
        oa = resolve_openai()
        print(f"\n[OpenAI]")
        print(f"  base_url:    {oa['base_url']}")
        print(f"  api_key:     {oa['api_key'][:8]}...{oa['api_key'][-4:] if len(oa['api_key']) > 12 else '***'}")
        print(f"  image_model: {oa['image_model']}")
    except RuntimeError as e:
        print(f"\n[OpenAI] {e}")
    # Resolve video (uses active backend from config.video_backend).
    try:
        vid = resolve_video()
        print(f"\n[Video backend: {vid['backend']}]")
        print(f"  base_url:             {vid.get('base_url','')}")
        print(f"  api_key:              {vid['api_key'][:8]}...{vid['api_key'][-4:] if len(vid['api_key']) > 12 else '***'}")
        print(f"  default_video_model:  {vid.get('default_video_model','')}")
    except RuntimeError as e:
        print(f"\n[Video] {e}")
