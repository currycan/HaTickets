# GitHub Issue 评论回复模板

> **用途**：ops 在 GitHub 上分诊 / 跟进 issue 时复制粘贴使用。GitHub 原生不支持评论模板，本文件作为 ops 团队的「快捷回复库」。
>
> **来源**：从 [`reference/08-ops-runbook.md`](../reference/08-ops-runbook.md) §3「用户支持 SOP」抽取（reference/ 目录 gitignored，本文件在仓库内可见，供新加入 ops 快速上手）。
>
> **使用约定**：
> 1. 复制对应小节的代码块内容到 GitHub 评论
> 2. 把 `<占位符>` 替换为实际值
> 3. 隐去用户上报中的敏感个人信息（姓名、手机号、身份证号）后再贴日志/dump
> 4. 回复后立即在 issue 加上对应优先级标签（`priority/p0` / `priority/p1` / `priority/p2` / `needs-info` / `wontfix`）

## 1. 首次回复模板（2h SLA 内）

收到任何新 issue，先用此模板补环境信息：

```markdown
你好，已收到反馈，感谢报告 🙇

为加快定位，请补充以下信息：

- 设备型号：
- Android 版本：
- 大麦 App 版本（设置 → 关于）：
- HaTickets 版本 / commit：
- Python 版本（`poetry run python --version`）：
- `mobile/config.jsonc` 内容（**请隐去观演人姓名、手机号**）：
- 完整错误日志（请使用代码块粘贴 traceback）：

收到信息后我们会在 24 小时内（P0/P1）或 1 周内（P2）给出反馈。
```

并执行：

- [ ] 加 `triage` 标签
- [ ] 通知 dev lead（疑似 P0/P1）

## 2. 环境问题模板（#32 类 — Poetry 虚拟环境未激活 / 依赖缺失）

```markdown
看起来是 Poetry 虚拟环境未激活，导致 `mobile/` 模块无法 import 已安装的依赖。

请按以下顺序排查：

1. 在仓库根目录运行：
   ```bash
   poetry install
   ```
2. **不要直接 `python mobile/damai_app.py`**。请通过封装脚本启动：
   ```bash
   mobile/scripts/start_ticket_grabbing.sh --probe --yes
   ```
3. 如仍报错，请贴出 `poetry env info` 输出。

详见 [`docs/quick-start.md`](../blob/master/docs/quick-start.md) 的「常见错误排查」一节。
```

并执行：

- [ ] 加 `priority/p0` 或 `priority/p2` 标签
- [ ] 标 `needs-info` 等 1 周后未回复则关闭

## 3. UI 变更模板（#29 / #31 类 — 大麦 App 升级导致文案/坐标变更）

```markdown
疑似大麦 App UI 变更（文案 / 资源 ID / 坐标 / 价格选项布局之一）。

我们已加入维护流程，预计 **<X> 天内**发布修复版本。

**临时方案**（在等待修复期间）：

- 在 `mobile/config.jsonc` 中尝试设置：
  ```jsonc
  {
    "rush_mode": true,
    "price_index": 0
  }
  ```
- 或运行 `mobile/scripts/start_ticket_grabbing.sh --probe --yes` 抓取当前 UI dump，附加到本 issue 帮助加速定位。

**进度跟踪**：内部维护流程已登记，后续修复 commit 会在本 issue 下回复关联。

感谢理解 🙇
```

并执行：

- [ ] 加 `priority/p0` 或 `priority/p1` 标签
- [ ] 通知 dev lead 触发 [`reference/.maintenance-checklist.md`](../reference/.maintenance-checklist.md) 6 步流程
- [ ] 在 `reference/10-changelog.md` 追加变更条目

## 4. 多场次模板（#25 类 — 多场次活动跳过场次选择）

```markdown
多场次（音乐节 / 巡演）活动需要在 `mobile/config.jsonc` 中明确指定 `city` 与 `date` 字段：

```jsonc
{
  "city": "<城市，如「上海」>",
  "date": "<MM.dd 格式，如「07.20」>",
  "rush_mode": true   // 当前版本兜底，后续版本会优化场次选择状态机
}
```

完整修复（自动选择指定场次）将随 v0.4.0 发布（预计 **W3** 上线）。

如有具体场次的 dump 截图（`d.dump_hierarchy()` 输出）愿意附上，可帮助加速场次选择状态机的覆盖率提升。
```

并执行：

- [ ] 加 `priority/p1` 标签
- [ ] 关联到 issue #25 主线讨论

## 5. 关闭模板

### 5.1 已修复

```markdown
此问题已在 commit <SHA> / PR #<NN> 修复，将随 **v0.X.Y** 发布。

请在新版本发布后再次验证，如仍有问题欢迎重开。

感谢报告 🙇
```

### 5.2 wontfix（设计决策）

```markdown
经 PM 评估，此场景**暂不在维护范围内**：

> <具体原因，例：选座功能涉及大麦风控强匹配，当前版本不支持，详见 reference/04-issues-matrix.md #27>

如有强需求，欢迎在 [`docs/`](../blob/master/docs/) 之外的 discussions 区继续讨论。
```

### 5.3 needs-info 超时

```markdown
由于已超过 1 周未收到补充信息，本 issue 暂时关闭。

如问题仍存在，欢迎在新评论中补充环境与日志，我们会重新打开。
```

## 优先级标签速查（来自 [reference/08-ops-runbook.md §3](../reference/08-ops-runbook.md)）

| 标签 | 含义 | SLA |
| --- | --- | --- |
| `priority/p0` | 阻塞新用户启动 | 24 h 反馈，3 天内修复 |
| `priority/p1` | 影响成功率 | 48 h 反馈，1 周内修复 |
| `priority/p2` | 改进或边缘场景 | 1 周反馈，下个 sprint 评估 |
| `needs-info` | 等用户补充 | 1 周无回复关闭 |
| `wontfix` | 不修复（合规 / 设计决策） | PM 决定后立即关闭 |

## 隐私处理

收到含真实姓名 / 手机号 / 身份证号的 issue 时：

1. **立即在评论中编辑**（GitHub「edit」原评论）替换为 `<HIDDEN>`
2. 在评论下补一句：「已隐去敏感信息，下次提交建议预先处理。」
3. 不在 dev / qa 群转发原始内容
