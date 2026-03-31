#!/bin/bash
# 大麦抢票 - 启动脚本
# 使用方法:
#   正式抢票: ./start_ticket_grabbing.sh [--yes] [--config mobile/config.local.jsonc]
#   安全探测: ./start_ticket_grabbing.sh --probe [--yes] [--config mobile/config.local.jsonc]

ASSUME_YES=false
CONFIG_OVERRIDE=""
PROBE_MODE=false
MODE_PROMPT_CONFIRMED=false

resolve_path() {
    local target="$1"
    if [[ "$target" = /* ]]; then
        printf '%s\n' "$target"
    else
        printf '%s\n' "$(cd "$(dirname "$target")" && pwd)/$(basename "$target")"
    fi
}

while [ $# -gt 0 ]; do
    case "$1" in
        -y|--yes)
            ASSUME_YES=true
            shift
            ;;
        --probe)
            PROBE_MODE=true
            shift
            ;;
        --config)
            if [ -z "$2" ]; then
                echo "❌ --config 需要一个文件路径"
                exit 1
            fi
            CONFIG_OVERRIDE="$(resolve_path "$2")"
            shift 2
            ;;
        --config=*)
            CONFIG_OVERRIDE="$(resolve_path "${1#*=}")"
            shift
            ;;
        *)
            shift
            ;;
    esac
done

if [ "$PROBE_MODE" = true ]; then
    echo "🛡️ 启动大麦安全探测脚本..."
else
    echo "🎫 启动大麦抢票脚本..."
fi

# 设置Android环境变量（优先使用已有环境变量，否则自动检测常见路径）
if [ -z "$ANDROID_HOME" ]; then
    if [ -d "$HOME/Library/Android/sdk" ]; then
        export ANDROID_HOME="$HOME/Library/Android/sdk"
    elif [ -d "$HOME/Android/Sdk" ]; then
        export ANDROID_HOME="$HOME/Android/Sdk"
    elif [ -d "/opt/android-sdk" ]; then
        export ANDROID_HOME="/opt/android-sdk"
    else
        echo "❌ 未找到 Android SDK，请设置 ANDROID_HOME 环境变量"
        exit 1
    fi
fi
export ANDROID_SDK_ROOT="$ANDROID_HOME"

# 检查Appium服务器是否运行
if ! curl -s http://127.0.0.1:4723/status > /dev/null; then
    echo "❌ Appium服务器未运行"
    echo "   请先运行: ./start_appium.sh"
    exit 1
fi

echo "✅ Appium服务器运行正常"

# 解析目录，确保从任意目录执行都能找到配置文件与虚拟环境
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOBILE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEFAULT_CONFIG_FILE="$MOBILE_DIR/config.jsonc"
if [ -n "$CONFIG_OVERRIDE" ]; then
    CONFIG_FILE="$CONFIG_OVERRIDE"
elif [ -n "$HATICKETS_CONFIG_PATH" ]; then
    CONFIG_FILE="$(resolve_path "$HATICKETS_CONFIG_PATH")"
else
    CONFIG_FILE="$DEFAULT_CONFIG_FILE"
fi

# 检查配置文件
if [ ! -f "$CONFIG_FILE" ]; then
    echo "❌ 配置文件不存在: $CONFIG_FILE"
    echo "   可先复制模板: cp mobile/config.example.jsonc mobile/config.jsonc"
    exit 1
fi

echo "✅ 配置文件存在: $CONFIG_FILE"
if [ "$CONFIG_FILE" != "$DEFAULT_CONFIG_FILE" ]; then
    echo "🧑‍💻 当前使用显式指定的开发者配置覆盖文件"
fi

resolve_python_bin() {
    if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
        printf '%s\n' "$ROOT_DIR/.venv/bin/python"
        return 0
    fi
    if command -v python3 >/dev/null 2>&1; then
        command -v python3
        return 0
    fi
    if command -v python >/dev/null 2>&1; then
        command -v python
        return 0
    fi
    return 1
}

extract_bool_flag() {
    local key="$1"
    if grep -Eq "\"$key\"[[:space:]]*:[[:space:]]*true" "$CONFIG_FILE"; then
        printf 'true\n'
    elif grep -Eq "\"$key\"[[:space:]]*:[[:space:]]*false" "$CONFIG_FILE"; then
        printf 'false\n'
    else
        printf '__missing__\n'
    fi
}

prompt_mode_switch() {
    local message="$1"
    if [ "$ASSUME_YES" = true ]; then
        echo "🤖 已启用 --yes，自动确认并继续"
        return 0
    fi
    read -p "$message (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        MODE_PROMPT_CONFIRMED=true
        return 0
    fi
    return 1
}

PYTHON_BIN="$(resolve_python_bin)"
if [ -z "$PYTHON_BIN" ]; then
    echo "❌ 未找到可用的 Python 环境"
    exit 1
fi

CURRENT_PROBE_ONLY="$(extract_bool_flag "probe_only")"
CURRENT_IF_COMMIT_ORDER="$(extract_bool_flag "if_commit_order")"

if [ "$PROBE_MODE" = true ]; then
    DESIRED_PROBE_ONLY="true"
    DESIRED_IF_COMMIT_ORDER="false"
else
    DESIRED_PROBE_ONLY="false"
    DESIRED_IF_COMMIT_ORDER="true"
fi

if [ "$CURRENT_PROBE_ONLY" != "$DESIRED_PROBE_ONLY" ] || [ "$CURRENT_IF_COMMIT_ORDER" != "$DESIRED_IF_COMMIT_ORDER" ]; then
    echo "========================================"
    if [ "$PROBE_MODE" = true ]; then
        echo "🛡️ 检测到当前配置不是安全探测模式"
        echo "   当前配置: probe_only=$CURRENT_PROBE_ONLY, if_commit_order=$CURRENT_IF_COMMIT_ORDER"
        echo "   即将改为: probe_only=true, if_commit_order=false"
        echo "   这次运行会写回配置文件，然后开始安全探测"
        if ! prompt_mode_switch "👉 是否立即切换到安全探测模式并继续？"; then
            echo "❌ 已取消，配置文件未修改"
            exit 1
        fi
    else
        echo "🚨 检测到当前配置还不是正式抢票模式"
        echo "   当前配置: probe_only=$CURRENT_PROBE_ONLY, if_commit_order=$CURRENT_IF_COMMIT_ORDER"
        echo "   即将改为: probe_only=false, if_commit_order=true"
        echo "   这次运行会写回配置文件，然后立即开始正式抢票"
        if ! prompt_mode_switch "👉 是否立即切换到正式抢票模式并继续？"; then
            echo "❌ 已取消，配置文件未修改"
            exit 1
        fi
    fi

    if ! PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" HATICKETS_CONFIG_PATH="$CONFIG_FILE" "$PYTHON_BIN" - "$DESIRED_PROBE_ONLY" "$DESIRED_IF_COMMIT_ORDER" <<'PY'
import sys
from mobile.config import update_runtime_mode

probe_only = sys.argv[1].lower() == "true"
if_commit_order = sys.argv[2].lower() == "true"
update_runtime_mode(probe_only, if_commit_order)
PY
    then
        echo "❌ 修改配置文件失败: $CONFIG_FILE"
        exit 1
    fi

    echo "✅ 已写回配置文件: $CONFIG_FILE"
    echo "   已更新为: probe_only=$DESIRED_PROBE_ONLY, if_commit_order=$DESIRED_IF_COMMIT_ORDER"
    echo "========================================"
fi

# 显示当前配置
echo "📋 当前配置:"
echo "   $(cat "$CONFIG_FILE" | grep -E '"keyword"|"city"|"users"' | head -3)"

if grep -Eq '"probe_only"[[:space:]]*:[[:space:]]*true' "$CONFIG_FILE"; then
    echo "🛡️ 当前模式: 安全探测模式"
    echo "   本次运行只会定位目标演出页，不会点击“立即购票/立即预订”"
elif grep -Eq '"if_commit_order"[[:space:]]*:[[:space:]]*false' "$CONFIG_FILE"; then
    echo "🧑‍💻 当前模式: 开发验证模式"
    echo "   本次运行会走到确认页并勾选观演人，但不会点击“立即提交”；这是开发调试路径"
else
    echo "🔥 当前模式: 正式提交模式"
    echo "   本次运行会尝试提交订单，请再次确认配置"
fi

# 确认是否继续
if [ "$ASSUME_YES" = true ]; then
    echo "🤖 已启用 --yes，跳过交互确认"
elif [ "$MODE_PROMPT_CONFIRMED" = true ]; then
    echo "✅ 已确认切换运行模式，继续执行"
else
    read -p "🤔 确认开始抢票？(y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "❌ 已取消"
        exit 1
    fi
fi

# 进入脚本目录
cd "$MOBILE_DIR"

echo "🚀 开始执行脚本..."
echo "   请确保："
echo "   1. 大麦APP已打开"
echo "   2. 大麦账号已保持登录"
echo "   3. 如果配置了 item_url + auto_navigate=true，可停留在首页"
echo "   4. 如果没有开启自动导航，请先手动进入演出详情页面"
if [ "$PROBE_MODE" = true ]; then
    echo "   5. 当前命令已锁定为安全探测模式，不会提交订单"
else
    echo "   5. 当前命令已锁定为正式抢票模式，会尝试提交订单"
fi
echo ""

# 运行抢票脚本（优先使用项目 .venv，其次使用 Poetry）
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    HATICKETS_CONFIG_PATH="$CONFIG_FILE" "$ROOT_DIR/.venv/bin/python" damai_app.py
elif command -v poetry &> /dev/null; then
    HATICKETS_CONFIG_PATH="$CONFIG_FILE" poetry run python damai_app.py
else
    echo "❌ 未找到可用的 Python 环境"
    echo "   请先安装依赖："
    echo "   1) 使用 Poetry: python3 -m pip install --user poetry"
    echo "      然后运行: poetry install"
    echo "   2) 或在 .venv 中安装: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi
