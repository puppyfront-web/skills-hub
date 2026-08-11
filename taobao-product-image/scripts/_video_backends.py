"""Video backend adapters — pluggable image-to-video providers.

Each adapter implements three functions:
  - submit(cfg, image_b64, prompt, model, duration, size) -> task_id
  - poll(cfg, task_id, timeout_sec, interval_sec) -> video_url
  - download(video_url, out_path) -> out_path

The dispatcher `get_backend(name)` returns the adapter module for a given name.

Built-in adapters:
  - zhipu    : CogVideoX-Flash (free) / CogVideoX-2 / CogVideoX-3
  - keling   : Placeholder for Kuaishou Kling (not implemented; raises NotImplementedError)
  - wan      : Placeholder for Aliyun DashScope Wan 2.1 (not implemented; raises NotImplementedError)
  - custom   : Generic OpenAI-style adapter for any compatible API

To add a new backend:
  1. Implement a module under this file (or as a separate _video_backend_<name>.py)
     with submit/poll/download signatures matching the protocol above.
  2. Register it in BACKEND_REGISTRY.

All adapters reuse `_gateway._http_request` + `_gateway._zhipu_jwt` for HTTP/JWT.
Pure stdlib; compatible with Python 3.9+.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict

import _gateway


# ---------------------------------------------------------------------------
# Zhipu (CogVideoX) — fully implemented
# ---------------------------------------------------------------------------

def zhipu_submit(
    cfg: Dict[str, Any], image_b64: str, prompt: str,
    model: str, duration: int, size: str,
) -> str:
    base_url = cfg["base_url"].rstrip("/")
    token = _gateway._zhipu_jwt(cfg["api_key"])
    url = f"{base_url}/videos/generations"
    payload = {
        "model": model,
        "image_request": {"url": f"data:image/jpeg;base64,{image_b64}"},
        "prompt": prompt,
        "video_request": {"duration": duration, "resolution": size},
    }
    body = json.dumps(payload).encode()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    status, resp_body = _gateway._http_request("POST", url, headers, body, timeout=60)
    data = json.loads(resp_body)
    if "error" in data:
        raise _gateway.GatewayError(
            f"Zhipu submit error: {data['error']}", status=status, body=resp_body[:500]
        )
    task_id = data.get("id") or data.get("task_id")
    if not task_id:
        raise _gateway.GatewayError(
            "Zhipu submit: 无 task_id 返回", status=status, body=resp_body[:500]
        )
    return task_id


def zhipu_poll(
    cfg: Dict[str, Any], task_id: str,
    timeout_sec: int, interval_sec: int,
) -> str:
    base_url = cfg["base_url"].rstrip("/")
    token = _gateway._zhipu_jwt(cfg["api_key"])
    url = f"{base_url}/async-result/{task_id}"
    headers = {"Authorization": f"Bearer {token}"}
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        status, resp_body = _gateway._http_request(
            "GET", url, headers, None, timeout=30, retry_transient=True
        )
        data = json.loads(resp_body)
        task_status = data.get("task_status", "")
        if task_status == "SUCCESS":
            video_url = (
                (data.get("video_result") or {}).get("url")
                or data.get("video_url")
            )
            if not video_url:
                raise _gateway.GatewayError(
                    "Zhipu SUCCESS 但无 video_url", status=status, body=resp_body[:500]
                )
            return video_url
        if task_status == "FAIL":
            raise _gateway.GatewayError(
                f"Zhipu 视频生成失败: {data}", status=status, body=resp_body[:500]
            )
        time.sleep(interval_sec)
    raise _gateway.TransientError(
        f"Zhipu 轮询超时 {timeout_sec}s, task_id={task_id}"
    )


# ---------------------------------------------------------------------------
# Custom (generic OpenAI-style) — implemented, configurable via cfg fields
# ---------------------------------------------------------------------------

def custom_submit(
    cfg: Dict[str, Any], image_b64: str, prompt: str,
    model: str, duration: int, size: str,
) -> str:
    """Generic adapter. Assumes the target API accepts Zhipu-like payload.

    Configurable fields (from cfg):
      - base_url
      - submit_path (default "/videos/generations")
      - auth_scheme: "bearer" | "jwt_zhipu" | "x-api-key"
    """
    base_url = cfg["base_url"].rstrip("/")
    submit_path = cfg.get("submit_path", "/videos/generations")
    auth_scheme = cfg.get("auth_scheme", "bearer")
    url = f"{base_url}{submit_path}"

    if auth_scheme == "jwt_zhipu":
        auth_value = f"Bearer {_gateway._zhipu_jwt(cfg['api_key'])}"
        auth_header = "Authorization"
    elif auth_scheme == "x-api-key":
        auth_value = cfg["api_key"]
        auth_header = "x-api-key"
    else:  # bearer
        auth_value = f"Bearer {cfg['api_key']}"
        auth_header = "Authorization"

    payload = {
        "model": model,
        "image_request": {"url": f"data:image/jpeg;base64,{image_b64}"},
        "prompt": prompt,
        "video_request": {"duration": duration, "resolution": size},
    }
    body = json.dumps(payload).encode()
    headers = {auth_header: auth_value, "Content-Type": "application/json"}
    status, resp_body = _gateway._http_request("POST", url, headers, body, timeout=60)
    data = json.loads(resp_body)
    if "error" in data:
        raise _gateway.GatewayError(
            f"Custom submit error: {data['error']}", status=status, body=resp_body[:500]
        )
    task_id = data.get("id") or data.get("task_id") or data.get("requestId")
    if not task_id:
        raise _gateway.GatewayError(
            "Custom submit: 无 task_id 返回", status=status, body=resp_body[:500]
        )
    return task_id


def custom_poll(
    cfg: Dict[str, Any], task_id: str,
    timeout_sec: int, interval_sec: int,
) -> str:
    base_url = cfg["base_url"].rstrip("/")
    poll_template = cfg.get("poll_path_template", "/async-result/{task_id}")
    auth_scheme = cfg.get("auth_scheme", "bearer")
    url = f"{base_url}{poll_template.format(task_id=task_id)}"

    if auth_scheme == "jwt_zhipu":
        auth_value = f"Bearer {_gateway._zhipu_jwt(cfg['api_key'])}"
        auth_header = "Authorization"
    elif auth_scheme == "x-api-key":
        auth_value = cfg["api_key"]
        auth_header = "x-api-key"
    else:
        auth_value = f"Bearer {cfg['api_key']}"
        auth_header = "Authorization"

    headers = {auth_header: auth_value}
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        status, resp_body = _gateway._http_request(
            "GET", url, headers, None, timeout=30, retry_transient=True
        )
        data = json.loads(resp_body)
        task_status = data.get("task_status") or data.get("status") or ""
        if task_status.upper() in ("SUCCESS", "SUCCEEDED", "COMPLETED"):
            video_url = (
                (data.get("video_result") or {}).get("url")
                or data.get("video_url")
                or (data.get("output") or {}).get("video_url")
                or (data.get("data") or {}).get("video_url")
            )
            if not video_url:
                raise _gateway.GatewayError(
                    "Custom SUCCESS 但无 video_url", status=status, body=resp_body[:500]
                )
            return video_url
        if task_status.upper() in ("FAIL", "FAILED", "ERROR"):
            raise _gateway.GatewayError(
                f"Custom 视频生成失败: {data}", status=status, body=resp_body[:500]
            )
        time.sleep(interval_sec)
    raise _gateway.TransientError(
        f"Custom 轮询超时 {timeout_sec}s, task_id={task_id}"
    )


# ---------------------------------------------------------------------------
# Placeholder adapters — raise NotImplementedError with helpful messages
# ---------------------------------------------------------------------------

def _not_implemented(name: str, why: str) -> Callable:
    def _raise(*a, **kw):
        raise NotImplementedError(
            f"视频后端 '{name}' 暂未实现：{why}\n"
            f"请改用 'zhipu' 或 'custom' 后端，或在 _video_backends.py 实现该 adapter。"
        )
    return _raise


# ---------------------------------------------------------------------------
# Shared download (all backends use the same — just a URL fetch)
# ---------------------------------------------------------------------------

def download(video_url: str, out_path: Path) -> Path:
    """Download a video URL to local path. Shared by all backends."""
    data = _gateway._fetch_url_raw(video_url)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    return out_path


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

BACKEND_REGISTRY: Dict[str, Dict[str, Callable]] = {
    "zhipu": {"submit": zhipu_submit, "poll": zhipu_poll, "download": download},
    "custom": {"submit": custom_submit, "poll": custom_poll, "download": download},
    # Placeholders — users can implement these in the future.
    "keling": {
        "submit": _not_implemented("keling", "需调用 https://klingai.com 开放接口；建议在 custom 后端用 base_url+auth_scheme 配置"),
        "poll": _not_implemented("keling", "需调用 https://klingai.com 开放接口"),
        "download": download,
    },
    "wan": {
        "submit": _not_implemented("wan", "需调用 Aliyun DashScope 接口；建议在 custom 后端用 base_url+auth_scheme 配置"),
        "poll": _not_implemented("wan", "需调用 Aliyun DashScope 接口"),
        "download": download,
    },
}


def get_backend(name: str) -> Dict[str, Callable]:
    """Return the adapter functions for a given backend name."""
    if name not in BACKEND_REGISTRY:
        raise ValueError(
            f"未知视频后端: {name}. 已注册: {list(BACKEND_REGISTRY)}"
        )
    return BACKEND_REGISTRY[name]
