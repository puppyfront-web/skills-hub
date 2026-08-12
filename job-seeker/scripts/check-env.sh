#!/usr/bin/env bash
# job-seeker/scripts/check-env.sh
# 检查 V2EX / LinkedIn / 小红书 三个拓岗平台的可用性
#
# 用法：
#   bash check-env.sh                    # 检查全部平台（人类可读输出）
#   bash check-env.sh --platform v2ex    # 只检查指定平台
#   bash check-env.sh --format json      # JSON 输出（供脚本解析）
#   bash check-env.sh --help

set -uo pipefail

# ---------- 参数解析 ----------
PLATFORM=""
FORMAT="text"
for arg in "$@"; do
  case "$arg" in
    --platform) shift_next=1 ;;
    --format) shift_next=2 ;;
    --help|-h)
      sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      if [[ "${shift_next:-0}" == "1" ]]; then PLATFORM="$arg"; shift_next=0
      elif [[ "${shift_next:-0}" == "2" ]]; then FORMAT="$arg"; shift_next=0
      fi
      ;;
  esac
done

# ---------- 路径定位 ----------
AGENT_REACH=""
for candidate in "$HOME/.local/bin/agent-reach" "/usr/local/bin/agent-reach" "$(command -v agent-reach 2>/dev/null)"; do
  [[ -x "$candidate" ]] && AGENT_REACH="$candidate" && break
done

# ---------- 检查函数 ----------
# 每个 check_* 函数把状态写到全局：STATUS_<PLATFORM>、DETAIL_<PLATFORM>、HINT_<PLATFORM>
STATUS_V2EX=""; DETAIL_V2EX=""; HINT_V2EX=""
STATUS_XHS="";  DETAIL_XHS="";  HINT_XHS=""
STATUS_LI="";   DETAIL_LI="";   HINT_LI=""

check_v2ex() {
  # V2EX 用公开 API 直接 ping，不依赖 agent-reach
  local http_code="000"
  http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    "https://www.v2ex.com/api/topics/hot.json" \
    -H "User-Agent: agent-reach/1.0" 2>/dev/null) || http_code="000"
  [[ -z "$http_code" ]] && http_code="000"
  if [[ "$http_code" == "200" ]]; then
    STATUS_V2EX="ok"
    DETAIL_V2EX="公开 API 可达（HTTP 200）"
    HINT_V2EX=""
  else
    STATUS_V2EX="fail"
    DETAIL_V2EX="API 不可达（HTTP ${http_code:-000}）"
    HINT_V2EX="检查网络；若大陆访问被墙：agent-reach configure proxy http://user:pass@ip:port"
  fi
}

# 通用：从 doctor 输出判断某平台状态
# $1 = 平台关键字（如 "小红书" / "LinkedIn"），$2 = 平台短名（用于全局变量）
parse_from_doctor() {
  local keyword="$1" short="$2"
  local doctor_out="${3:-}"
  if [[ -z "$doctor_out" ]]; then
    eval "STATUS_${short}=\"unknown\""
    eval "DETAIL_${short}=\"agent-reach 不可用，无法检查\""
    eval "HINT_${short}=\"安装 agent-reach: pipx install agent-reach\""
    return
  fi
  # 在 doctor 输出里找含 keyword 的行
  local match
  match=$(echo "$doctor_out" | grep -i "$keyword" | head -1 || true)
  if [[ -z "$match" ]]; then
    eval "STATUS_${short}=\"unknown\""
    eval "DETAIL_${short}=\"doctor 输出未识别到 $keyword\""
    return
  fi
  # 判定：行首带 ✅ 表示可用；含"未配置"/"未安装"表示需配置；否则按 fallback 测试
  if echo "$match" | grep -q "✅"; then
    eval "STATUS_${short}=\"ok\""
    eval "DETAIL_${short}=\"$(echo "$match" | sed 's/^ *//; s/  */ /g')\""
    eval "HINT_${short}=\"\""
  elif echo "$match" | grep -qE "未配置|未安装|配置后可用|--"; then
    eval "STATUS_${short}=\"unconfigured\""
    eval "DETAIL_${short}=\"$(echo "$match" | sed 's/^ *//; s/  */ /g')\""
    if [[ "$short" == "XHS" ]]; then
      eval "HINT_${short}=\"docker run -d --name xiaohongshu-mcp -p 18060:18060 --platform linux/amd64 xpzouying/xiaohongshu-mcp && mcporter config add xiaohongshu http://localhost:18060/mcp\""
    elif [[ "$short" == "LI" ]]; then
      eval "HINT_${short}=\"pip install linkedin-scraper-mcp && (起服务) && mcporter config add linkedin http://localhost:3000/mcp\""
    fi
  else
    eval "STATUS_${short}=\"unknown\""
    eval "DETAIL_${short}=\"$(echo "$match" | sed 's/^ *//; s/  */ /g')\""
  fi
}

