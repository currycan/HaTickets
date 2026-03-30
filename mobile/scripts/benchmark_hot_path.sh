#!/bin/bash
# 从当前抢票界面出发，安全压测最后热路径
# 使用方法: ./mobile/scripts/benchmark_hot_path.sh [--runs 5] [--price 580元] [--price-index 2] [--city 成都] [--date 04.18] [--json]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOCAL_CONFIG_FILE="$REPO_ROOT/mobile/config.local.jsonc"
DEFAULT_CONFIG_FILE="$REPO_ROOT/mobile/config.jsonc"
if [ -f "$LOCAL_CONFIG_FILE" ]; then
    CONFIG_FILE="$LOCAL_CONFIG_FILE"
else
    CONFIG_FILE="$DEFAULT_CONFIG_FILE"
fi

if ! curl -s http://127.0.0.1:4723/status > /dev/null; then
    echo "❌ Appium服务器未运行"
    echo "   请先运行: ./mobile/scripts/start_appium.sh"
    exit 1
fi

if [ ! -f "$CONFIG_FILE" ]; then
    echo "❌ 配置文件不存在: $CONFIG_FILE"
    echo "   可先复制模板: cp mobile/config.example.jsonc mobile/config.local.jsonc"
    exit 1
fi

cd "$REPO_ROOT"

echo "⏱️  开始热路径压测..."
echo "   请确保手机已经停在目标演出的详情页或票档页"
echo "   当前配置文件: $CONFIG_FILE"
echo "   本脚本会强制使用安全模式: if_commit_order=false, auto_navigate=false, rush_mode=true"
echo ""

poetry run python mobile/hot_path_benchmark.py "$@"
