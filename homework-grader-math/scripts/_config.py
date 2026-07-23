"""
技能独立配置模块。

本技能不绑定任何 agent runtime（openclaw / zcode 等），配置自包含。
配置来源优先级（高 → 低）：
  1. 命令行显式参数（--host/--token/--model 等）
  2. 环境变量（HWM_* 前缀，避免和其他工具冲突）
  3. 技能自己的 config.json（~/.openclaw/skill-state/homework-grader-math/config.json）
  4. （可选 fallback）~/.openclaw/openclaw.json 的 gateway 配置——仅为兼容 invoice-ocr 旧习惯，本技能不强依赖

config.json 结构：
{
  "base_url": "https://deepkey.top",     # OpenAI 兼容 API 地址
  "api_key": "sk-...",                   # API key
  "model": "gpt-5.4",                    # 默认模型（文本+视觉都用它）
  "text_model": null,                    # 可选：文本专用模型（不填用 model）
  "vision_model": null                   # 可选：视觉专用模型（不填用 model）
}

运行时状态（config.json）放在 ~/​.openclaw/skill-state/ 下，仅为复用已有的状态目录，
不代表依赖 openclaw runtime——任何环境都能用，换个 state_dir 即可。
"""

import json
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------

# 状态目录：技能运行时数据（config / 题库 / 学情 / 卡片）。
# 解析优先级（高 → 低）：
#   1. 环境变量 HWM_STATE_DIR
#   2. 代码旁边的 .state/ 目录（技能自包含模式，迁移到 skills-hub 时用）
#   3. ~/.openclaw/skill-state/homework-grader-math（默认，兼容旧部署）
#
# 当技能被整体迁移到其他目录（如 skills-hub）时，把 .state/ 一起迁过去，
# 代码会自动找到自己的状态，无需改配置。
DEFAULT_STATE_DIR = Path.home() / ".openclaw" / "skill-state" / "homework-grader-math"
# 代码旁的状态目录：scripts 的父目录下的 .state/
_BUNDLED_STATE_DIR = Path(__file__).resolve().parent.parent / ".state"
CONFIG_PATH = DEFAULT_STATE_DIR / "config.json"


def state_dir() -> Path:
    """返回技能状态目录。

    优先级：HWM_STATE_DIR 环境变量 > 代码旁 .state/（若存在）> 默认 ~/.openclaw/...
    """
    env = os.environ.get("HWM_STATE_DIR")
    if env:
        return Path(env)
    # 代码旁的 .state/ 存在就用它（自包含模式）
    if _BUNDLED_STATE_DIR.is_dir():
        return _BUNDLED_STATE_DIR
    return DEFAULT_STATE_DIR


def config_path() -> Path:
    """返回 config.json 路径。"""
    return state_dir() / "config.json"


