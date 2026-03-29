#!/bin/bash
# 大麦抢票 - 抢票启动脚本
# 使用方法: ./start_ticket_grabbing.sh

echo "🎫 启动大麦抢票脚本..."

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CONFIG_FILE="$REPO_ROOT/mobile/config.jsonc"

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
    echo "   请先运行: ./mobile/scripts/start_appium.sh"
    exit 1
fi

echo "✅ Appium服务器运行正常"

# 检查配置文件
if [ ! -f "$CONFIG_FILE" ]; then
    echo "❌ 配置文件不存在: $CONFIG_FILE"
    exit 1
fi

echo "✅ 配置文件存在"

# 显示当前配置
echo "📋 当前配置:"
echo "   $(cat "$CONFIG_FILE" | grep -E '"device_name"|"udid"|"keyword"|"city"|"users"|"probe_only"' | head -6)"

# 确认是否继续
read -p "🤔 确认开始抢票？(y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "❌ 已取消"
    exit 1
fi

# 进入脚本目录
cd "$REPO_ROOT/mobile"

echo "🚀 开始抢票..."
echo "   请确保："
echo "   1. 大麦APP已打开"
echo "   2. 已手动打开目标演出详情页面"
echo "   3. 首次运行可先用 probe_only 模式验证页面"
echo ""

# 运行抢票脚本
poetry run python damai_app.py
