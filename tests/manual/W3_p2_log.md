# W3 P2 三连真机回归日志（refs #28 #23 #24）

> **范围**: W3-02（P2 修复栈）已合入 master 后的真机回归。覆盖：
> - #28 wait_for_home_ready 首页探测超时 + UI dump
> - #23 select_search_result 0/1/N 分流 + UI dump
> - #24 PageProbe unknown_threshold 阈值告警 + force_state
>
> **目标**: 在不少于 1 台真机上，覆盖三个 issue 的关键分支并回填本表。
> **保密要求**: 演出名脱敏，不写真实观演人姓名 / 手机号 / 身份证号。
> **关联**: refs #28 #23 #24；PR #42；上游模板 `tests/manual/W2_nlp_realmachine_log.md`。

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

## #28 首页探测超时

> 实现位于 `mobile/page_helpers.py:wait_for_home_ready`。
> 期望：超时抛 `HomeNotReadyError`，异常 message 含 `dump=tmp/home_probe_<ts>.xml`，dump 文件已落地，日志输出当前可见 texts + resource_ids。

| # | 测试 | 步骤 | 预期 | 实际 | 通过 |
| --- | --- | --- | --- | --- | --- |
| 28-1 | 已在大麦首页 fast path | 启动脚本时已停留在大麦 MainActivity | 立即返回，state=homepage，<1.5s 完成 | _ | _ |
| 28-2 | 关闭大麦 App 后启动 | 强停大麦后启动脚本 | 8s 后抛 `HomeNotReadyError`，异常含 `首页未就绪` + dump 路径，文件存在 | _ | _ |
| 28-3 | 启动大麦但停在详情页 | 进入详情页后启动脚本 | 8s 后抛 `HomeNotReadyError`，dump 显示详情页 hierarchy（包含 `purchase_status_bar` 等关键 id） | _ | _ |
| 28-4 | dump 路径与命名 | 任一超时 case 后查看 `ls tmp/home_probe_*.xml` | 命名形如 `home_probe_YYYYMMDD_HHMMSS.xml`，体积 >0 | _ | _ |

---

## #23 搜索结果

> 实现位于 `mobile/page_helpers.py:select_search_result`。
> 0 结果 → `SearchEmptyError`（异常 message 含 keyword + dump）；1 结果 → 自动选中点击；N 结果 + target_title 命中 → 模糊匹配选中；N 结果 + target_title 未命中（默认 `strict=False`）→ 回退首条并 warning；`strict=True` 未命中则 `SearchAmbiguousError`。

| # | 测试 | 关键词 | target_title | strict | 预期 | 实际 | 通过 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 23-1 | 0 结果 | `xQyZpQ123abcdNOTEXIST` | `None` | `False` | `SearchEmptyError`，message 含 keyword + `dump=tmp/search_probe_*.xml` | _ | _ |
| 23-2 | 1 结果 | `<极冷门演出全名脱敏>` | `None` | `False` | 自动选中并跳转 `ProjectDetailActivity` | _ | _ |
| 23-3 | N 结果 + target 匹配 | `周杰伦` | `周杰伦` | `False` | 选中含 `周杰伦` 的卡片，跳转 ProjectDetailActivity | _ | _ |
| 23-4 | N 结果 + 无 target（fallback first） | `周杰伦` | `None` | `False` | 选中第一个，warning `回退到 idx=0 title=...` | _ | _ |
| 23-5 | N 结果 + target 未命中 + strict | `周杰伦` | `不存在的歌手` | `True` | `SearchAmbiguousError`，message 含 `共 N 条` + dump | _ | _ |
| 23-6 | dump 路径与命名 | 23-1 / 23-5 后查看 `ls tmp/search_probe_*.xml` | _ | _ | 命名形如 `search_probe_YYYYMMDD_HHMMSS.xml` | _ | _ |

---

## #24 page_probe unknown 阈值

> 实现位于 `mobile/page_probe.py:PageProbe._track_unknown` 与 `force_state`。
> 连续 N=`unknown_threshold` 次返回 `state="unknown"` 时：logger.warning(`UNKNOWN_THRESHOLD reached after N classifications`) + 自动写 `tmp/page_probe_unknown_<ts>.xml` + 设 `probe.dumped_xml_path`。
> `force_state(state)` 短路返回；`force_state(None)` 清除并恢复真实探测。

