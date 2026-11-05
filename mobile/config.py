# -*- coding: UTF-8 -*-
"""
__Author__ = "WECENG"
__Version__ = "1.0.0"
__Description__ = "配置类"
__Created__ = 2023/10/27 09:54
"""

import contextlib
import json
import logging
import re
import sys
import os
import tempfile
from datetime import datetime

try:  # POSIX 文件锁（macOS / Linux）
    import fcntl
except ImportError:  # pragma: no cover — Windows
    fcntl = None

try:  # Windows 文件锁降级
    import msvcrt
except ImportError:
    msvcrt = None

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.config_validator import validate_non_empty_list

DEFAULT_CONFIG_FILENAME = "config.jsonc"
LOCAL_CONFIG_FILENAME = "config.local.jsonc"
CONFIG_OVERRIDE_ENV_VAR = "HATICKETS_CONFIG_PATH"
# U-12：serial 覆盖通道（--serial CLI 参数 / 脚本 export 均写此环境变量），
# 非空时优先于配置文件的 serial 字段；空/纯空白视为未设置（与 CONFIG_OVERRIDE_ENV_VAR 口径一致）。
SERIAL_OVERRIDE_ENV_VAR = "HATICKETS_SERIAL"
# P0 运行授权必须是一次性的进程状态，不能依赖会残留/并发互相覆盖的配置写回。
# start_ticket_grabbing.sh 只在当前子进程注入以下变量；退出后授权自然失效。
RUNTIME_MODE_ENV_VAR = "HATICKETS_RUN_MODE"
COMMIT_AUTH_ENV_VAR = "HATICKETS_COMMIT_AUTH"
COMMIT_AUTH_VALUE = "confirmed-by-start-script"
DEFAULT_CONFIG_FILENAMES = (DEFAULT_CONFIG_FILENAME,)

PRICE_INDEX_LARGE_WARNING_THRESHOLD = 50

# ── U-05 启动期防护常量 ──
# 与 mobile/config.example.jsonc 的模板字面量一一对应，只做 strip 后精确全等匹配。
# 注意：keyword 的示例值 "张杰 演唱会" 是合法真实搜索词，刻意不列入黑名单。
PLACEHOLDER_LITERALS = {
    "serial": frozenset({"你的设备序列号"}),
    "users": frozenset({"你的真实观演人姓名"}),  # 逐元素匹配
    "city": frozenset({"演出城市"}),
    "date": frozenset({"场次日期"}),
    "price": frozenset({"票档原文"}),
}

# 权威 schema：与 Config.to_dict() 的 key 集合一一对应
# （tests/unit/test_mobile_config.py 有防漂移守卫，新增字段务必同步更新）
KNOWN_CONFIG_KEYS = frozenset(
    {
        "serial",
        "app_package",
        "app_activity",
        "keyword",
        "target_title",
        "target_venue",
        "users",
        "city",
        "date",
        "price",
        "price_index",
        "if_commit_order",
        "probe_only",
        "auto_navigate",
        "sell_start_time",
        "countdown_lead_ms",
        "presale_prewarm_enabled",
        "wait_cta_ready_timeout_ms",
        "fast_retry_count",
        "fast_retry_interval_ms",
        "rush_mode",
        "rush_skip_session",
        "rush_skip_price_dump",
        "rush_aggressive_retry",
    }
)

DEPRECATED_CONFIG_KEYS = {
    "udid": "已废弃，请改用 serial（值含义相同，即 adb devices 输出的序列号）",
    "item_url": "已废弃，请改用 keyword / target_title 配合 auto_navigate=true",
    "device_name": "Appium 时代字段，u2 模式下已忽略",
    "server_url": "Appium 时代字段，u2 模式下已忽略",
    "platform_version": "Appium 时代字段，u2 模式下已忽略",
    "driver_backend": "Appium 时代字段，u2 模式下已忽略",
}

logger = logging.getLogger(__name__)


class ConfigError(ValueError):
    """配置加载/校验失败。继承 ValueError 以保持现有 except 兼容。"""


def _strip_jsonc_comments(text):
    """移除 JSONC 文件中的 // 和 /* */ 注释"""
    # 移除单行注释（不在字符串内的 //）
    text = re.sub(r"(?<!:)//.*?$", "", text, flags=re.MULTILINE)
    # 移除多行注释
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return text


