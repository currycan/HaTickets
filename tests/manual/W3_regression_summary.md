# W3 回归总结（YYYY-MM-DD ~ YYYY-MM-DD）

> **范围**: W3 sprint（W3-01 多场次 #25 + W3-02 P2 三连 #28 #23 #24）真机 + 单测综合回归。
> **填写人**: qa（脱敏 ID）
> **关联**: refs #25 #28 #23 #24；PR #40（W3-01）+ PR #42（W3-02）+ 本 PR（W3-03 回归矩阵 + 单测增补）。

---

## 通过率汇总

> `通过 / 计划` 形式填写。`通过` = ✅ 行数；`计划` = 各 manual log 中表格行总数。

- W3-01 #25：__/_5 场景通过（详见 [`W3_multi_session_log.md`](W3_multi_session_log.md)）
- W3-02 #28：__/_4 场景通过（详见 [`W3_p2_log.md`](W3_p2_log.md) #28 区块）
- W3-02 #23：__/_6 场景通过（详见 [`W3_p2_log.md`](W3_p2_log.md) #23 区块；含 strict/dump 子用例）
- W3-02 #24：__/_5 场景通过（详见 [`W3_p2_log.md`](W3_p2_log.md) #24 区块）

整体通过率：__/_20

---

## 单测与覆盖率

| 项 | 结果 |
| --- | --- |
| `poetry run pytest --cov=mobile --cov-fail-under=80` | _ passed / coverage _% |
| 新增单测条数（C1+C2） | 3 |
| CI 矩阵（Py 3.8/3.9/3.10/3.12/3.13） | _ |

---

## 发现的新问题

> 一行一个 issue：编号 + 一句话症状 + 严重度（P0/P1/P2）+ 责任人。
> 若无新问题，写 "本轮回归未发现新问题"。

| issue # | 一句话症状 | 严重度 | owner | 发现于场景 |
| --- | --- | --- | --- | --- |
| TBD-1 | `PageProbe(unknown_threshold=0)` 当前 clamp 到 1（第一次 unknown 即告警），与"0 = 禁用告警"的直觉语义相反。需要 PM 决定：澄清文档（保留 clamp）/ 改源码（0=disable）。`tests/unit/test_page_probe.py::TestUnknownThresholdZeroBoundary::test_unknown_threshold_zero_disables_alert` 已用 `xfail(strict=True)` 锁定预期，待源码修复后 xfail 自动失效。 | P2 | dev / pm | C2 边界单测 |

---

## 已知风险 / 跳过项

- _

---

## 真机环境

| 项 | 值 |
| --- | --- |
| 真机型号 / Android 版本 | _ |
| Damai App 版本 | _ |
| 仓库 commit | _ |
| 操作者 | _ |
| 执行起止时间 | _ |

---

## 下一步建议

- 若 #25 / #28 / #23 / #24 全部 ✅：在各 issue 下评论「W3-03 通过」并提请 PM 决定是否 close。
- 若发现新问题：在 `reference/04-issues-matrix.md` 增加新行；在 W4 sprint 计划中纳入。
- 若覆盖率 < 80%：在本 PR 中追加单测，**不**降低覆盖率门槛。
