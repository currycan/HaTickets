# tests/manual/ — 真机回归手册

> mobile/ 模块依赖 UIAutomator2 直连真机，自动化测试以 Mock 为主（`tests/conftest.py`）。
> **本目录**承担纯单测覆盖不到的真机验证：UI hierarchy、Damai App 实际响应、抢票热路径性能。

## 目录结构

```
tests/manual/
├── README.md                         本文件 — 入口与使用方法
├── runbook.md                        标准回归流程（每次发版前跑）
├── android_compatibility_matrix.md   跨 Android 版本兼容性记录（持续更新）
├── templates/                        可复制模板
│   ├── _smoke_log.md                 冒烟测试模板
│   ├── _nlp_5_show_log.md            5 演出 NLP 测试矩阵模板
│   ├── _multi_session_log.md         多场次测试矩阵模板
│   ├── _p2_scenarios_log.md          P2 边界场景模板
│   └── _findings.md                  发现新 issue 的归档模板
└── history/                          历史回归记录（按 YYYY-MM-DD 归档）
    ├── 2026-05-15-W2-v0.4.0-rc1.md   NLP autodetect (#26) + price 越界守护 (#31)
    ├── 2026-05-15-findings.md        W2 真机发现归档
    ├── 2026-05-22-W3-multi.md        多场次回归 (#25)
    ├── 2026-05-22-W3-p2.md           P2 三连 (#28 #23 #24)
    └── 2026-05-22-W3-summary.md      W3 综合回归汇总
```

## 使用方法

### 一次完整回归（每次发版前）

1. 打开 [`runbook.md`](runbook.md)，按 6 步流程逐步执行。
2. 每步从 [`templates/`](templates/) 复制对应模板到工作副本，填数据。
3. 完成后把工作副本 `git mv` 到 [`history/`](history/)，命名 `YYYY-MM-DD-<release-or-tag>.md`。
4. 更新 [`android_compatibility_matrix.md`](android_compatibility_matrix.md) 一行。
5. 任何 ❌ 的问题在 [`templates/_findings.md`](templates/_findings.md) 复制 Finding 模板登记 → `gh issue create` 上报。

### 临时只跑一类场景

如：仅验证某个 P0 hotfix → 直接复制 `templates/_smoke_log.md` 跑冒烟即可，归档到 `history/<日期>-hotfix-<issue#>.md`。

## 触发条件（详见 runbook.md）

- 任一 P0 / P1 PR 合入
- 准备打 tag 发版
- 收到大麦 App 更新通知（高优先级，可能需要立即兼容性回归）

## SLA（详见 runbook.md）

- P0 PR：24h 内完成回归
- P1 PR：48h 内完成回归
- 其他：与 sprint 同步

## 保密红线

- 演出名脱敏（首字母 + 类型，如 "XX 演唱会"、"ZZ 音乐节"）
- 不写真实观演人姓名 / 手机号 / 身份证号 / 订单号
- 截图、dump 文件、config 摘录均需脱敏后再纳入文档或 issue

## 关联文档

- [`reference/06-test-gap-analysis.md`](../../reference/06-test-gap-analysis.md)：测试缺口与维护节奏
- [`reference/04-issues-matrix.md`](../../reference/04-issues-matrix.md)：每个 issue 的真机验证状态
- [`reference/08-ops-runbook.md`](../../reference/08-ops-runbook.md)：抢票现场与回滚流程
- [`mobile/scripts/benchmark_hot_path.sh`](../../mobile/scripts/benchmark_hot_path.sh)：抢票热路径性能基准