def _load_config_dict_from_path(path):
    try:
        with open(path, "r", encoding="utf-8") as config_file:
            raw_text = config_file.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"配置文件未找到: {path}")

    try:
        return json.loads(_strip_jsonc_comments(raw_text))
    except json.JSONDecodeError as e:
        raise ValueError(f"配置文件格式错误: {e}")


def _dump_config_dict(config_dict):
    return json.dumps(config_dict, ensure_ascii=False, indent=2) + "\n"


def _resolve_explicit_config_path(config_path=None):
    if config_path is not None:
        return os.fspath(config_path)

    env_path = os.environ.get(CONFIG_OVERRIDE_ENV_VAR)
    if env_path and env_path.strip():
        return env_path.strip()

    return None


def _resolve_existing_config_path(config_path=None):
    explicit_path = _resolve_explicit_config_path(config_path)
    if explicit_path is not None:
        if os.path.exists(explicit_path):
            return explicit_path
        raise FileNotFoundError(f"配置文件未找到: {explicit_path}")

    if os.path.exists(DEFAULT_CONFIG_FILENAME):
        return DEFAULT_CONFIG_FILENAME

    raise FileNotFoundError(f"配置文件未找到: {DEFAULT_CONFIG_FILENAME}")


def _resolve_writable_config_path(config_path=None):
    explicit_path = _resolve_explicit_config_path(config_path)
    if explicit_path is not None:
        return explicit_path

    return DEFAULT_CONFIG_FILENAME


def load_config_dict(config_path=None):
    """Load a JSONC config file into a plain dictionary."""
    return _load_config_dict_from_path(_resolve_existing_config_path(config_path))


def save_config_dict(config_dict, config_path=None):
    """Persist a config dictionary back to disk as UTF-8 JSON."""
    resolved_path = _resolve_writable_config_path(config_path)
    with open(resolved_path, "w", encoding="utf-8") as config_file:
        config_file.write(_dump_config_dict(config_dict))


# ---------------------------------------------------------------------------
# U-05 — 启动期配置防护：占位符黑名单 + 未知/废弃字段提示
# ---------------------------------------------------------------------------


def _placeholder_violations(config_dict):
    """返回 [(key, 占位符值), ...]；users 逐元素检查，非字符串值一律跳过。"""
    hits = []
    for key, literals in PLACEHOLDER_LITERALS.items():
        value = config_dict.get(key)
        if key == "users" and isinstance(value, list):
            hits.extend(
                (key, item)
                for item in value
                if isinstance(item, str) and item.strip() in literals
            )
        elif isinstance(value, str) and value.strip() in literals:
            hits.append((key, value))
    return hits


def unknown_config_keys(config_dict):
    """既不在 KNOWN 也不在 DEPRECATED 的 key；'_' 前缀视为用户注释键，跳过。"""
    return sorted(
        key
        for key in config_dict
        if key not in KNOWN_CONFIG_KEYS
        and key not in DEPRECATED_CONFIG_KEYS
        and not key.startswith("_")
    )


def filter_known_config_keys(config_dict):
    """只保留权威 schema 内的键（供 Config(**...) 安全构造用）。"""
    return {k: v for k, v in config_dict.items() if k in KNOWN_CONFIG_KEYS}


def migrate_deprecated_config(config_dict):
    """返回 (清理后的新 dict, warnings)。udid→serial 迁移，其余废弃 key 剔除。"""
    cleaned, warnings = dict(config_dict), []
    if cleaned.get("udid") and not cleaned.get("serial"):
        cleaned["serial"] = cleaned["udid"]
        warnings.append("字段 udid 已废弃，已自动迁移为 serial")
    for key, hint in DEPRECATED_CONFIG_KEYS.items():
        if cleaned.pop(key, None) is not None:
            warnings.append(f"字段 {key} {hint}，已从配置中移除")
    return cleaned, warnings


