---
name: 🐛 Bug 报告 / Bug report
about: 抢票脚本运行异常、报错、文案/坐标不匹配等问题
title: "[BUG] "
labels: ["bug", "triage"]
assignees: []
---

> **隐私提示**：请在粘贴 `mobile/config.jsonc` 与日志前，**隐去观演人姓名、手机号、身份证号**。如不慎泄露，ops 收到后会在评论中编辑替换，但仍建议自查后再提交。

## 环境信息（必填）

> 运维分类需要这一节才能定位优先级，请勿删除。

- 设备型号（例：Pixel 6 / Xiaomi 13）：
- Android 版本（设置 → 关于手机）：
- 大麦 App 版本（大麦 → 我的 → 设置 → 关于）：
- HaTickets 版本 / commit SHA：
- Python 版本（`poetry run python --version`）：
- 操作系统（macOS 14 / Ubuntu 22.04 / Windows 11）：

## 复现步骤

1.
2.
3.

## 期望行为

<!-- 例：脚本应在开售后 3s 内进入下单确认页 -->

## 实际行为

<!-- 例：脚本卡在「立即预订」轮询超时 -->

## 完整日志（必填）

> 完整 traceback 比截图更有助于定位。请使用代码块。

```
<粘贴 poetry run 输出 / start_ticket_grabbing.sh 输出 / tmp/failures/*.log>
```

## 配置（隐去敏感字段）

```jsonc
{
  "serial": "<DEVICE_SERIAL>",
  "keyword": "<演出关键词>",
  "users": ["<HIDDEN>"],
  "city": "<城市>",
  "date": "<MM.dd>",
  // ... 其他字段
}
```

## 相关截图 / dump（可选）

<!-- 拖拽上传或附 tmp/failures/ 中文件 -->

## 自查清单

- [ ] 已运行 `poetry install`，且通过 `mobile/scripts/start_ticket_grabbing.sh --probe --yes` 启动（未直接 `python xxx.py`）
- [ ] 已阅读 `docs/quick-start.md` 的「常见错误排查」一节
- [ ] 已搜索现有 issues 确认无重复
- [ ] 已隐去敏感个人信息

---

> **SLA**：ops 在 2 小时内分诊（添加优先级标签）。P0/P1 见 [reference/08-ops-runbook.md §3](https://github.com/currycan/HaTickets/blob/master/reference/08-ops-runbook.md)。
