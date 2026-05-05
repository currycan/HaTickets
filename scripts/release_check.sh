#!/usr/bin/env bash
#
# scripts/release_check.sh — 发布前自动检查
#
# 来源：reference/08-ops-runbook.md §4「发布 checklist」脚本化
#
# 检查项（任一不通过 → exit 1，绿全 → exit 0 提示人工 git tag）：
#   C1. 当前分支必须是 master
#   C2. master 工作区必须 clean（无未提交、无 untracked）
#   C3. `poetry run test` 通过（覆盖率 ≥80% 由 pyproject.toml 强制）
#   C4. `bash mobile/scripts/benchmark_hot_path.sh --runs 3` 跑通并打印平均耗时
#   C5. reference/10-changelog.md 含本次发布条目（与 --tag 比对，或要求 7 天内有更新）
#
# 用法：
#   scripts/release_check.sh                # 通用检查
#   scripts/release_check.sh --tag v0.3.5   # 指定版本号，强校验 changelog
#   scripts/release_check.sh --skip-bench   # 离机环境跳过 benchmark（仅供 CI 调试）
#
# 退出码：
#   0 = 全部通过，可人工 `git tag -a vX.Y.Z -m "..." && git push origin vX.Y.Z`
#   1 = 任一检查失败
#

set -uo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

# ── 颜色 ──────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    readonly RED=$'\033[0;31m'
    readonly GRN=$'\033[0;32m'
    readonly YLW=$'\033[1;33m'
    readonly BLU=$'\033[0;34m'
    readonly RST=$'\033[0m'
else
    readonly RED=''
    readonly GRN=''
    readonly YLW=''
    readonly BLU=''
    readonly RST=''
fi

ok()    { printf '%s✓%s %s\n' "$GRN" "$RST" "$1"; }
fail()  { printf '%s✗%s %s\n' "$RED" "$RST" "$1"; }
warn()  { printf '%s!%s %s\n' "$YLW" "$RST" "$1"; }
info()  { printf '%s•%s %s\n' "$BLU" "$RST" "$1"; }
header(){ printf '\n%s── %s ──%s\n' "$BLU" "$1" "$RST"; }

# ── 参数解析 ──────────────────────────────────────────────────────────────
TAG=""
SKIP_BENCH=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --tag)
            shift
            [[ $# -gt 0 ]] || { fail "--tag 需要版本号参数（如 v0.3.5）"; exit 1; }
            TAG="$1"
            ;;
        --tag=*)
            TAG="${1#*=}"
            ;;
        --skip-bench)
            SKIP_BENCH=true
            ;;
        -h|--help)
            sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            fail "未知参数：$1（用 --help 查看用法）"
            exit 1
            ;;
    esac
    shift
done

FAILED=0
record_fail() { FAILED=$((FAILED + 1)); }

# ── C1：分支检查 ──────────────────────────────────────────────────────────
header "C1. 分支检查"
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")"
if [[ "$CURRENT_BRANCH" == "master" ]]; then
    ok "当前分支为 master"
else
    fail "当前分支为 '$CURRENT_BRANCH'，需切到 master：git checkout master && git pull --ff-only origin master"
    record_fail
fi

# ── C2：工作区 clean 检查 ─────────────────────────────────────────────────
header "C2. 工作区 clean"
DIRTY_OUTPUT="$(git status --porcelain)"
if [[ -z "$DIRTY_OUTPUT" ]]; then
    ok "工作区无未提交修改与 untracked 文件"
else
    fail "工作区不 clean，下面是 git status --porcelain 输出："
    printf '%s\n' "$DIRTY_OUTPUT" | sed 's/^/    /'
    record_fail
fi

# ── C3：测试套件 ──────────────────────────────────────────────────────────
header "C3. poetry run test"
if ! command -v poetry >/dev/null 2>&1; then
    fail "未找到 poetry 命令；请先安装 Poetry（https://python-poetry.org/）"
    record_fail
else
    info "执行 poetry run test（覆盖率 ≥80% 由 pyproject.toml 强制）..."
    if poetry run test; then
        ok "测试套件通过"
    else
        fail "测试套件失败 —— 修实现而不是放宽阈值"
        record_fail
    fi
fi