def validate_config_dict(config_dict, strict_placeholders=True, source="配置文件"):
    """启动期防护（U-05）：占位符黑名单（可硬失败）+ 未知/废弃 key 警告（永不失败）。

    占位符命中时收集全部违规 key 后一次性抛 ConfigError（中文 + 修改指引）；
    ConfigError 继承 ValueError，damai_app/__main__.py 的 except ValueError
    会干净打印，不出英文 traceback。
    """
    for key in config_dict:
        if key in DEPRECATED_CONFIG_KEYS:
            logger.warning("字段 %s %s", key, DEPRECATED_CONFIG_KEYS[key])
    for key in unknown_config_keys(config_dict):
        logger.warning(
            "字段 %s 未识别，将被忽略；请对照 mobile/config.example.jsonc 检查拼写",
            key,
        )

    violations = _placeholder_violations(config_dict)
    if not violations:
        return
    lines = [f"{source}中仍保留模板占位符，尚未填入真实值："]
    for key, value in violations:
        lines.append(f'  - {key} = "{value}"')
    lines += [
        "请编辑 mobile/config.jsonc：",
        "  serial → 填 adb devices 输出的设备序列号（或设为 null 由脚本自动识别单台设备）",
        "  users  → 填已在大麦 App「观演人」中添加并保存的真实姓名",
        "  city/date/price → 填大麦 App 演出页面上的原文",
    ]
    message = "\n".join(lines)
    if strict_placeholders:
        raise ConfigError(message)
    logger.warning(message)


# ---------------------------------------------------------------------------
# U-06 — 注释保留的 JSONC 定点补丁 + 文件锁 + 原子写回
# ---------------------------------------------------------------------------


def _skip_string(text, index):
    """text[index] 必须是双引号；返回闭引号之后的下标（处理反斜杠转义）。"""
    index += 1
    length = len(text)
    while index < length:
        ch = text[index]
        if ch == "\\":
            index += 2
            continue
        if ch == '"':
            return index + 1
        index += 1
    return length  # 未闭合字符串：交给后续 json 解析报错


def _skip_ws_and_comments(text, index):
    """跳过空白与 // 、/* */ 注释，返回下一个代码字符下标。"""
    length = len(text)
    while index < length:
        ch = text[index]
        if ch in " \t\r\n":
            index += 1
        elif text.startswith("//", index):
            newline = text.find("\n", index)
            index = length if newline < 0 else newline
        elif text.startswith("/*", index):
            close = text.find("*/", index + 2)
            index = length if close < 0 else close + 2
        else:
            break
    return index


def _skip_value(text, index):
    """index 位于值 token 起始处；返回值结束下标（不含）。

    标量扫到分隔符为止；[ / { 扫到配对括号（内部字符串/注释同样跳过）。
    """
    length = len(text)
    ch = text[index]
    if ch == '"':
        return _skip_string(text, index)
    if ch in "{[":
        depth = 0
        while index < length:
            current = text[index]
            if text.startswith("//", index):
                newline = text.find("\n", index)
                index = length if newline < 0 else newline + 1
            elif text.startswith("/*", index):
                close = text.find("*/", index + 2)
                index = length if close < 0 else close + 2
            elif current == '"':
                index = _skip_string(text, index)
            elif current in "{[":
                depth += 1
                index += 1
            elif current in "}]":
                depth -= 1
                index += 1
                if depth == 0:
                    return index
            else:
                index += 1
        return length
    end = index
    while (
        end < length
        and text[end] not in ",}] \t\r\n"
        and not text.startswith("//", end)
        and not text.startswith("/*", end)
    ):
        end += 1
    return end


def _find_member_value_spans(text, key):
    """单遍扫描，返回深度==1 处 "key": <value> 的 [(value_start, value_end)]。

    状态机跳过字符串（含转义）、// 行注释、/* */ 块注释——键名出现在注释文本
    或字符串值内（如模板三态说明里的 probe_only 字样）天然不会命中。
    """
    spans = []
    index, depth, length = 0, 0, len(text)
    while index < length:
        ch = text[index]
        if text.startswith("//", index):
            newline = text.find("\n", index)
            index = length if newline < 0 else newline
        elif text.startswith("/*", index):
            close = text.find("*/", index + 2)
            index = length if close < 0 else close + 2
        elif ch == '"':
            token_start = index
            index = _skip_string(text, index)
            if depth == 1 and text[token_start + 1 : index - 1] == key:
                colon = _skip_ws_and_comments(text, index)
                if colon < length and text[colon] == ":":
                    value_start = _skip_ws_and_comments(text, colon + 1)
                    value_end = _skip_value(text, value_start)
                    spans.append((value_start, value_end))
                    index = value_end
        elif ch in "{[":
            depth += 1
            index += 1
        elif ch in "}]":
            depth -= 1
            index += 1
        else:
            index += 1
    return spans


