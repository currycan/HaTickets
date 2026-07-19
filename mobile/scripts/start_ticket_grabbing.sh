#!/bin/bash
# 大麦抢票 - 启动脚本
# 使用方法:
#   安全探测: ./start_ticket_grabbing.sh --probe [--yes] [--config mobile/config.local.jsonc]
#   正式抢票: ./start_ticket_grabbing.sh --commit [--yes] [--config mobile/config.local.jsonc]

# ══ 参数解析与模式判定（U-02 / U-01）══
# 必须先于一切副作用（poetry 预检、adb、启动子进程）执行：
# - 未知参数一律 fail-fast，防止 --porbe 等 typo 静默直达真实下单
# - --commit 是真实下单的唯一授权旗标；--yes 只跳过普通确认，不再能单独触发真下单

usage() {
    cat <<'USAGE'
用法:
  安全探测(不点击/不下单): ./mobile/scripts/start_ticket_grabbing.sh --probe [--yes] [--config <path>]
  正式抢票(真实提交订单): ./mobile/scripts/start_ticket_grabbing.sh --commit [--yes] [--config <path>]

参数:
  --probe               安全探测模式，停在“立即购票”之前，绝不下单
  --commit              正式抢票模式，唯一会给本次进程授予真实提交权限的旗标
  -y, --yes             跳过普通交互确认；不再能单独授权真实下单（真下单必须显式 --commit）
  --config <path>       显式指定配置文件（亦支持 --config=<path>）
  --serial <serial>     覆盖配置中的 serial（U-12，经 HATICKETS_SERIAL 环境变量透传，
                        不写回配置文件；同一份 config 可被多台设备复用）
  --result-json <path>  运行摘要 JSON 输出路径（默认 mobile/tmp/run_summary.json）
  -h, --help            显示本帮助

退出码（U-12，供 cron/systemd 编排消费，详见 README「退出码与运行摘要」）:
  0=成功  10=重试耗尽  11=不可重试失败(勿自动重启)  12=配置/设备错误
  13=同一设备已有实例运行  130=用户中断
  （脚本自身 pre-flight 失败仍用 1-4，编排器按 >=10 判定 run 层结果）

注意:
  * 未知参数一律报错退出，不会静默忽略——防止 --porbe 等拼写错误直达真实下单
  * --probe 与 --commit 互斥；两者都不带时，交互终端会进入强确认闸门，非交互环境直接报错退出
  * --commit 无 --yes 时需手动输入确认词；--commit --yes 跳过确认词但仍打印摘要并倒数 3 秒
USAGE
}