# ---------- 主流程 ----------

DOCTOR_OUT=""
if [[ -n "$AGENT_REACH" ]]; then
  DOCTOR_OUT=$("$AGENT_REACH" doctor 2>&1 || true)
fi

# 检查 V2EX（独立，因为用 curl 而非 agent-reach）
if [[ -z "$PLATFORM" || "$PLATFORM" == "v2ex" ]]; then
  check_v2ex
fi

# 检查小红书（基于 agent-reach doctor）
if [[ -z "$PLATFORM" || "$PLATFORM" == "xiaohongshu" ]]; then
  parse_from_doctor "小红书" "XHS" "$DOCTOR_OUT"
fi

# 检查 LinkedIn（基于 agent-reach doctor）
if [[ -z "$PLATFORM" || "$PLATFORM" == "linkedin" ]]; then
  parse_from_doctor "LinkedIn" "LI" "$DOCTOR_OUT"
fi

# ---------- 输出 ----------
status_label() {
  case "$1" in
    ok)           echo "✅ 可用" ;;
    unconfigured) echo "⚠️  需配置" ;;
    fail)         echo "❌ 不可用" ;;
    *)            echo "❓ 未知" ;;
  esac
}

if [[ "$FORMAT" == "json" ]]; then
  cat <<EOF
{
  "v2ex": {"status": "${STATUS_V2EX:-unknown}", "detail": "${DETAIL_V2EX:-}", "hint": "${HINT_V2EX:-}"},
  "xiaohongshu": {"status": "${STATUS_XHS:-unknown}", "detail": "${DETAIL_XHS:-}", "hint": "${HINT_XHS:-}"},
  "linkedin": {"status": "${STATUS_LI:-unknown}", "detail": "${DETAIL_LI:-}", "hint": "${HINT_LI:-}"},
  "agent_reach_path": "${AGENT_REACH:-not_found}"
}
EOF
  exit 0
fi

# 人类可读输出
echo "════════════════════════════════════════"
echo "  求职助手 · 平台可用性检查"
echo "════════════════════════════════════════"
echo

if [[ -z "$PLATFORM" || "$PLATFORM" == "v2ex" ]]; then
  echo "V2EX        $(status_label "${STATUS_V2EX:-unknown}")"
  [[ -n "${DETAIL_V2EX:-}" ]] && echo "            ${DETAIL_V2EX}"
  [[ -n "${HINT_V2EX:-}" ]]    && echo "            → ${HINT_V2EX}"
fi
if [[ -z "$PLATFORM" || "$PLATFORM" == "xiaohongshu" ]]; then
  echo "小红书      $(status_label "${STATUS_XHS:-unknown}")"
  [[ -n "${DETAIL_XHS:-}" ]] && echo "            ${DETAIL_XHS}"
  [[ -n "${HINT_XHS:-}" ]]    && echo "            → ${HINT_XHS}"
fi
if [[ -z "$PLATFORM" || "$PLATFORM" == "linkedin" ]]; then
  echo "LinkedIn    $(status_label "${STATUS_LI:-unknown}")"
  [[ -n "${DETAIL_LI:-}" ]] && echo "            ${DETAIL_LI}"
  [[ -n "${HINT_LI:-}" ]]    && echo "            → ${HINT_LI}"
fi
echo
if [[ -z "$AGENT_REACH" ]]; then
  echo "⚠️  agent-reach 未找到，LinkedIn/小红书 检查基于此工具。请先安装：pipx install agent-reach"
fi

# 退出码：V2EX 不可用 → 非 0（这是唯一零依赖平台）
if [[ "${STATUS_V2EX:-}" != "ok" ]]; then
  exit 1
fi
exit 0