def _strip_jsonc_comments_scanned(text):
    """状态机版剥注释：字符串值内的 // 与 /* 不受影响。

    U-06 写回校验专用——正则版 _strip_jsonc_comments 会把 "A//B" 类字符串值
    误剥成坏 JSON，导致对合法补丁误报；读路径为兼容保持正则版不动。
    """
    parts = []
    index, length = 0, len(text)
    while index < length:
        ch = text[index]
        if text.startswith("//", index):
            newline = text.find("\n", index)
            index = length if newline < 0 else newline
        elif text.startswith("/*", index):
            close = text.find("*/", index + 2)
            index = length if close < 0 else close + 2
        elif ch == '"':
            end = _skip_string(text, index)
            parts.append(text[index:end])
            index = end
        else:
            parts.append(ch)
            index += 1
    return "".join(parts)


def _find_root_close_brace(text):
    """返回最外层对象闭合 } 的下标（扫描器语义），找不到返回 -1。"""
    index, depth, length = 0, 0, len(text)
    close_index = -1
    while index < length:
        ch = text[index]
        if text.startswith("//", index):
            newline = text.find("\n", index)
            index = length if newline < 0 else newline
        elif text.startswith("/*", index):
            close = text.find("*/", index + 2)
            index = length if close < 0 else close + 2
        elif ch == '"':
            index = _skip_string(text, index)
        elif ch in "{[":
            depth += 1
            index += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0 and ch == "}":
                close_index = index
            index += 1
        else:
            index += 1
    return close_index


def _last_code_index(text, end):
    """返回 [0, end) 内最后一个非注释、非空白代码字符下标，找不到返回 -1。"""
    index, last = 0, -1
    while index < end:
        ch = text[index]
        if text.startswith("//", index):
            newline = text.find("\n", index)
            index = end if newline < 0 else min(newline + 1, end)
        elif text.startswith("/*", index):
            close = text.find("*/", index + 2)
            index = end if close < 0 else min(close + 2, end)
        elif ch == '"':
            string_end = min(_skip_string(text, index), end)
            last = string_end - 1
            index = string_end
        elif ch in " \t\r\n":
            index += 1
        else:
            last = index
            index += 1
    return last


def _append_members_before_closing_brace(text, missing, object_empty):
    """在最外层 } 前追加成员；非空对象在末成员值后补逗号（行尾注释保持原位）。"""
    close_index = _find_root_close_brace(text)
    if close_index < 0:
        raise ConfigError("配置补丁失败: 找不到最外层 }，无法追加缺失字段")
    members = ",\n".join(
        f'  "{key}": {json.dumps(value, ensure_ascii=False)}'
        for key, value in missing.items()
    )
    if object_empty:
        return text[:close_index] + "\n" + members + "\n" + text[close_index:]
    last_code = _last_code_index(text, close_index)
    return (
        text[: last_code + 1]
        + ","
        + text[last_code + 1 : close_index]
        + members
        + "\n"
        + text[close_index:]
    )


def patch_jsonc_text(text, updates):
    """注释保留的定点补丁：只替换 updates 各键的值 token，其余字节原样保留。

    重复键全部替换（与 json.loads 取末次的解析语义对齐，杜绝「改第一处
    读第二处」错位）；缺键在最外层 } 前追加成员。
    """
    try:
        parsed_original = json.loads(_strip_jsonc_comments_scanned(text))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件格式错误: {exc}")

    edits, missing = [], {}
    for key, value in updates.items():
        spans = _find_member_value_spans(text, key)
        if not spans:
            missing[key] = value
            continue
        replacement = json.dumps(value, ensure_ascii=False)
        edits.extend((start, end, replacement) for start, end in spans)
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    if missing:
        text = _append_members_before_closing_brace(
            text, missing, object_empty=not parsed_original
        )
    return text


def _verify_patched(new_text, updates):
    """写前兜底：新文本必须可解析，且每个键的解析值==期望值，否则拒绝落盘。"""
    try:
        parsed = json.loads(_strip_jsonc_comments_scanned(new_text))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置补丁产生了不可解析的文件，已中止写回: {exc}")
    for key, value in updates.items():
        if parsed.get(key) != value:
            raise ConfigError(
                f"配置补丁校验失败: {key} 期望 {value!r}，"
                f"实际 {parsed.get(key)!r}，已中止写回"
            )


@contextlib.contextmanager
def _config_write_lock(resolved_path):
    """Sidecar 排它锁：锁文件本体永不被 os.replace 换掉，锁语义稳定。

    fcntl（POSIX）→ msvcrt（Windows）→ no-op + warning 三级降级，
    锁不可用时功能不阻断。
    """
    lock_path = str(resolved_path) + ".lock"
    with open(lock_path, "a+") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        elif msvcrt is not None:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            logger.warning("当前平台不支持文件锁，写回未加锁: %s", resolved_path)
            yield


