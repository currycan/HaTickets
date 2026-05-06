# 多场次回归矩阵（_multi_session_log.md）

<!-- 复制时删除以下 [TODO] 标记 -->

> **范围**: 多场次场景下场次选择 (#25) 的真机回归。
> **预算**: 20 min。
> **保密要求**: 演出名脱敏（首字母 + 类型），不写真实观演人姓名 / 手机号 / 身份证号 / 订单号。
> **上游模板**: 抽自 `history/2026-05-22-W3-multi.md`。
> **关联实现**: `mobile/event_navigator.py:select_session`。

---

## 测试环境

| 项 | 值（待 qa 填写） |
| --- | --- |
| 真机型号 / Android 版本 | [TODO] _ |
| Damai App 版本 | [TODO] _ |
| 仓库 commit (`git rev-parse --short HEAD`) | [TODO] _ |
| 操作者（脱敏 ID，如 qa-01） | [TODO] _ |
| 执行日期（YYYY-MM-DD） | [TODO] _ |

---

## Task A：多场次 5 场景回归矩阵

> **填表说明**
> - `命中场次`：从 `mobile/scripts/start_ticket_grabbing.sh --probe --yes` 的日志中读取 `select_session: ... 命中 idx=K/N` 字段，记 `idx (K/N) - <场次描述>`，如 `0 (0/3) - 04.05 上海`。
> - `通过` 列写 ✅/❌；❌ 行须在 `_findings.md` 开 issue 并回链。
> - `# 4` 与 `# 5` 不需要真机活动，可在任意已 reach `sku_page` 的演出上验证；重点验证配置项的兼容/语义。
> <!-- 视场景调整：若 W4 后续引入新别名，把 alias 行扩展为 4-N -->

| # | 演出名（脱敏） | 类型 | date 配置 | city 配置 | 命中场次 | 通过 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | [TODO] 巡演 A 上海站 | 多日同城（同一城市多个日期） | [TODO] `04.05` | [TODO] `上海` | [TODO] _ | [TODO] _ | 期望唯一命中 04.05 |
| 2 | [TODO] 巡演 B 全国 | 多日多城（多个城市各一日期） | [TODO] `04.10` | [TODO] `北京` | [TODO] _ | [TODO] _ | 期望命中 北京 04.10；只填 date 应 ambiguous → 必须配合 city |
| 3 | [TODO] 音乐节 C 单日 | 多艺人单日（单日多场次卡片） | [TODO] `05.01` | [TODO] `上海` | [TODO] _ | [TODO] _ | 单日内若 SESSION_PICKER 不弹出（只有 1 场次），日志应说明 "skipped session selection"；fallback_index 失效 |
| 4 | [TODO] rush_mode alias 兼容 | _ | _ | _ | _ | [TODO] _ | `config` 中 `rush_mode: true` 启动期应自动展开为三个子开关默认值，且日志显示 `rush_skip_session=false / rush_skip_price_dump=true / rush_aggressive_retry=true` |
| 5 | [TODO] rush_skip_session=true（单场次场景） | _ | _ | _ | _ | [TODO] _ | 单场次演出 + `rush_skip_session: true`：跳过场次选择直接 sku_page；多场次演出 + `rush_skip_session: true`：日志应有 warning 或 fail-fast |

---

## Task B：执行步骤（人工 qa 真机现场操作）

```bash
cd /Users/andrew/Documents/GitHub/HaTickets
git checkout master && git pull

# 准备 config.local.jsonc，填入对应行的 keyword / date / city
# 然后逐场景执行：
bash mobile/scripts/start_ticket_grabbing.sh --probe --yes 2>&1 | tee tmp/multi_session_case_$N.log
# 把 select_session 命中行复制到 Task A 表格对应行

# 场景 4 验证：直接构造 alias 配置
# config.local.jsonc:
# {"rush_mode": true, ...}
# 启动后日志应有：rush_mode alias → rush_skip_session=false, rush_skip_price_dump=true, rush_aggressive_retry=true

# 场景 5：手动构造 rush_skip_session=true 单场次/多场次 各一次
```

---

## Task C：发现问题汇总

> 任一行 ❌ 都要在此区列出 issue 链接 + 一句话症状。
> 若已知症状但暂无 issue，先标记 "TBD" 并在 `_findings.md` 中提请创建。

- [TODO] _

---

## 归档

- 完成填写后，将本文件 `git mv` 到 `history/<日期>-<标签>.md`。
- 若 5 场景全部 ✅，在 #25 issue 下评论：「真机回归全部通过 — log: history/<新文件名>@<commit>」。