ASSUME_YES=false
CONFIG_OVERRIDE=""
SERIAL_OVERRIDE=""
RESULT_JSON=""
PROBE_MODE=false
COMMIT_MODE=false
MODE_PROMPT_CONFIRMED=false
FORCE_CONFIRM_WORD=false

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
        --commit)
            COMMIT_MODE=true
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
            if [ -z "${1#*=}" ]; then
                echo "❌ --config= 需要一个文件路径"
                exit 1
            fi
            CONFIG_OVERRIDE="$(resolve_path "${1#*=}")"
            shift
            ;;
        --serial)
            if [ -z "$2" ]; then
                echo "❌ --serial 需要一个设备序列号"
                exit 1
            fi
            SERIAL_OVERRIDE="$2"
            shift 2
            ;;
        --serial=*)
            if [ -z "${1#*=}" ]; then
                echo "❌ --serial= 需要一个设备序列号"
                exit 1
            fi
            SERIAL_OVERRIDE="${1#*=}"
            shift
            ;;
        --result-json)
            if [ -z "$2" ]; then
                echo "❌ --result-json 需要一个文件路径"
                exit 1
            fi
            mkdir -p "$(dirname "$2")" 2>/dev/null || true
            RESULT_JSON="$(resolve_path "$2")"
            shift 2
            ;;
        --result-json=*)
            if [ -z "${1#*=}" ]; then
                echo "❌ --result-json= 需要一个文件路径"
                exit 1
            fi
            mkdir -p "$(dirname "${1#*=}")" 2>/dev/null || true
            RESULT_JSON="$(resolve_path "${1#*=}")"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "❌ 未知参数: $1" >&2
            echo "" >&2
            usage >&2
            exit 1
            ;;
    esac
done

# ── 模式判定（U-01：资金防护闸门，早于一切预检）──
if [ "$PROBE_MODE" = true ] && [ "$COMMIT_MODE" = true ]; then
    echo "❌ --probe 与 --commit 互斥：安全探测请用 --probe，正式下单请用 --commit"
    exit 1
fi

if [ "$PROBE_MODE" = false ] && [ "$COMMIT_MODE" = false ]; then
    if [ "$ASSUME_YES" = true ]; then
        echo "❌ --yes 不再单独授权真实下单（资金误操作防护）。"
        echo "   安全探测:  $0 --probe"
        echo "   正式抢票:  $0 --commit --yes   # 会打印下单摘要并倒数 3 秒"
        exit 1
    elif [ -t 0 ]; then
        echo "⚠️ 未指定 --probe / --commit，将按正式下单处理，需通过强确认闸门"
        COMMIT_MODE=true
        FORCE_CONFIRM_WORD=true
    else
        echo "❌ 非交互环境必须显式指定 --probe 或 --commit [--yes]"
        exit 1
    fi
fi

if [ "$COMMIT_MODE" = true ] && [ "$ASSUME_YES" = false ] && [ ! -t 0 ]; then
    echo "❌ 非交互环境请用 --commit --yes（确认词需要交互终端输入）"
    exit 1
fi

# 解析项目目录并选择本次运行唯一使用的 Python：
# 1) 优先复用项目 .venv（无需 Poetry 命令仍可运行）；
# 2) 其次复用 Poetry 已创建的环境；
# 3) 两者都没有时只用系统 Python 做版本诊断，不直接作为运行环境。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON_BIN=""
PYTHON_ENV_SOURCE=""

if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
    PYTHON_ENV_SOURCE="project_venv"
elif command -v poetry >/dev/null 2>&1; then
    POETRY_PYTHON="$(
        cd "$ROOT_DIR" &&
        poetry env info --executable 2>/dev/null
    )" || POETRY_PYTHON=""
    if [ -n "$POETRY_PYTHON" ] && [ -x "$POETRY_PYTHON" ]; then
        PYTHON_BIN="$POETRY_PYTHON"
        PYTHON_ENV_SOURCE="poetry"
    else
        PYTHON_ENV_SOURCE="poetry_uninitialized"
    fi
fi

if [ -z "$PYTHON_BIN" ]; then
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="$(command -v python3)"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_BIN="$(command -v python)"
    else
        echo "❌ 未找到可用的 Python 解释器（python3 / python / .venv/bin/python 均不存在）"
        exit 4
    fi
    if [ -z "$PYTHON_ENV_SOURCE" ]; then
        PYTHON_ENV_SOURCE="system"
    fi
fi

# Python 版本检查必须早于依赖 import，避免 3.14 等不支持版本先报出误导性的缺包信息。
# 推荐 3.10~3.13；3.8/3.9 给警告（兼容老用户）；其他报错退出（含 3.14 — uiautomator2/selenium wheel 未就绪，issue #21）
PY_VERSION="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)"
case "$PY_VERSION" in
    3.10|3.11|3.12|3.13)
        # 已在 CI 验证，静默通过
        ;;
    3.8|3.9)
        echo "⚠️  Python $PY_VERSION 处于受限支持区间，建议升级到 3.10 ~ 3.13（仍会继续执行）"
        ;;
    "")
        # ${} 必须保留：macOS bash 3.2 下 $VAR 紧跟全角字符会吞掉变量值（真机实测）
        echo "❌ 无法获取 Python 版本号（解释器：${PYTHON_BIN}）"
        exit 4
        ;;
    *)
        echo "❌ Python $PY_VERSION 暂不支持。请使用 3.10 ~ 3.13。"
        echo "   issue #21: Python 3.14 暂未支持（uiautomator2 / selenium 上游 wheel 未就绪）"
        exit 4
        ;;
esac

# 版本通过后再检查环境与依赖。启动脚本绝不在抢票前临场安装依赖：
# 安装可能受网络影响并引入不可控延迟，必须由用户在准备阶段显式完成。
if [ "$PYTHON_ENV_SOURCE" = "system" ]; then
    echo "❌ 未找到项目 .venv，且 Poetry 未安装。"
    echo "   请先安装 Poetry，再在仓库根目录运行: poetry install"
    echo "   Poetry 安装说明: https://python-poetry.org/docs/#installation"
    exit 2
fi

if [ "$PYTHON_ENV_SOURCE" = "poetry_uninitialized" ]; then
    echo "❌ Poetry 环境尚未准备好。启动脚本不会自动安装依赖。"
    echo "   请先在仓库根目录运行: poetry install"
    exit 3
fi

if ! "$PYTHON_BIN" -c "import selenium, uiautomator2, adbutils" >/dev/null 2>&1; then
    echo "❌ 当前 Python 环境缺少关键依赖（selenium / uiautomator2 / adbutils）。"
    echo "   启动脚本不会自动安装依赖，以免在开抢前产生不可控延迟。"
    echo "   请先在仓库根目录显式运行: poetry install"
    exit 3
fi