def _atomic_write_text(resolved_path, text):
    """同目录临时文件 + fsync + os.replace：读者永远只见完整文件。"""
    dir_name = os.path.dirname(os.path.abspath(resolved_path)) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as tmp_file:
            tmp_file.write(text)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, resolved_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def update_config_values(updates, config_path=None):
    """注释保留 + 文件锁 + .bak 备份 + 原子替换的统一配置写回入口（U-06）。

    read→patch→verify→backup→replace 全程在 sidecar 锁内串行化，
    并发 read-modify-write 不再交叉损坏（last-writer-wins）。
    返回 (previous_values, dict(updates))——previous_values 仅含文件中原本
    存在的键（缺键不出现，调用方自行决定缺省语义）。
    """
    updates = dict(updates)
    resolved_path = _resolve_existing_config_path(config_path)
    if not updates:
        return {}, {}
    with _config_write_lock(resolved_path):
        with open(resolved_path, "r", encoding="utf-8", newline="") as config_file:
            original_text = config_file.read()
        try:
            previous_parsed = json.loads(_strip_jsonc_comments_scanned(original_text))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"配置文件格式错误: {exc}")
        new_text = patch_jsonc_text(original_text, updates)
        _verify_patched(new_text, updates)
        backup_path = str(resolved_path) + ".bak"
        with open(backup_path, "w", encoding="utf-8", newline="") as backup_file:
            backup_file.write(original_text)
        logger.info("配置写回前已备份原文件: %s", backup_path)
        _atomic_write_text(resolved_path, new_text)
    previous_values = {
        key: previous_parsed[key] for key in updates if key in previous_parsed
    }
    return previous_values, updates


def update_runtime_mode(probe_only, if_commit_order, config_path=None):
    """Update runtime mode flags in the target config file and persist them.

    U-06：底层改为 update_config_values（注释保留 + 锁 + .bak），
    签名与 (previous_flags, new_flags) 返回契约保持不变。
    """
    if not isinstance(probe_only, bool):
        raise ValueError(f"probe_only 必须是布尔值，实际值: {probe_only!r}")
    if not isinstance(if_commit_order, bool):
        raise ValueError(f"if_commit_order 必须是布尔值，实际值: {if_commit_order!r}")

    previous_values, _ = update_config_values(
        {"probe_only": probe_only, "if_commit_order": if_commit_order}, config_path
    )
    # 与旧实现逐位对齐：缺键时 probe_only 缺省 False、if_commit_order 缺省 None
    previous_flags = {
        "probe_only": previous_values.get("probe_only", False),
        "if_commit_order": previous_values.get("if_commit_order"),
    }
    return previous_flags, {
        "probe_only": probe_only,
        "if_commit_order": if_commit_order,
    }


_FLAG_MISSING = "__missing__"
_FLAG_INVALID = "__invalid__"


def _normalize_flag(value):
    """把 JSON 值规范化为 shell 可比较的四态字符串。

    注意必须用 ``is True / is False`` 身份判断——Python 的 bool 是 int 子类，
    truthiness 判断会把 1/0 误归为 true/false。
    """
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:  # 键缺失（dict.get 默认）或显式 null
        return _FLAG_MISSING
    return _FLAG_INVALID


def read_runtime_mode(config_path=None):
    """读取运行模式旗标——与实际执行（Config.load_config）同一解析器（U-03）。

    供 start_ticket_grabbing.sh 调用：shell 不再用 grep 文本匹配 JSONC
    （grep 会命中被注释掉的键值行），保证「屏显、改写判定、实际执行」三者同源。

    返回 (probe_only, if_commit_order) 规范化字符串二元组，取值为
    "true" / "false" / "__missing__"（键缺失或显式 null）/ "__invalid__"（非布尔值）。
    解析失败时向上抛 FileNotFoundError / ValueError，由调用方决定退出。

    契约：shell 侧 heredoc 只允许向 stdout 打印两行旗标——本模块 import 链路
    不得引入任何 stdout 输出（logging 默认走 stderr），否则 shell 的按行拆分会错判模式。
    """
    config_dict = load_config_dict(config_path)
    return (
        _normalize_flag(config_dict.get("probe_only")),
        _normalize_flag(config_dict.get("if_commit_order")),
    )


