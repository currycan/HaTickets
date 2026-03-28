# Mobile 端抢票逻辑 (Appium)

> 源码目录: `mobile/`

## 技术栈

- Python 3.8+
- Appium + UIAutomator2
- 仅支持 Android（硬编码 `platformName: "Android"`）

## 模块结构

| 文件 | 职责 |
|------|------|
| `damai_app.py` | 当前版本：`DamaiBot` 类，移动端抢票主流程 |
| `config.py` | 配置容器 + `load_config()` 静态方法 |
| `config.jsonc` | 用户本地配置文件 |
| `config.example.jsonc` | 配置样例 |

## 配置项 (`config.jsonc`)

- `server_url`: Appium 服务器地址
- `keyword`: 搜索关键词
- `users`: 观演人姓名列表
- `city`: 目标城市
- `date`: 目标日期
- `price`: 目标票价
- `price_index`: 票价在列表中的索引位置
- `if_commit_order`: 是否自动提交订单
- `probe_only`: 仅做详情页探测，不执行购票点击

## 主流程

```
DamaiBot.__init__()
  ├── Config.load_config()    # 读取 config.jsonc
  └── _setup_driver()         # 初始化 Appium 连接

run_with_retry(max_retries=3)
  └── run_ticket_grabbing()   # 单次抢票流程
        ├── dismiss_startup_popups()   # 处理首启弹窗
        ├── probe_current_page()       # 探测当前页面状态
        │   ├── 非 detail_page => 直接失败
        │   └── probe_only => 验证控件后提前结束
        ├── 1. 选择城市
        ├── 2. 点击预约按钮
        ├── 3. 选择票价
        ├── 4. 选择数量
        ├── 5. 确定购买
        ├── 6. 选择用户
        └── 7. 提交订单
```

## 详细流程

### 1. 驱动初始化 (`_setup_driver`)

**Appium Capabilities**:
```python
platformName: "Android"
deviceName: "emulator-5554"
automationName: "UiAutomator2"
noReset: True           # 保持 APP 登录态
disableWindowAnimation: True  # 禁用动画提速
```

说明：
- 当前实现不再硬编码 `platformVersion`
- 这样可以避免 Appium 因设备实际 Android 版本和代码常量不一致而拒绝创建会话

**激进性能优化**:
```python
waitForIdleTimeout: 0       # 不等待页面空闲
actionAcknowledgmentTimeout: 0  # 禁止等待动作确认
keyInjectionDelay: 0        # 禁止输入延迟
waitForSelectorTimeout: 300  # 元素查找超时 300ms
enableNotificationListener: False  # 禁用通知监听
```

**显式等待**: `WebDriverWait` 超时仅 2 秒（常规为 5-10 秒）

### 2. 点击优化

这是 Mobile 端区别于 Web 端的核心设计。

**`ultra_fast_click()`** — 单元素极速点击:
```python
# 不等待 clickable 状态，只等 presence
el = WebDriverWait(driver, 1.5).until(
    EC.presence_of_element_located(locator)
)
# 获取坐标，用 gesture 替代 element.click()
rect = el.rect
driver.execute_script("mobile: clickGesture", {
    "x": center_x, "y": center_y,
    "duration": 50  # 极短点击时间
})
```

**为什么用坐标点击？**
- `element.click()` 需要等待元素 clickable、可见性检查等，开销大
- `clickGesture` 直接在屏幕坐标执行手势，绕过所有检查

**`ultra_batch_click()`** — 批量用户选择优化:
1. 先**批量收集**所有目标元素的坐标（一次遍历）
2. 再**快速连续点击**所有坐标（元素间 delay 仅 0.01s）
3. 避免了"找一个点一个"的串行等待

**`smart_wait_and_click()`** — 智能备选点击:
- 接受主选择器 + 备用选择器列表
- 依次尝试，第一个成功即返回

### 3. 启动探测和安全探针

**`dismiss_startup_popups()`**
- 处理 Android 全屏提示
- 处理大麦隐私协议弹窗
- 处理系统级 `Add to home screen` 取消按钮

**`probe_current_page()`**
- 探测 `consent_dialog`、`homepage`、`search_page`、`detail_page`、`order_confirm_page`
- 同时返回关键控件是否可见：
  - `purchase_button`
  - `price_container`
  - `quantity_picker`
  - `submit_button`
- 同时输出当前 Activity，方便定位卡在首页、搜索页还是订单页

**`probe_only` 模式**
- 只验证详情页关键控件是否就绪
- 就绪后停止在真正购票点击前
- 适合首次接设备、校验页面、验证选择器

### 4. 抢票各步骤

**城市选择**: 三种选择器备选
1. `UiSelector().text("城市名")` — 精确匹配
2. `UiSelector().textContains("城市名")` — 模糊匹配
3. XPath `//*[@text="城市名"]` — 兜底

**预约按钮**: 三种选择器备选
1. 按资源 ID 定位 (`cn.damai:id/...`)
2. 正则匹配文本 (`.*预约.*|.*购买.*|.*立即.*`)
3. XPath 文本包含

**票价选择**: 这是一个特殊处理
- 大麦 APP 的票价元素 **text 是空的**（被隐藏了）
- 只能通过 `FrameLayout` 的 **index** 和 `clickable=true` 来定位
- 配置中的 `price_index` 就是为此设计的
- 先尝试 `find_element` 直接查找（不等待），失败后走 `WebDriverWait`

**数量选择**:
- 查找 `+` 按钮（`img_jia`）
- 获取坐标后用 `clickGesture` 点击 (用户数 - 1) 次
- 每次点击间隔仅 0.02s

**用户选择**: 使用 `ultra_batch_click()`
- 为每个用户名构造 `UiSelector().text("用户名")` 选择器
- 批量收集坐标后快速连续点击

**提交订单**: 三种选择器备选
1. `UiSelector().text("立即提交")`
2. 正则匹配 `.*提交.*|.*确认.*`
3. XPath 文本包含

### 5. 重试机制

`run_with_retry(max_retries=3)`:
- 最多尝试 3 次
- 失败后等待 2 秒
- 重新初始化 driver（`driver.quit()` + `_setup_driver()`）
- 全部失败则退出

## 前置条件

运行前需要：
1. 用户已手动打开大麦 APP 并登录
2. 用户已导航到目标演出的详情页
3. Appium 服务器已启动（`./mobile/scripts/start_appium.sh`）

脚本从"详情页已打开"的状态开始执行，不包含搜索/导航步骤。

## MVP 验证结论

`2026-03-29` 的真实模拟器测试结论：
- Appium + Android 模拟器 + 大麦 App 可以正常启动和探测页面
- 大麦首启弹窗可以通过启动探测层稳定处理
- 目标商品的 deeplink 会短暂进入 `ProjectDetailActivity`，随后回到首页，不适合作为默认导航方案
- 因此当前推荐流程仍然是：用户手动打开目标演出详情页，再由脚本接管后续步骤

## 平台限制

- **仅 Android**: capabilities 硬编码 Android + UiAutomator2
- **不支持 iOS**: 需要 XCUITest 引擎 + 完全不同的元素定位方式
- **不支持选座**: 流程中没有选座步骤
- 设备名硬编码为 `emulator-5554`（Android 模拟器默认端口）

## 性能设计理念

整体设计以**速度优先**为核心：
- 所有等待时间压缩到最短（0.01s~2s）
- 禁用一切不必要的系统功能（动画、通知、空闲检测）
- 用坐标点击替代元素交互
- 批量操作先收集再执行，减少串行等待