unset POETRY_PYTHON

if [ "$PROBE_MODE" = true ]; then
    echo "🛡️ 启动大麦安全探测脚本..."
else
    echo "🎫 启动大麦抢票脚本..."
fi

# 启动预检（U-05）：与 run_from_prompt.sh 共享同一函数库。
# ANDROID_HOME 软化：adb 已在 PATH（如 brew install android-platform-tools）
# 即放行，不再因 ANDROID_HOME 未设置而 exit 1。
source "$SCRIPT_DIR/lib/preflight.sh"
preflight_check_adb || exit 1
MOBILE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
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

# 占位符黑名单预检（U-05）。运行模式只在本次子进程中覆盖，不再写配置文件。
preflight_check_placeholders "$CONFIG_FILE" || exit 1

# serial 精确预检（U-05 / U-12）：
# - 传入 --serial 时，生效的是覆盖值——直接校验该设备在 adb devices 中且状态
#   为 device（精确匹配，unauthorized/offline 均拦截），缺失则 exit 12，
#   避免 u2.connect 长超时后才报错；此时不再用配置文件里的 serial 预检。
# - 未传 --serial 时维持原逻辑：配置了 serial 用 adb -s <serial> get-state 校验，
#   serial 为 null/缺失时退回「任意一台在线」检查。
if [ -n "$SERIAL_OVERRIDE" ]; then
    if [ "${HATICKETS_SKIP_PREFLIGHT:-0}" != "1" ] && [ "${HATICKETS_SKIP_SERIAL_PREFLIGHT:-0}" != "1" ]; then
        if ! adb devices 2>/dev/null | grep -Eq "^${SERIAL_OVERRIDE}[[:space:]]+device$"; then
            # ${} 必须保留：macOS bash 3.2 下 $VAR 紧跟全角字符会吞掉变量值（真机实测）
            echo "❌ 未找到指定设备: ${SERIAL_OVERRIDE}（adb devices 中不存在或未授权/离线）"
            echo "   当前在线设备清单："
            adb devices 2>/dev/null | sed 1d | sed '/^$/d' | sed 's/^/     /'
            exit 12
        fi
        echo "✅ 指定设备在线: $SERIAL_OVERRIDE"
    fi
else
    preflight_check_device "$CONFIG_FILE" || exit 1
fi

# 强确认闸门（U-01）：真实下单的最后一道人工闸门。
# 授权只在本次子进程中生效；确认词输错时配置文件保证逐字节未变。
require_commit_confirmation() {
    if [ "$ASSUME_YES" = true ] && [ "$FORCE_CONFIRM_WORD" = false ]; then
        return 0    # --commit --yes：跳过确认词，但摘要+倒数不可跳过
    fi
    local kw
    kw="$(PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" \
        HATICKETS_CONFIG_PATH="$CONFIG_FILE" \
        "$PYTHON_BIN" - 2>/dev/null <<'PY'
from mobile.config import load_config_dict

kw = load_config_dict().get("keyword")
print(kw if isinstance(kw, str) else "")
PY
)" || kw=""
    echo "🚨 即将进入正式提交模式（会真实下单付款）"
    if [ -n "$kw" ]; then
        echo "👉 请输入 GO 或目标演出关键词「${kw}」以继续："
    else
        echo "👉 请输入 GO 以继续："
    fi
    read -r REPLY
    # 去首尾空白后整行比对；GO 大小写敏感；kw 为空时绝不放宽
    REPLY="${REPLY#"${REPLY%%[![:space:]]*}"}"
    REPLY="${REPLY%"${REPLY##*[![:space:]]}"}"
    if [ "$REPLY" != "GO" ] && { [ -z "$kw" ] || [ "$REPLY" != "$kw" ]; }; then
        echo "❌ 确认词不匹配，已退出。配置文件未被修改: $CONFIG_FILE"
        exit 1
    fi
    MODE_PROMPT_CONFIRMED=true
}

# 正式下单摘要（U-01）：--commit 路径启动前无条件打印。
# python heredoc 失败时降级为 grep 原文打印，摘要降级不放行也不阻断。
print_commit_summary() {
    PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" \
    HATICKETS_CONFIG_PATH="$CONFIG_FILE" \
    "$PYTHON_BIN" - <<'PY' || grep -E '"keyword"|"target_title"|"price"|"users"|"city"|"date"' "$CONFIG_FILE"
from mobile.config import load_config_dict

c = load_config_dict()
users = c.get("users") or []
print("========== 正式下单摘要 ==========")
print(f"  演出:   {c.get('target_title') or c.get('keyword')}")
print(f"  票档:   {c.get('price')} (price_index={c.get('price_index')})")
print(f"  观演人: {len(users)} 人 -> {', '.join(str(u) for u in users)}")
print(f"  场次:   city={c.get('city')} date={c.get('date')}")
print("==================================")
PY
}

