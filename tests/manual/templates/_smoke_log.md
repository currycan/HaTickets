# 冒烟测试日志（_smoke_log.md）

<!-- 复制时删除以下 [TODO] 标记 -->
<!-- 视场景调整：可在 P0 PR 合入或紧急 hotfix 后只跑本表 -->

> **范围**: 发版前最小集冒烟。验证 mobile/ 主干在目标真机上可启动并到达详情页。
> **预算**: 10 min。
> **保密要求**: 演出名脱敏（首字母 + 类型），不写真实观演人姓名 / 手机号 / 身份证号 / 订单号。

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

## Task A：依赖与基础启动

| # | 测试 | 命令 | 预期 | 实际（待填） | 通过 |
| --- | --- | --- | --- | --- | --- |
| 1 | poetry 安装 | `poetry install` | 0 错误，依赖落地 | [TODO] _ | [TODO] _ |
| 2 | adb 连接 | `adb devices` | 设备状态为 `device`（非 `unauthorized` / `offline`） | [TODO] _ | [TODO] _ |
| 3 | Damai 进程可启动 | `adb shell am start -n cn.damai/.homepage.MainActivity` | 首页可见，无 ANR | [TODO] _ | [TODO] _ |

---

## Task B：probe 到详情页

```bash
bash mobile/scripts/start_ticket_grabbing.sh --probe --yes 2>&1 | tee tmp/smoke_probe.log
```

| # | 测试 | 预期 | 实际（待填） | 通过 |
| --- | --- | --- | --- | --- |
| 1 | 启动期 ConfigError 不触发 | 无 ConfigError，进入主流程 | [TODO] _ | [TODO] _ |
| 2 | 首页就绪 (#28) | 日志含 `wait_for_home_ready ... state=homepage` | [TODO] _ | [TODO] _ |
| 3 | 搜索 + 选中 (#23) | 日志含 `select_search_result picked` | [TODO] _ | [TODO] _ |
| 4 | 抵达详情页 | 日志含 `reach detail page` 或 `ProjectDetailActivity` | [TODO] _ | [TODO] _ |
| 5 | probe 模式安全退出 | 退出码 0 / 不下单 | [TODO] _ | [TODO] _ |

---

## Task C：失败工件检查

> 如本次有任意 ❌，把 `tmp/` 下相关文件清单填入；正常路径可写「无」。

| 文件类型 | 命名样式 | 是否生成 | 路径（脱敏） |
| --- | --- | --- | --- |
| home_probe dump | `tmp/home_probe_*.xml` | [TODO] 无/有 | [TODO] _ |
| search_probe dump | `tmp/search_probe_*.xml` | [TODO] 无/有 | [TODO] _ |
| page_probe unknown | `tmp/page_probe_unknown_*.xml` | [TODO] 无/有 | [TODO] _ |
| 失败工件包 | `tmp/failure_*.zip`（W4-02 引入） | [TODO] 无/有 | [TODO] _ |

---

## 验收

- [ ] Task A 三行全 ✅
- [ ] Task B 五行全 ✅
- [ ] 任一 ❌ 已在同次回归的 `_findings.md` 中追加 Finding
- [ ] 本日志不含真实姓名 / 手机号 / 身份证号 / 订单号

---

## 性能数据（在本次回归末尾追加）

```bash
bash mobile/scripts/benchmark_hot_path.sh --runs 5
```

| 指标 | 中位数 (ms) | 备注 |
| --- | --- | --- |
| wait_for_home_ready | [TODO] _ | _ |
| select_search_result | [TODO] _ | _ |
| select_session | [TODO] _ | _ |
| _submit_order_fast | [TODO] _ | _ |
