---
name: ✨ 功能请求 / Feature request
about: 新功能、改进建议、新场景支持（如新 UI 流程、新场次类型）
title: "[FEATURE] "
labels: ["enhancement", "triage"]
assignees: []
---

## 想解决的问题

<!-- 描述你遇到的实际场景或痛点。避免「我想要 X」式的解决方案先行；先讲问题。 -->

## 期望的行为

<!-- 你希望脚本怎么处理？请尽量具体。 -->

## 当前的 workaround（如有）

<!-- 例：手动在 config.jsonc 里设置 rush_mode=true；或人工选场次后再启动脚本 -->

## 替代方案

<!-- 你考虑过的其他做法？为什么不采纳？ -->

## 影响范围（请勾选）

- [ ] 仅自己的使用场景
- [ ] 影响 NLP 自动配置流程（`prompt_runner.py`）
- [ ] 影响热路径（开售后 3 秒内）
- [ ] 涉及多场次 / 选座 / 抢购排队等新场景
- [ ] 需新增对外可见的 CLI 参数 / config 字段
- [ ] 需修改文档（`docs/`）

## 优先级建议

> 最终由 PM 评定。详见 [reference/04-issues-matrix.md 优先级总览](https://github.com/currycan/HaTickets/blob/master/reference/04-issues-matrix.md)。

- [ ] P0 — 阻塞核心抢票流程
- [ ] P1 — 影响成功率或体验
- [ ] P2 — 改进或边缘场景

## 相关 issue / PR

<!-- 关联已有讨论。 -->

---

> **回复 SLA**：1 周内 PM 给出立项 / backlog / wontfix 决定。已立项的 feature 会进入下一轮 sprint 评估（参见 `reference/07-roadmap-4w.md`，团队内可见）。