# EFFECTIVE_* 只由本次命令旗标驱动（P0）：不读取、不改写磁盘中的模式字段。
if [ "$PROBE_MODE" = true ]; then
    EFFECTIVE_PROBE_ONLY="true"
    EFFECTIVE_IF_COMMIT_ORDER="false"
    RUNTIME_MODE="probe"
else
    # COMMIT_MODE=true（裸调用已在顶部转为 commit 或退出）
    require_commit_confirmation
    EFFECTIVE_PROBE_ONLY="false"
    EFFECTIVE_IF_COMMIT_ORDER="true"
    RUNTIME_MODE="commit"
fi

echo "🔐 本次运行模式仅对当前进程生效，不修改配置文件"
echo "   生效值: probe_only=$EFFECTIVE_PROBE_ONLY, if_commit_order=$EFFECTIVE_IF_COMMIT_ORDER"

# 显示当前配置
echo "📋 当前配置:"
echo "   $(cat "$CONFIG_FILE" | grep -E '"keyword"|"city"|"users"' | head -3)"

# 模式横幅使用本次命令的不可变进程模式，不再从磁盘配置二次推断
if [ "$EFFECTIVE_PROBE_ONLY" = "true" ]; then
    echo "🛡️ 当前模式: 安全探测模式"
    echo "   本次运行只会定位目标演出页，不会点击“立即购票/立即预订”"
elif [ "$EFFECTIVE_IF_COMMIT_ORDER" = "false" ]; then
    echo "🧑‍💻 当前模式: 开发验证模式"
    echo "   本次运行会走到确认页并勾选观演人，但不会点击“立即提交”；这是开发调试路径"
else
    echo "🔥 当前模式: 正式提交模式"
    echo "   本次运行会尝试提交订单，请再次确认配置"
fi

# 确认是否继续（--commit 路径已由强确认闸门覆盖，不再二次询问）
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

# --commit 路径：启动前无条件打印下单摘要并倒数 3 秒（U-01，--yes 也不可跳过）
if [ "$COMMIT_MODE" = true ]; then
    print_commit_summary
    echo "⏳ 3 秒后启动正式抢票，Ctrl-C 可随时取消..."
    echo "   （中途取消后授权立即失效；下次正式提交仍需重新传入 --commit）"
    for i in 3 2 1; do
        echo "   $i..."
        sleep 1
    done
fi

# 测试钩子：HATICKETS_DRY_RUN=1 时到此为止，不启动抢票进程（仅自动化测试用，方向安全——只会少做不会多做）
if [ "${HATICKETS_DRY_RUN:-0}" = "1" ]; then
    echo "🧪 DRY-RUN 模式：跳过实际执行（python -m damai_app 未启动）"
    exit 0
fi

# 进入脚本目录
cd "$MOBILE_DIR"

echo "🚀 开始执行脚本..."
echo "   请确保："
echo "   1. 大麦APP已打开"
echo "   2. 大麦账号已保持登录"
echo "   3. 如果 auto_navigate=true，可停留在大麦首页（脚本会用 keyword 自动搜索进入目标演出）"
echo "   4. 如果没有开启自动导航，请先手动进入演出详情页面"
if [ "$PROBE_MODE" = true ]; then
    echo "   5. 当前命令已锁定为安全探测模式，不会提交订单"
else
    echo "   5. 当前命令已锁定为正式抢票模式，会尝试提交订单"
fi
echo ""

# P0：运行模式/正式提交授权只透传给这一次 Python 子进程，不写回配置文件。
export HATICKETS_RUN_MODE="$RUNTIME_MODE"
if [ "$COMMIT_MODE" = true ]; then
    export HATICKETS_COMMIT_AUTH="confirmed-by-start-script"
else
    unset HATICKETS_COMMIT_AUTH
fi

# U-12：serial 覆盖 / 摘要路径经环境变量透传（仅在非空时 export，不写回配置文件）
[ -n "$SERIAL_OVERRIDE" ] && export HATICKETS_SERIAL="$SERIAL_OVERRIDE"
[ -n "$RESULT_JSON" ] && export HATICKETS_RESULT_JSON="$RESULT_JSON"

# 使用预检通过的同一个 Python 环境运行抢票脚本，避免“预检 A、实际执行 B”。
# U-12：显式 exit $? 让 python 的语义化退出码（0/10/11/12/130）穿透脚本，
# 供 cron/systemd 等外部编排消费。
HATICKETS_CONFIG_PATH="$CONFIG_FILE" "$PYTHON_BIN" -m damai_app
exit $?
