# 5 演出 NLP 真机测试矩阵（_nlp_5_show_log.md）

<!-- 复制时删除以下 [TODO] 标记 -->

> **范围**: NLP autodetect (#26) + price_index 越界守护 (#31) 的回归矩阵。
> **预算**: 30 min。
> **保密要求**: 演出名脱敏（首字母 + 类型），不写真实观演人姓名 / 手机号 / 身份证号 / 订单号。
> **上游模板**: 抽自 `history/2026-05-15-W2-v0.4.0-rc1.md`。

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

## Task A：5 演出 NLP probe 测试矩阵

> 在每台真机上执行 `bash mobile/scripts/run_from_prompt.sh --mode summary --yes "<自然语言输入>"`，把 stdout 的关键字段填入对应行。
> `actionable=no` 视为预期失败（仅 #5 故意缺信息）；其它 4 条若 actionable=no，需要在「diagnostics（关键）」列写出 `confidence` 与 `missing` 字段并在 `_findings.md` 中开 issue。
> <!-- 视场景调整：演出名脱敏到首字母 + 类型，禁止真实演出名 -->

| # | 演出名（脱敏） | 类型 | 自然语言输入 | 预期 actionable | 实际 actionable | 命中演出名 | 命中场次 | 命中价格 | confidence | diagnostics（关键） | 通过 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | [TODO] XX 演唱会 单场次 | 单场次 | [TODO] "<演出代号> <日期> <城市> <价格>" | yes | [TODO] _ | [TODO] _ | [TODO] _ | [TODO] _ | [TODO] _ | [TODO] _ | [TODO] _ |
| 2 | [TODO] YY 演唱会 全国巡演 | 多场次 | [TODO] "<演出代号> <日期> <城市> <价格>" | yes | [TODO] _ | [TODO] _ | [TODO] _ | [TODO] _ | [TODO] _ | [TODO] _ | [TODO] _ |
| 3 | [TODO] ZZ 音乐节 | 多日多场 | [TODO] "<演出代号> 五一 <城市> 2 张" | yes | [TODO] _ | [TODO] _ | [TODO] _ | [TODO] _ | [TODO] _ | [TODO] _ | [TODO] _ |
| 4 | [TODO] 日期模糊场景 | 单场次 | [TODO] "<演出代号> <城市> ¥A-B" | yes | [TODO] _ | [TODO] _ | [TODO] _ | [TODO] _ | [TODO] _ | [TODO] _ | [TODO] _ |
| 5 | [TODO] 故意缺信息 | 单场次 | [TODO] "<仅演出代号>" | no（应触发交互或 exit 5） | [TODO] _ | [TODO] _ | [TODO] _ | [TODO] _ | [TODO] _ | [TODO] _ | [TODO] _ |

> **填表说明**
> - `命中演出名 / 命中场次 / 命中价格` 取自 summary 输出的 `selected_show / selected_session / selected_price` 字段。
> - `confidence` 取 summary 顶层的同名字段。
> - `diagnostics（关键）` 摘录 actionable=no 时的 `missing` / `errors` / `dump_path`（若有）；actionable=yes 时记 "ok"。
> - `通过` 列写 ✅/❌；❌ 行须在 `_findings.md` 开 issue。

---

## Task B：price_index 越界守护

```bash
# 先备份 config
cp mobile/config.local.jsonc mobile/config.local.jsonc.bak 2>/dev/null || cp mobile/config.jsonc mobile/config.local.jsonc

# 设置越界值
sed -i.bak 's/"price_index": [0-9]*/"price_index": 999/' mobile/config.local.jsonc
bash mobile/scripts/start_ticket_grabbing.sh --probe --yes
# 预期：启动期立即 ConfigError 退出，stderr 含 "price_index"，进程不进入抢票流程

# 还原
mv mobile/config.local.jsonc.bak mobile/config.local.jsonc
```

| 子场景 | 预期 | 实际（待填） | 通过 |
| --- | --- | --- | --- |
| price_index = 999（远超合法范围） | 启动期 ConfigError 立即退出 | [TODO] _ | [TODO] _ |
| price_index = -1（负数） | 启动期 ConfigError 立即退出 | [TODO] _ | [TODO] _ |
| price_index = 51（仅大于 warning 阈值） | 启动 warning，但流程继续 | [TODO] _ | [TODO] _ |

---

## Task C：无价格卡片 dump

| 触发方式 | tmp/price_dump_*.xml 是否生成 | dump 文件名（脱敏） | 通过 |
| --- | --- | --- | --- |
| 无价格卡片演出 / 已售罄 | [TODO] _ | [TODO] _ | [TODO] _ |

---

## 执行人签字

| 角色 | ID（脱敏） | 完成时间 | 备注 |
| --- | --- | --- | --- |
| 真机执行 qa | [TODO] _ | [TODO] _ | _ |
| 复核 qa（可选） | [TODO] _ | [TODO] _ | _ |

---

## 验收对照

- [ ] Task A 5 行全部填写，且 ≥4 行 actionable=yes
- [ ] Task B 三个子场景均按预期触发（前两行 ConfigError，第三行仅 warning）
- [ ] Task C 至少 1 行 dump 已生成，文件路径已记录
- [ ] 本日志不含真实姓名 / 手机号 / 身份证号 / 订单号
