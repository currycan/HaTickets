# 真机回归 Runbook

> 每次发版前必跑。从模板复制 → 填数据 → 归档到 `history/`。

## 触发条件

- 任一 P0 / P1 PR 合入
- 准备打 tag 发版
- 收到大麦 App 更新通知

## 流程（每次回归 1-2h）

### 1. 冒烟（10 min）

复制 `templates/_smoke_log.md` → 填：

- 设备 / Android / Damai 版本
- `poetry install` 通过
- `mobile/scripts/start_ticket_grabbing.sh --probe --yes` 进到详情页

### 2. NLP 5 演出（30 min）

复制 `templates/_nlp_5_show_log.md` → 跑 5 个不同演出。覆盖：

- #26 NLP autodetect 4 类正常输入 + 1 类故意缺信息
- #31 price_index 越界守护 3 子场景
- 无价格卡片 / 缺货 dump

### 3. 多场次（20 min）

复制 `templates/_multi_session_log.md` → 跑 ≥2 个多场次活动。覆盖：

- #25 多日同城 / 多日多城 / 单日多场
- `rush_mode` alias 兼容
- `rush_skip_session` 单场次/多场次行为

### 4. P2 边界（30 min）

复制 `templates/_p2_scenarios_log.md` → 跑三类场景：

- #28 `wait_for_home_ready` 超时与 dump
- #23 `select_search_result` 0/1/N 分流（含 strict 模式）
- #24 `PageProbe` unknown_threshold 阈值与 `force_state`

### 5. 性能基准（10 min）

```bash
bash mobile/scripts/benchmark_hot_path.sh --runs 5
```

把中位数填到本次冒烟日志最末「性能数据」段。

### 6. 归档

- 把填好的文件 `git mv` 到 `history/<日期>-<标签>.md`，命名形如 `2026-MM-DD-<release-or-tag>.md`、`2026-MM-DD-findings.md`、`2026-MM-DD-summary.md`。
- 更新 `android_compatibility_matrix.md` 一行：本次设备 / Android / Damai 版本 + 通过/失败摘要。
- 把 findings 中的新 issue 用 `gh issue create` 上报，并在对应 PR 描述中回链。

## SLA

- P0 PR：24h 内完成回归
- P1 PR：48h 内完成回归
- 其他：与 sprint 同步

## 不做的事

- ❌ 不在模板 / 历史日志中含真实演出名 / 真实姓名 / 手机号 / 身份证号 / 订单号
- ❌ 不删除已有 history（只 `git mv` 重组）
- ❌ 不下调 80% 覆盖率门槛来"绕过"失败（应在本 PR 增补单测）

## 关联

- `reference/06-test-gap-analysis.md`：测试维护节奏
- `reference/05-fix-plans/`：每个 issue 的修复计划与回归点
- `reference/08-ops-runbook.md`：抢票现场与回滚流程