| # | 测试 | 步骤 | 预期 | 实际 | 通过 |
| --- | --- | --- | --- | --- | --- |
| 24-1 | 在弹窗未关时连续 probe | 启动脚本时手动让首页弹窗保持显示，连续 3 次 `PageProbe.classify()` | 第 3 次返回 unknown 后 logger.warning `UNKNOWN_THRESHOLD reached after 3 classifications`；`probe.dumped_xml_path` 非 None；文件存在且为弹窗 hierarchy | _ | _ |
| 24-2 | 任一已知状态后计数重置 | 关闭弹窗 → probe 一次（应为 homepage）→ 再次让弹窗出现 → 仅 1 次 unknown 不应触发告警 | 不出现告警；`dumped_xml_path` 维持 24-1 的旧值 | _ | _ |
| 24-3 | `force_state(PageState.HOMEPAGE)` 短路 | 在任意页面调用 `probe.force_state(PageState.HOMEPAGE)`；后续 `classify()` | state=homepage，且未触发 dump_hierarchy / app_current 调用（adb 抓包/日志确认） | _ | _ |
| 24-4 | `force_state(None)` 恢复 | 24-3 后调用 `probe.force_state(None)` 然后 classify | 返回真实 state（与设备实际页面一致） | _ | _ |
| 24-5 | dump 路径与命名 | 24-1 后查看 `ls tmp/page_probe_unknown_*.xml` | 命名形如 `page_probe_unknown_YYYYMMDD_HHMMSS.xml` | _ | _ |

---

## 执行步骤（人工 qa 真机现场操作）

```bash
cd /Users/andrew/Documents/GitHub/HaTickets
git checkout master && git pull   # W3-02 PR #42 已合入

# === #28 ===
# 28-1
adb shell am start -n cn.damai/.homepage.MainActivity   # 确保在首页
poetry run python -c "
import time, uiautomator2 as u2
from mobile.page_probe import PageProbe
from mobile.event_navigator import wait_for_home_ready, HomeNotReadyError
d = u2.connect()
probe = PageProbe(d, cache_ttl_s=0.0)
t0=time.time(); r=wait_for_home_ready(d, probe, timeout=8.0); print('elapsed_ms=', (time.time()-t0)*1000, 'state=', r['state'])
"

# 28-2
adb shell am force-stop cn.damai
poetry run python -c "
import time, uiautomator2 as u2
from mobile.page_probe import PageProbe
from mobile.event_navigator import wait_for_home_ready, HomeNotReadyError
d = u2.connect()
probe = PageProbe(d, cache_ttl_s=0.0)
try:
    wait_for_home_ready(d, probe, timeout=8.0)
except HomeNotReadyError as e:
    print('OK:', e)
import glob; print('dumps:', glob.glob('tmp/home_probe_*.xml'))
"

# === #23 ===
# 进搜索页（首页 → 搜索按钮）
poetry run python -c "
import time, uiautomator2 as u2
d = u2.connect()
d(resourceId='cn.damai:id/pioneer_homepage_header_search_btn').click(); time.sleep(1.2)
# 关位置权限弹窗（如有）
for _ in range(3):
    btn = d(resourceId='cn.damai:id/damai_theme_dialog_cancel_btn')
    if not btn.exists: break
    btn.click(); time.sleep(0.6)
print('on search:', d.app_current().get('activity'))
"

# 23-1 / 23-3 / 23-4 / 23-5：在搜索页执行
poetry run python -c "
import time, uiautomator2 as u2
from mobile.event_navigator import select_search_result, SearchEmptyError, SearchAmbiguousError
d = u2.connect()
inp = d(resourceId='cn.damai:id/header_search_v2_input')
inp.click(); time.sleep(0.3)
btn = d(resourceId='cn.damai:id/header_search_v2_input_delete')
if btn.exists: btn.click(); time.sleep(0.2)
keyword = '<填入测试关键词>'
inp.set_text(keyword); time.sleep(0.2)
d.press('enter'); time.sleep(1.0)
if d(text='演出').exists: d(text='演出').click(); time.sleep(0.5)
try:
    chosen = select_search_result(d, keyword=keyword, target_title='<或 None>', strict=False, timeout=5.0)
    print('picked:', chosen)
except (SearchEmptyError, SearchAmbiguousError) as e:
    print(type(e).__name__, e)
"

# === #24 ===
# 24-1：让首页弹窗保持显示（不点关闭），连续 3 次 probe
adb shell am start -n cn.damai/.homepage.MainActivity
sleep 2
poetry run python -c "
from mobile.page_probe import PageProbe, PageState
import uiautomator2 as u2
d = u2.connect()
# 关弹窗以外的窗，但保留首页弹窗
probe = PageProbe(d, cache_ttl_s=0.0, unknown_threshold=3, dump_dir='tmp')
# 强制让 probe 看到 unknown：用一个非大麦 Activity（如设置）
import os
os.system('adb shell am start -a android.settings.SETTINGS')
import time; time.sleep(1.5)
for i in range(3):
    r = probe.classify()
    print(i, r['state'])
print('dumped:', probe.dumped_xml_path)

# 24-3 / 24-4
probe.force_state(PageState.HOMEPAGE)
print('forced:', probe.classify()['state'])
probe.force_state(None)
print('cleared:', probe.classify()['state'])
"
```

---

## 发现问题汇总

> 任一行 ❌ 都要在此区列出 issue 链接 + 一句话症状。

- _

---

## 归档

- 完成填写后将本文件链接同步到 `reference/04-issues-matrix.md` 中 #28 / #23 / #24 条目「real-device validation」字段。
- 三个 issue 的关键分支全部 ✅ 后，在各自 issue 下评论「W3-03 真机回归通过 — log: tests/manual/W3_p2_log.md@<commit>」。
