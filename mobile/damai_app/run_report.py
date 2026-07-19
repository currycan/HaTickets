# -*- coding: UTF-8 -*-
"""Run 出口层：语义化退出码常量 + 机器可读运行摘要落盘（U-12）。

本模块只在进程收尾阶段被 ``__main__`` 使用，不参与抢票热路径；
orchestrator 不依赖它，避免进入 damai_app 包的热 import 链。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import logger

# ── 语义化退出码（外部编排契约：cron/systemd 依赖这些数值，发布后勿改动）──
EXIT_SUCCESS = 0  # 抢票/探测/验证成功
EXIT_RETRIES_EXHAUSTED = 10  # run_with_retry 全部重试失败（可安全重启重试）
# 不可重试失败（sold_out / session_invalid / session_not_found /
# reservation_only / attendee_unselected / submit_unverified 等）。
# ⚠️ 编排器绝不能对 11 做自动重启——submit_unverified 场景可能重复下单。
EXIT_TERMINAL_FAILURE = 11
EXIT_CONFIG_OR_DEVICE_ERROR = 12  # 配置解析/校验失败、设备连接失败、运行期设备异常
EXIT_LOCK_CONFLICT = 13  # 同一设备（或 global）已有 HaTickets 实例运行
EXIT_INTERRUPTED = 130  # SIGINT / Ctrl-C

RUN_SUMMARY_SCHEMA_VERSION = 2
RESULT_JSON_ENV_VAR = "HATICKETS_RESULT_JSON"
# 相对 cwd；start_ticket_grabbing.sh 会先 cd 到 mobile/，即 mobile/tmp/（已 gitignore）。
# 固定路径是「最近一次 run」语义，需要保留历史请用 --result-json 指定带时间戳的路径。
DEFAULT_RESULT_JSON = "tmp/run_summary.json"


def write_run_summary(path, summary) -> bool:
    """原子写 JSON 运行摘要（.part 临时文件 + os.replace）。

    摘要是副产物：任何失败（目录不可写、磁盘满、不可序列化对象）只记
    warning、绝不抛出——写摘要失败绝不能污染进程退出码。
    """
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, target)
        return True
    except Exception as exc:  # noqa: BLE001 — 出口层兜底
        logger.warning("run summary 写入失败（不影响退出码）: %s", exc)
        return False