class Config:
    def __init__(
        self,
        keyword,
        users,
        city,
        date,
        price,
        price_index,
        if_commit_order,
        probe_only=False,
        app_package="cn.damai",
        app_activity=".launcher.splash.SplashMainActivity",
        sell_start_time=None,
        countdown_lead_ms=3000,
        presale_prewarm_enabled=True,
        wait_cta_ready_timeout_ms=0,
        fast_retry_count=8,
        fast_retry_interval_ms=120,
        rush_mode=False,
        rush_skip_session=False,
        rush_skip_price_dump=True,
        rush_aggressive_retry=True,
        auto_navigate=True,
        target_title=None,
        target_venue=None,
        serial=None,
        # Deprecated Appium-era params — accepted for config file compat, ignored
        driver_backend="u2",
        server_url=None,
        device_name="Android",
        udid=None,
        platform_version=None,
    ):

        # Validate users
        validate_non_empty_list(users, "users")

        # Validate price_index
        if (
            not isinstance(price_index, int)
            or isinstance(price_index, bool)
            or price_index < 0
        ):
            raise ValueError(f"price_index 必须是非负整数，实际值: {price_index!r}")

        if keyword is None or not isinstance(keyword, str) or len(keyword.strip()) == 0:
            raise ValueError(f"keyword 不能为空，必须是非空字符串，实际值: {keyword!r}")

        if not isinstance(if_commit_order, bool):
            raise ValueError(
                f"if_commit_order 必须是布尔值，实际值: {if_commit_order!r}"
            )

        if not isinstance(probe_only, bool):
            raise ValueError(f"probe_only 必须是布尔值，实际值: {probe_only!r}")

        if serial is not None and (
            not isinstance(serial, str) or len(serial.strip()) == 0
        ):
            raise ValueError(f"serial 必须是非空字符串或 null，实际值: {serial!r}")

        if not isinstance(app_package, str) or len(app_package.strip()) == 0:
            raise ValueError(f"app_package 必须是非空字符串，实际值: {app_package!r}")

        if not isinstance(app_activity, str) or len(app_activity.strip()) == 0:
            raise ValueError(f"app_activity 必须是非空字符串，实际值: {app_activity!r}")

        if not isinstance(auto_navigate, bool):
            raise ValueError(f"auto_navigate 必须是布尔值，实际值: {auto_navigate!r}")

        if target_title is not None and (
            not isinstance(target_title, str) or len(target_title.strip()) == 0
        ):
            raise ValueError(
                f"target_title 必须是非空字符串或 null，实际值: {target_title!r}"
            )

        if target_venue is not None and (
            not isinstance(target_venue, str) or len(target_venue.strip()) == 0
        ):
            raise ValueError(
                f"target_venue 必须是非空字符串或 null，实际值: {target_venue!r}"
            )

        # Validate sell_start_time
        if sell_start_time is not None:
            if not isinstance(sell_start_time, str):
                raise ValueError(
                    f"sell_start_time 必须是 ISO 格式的时间字符串或 null，实际值: {sell_start_time!r}"
                )
            try:
                datetime.fromisoformat(sell_start_time)
            except (ValueError, TypeError):
                raise ValueError(
                    f"sell_start_time 无法解析为 ISO 时间格式，实际值: {sell_start_time!r}"
                )

        # Validate countdown_lead_ms
        if (
            not isinstance(countdown_lead_ms, int)
            or isinstance(countdown_lead_ms, bool)
            or countdown_lead_ms < 0
        ):
            raise ValueError(
                f"countdown_lead_ms 必须是非负整数，实际值: {countdown_lead_ms!r}"
            )

        if not isinstance(presale_prewarm_enabled, bool):
            raise ValueError(
                "presale_prewarm_enabled 必须是布尔值，"
                f"实际值: {presale_prewarm_enabled!r}"
            )

        if (
            not isinstance(wait_cta_ready_timeout_ms, int)
            or isinstance(wait_cta_ready_timeout_ms, bool)
            or wait_cta_ready_timeout_ms < 0
        ):
            raise ValueError(
                f"wait_cta_ready_timeout_ms 必须是非负整数，实际值: {wait_cta_ready_timeout_ms!r}"
            )

        # Validate fast_retry_count
        if (
            not isinstance(fast_retry_count, int)
            or isinstance(fast_retry_count, bool)
            or fast_retry_count < 0
        ):
            raise ValueError(
                f"fast_retry_count 必须是非负整数，实际值: {fast_retry_count!r}"
            )

        # Validate fast_retry_interval_ms
        if (
            not isinstance(fast_retry_interval_ms, int)
            or isinstance(fast_retry_interval_ms, bool)
            or fast_retry_interval_ms < 0
        ):
            raise ValueError(
                f"fast_retry_interval_ms 必须是非负整数，实际值: {fast_retry_interval_ms!r}"
            )

        if not isinstance(rush_mode, bool):
            raise ValueError(f"rush_mode 必须是布尔值，实际值: {rush_mode!r}")

        for _name, _value in (
            ("rush_skip_session", rush_skip_session),
            ("rush_skip_price_dump", rush_skip_price_dump),
            ("rush_aggressive_retry", rush_aggressive_retry),
        ):
            if not isinstance(_value, bool):
                raise ValueError(f"{_name} 必须是布尔值，实际值: {_value!r}")

        self.keyword = keyword.strip()
        self.users = users
        self.city = city
        self.date = date
        self.price = price
        self.price_index = price_index
        self.if_commit_order = if_commit_order
        self.probe_only = probe_only
        self.app_package = app_package
        self.app_activity = app_activity
        self.sell_start_time = sell_start_time
        self.countdown_lead_ms = countdown_lead_ms
        self.presale_prewarm_enabled = presale_prewarm_enabled
        self.wait_cta_ready_timeout_ms = wait_cta_ready_timeout_ms
        self.fast_retry_count = fast_retry_count
        self.fast_retry_interval_ms = fast_retry_interval_ms
        self.rush_mode = rush_mode
        # rush_mode 是 alias：当前 release 周期保留兼容；W4 评估废弃。
        # 解析规则：rush_mode=True 时统一翻转 3 个子开关到「快速」侧；
        # 但 rush_skip_session 强制为 False — 多场次场景下永远不能跳过选场（issue #25 根因）。
        if rush_mode:
            if rush_skip_session:
                logger.warning(
                    "rush_mode=True 不会启用 rush_skip_session（多场次场景需选场）"
                )
            self.rush_skip_session = False
            self.rush_skip_price_dump = rush_skip_price_dump
            self.rush_aggressive_retry = rush_aggressive_retry
        else:
            self.rush_skip_session = rush_skip_session
            self.rush_skip_price_dump = rush_skip_price_dump
            self.rush_aggressive_retry = rush_aggressive_retry

        logger.info(
            "rush effective: rush_mode=%s, skip_session=%s, skip_price_dump=%s, aggressive_retry=%s",
            self.rush_mode,
            self.rush_skip_session,
            self.rush_skip_price_dump,
            self.rush_aggressive_retry,
        )

        self.auto_navigate = auto_navigate
        self.target_title = (
            target_title.strip() if isinstance(target_title, str) else None
        )
        self.target_venue = (
            target_venue.strip() if isinstance(target_venue, str) else None
        )
        self.serial = serial.strip() if isinstance(serial, str) else None

    def to_dict(self):
        """Return the config as a plain dictionary for rewriting config.jsonc."""
        return {
            "serial": self.serial,
            "app_package": self.app_package,
            "app_activity": self.app_activity,
            "keyword": self.keyword,
            "target_title": self.target_title,
            "target_venue": self.target_venue,
            "users": self.users,
            "city": self.city,
            "date": self.date,
            "price": self.price,
            "price_index": self.price_index,
            "if_commit_order": self.if_commit_order,
            "probe_only": self.probe_only,
            "auto_navigate": self.auto_navigate,
            "sell_start_time": self.sell_start_time,
            "countdown_lead_ms": self.countdown_lead_ms,
            "presale_prewarm_enabled": self.presale_prewarm_enabled,
            "wait_cta_ready_timeout_ms": self.wait_cta_ready_timeout_ms,
            "fast_retry_count": self.fast_retry_count,
            "fast_retry_interval_ms": self.fast_retry_interval_ms,
            "rush_mode": self.rush_mode,
            "rush_skip_session": self.rush_skip_session,
            "rush_skip_price_dump": self.rush_skip_price_dump,
            "rush_aggressive_retry": self.rush_aggressive_retry,
        }

    @staticmethod
    def load_config(config_path=None, strict_placeholders=True):
        config = load_config_dict(config_path)

        required_keys = [
            "users",
            "city",
            "date",
            "price",
            "price_index",
            "if_commit_order",
        ]
        missing = [k for k in required_keys if k not in config]
        if missing:
            raise KeyError(f"配置文件缺少必需字段: {', '.join(missing)}")

        if "keyword" not in config:
            raise KeyError("配置文件缺少必需字段: keyword")

        # U-05 — 启动期防护：占位符黑名单（默认硬失败）+ 未知/废弃字段警告
        validate_config_dict(config, strict_placeholders=strict_placeholders)

        # U-05 — udid 兼容回退：旧文档教用户填 udid，此前被静默丢弃；
        # 废弃警告已由 validate_config_dict 输出。
        # U-12 — HATICKETS_SERIAL 环境变量非空时优先于文件值（不写回配置文件）。
        env_serial = os.environ.get(SERIAL_OVERRIDE_ENV_VAR, "").strip()
        serial = env_serial or config.get("serial") or config.get("udid")

        # P0 — 本次进程的运行模式覆盖磁盘配置，不写回文件：
        # - probe 始终强制安全探测；
        # - validation 可走到确认页但绝不提交；
        # - commit 还必须带启动脚本注入的第二道显式授权。
        # 未设置时保持 Config 的库级兼容语义；damai_app 入口会对磁盘中残留的
        # if_commit_order=true 再做 fail-closed 拦截。
        runtime_mode = os.environ.get(RUNTIME_MODE_ENV_VAR, "").strip().lower()
        if runtime_mode and runtime_mode not in {"probe", "validation", "commit"}:
            raise ConfigError(
                f"{RUNTIME_MODE_ENV_VAR} 必须是 probe/validation/commit，"
                f"实际值: {runtime_mode!r}"
            )
        if runtime_mode == "probe":
            effective_probe_only = True
            effective_if_commit_order = False
        elif runtime_mode == "validation":
            effective_probe_only = False
            effective_if_commit_order = False
        elif runtime_mode == "commit":
            if os.environ.get(COMMIT_AUTH_ENV_VAR) != COMMIT_AUTH_VALUE:
                raise ConfigError(
                    "正式提交授权缺失或已失效；请通过 "
                    "start_ticket_grabbing.sh --commit 启动"
                )
            formal_required = {
                "target_title": config.get("target_title"),
                "date": config.get("date"),
                "price": config.get("price"),
            }
            invalid_formal = [
                key
                for key, value in formal_required.items()
                if not isinstance(value, str) or not value.strip()
            ]
            if invalid_formal:
                raise ConfigError(
                    "正式提交必须配置非空精确字段: "
                    + ", ".join(invalid_formal)
                )
            effective_probe_only = False
            effective_if_commit_order = True
        else:
            effective_probe_only = config.get("probe_only", False)
            effective_if_commit_order = config["if_commit_order"]

        # P1 #31 — 启动期校验 price_index 范围
        raw_price_index = config["price_index"]
        if isinstance(raw_price_index, int) and not isinstance(raw_price_index, bool):
            if raw_price_index < 0:
                raise ConfigError(f"price_index 不能为负数（当前 {raw_price_index}）")
            if raw_price_index > PRICE_INDEX_LARGE_WARNING_THRESHOLD:
                logger.warning(
                    "price_index=%d 异常大（>%d），请确认 mobile/config.jsonc 是否填错",
                    raw_price_index,
                    PRICE_INDEX_LARGE_WARNING_THRESHOLD,
                )

        return Config(
            keyword=config.get("keyword"),
            users=config["users"],
            city=config["city"],
            date=config["date"],
            price=config["price"],
            price_index=config["price_index"],
            if_commit_order=effective_if_commit_order,
            probe_only=effective_probe_only,
            app_package=config.get("app_package", "cn.damai"),
            app_activity=config.get(
                "app_activity", ".launcher.splash.SplashMainActivity"
            ),
            sell_start_time=config.get("sell_start_time"),
            countdown_lead_ms=config.get("countdown_lead_ms", 3000),
            presale_prewarm_enabled=config.get(
                "presale_prewarm_enabled", True
            ),
            wait_cta_ready_timeout_ms=config.get("wait_cta_ready_timeout_ms", 0),
            fast_retry_count=config.get("fast_retry_count", 8),
            fast_retry_interval_ms=config.get("fast_retry_interval_ms", 120),
            rush_mode=config.get("rush_mode", False),
            rush_skip_session=config.get("rush_skip_session", False),
            rush_skip_price_dump=config.get("rush_skip_price_dump", True),
            rush_aggressive_retry=config.get("rush_aggressive_retry", True),
            auto_navigate=config.get("auto_navigate", True),
            target_title=config.get("target_title"),
            target_venue=config.get("target_venue"),
            serial=serial,
        )
