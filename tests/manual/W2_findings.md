# W2 真机验证发现归档

> **用途**: 记录 W2 真机执行 (`tests/manual/W2_nlp_realmachine_log.md`) 中发现的、不在预期内的失败、回归或可用性问题。
> **流程**: 每发现一个新问题 →
>   1. 在本文件追加一条「Finding 模板」并填写
>   2. 用 `gh issue create` 上报（含 OS / Android / Damai App 版本 / 脱敏 config / 错误日志摘要）
>   3. 在对应 PR 描述中引用新 issue 编号
> **保密要求**: 截图、日志、config 必须脱敏（演出名首字母 + 类型，无姓名/手机号/身份证号/订单号）。

---

## 已发现 Findings

> 暂无；qa 真机执行后追加。

---

## Finding 模板（复制后填写）

```markdown
### Finding YYYY-MM-DD-NN：<一句话标题>

- **发现时间**: YYYY-MM-DD HH:MM (Asia/Shanghai)
- **执行人 ID（脱敏）**: qa-XX
- **真机型号 / Android**: _
- **Damai App 版本**: _
- **仓库 commit**: `git rev-parse --short HEAD` 输出
- **触发场景**: （Task A #N / Task B-1 / Task B-2 / Task C 步骤 N / 其它）
- **复现步骤**:
  1. _
  2. _
  3. _
- **预期**: _
- **实际**: _
- **关键日志（脱敏）**:
  ```
  <stderr / 关键 stack trace>
  ```
- **附件**:
  - tmp/price_dump_*.xml（路径，已脱敏）
  - 截图链接（脱敏后存放位置）
- **严重度**: P0 / P1 / P2 / P3
- **建议归类**: bug / docs / UX / 第三方变更
- **关联 issue**: #N（gh issue create 创建后回填）
- **关联 PR**: #N（修复 PR，事后回填）
```

---

## gh issue 上报示例（便于 qa 复用）

```bash
gh issue create \
  --title "[W2 真机] <一句话现象>" \
  --label "bug,W2,real-machine" \
  --body "$(cat <<'EOF'
## 现象
<一句话描述>

## 复现步骤
1. ...
2. ...

## 环境
- 真机 / Android: _
- Damai App: _
- HaTickets commit: _
- config 关键字段（脱敏）: price_index=__, keyword=__

## 关键日志
<脱敏后的 stderr / stack trace>

## 关联
- 真机日志: tests/manual/W2_nlp_realmachine_log.md (Task __)
- 详细记录: tests/manual/W2_findings.md (Finding __)
EOF
)"
```

---

## 归档完成判定

- [ ] 本文件每条 Finding 都已建对应 GitHub issue（除非确认为操作失误）
- [ ] 每条 Finding 都标注严重度
- [ ] 截图与 dump 路径不含真实演出名 / 姓名 / 手机号