# ── C4：热路径 benchmark ──────────────────────────────────────────────────
header "C4. benchmark_hot_path.sh --runs 3"
if [[ "$SKIP_BENCH" == "true" ]]; then
    warn "已传 --skip-bench，跳过基准测试（仅供 CI 调试，正式发布不允许）"
elif ! command -v adb >/dev/null 2>&1; then
    fail "未找到 adb 命令；benchmark 需要 Android 真机连接，请在带真机的 ops 工作站重跑"
    record_fail
else
    DEVICE_COUNT="$(adb devices 2>/dev/null | awk 'NR>1 && $2=="device" {n++} END{print n+0}')"
    if [[ "$DEVICE_COUNT" -lt 1 ]]; then
        fail "adb 未检测到已授权设备（需至少 1 台 device 状态）"
        record_fail
    else
        info "检测到 $DEVICE_COUNT 台 adb 设备，开始跑 3 次基准..."
        BENCH_LOG="$(mktemp -t hatickets-bench.XXXXXX.log)"
        if bash mobile/scripts/benchmark_hot_path.sh --runs 3 2>&1 | tee "$BENCH_LOG"; then
            # 提取平均耗时（脚本输出格式可能演进，宽松匹配「平均」「average」「耗时」相关行）
            AVG_LINE="$(grep -iE '平均|average|avg' "$BENCH_LOG" | tail -1 || true)"
            if [[ -n "$AVG_LINE" ]]; then
                ok "基准跑通：$AVG_LINE"
            else
                ok "基准跑通（未解析到平均耗时行，请人工查看上方输出）"
            fi
            info "基准日志保留在：$BENCH_LOG"
        else
            fail "benchmark_hot_path.sh 执行失败，请检查真机状态与配置"
            record_fail
        fi
    fi
fi

# ── C5：changelog 校验 ────────────────────────────────────────────────────
header "C5. reference/10-changelog.md 含本次发布条目"
CHANGELOG="reference/10-changelog.md"
if [[ ! -f "$CHANGELOG" ]]; then
    fail "未找到 $CHANGELOG（reference/ 目录是 gitignored，请先 clone 或同步团队战情室）"
    record_fail
elif [[ -n "$TAG" ]]; then
    if grep -qE "^## .*${TAG}\b" "$CHANGELOG"; then
        ok "changelog 含 $TAG 标题条目"
    else
        fail "changelog 未找到 '## … $TAG' 标题条目，请先在 $CHANGELOG 顶部追加发布条目"
        record_fail
    fi
else
    # 未传 --tag：要求最近 7 天内至少有一条新条目
    TODAY="$(date +%Y-%m-%d)"
    SEVEN_DAYS_AGO="$(date -v-7d +%Y-%m-%d 2>/dev/null || date -d '7 days ago' +%Y-%m-%d 2>/dev/null || echo "")"
    if [[ -z "$SEVEN_DAYS_AGO" ]]; then
        warn "无法计算 7 天前日期（date 命令兼容性问题）；改为手动确认"
        warn "请目视检查 $CHANGELOG 顶部是否含本次发布条目"
    else
        RECENT_HIT="$(awk -v from="$SEVEN_DAYS_AGO" -v to="$TODAY" '
            /^## [0-9]{4}-[0-9]{2}-[0-9]{2}/ {
                d=$2; gsub(/[^0-9-]/,"",d)
                if (d >= from && d <= to) { print; exit }
            }
        ' "$CHANGELOG" || true)"
        if [[ -n "$RECENT_HIT" ]]; then
            ok "changelog 近 7 天有更新：$RECENT_HIT"
        else
            fail "changelog 近 7 天无更新条目；发布前请追加本次变更说明"
            record_fail
        fi
    fi
fi

# ── 汇总 ──────────────────────────────────────────────────────────────────
header "结果"
if [[ $FAILED -eq 0 ]]; then
    ok "全部检查通过 —— 可执行人工发布步骤："
    if [[ -n "$TAG" ]]; then
        printf '\n    git tag -a %s -m "release notes here"\n    git push origin %s\n\n' "$TAG" "$TAG"
    else
        printf '\n    git tag -a vX.Y.Z -m "release notes here"\n    git push origin vX.Y.Z\n\n'
    fi
    printf '注意：本脚本不会自动 tag，避免误操作。\n'
    exit 0
else
    fail "$FAILED 项检查未通过 —— 修复后重跑 $0"
    exit 1
fi
