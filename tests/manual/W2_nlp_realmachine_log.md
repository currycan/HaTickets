# W2 NLP 真机 probe 验证日志

> **范围**: W2-01 (#26 NLP autodetect) + W2-02 (#31 price_index 越界守护) 已合入 master 后的真机回归。
> **目标**: 在不少于 1 台真机上验证 5 类 NLP 输入与 2 类越界场景，所有结果回填本表后归档。
> **保密要求**: 演出名脱敏（首字母 + 类型），不写真实观演人姓名、手机号、身份证号。
> **关联**: refs #26、#31；上游模板参考 `tests/manual/W1_realmachine_log.md`（如未存在，本文件视作首份模板）。

---

## 测试环境

| 项 | 值（待 qa 填写） |
| --- | --- |
| 真机型号 / Android 版本 | _ |
| Damai App 版本 | _ |
| 仓库 commit (`git rev-parse --short HEAD`) | _ |
| 操作者（脱敏 ID，如 qa-01） | _ |
| 执行日期（YYYY-MM-DD） | _ |

---

## Task A：5 演出 NLP probe 测试矩阵

> 在每台真机上执行 `bash mobile/scripts/run_from_prompt.sh --mode summary --yes "<自然语言输入>"`，把 stdout 的关键字段填入对应行。
> `actionable=no` 视为预期失败（仅 #5 故意缺信息）；其它 4 条若 actionable=no，需要在「diagnostics（关键）」列写出 `confidence` 与 `missing` 字段并在 Task D 中开 issue。

| # | 演出名（脱敏） | 类型 | 自然语言输入 | 预期 actionable | 实际 actionable | 命中演出名 | 命中场次 | 命中价格 | confidence | diagnostics（关键） | 通过 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | XX 演唱会上海站 | 单场次 | "XX 4月6号 上海 880 元" | yes | _ | _ | _ | _ | _ | _ | _ |
| 2 | YY 演唱会全国巡演 | 多场次 | "YY 4月10号 北京 1280 元" | yes | _ | _ | _ | _ | _ | _ | _ |
| 3 | ZZ 音乐节 2026 | 多日多场 | "ZZ 五一 上海 2 张" | yes | _ | _ | _ | _ | _ | _ | _ |
| 4 | 日期模糊场景 | 单场次 | "AA 演唱会 上海 ¥899-1280" | yes | _ | _ | _ | _ | _ | _ | _ |
| 5 | 故意缺信息 | 单场次 | "BB 演唱会" | no（应触发交互或 exit 5） | _ | _ | _ | _ | _ | _ | _ |

> **填表说明**
> - `命中演出名 / 命中场次 / 命中价格` 取自 summary 输出的 `selected_show / selected_session / selected_price` 字段。
> - `confidence` 取 summary 顶层的同名字段。
> - `diagnostics（关键）` 摘录 actionable=no 时的 `missing` / `errors` / `dump_path`（若有）；actionable=yes 时记 "ok"。
> - `通过` 列写 ✅/❌；❌ 行须在 Task D 开 issue。

---

## Task B：执行步骤（人工 qa 真机现场操作）

```bash
cd /Users/andrew/Documents/GitHub/HaTickets
git checkout master && git pull   # W2-01 W2-02 已合入

# 1. 5 演出 NLP probe（逐个执行，回填 Task A 表格）
for i in 1 2 3 4 5; do
  echo "===== Case #$i ====="
  bash mobile/scripts/run_from_prompt.sh --mode summary --yes "<第 i 个测试输入>"
  # 把输出复制到日志表对应行
done

# 2. price_index 越界场景（验证 W2-02 启动期 ConfigError）
cp mobile/config.local.jsonc mobile/config.local.jsonc.bak 2>/dev/null || cp mobile/config.jsonc mobile/config.local.jsonc
sed -i.bak 's/"price_index": [0-9]*/"price_index": 999/' mobile/config.local.jsonc
bash mobile/scripts/start_ticket_grabbing.sh --probe --yes
# 预期：启动期立即 ConfigError 退出，stderr 含 "price_index"，进程不进入抢票流程
mv mobile/config.local.jsonc.bak mobile/config.local.jsonc

# 3. 无价格卡片 / 缺货场景（手动构造或等遇到）
# 触发后检查 tmp/ 下是否生成 dump
ls -lt tmp/price_dump_*.xml 2>/dev/null | head -5
```

### B-1 越界场景结果

| 子场景 | 预期 | 实际（待填） | 通过 |
| --- | --- | --- | --- |
| price_index = 999（远超合法范围） | 启动期 ConfigError 立即退出 | _ | _ |
| price_index = -1（负数） | 启动期 ConfigError 立即退出 | _ | _ |
| price_index = 51（仅大于 warning 阈值） | 启动 warning，但流程继续 | _ | _ |

### B-2 无价格卡片 dump

| 触发方式 | tmp/price_dump_\*.xml 是否生成 | dump 文件名（脱敏） | 通过 |
| --- | --- | --- | --- |
| 无价格卡片演出 / 已售罄 | _ | _ | _ |

---

## Task C：冷启动到首次 actionable 时长（招募 1 个非技术用户）

> 招募一名未参与 HaTickets 开发的志愿者，从零开始按 `docs/quick-start.md` 操作；qa 在旁记录每步耗时。

| 步骤 | 耗时 (min) | 备注 |
| --- | --- | --- |
| 1. clone 仓库 | _ | _ |
| 2. poetry install | _ | _ |
| 3. 设备连接 + adb devices | _ | _ |
| 4. 第一次 run_from_prompt summary | _ | _ |
| 5. 第一次 run_from_prompt apply（写 config） | _ | _ |
| 6. 第一次 probe 成功 | _ | _ |
| **总计** | _ | 目标 ≤ 30 min（北极星指标） |

### C 备注与障碍记录

> 用户在哪一步卡住、复述了哪些理解偏差、希望 docs 补什么截图，逐条列下。

- _

---

## 执行人签字

| 角色 | ID（脱敏） | 完成时间 | 备注 |
| --- | --- | --- | --- |
| 真机执行 qa | _ | _ | _ |
| 复核 qa（可选） | _ | _ | _ |

---

## 验收对照（PM/PR 用）

- [ ] Task A 5 行全部填写，且 ≥4 行 actionable=yes
- [ ] Task B-1 三个子场景均按预期触发（前两行 ConfigError，第三行仅 warning）
- [ ] Task B-2 至少 1 行 dump 已生成，文件路径已记录
- [ ] Task C 总计耗时填写完毕；若 > 30 min 在 Task D 归档 docs 改进 issue
- [ ] 本日志不含真实姓名/手机号/身份证号