# ---------------------------------------------------------------------------
# 默认配置（首次使用时初始化用）
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "base_url": "https://deepkey.top",
    "api_key": "sk-PXyOjxhJEbKOEz2EOqrZ0sIsWkwZ3oVHjznYzoxpbO0Kia4c",
    "model": "gpt-5.4",          # 文本+视觉都用这个（gpt-5.4 经测试支持看图）
    "text_model": None,          # 不填则用 model
    "vision_model": None,        # 不填则用 model
}


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """加载 config.json。不存在则用 DEFAULT_CONFIG 初始化。"""
    p = config_path()
    if not p.is_file():
        save_config(DEFAULT_CONFIG)
        return {**DEFAULT_CONFIG}
    try:
        with open(p, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        # 补默认字段
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    except (json.JSONDecodeError, OSError):
        return {**DEFAULT_CONFIG}


def save_config(cfg: dict) -> None:
    """保存 config.json。"""
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def update_config(**kwargs) -> dict:
    """更新部分配置字段，返回新配置。"""
    cfg = load_config()
    cfg.update({k: v for k, v in kwargs.items() if v is not None})
    save_config(cfg)
    return cfg


# ---------------------------------------------------------------------------
# 解析连接信息（供 _gateway.py 调用）
# ---------------------------------------------------------------------------

def resolve_connection(host: str | None = None, port: int | None = None,
                       token: str | None = None, model: str | None = None) -> dict:
    """解析模型服务的连接信息。

    优先级：显式参数 > 环境变量 > config.json > （可选 openclaw fallback）。
    返回 {base_url, api_key, model}。

    注意：本函数返回的是完整的 base_url（如 https://deepkey.top），
    而非 host:port。这是为了直接对接 OpenAI 兼容的云端 API。
    """
    cfg = load_config()

    # 1. 显式参数（来自命令行）
    if token and host:
        # 调用方用的是 host:port 风格（兼容旧接口）
        port_str = f":{port}" if port else ""
        return {
            "base_url": f"http://{host}{port_str}",
            "api_key": token,
            "model": model or cfg.get("model", "gpt-5.4"),
        }

    # 2. 环境变量（HWM_ 前缀 = HomeWork Math）
    env_base = os.environ.get("HWM_BASE_URL")
    env_key = os.environ.get("HWM_API_KEY")
    env_model = os.environ.get("HWM_MODEL")
    if env_base and env_key:
        return {
            "base_url": env_base,
            "api_key": env_key,
            "model": model or env_model or cfg.get("model", "gpt-5.4"),
        }

    # 3. config.json（主路径）
    if cfg.get("base_url") and cfg.get("api_key"):
        return {
            "base_url": cfg["base_url"],
            "api_key": cfg["api_key"],
            "model": model or cfg.get("model", "gpt-5.4"),
        }

    # 4. 可选 fallback：openclaw gateway（仅为兼容旧习惯，本技能不强依赖）
    oc_token, oc_host, oc_port = _try_openclaw_gateway()
    if oc_token:
        port_str = f":{oc_port}" if oc_port else ""
        return {
            "base_url": f"http://{oc_host}{port_str}",
            "api_key": oc_token,
            "model": model or cfg.get("model", "openclaw/default"),
        }

    # 都没有：返回空，上层会提示配置缺失
    return {"base_url": "", "api_key": "", "model": model or "gpt-5.4"}


def _try_openclaw_gateway() -> tuple:
    """尝试从 ~/​.openclaw/openclaw.json 读 gateway 配置（可选 fallback）。

    返回 (token, host, port) 或 (None, None, None)。
    本技能不强依赖 openclaw，这只是一个兼容性 fallback。
    """
    import re
    oc_paths = [
        Path.home() / ".openclaw" / "openclaw.json",
        Path.home() / ".openclaw" / "config.yaml",
    ]
    for p in oc_paths:
        if not p.is_file():
            continue
        try:
            if p.suffix == ".json":
                with open(p) as f:
                    cfg = json.load(f)
                gw = cfg.get("gateway", {})
                auth = gw.get("auth", {})
                token = auth.get("token") or auth.get("password")
                host = gw.get("http", {}).get("host", "127.0.0.1")
                port = gw.get("http", {}).get("port", 18789)
                if token:
                    return token, host, port
        except Exception:
            continue
    return None, None, None


# ---------------------------------------------------------------------------
# 命令行辅助
# ---------------------------------------------------------------------------

def is_configured() -> bool:
    """检查是否已配置可用连接。"""
    conn = resolve_connection()
    return bool(conn.get("base_url") and conn.get("api_key"))


def config_status_text() -> str:
    """返回配置状态的人类可读文本（给 setup 命令用）。"""
    conn = resolve_connection()
    if not conn.get("base_url") or not conn.get("api_key"):
        return ("❌ 还没配置模型服务。\n"
                "运行 setup 命令配置：\n"
                "  python3 grade.py --setup --base-url <URL> --api-key <KEY> --model <MODEL>\n"
                "或编辑 config.json：\n"
                f"  {config_path()}")
    # 脱敏显示 key
    key = conn["api_key"]
    masked = key[:6] + "***" + key[-4:] if len(key) > 12 else "***"
    return (f"✅ 已配置\n"
            f"  服务地址: {conn['base_url']}\n"
            f"  模型: {conn['model']}\n"
            f"  API Key: {masked}\n"
            f"  配置文件: {config_path()}")
