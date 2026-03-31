# 迁移计划：Mobile 模块 Appium → openatx/uiautomator2

## 背景与目标

**目标**：去掉 Appium Server（Node.js 进程）这一中间层，改用 `openatx/uiautomator2` 直连设备，降低每次操作的通信延迟，简化启动流程。

**收益**：
- 连接建立时间：~3s → ~0.5s
- 每次 find_element 少一层 HTTP relay
- 不再依赖 Node.js / Appium Server 进程
- 无需先运行 `start_appium.sh`

**回滚保障**：通过 `driver_backend` 配置开关，任意阶段可切回 Appium。

---

## 当前代码状态（2026-03-31 审查）

迁移前需了解的已有改动，这些在实施时必须保留：

### `config.py` 已有 `update_runtime_mode()`

```python
def update_runtime_mode(probe_only, if_commit_order, config_path=None):
    """Update runtime mode flags in the target config file and persist them."""
```

此函数已存在，由 `start_ticket_grabbing.sh` 调用用于在运行前自动回写配置。Step 2 新增字段时不得破坏此函数签名。

### `start_ticket_grabbing.sh` 已有 `--probe` 标志

脚本已实现命令语义固定：
- `./start_ticket_grabbing.sh --probe [--yes]`：安全探测（强制 probe_only=true, if_commit_order=false）
- `./start_ticket_grabbing.sh [--yes]`：正式抢票（强制 probe_only=false, if_commit_order=true）

配置与命令不一致时会自动提示并回写配置文件。**Step 6 只需去掉 Appium 检查，不需要重新设计此脚本。**

### `damai_app.py` 已有以下关键接口和行为

**`run_ticket_grabbing(initial_page_probe=None)` 的 `fast_validation_hot_path`：**

当同时满足以下条件时，跳过 `dismiss_startup_popups()` 和 `check_session_valid()`：
```python
fast_validation_hot_path = (
    config.rush_mode
    and not config.if_commit_order
    and initial_page_probe is not None          # benchmark 传入
    and page_probe["state"] in {"detail_page", "sku_page"}
)
```
这是 benchmark 反复跑热路径时最关键的优化。迁移后 `_setup_u2_driver()` 中的 `app_start(stop=False)` 只在初始化时执行一次，不影响此跳过逻辑。

**`_ensure_attendees_selected_on_confirm_page(require_attendee_section=False)` 签名变更：**
- `require_attendee_section=True`：观演人区域不可见时返回 `False`（严格模式，正式提交路径）
- `require_attendee_section=False`（默认）：观演人区域不可见时返回 `True`（宽松模式）
- 调用方式：`require_attendee_section=self.config.rush_mode and not self.config.if_commit_order`

**rush+validation 模式下观演人勾选的快速路径：**
新增 `_click_attendee_checkbox_fast()`，跳过勾选后验证（无 `_attendee_selected_count` 回查），仅做 `checkbox.click()` + `_click_element_center()`。

**`_attendee_selected_count(use_source_fallback=True)` 新参数：**
- 当 `rush_mode=True` 且 `if_commit_order=False` 时，传 `use_source_fallback=False` 跳过 `driver.page_source` XML 扫描，避免高延迟。
- `driver.page_source` 在 u2 中对应 `d.dump_hierarchy()`（返回 XML 字符串，接口相似，见 API 映射表）。

**`burst_count` 行为：**
```python
burst_count = 1 if not self.config.if_commit_order else 2
```
验证模式单击购买按钮，正式提交模式双击。测试已同步更新，`count=2` 断言已改为 `count=1`（validation 路径），迁移时不得恢复为 `count=2`。

**`select_performance_date(timeout=1.0)`**：timeout 已参数化。

**rush_mode 热路径多处优化**（迁移时必须保留）：
- 城市选择 timeout=0.35，失败不阻断流程
- 跳过 `wait_cta_ready_timeout_ms` 热路径准备
- SKU 页避免重探测（`page_probe = dict(page_probe)` 复用）
- `skip_price_selection`：检测 `layout_num` 元素存在时跳过票档点击
- `_wait_for_submit_ready()` rush 模式使用轻量选择器（去掉 XPath，增加 `By.ID, "cn.damai:id/checkbox"`）

### `hot_path_benchmark.py` 已有 `StepTimelineRecorder`

benchmark 脚本已实现步骤级耗时采集，通过 logging handler 捕获 damai_app 的 INFO 日志并计算相邻步骤 delta。结果结构中包含 `step_timeline` 字段。迁移后此功能应继续正常工作（依赖 logger 名称 `"mobile.damai_app"` / `"damai_app"`）。

### 当前测试行为

- commit-disabled 路径：完成观演人勾选后返回 `True`，这是有意为之，迁移时不得回退
- 所有涉及提交流程的测试均已 mock `_ensure_attendees_selected_on_confirm_page`，迁移后新增测试应保持此模式
- `burst_click_coords` 断言已改为 `count=1`（validation 路径），不得恢复为 `count=2`

---

## 架构变化

```
【当前架构】
Python (damai_app.py)
    ↓ HTTP (WebDriver Protocol)
Appium Server (Node.js :4723)    ← 要去掉的层
    ↓ JSON-RPC over HTTP
ATX Agent (Android device)
    ↓
UIAutomator2 Framework

【目标架构】
Python (damai_app.py)
    ↓ HTTP (JSON-RPC 直连)
ATX Agent (Android device)
    ↓
UIAutomator2 Framework
```

---

## 涉及文件

```
mobile/
├── damai_app.py              ← 主要改动（driver 初始化 + API 替换）
├── config.py                 ← server_url 变为可选，新增 serial/driver_backend 字段
├── config.jsonc              ← 更新字段（server_url → serial，platform_version 删除）
├── config.example.jsonc      ← 更新示例
└── scripts/
    ├── start_ticket_grabbing.sh  ← 仅去掉 Appium 检查，--probe 逻辑已有，不动
    └── start_appium.sh           ← 保留但注释标注已不需要

pyproject.toml                ← 依赖变更
tests/conftest.py             ← 新增 mock_u2_driver fixture（保留 mock_appium_driver）
tests/unit/test_mobile_config.py  ← 新增 serial/driver_backend 字段测试
```

---

## API 映射表

| 当前 Appium 调用 | uiautomator2 等价 | 备注 |
|---|---|---|
| `webdriver.Remote(url, options)` | `u2.connect(serial)` | serial=None 自动选第一台设备 |
| `driver.find_element(By.ID, "cn.damai:id/foo")` | `d(resourceId="cn.damai:id/foo")` | |
| `driver.find_elements(By.ID, "...")` | `list(d(resourceId="..."))` | |
| `driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("X")')` | `d(text="X")` | |
| `driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("X")')` | `d(textContains="X")` | |
| `driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*X.*")')` | `d(textMatches=".*X.*")` | |
| `driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, '...className("X").clickable(true).instance(N)')` | `d(className="X", clickable=True, instance=N)` | |
| `driver.find_element(By.XPATH, '//*[@text="X"]')` | `d.xpath('//*[@text="X"]').get_element()` | |
| `driver.find_elements(By.CLASS_NAME, "android.widget.FrameLayout")` | `list(d(className="android.widget.FrameLayout"))` | |
| `container.find_elements(By.XPATH, ".//*")` | 改为 `d.xpath('...')` 绝对路径 | u2 不支持在 element 上再 find_elements |
| `element.rect` → `{'x','y','width','height'}` | `el.info['bounds']` → `{'left','top','right','bottom'}` + 转换函数 | 见下方 `_element_rect()` |
| `element.text` | `el.get_text()` 或 `el.info['text']` | |
| `element.get_attribute("clickable")` | `el.info['clickable']` | 返回 bool，不是字符串，删除 `.lower() == "true"` 转换 |
| `driver.execute_script("mobile: clickGesture", {"x":x,"y":y,"duration":50})` | `d.click(x, y)` | duration≤50 直接用 click，更长用 long_click |
| `driver.current_activity` | `d.app_current()['activity']` | |
| `driver.update_settings({...})` | `d.settings[key] = value` | key 名称有差异，见下方 |
| `WebDriverWait(driver, t).until(EC.presence_of_element_located(...))` | `d(...).wait(timeout=t)` 返回 bool | |
| `driver.get_screenshot_as_png()` | `d.screenshot(format='raw')` | 返回 bytes |
| `driver.page_source` | `d.dump_hierarchy()` 或 `d.page_source` | 两者均返回 XML 字符串；`page_source` 是 `dump_hierarchy()` 的别名，行为相同 |

### settings 映射

| Appium `update_settings` key | u2 等价 |
|---|---|
| `waitForIdleTimeout: 0` | `d.settings['wait_timeout'] = 0` |
| `waitForSelectorTimeout: 300` | 默认 wait_timeout 已足够，无需显式设置 |
| `actionAcknowledgmentTimeout: 0` | 无直接等价，u2 默认已足够激进 |
| `keyInjectionDelay: 0` | `d.settings['key_injection_delay'] = 0` |
| `disableWindowAnimation: True` | 通过 adb 命令设置，非 u2 settings |

---

## 实施步骤

### Step 1：依赖变更（`pyproject.toml`）

```toml
[tool.poetry.dependencies]
python = "^3.8"
selenium = ">=4.22.0,<4.28"   # 保留（Web 模块仍用 Selenium）
# 删除：appium-python-client = "^4.0.0"
uiautomator2 = "^3.2"         # 新增
adbutils = "^2.9"             # 新增（u2 依赖，显式声明）
```

**验证**：`poetry install && poetry run pytest` 仍通过（此时代码尚未改动，测试应全部通过）。

---

### Step 2：Config 字段变更（`config.py` + `config.jsonc`）

**`config.py` 的 `Config.__init__` 新增参数：**

```python
def __init__(self, ...,
             serial=None,              # 新增：设备序列号（替代 server_url 的角色）
             driver_backend="u2",      # 新增："u2" | "appium"
             server_url=None):         # 变为可选（driver_backend="appium" 时需要）
```

**`server_url` 校验逻辑改为条件校验：**
```python
if driver_backend == "appium":
    validate_url(server_url, "server_url")
```

**注意**：`update_runtime_mode()` 函数签名和行为不变，只是增量添加新字段的读取。

**`Config.load_config()` 新增字段读取：**
```python
return Config(...,
              config.get('serial'),
              config.get('driver_backend', 'u2'),
              config.get('server_url'))
```

**`config.jsonc` 新增/变更字段：**
```jsonc
{
  // 新增（替代 server_url）：
  "serial": null,            // null = 自动检测；或填 "emulator-5554" / "c6c4eb67"

  // 新增（回滚开关）：
  "driver_backend": "u2",   // "u2"（默认）| "appium"（回滚用）

  // 变为可选（driver_backend="appium" 时才需要）：
  // "server_url": "http://127.0.0.1:4723",

  // 删除（u2 不需要）：
  // "platform_version": "16",

  // 其余字段完全不变
}
```

**验证**：`poetry run pytest tests/unit/test_mobile_config.py`

---

### Step 3：新增 `_setup_u2_driver()`，保留 Appium 分支

在 `damai_app.py` 的 `_setup_driver()` 中添加分支：

```python
def _setup_driver(self):
    if getattr(self.config, 'driver_backend', 'u2') == 'appium':
        self._setup_appium_driver()   # 原有逻辑，完整保留，改名
    else:
        self._setup_u2_driver()

def _setup_appium_driver(self):
    """原 _setup_driver() 逻辑，完整保留。"""
    self._preflight_validate_device_target()
    device_app_info = AppiumOptions()
    device_app_info.load_capabilities(self._build_capabilities())
    self.driver = webdriver.Remote(self.config.server_url, options=device_app_info)
    self.driver.update_settings({
        "waitForIdleTimeout": 0,
        "actionAcknowledgmentTimeout": 0,
        "keyInjectionDelay": 0,
        "waitForSelectorTimeout": 300,
        "ignoreUnimportantViews": False,
        "allowInvisibleElements": True,
        "enableNotificationListener": False,
    })
    self.wait = WebDriverWait(self.driver, 2)

def _setup_u2_driver(self):
    """uiautomator2 直连驱动初始化。"""
    import uiautomator2 as u2
    serial = getattr(self.config, 'serial', None) or self.config.udid or None
    self.d = u2.connect(serial)
    self.d.settings['wait_timeout'] = 2
    self.d.settings['operation_delay'] = (0, 0)
    self.d.app_start(
        self.config.app_package,
        activity=self.config.app_activity,
        stop=False,
    )
    # 兼容：令 self.driver 指向同一对象，避免其他 None 检查报错
    self.driver = self.d
```

**验证**：`driver_backend="appium"` 路径行为不变；`driver_backend="u2"` 可连通设备并启动 APP。

---

### Step 4：添加坐标/rect 适配方法

在 `DamaiBot` 中新增，替换所有 `element.rect` 直接访问：

```python
def _element_rect(self, el):
    """统一返回 {'x', 'y', 'width', 'height'}，兼容 Appium 和 u2。"""
    if hasattr(el, 'rect'):
        return el.rect   # Appium element
    b = el.info['bounds']
    return {
        'x': b['left'],
        'y': b['top'],
        'width': b['right'] - b['left'],
        'height': b['bottom'] - b['top'],
    }
```

将 `damai_app.py` 中所有 `element.rect` / `el.rect` 改为 `self._element_rect(el)`。

改写 `_click_coordinates()`（热路径，需保留 burst_click 和 rush_mode 行为）：

```python
def _click_coordinates(self, x, y, duration=50):
    if getattr(self.config, 'driver_backend', 'u2') == 'appium':
        self.driver.execute_script(
            "mobile: clickGesture",
            {"x": x, "y": y, "duration": duration},
        )
    else:
        if duration <= 50:
            self.d.click(x, y)
        else:
            self.d.long_click(x, y, duration / 1000)
```

**验证**：坐标点击单元测试通过；rush_mode burst_click 行为不变。

---

### Step 5：替换 find_element / find_elements 调用（量最大的一步）

#### 5a：新增统一查找辅助方法

```python
def _find(self, by, value):
    """统一查找入口，返回 u2 selector 或直接返回 Appium element。"""
    if getattr(self.config, 'driver_backend', 'u2') != 'u2':
        return self.driver.find_element(by, value)
    return self._appium_selector_to_u2(by, value)

def _find_all(self, by, value):
    """返回元素列表（u2 或 Appium）。"""
    if getattr(self.config, 'driver_backend', 'u2') != 'u2':
        return self.driver.find_elements(by=by, value=value)
    return list(self._appium_selector_to_u2(by, value))

def _appium_selector_to_u2(self, by, value):
    """将 (by, value) 对转换为 u2 selector。"""
    from selenium.webdriver.common.by import By
    try:
        from appium.webdriver.common.appiumby import AppiumBy
        UIAUTOMATOR = AppiumBy.ANDROID_UIAUTOMATOR
    except ImportError:
        UIAUTOMATOR = "android uiautomator"

    if by == By.ID:
        return self.d(resourceId=value)
    if by == By.CLASS_NAME:
        return self.d(className=value)
    if by == By.XPATH:
        return self.d.xpath(value)
    if by == UIAUTOMATOR:
        return self._parse_uiselector(value)
    raise ValueError(f"不支持的 by 类型: {by}")

def _parse_uiselector(self, uiselector_str):
    """将常见的 UiSelector 字符串解析为 u2 selector kwargs。"""
    import re
    kwargs = {}
    m = re.search(r'\.text\("([^"]+)"\)', uiselector_str)
    if m:
        kwargs['text'] = m.group(1)
    m = re.search(r'\.textContains\("([^"]+)"\)', uiselector_str)
    if m:
        kwargs['textContains'] = m.group(1)
    m = re.search(r'\.textMatches\("([^"]+)"\)', uiselector_str)
    if m:
        kwargs['textMatches'] = m.group(1)
    m = re.search(r'\.className\("([^"]+)"\)', uiselector_str)
    if m:
        kwargs['className'] = m.group(1)
    m = re.search(r'\.clickable\((true|false)\)', uiselector_str)
    if m:
        kwargs['clickable'] = (m.group(1) == 'true')
    m = re.search(r'\.instance\((\d+)\)', uiselector_str)
    if m:
        kwargs['instance'] = int(m.group(1))
    if not kwargs:
        raise ValueError(f"无法解析 UiSelector: {uiselector_str}")
    return self.d(**kwargs)
```

#### 5b：全量替换调用点

在 `damai_app.py` 中替换：
- `self.driver.find_element(by, value)` → `self._find(by, value).get_element()`（或直接用 `.wait()` / `.exists`）
- `self.driver.find_elements(by=by, value=value)` → `self._find_all(by, value)`
- `container.find_elements(by=by, value=value)` → 改用 `d.xpath(...)` 绝对路径（u2 不支持在 element 上再 find_elements）

**`_has_element()` 改写：**
```python
def _has_element(self, by, value):
    try:
        if getattr(self.config, 'driver_backend', 'u2') != 'u2':
            return len(self.driver.find_elements(by=by, value=value)) > 0
        return self._find(by, value).exists(timeout=0)
    except Exception:
        return False
```

**`get_attribute("clickable")` 调用改写：**
```python
# 原来
str(card.get_attribute("clickable")).lower() == "true"
# 改为（u2 的 info['clickable'] 直接是 bool）
el.info.get('clickable', False)
```

**验证**：设备上完整跑一遍 `--probe` 模式；`StepTimelineRecorder` 日志正常输出。

---

### Step 6：更新 `start_ticket_grabbing.sh`

仅删除 Appium 检查段，其余逻辑（`--probe`、配置回写、`MODE_PROMPT_CONFIRMED`）完整保留：

```bash
# 删除这一块（约 5 行）：
if ! curl -s http://127.0.0.1:4723/status > /dev/null; then
    echo "❌ Appium服务器未运行"
    echo "   请先运行: ./start_appium.sh"
    exit 1
fi
echo "✅ Appium服务器运行正常"

# 新增 adb 设备检查（放在相同位置）：
if ! adb devices 2>/dev/null | grep -q "device$"; then
    echo "❌ 未检测到已连接的 Android 设备"
    echo "   请通过 USB 连接设备并开启 USB 调试模式"
    echo "   连接后执行: adb devices"
    exit 1
fi
echo "✅ 设备连接正常"
```

**验证**：`./start_ticket_grabbing.sh --probe --yes` 不再提示"Appium未运行"。

---

### Step 7：切换默认值 + 更新测试

1. 确认 `config.py` 中 `driver_backend` 默认值为 `"u2"`
2. `config.example.jsonc` 注释掉 `server_url`、删除 `platform_version`、新增 `serial: null` 示例
3. **新增** `tests/conftest.py` fixture：

```python
@pytest.fixture
def mock_u2_driver(mocker):
    mock_d = MagicMock()
    mock_d.app_current.return_value = {
        'activity': '.launcher.splash.SplashMainActivity',
        'package': 'cn.damai',
    }
    mock_d.settings = {}
    mock_d.xpath.return_value = MagicMock()
    mocker.patch("uiautomator2.connect", return_value=mock_d)
    return mock_d

# 保留原 mock_appium_driver fixture（driver_backend="appium" 测试路径仍需要）
```

4. `tests/unit/test_mobile_config.py` 新增：
   - `serial` 字段：null / 字符串校验
   - `driver_backend` 字段：`"u2"` / `"appium"` 合法；其他值抛 ValueError
   - `server_url` 在 `driver_backend="u2"` 时可为 None，在 `driver_backend="appium"` 时必须合法 URL
   - 已有 `test_update_runtime_mode_*` 测试无需改动

5. `tests/unit/test_mobile_damai_app.py` 注意事项：
   - commit-disabled 测试当前预期返回 `True`（已更新），不得回退
   - `_setup_driver` mock 需同时覆盖 Appium 和 u2 两条路径

**验证**：`poetry run pytest --cov-fail-under=80` 通过。

---

### Step 8：清理（观察稳定 1 周后）

1. 删除 `_setup_appium_driver()` 及所有 `driver_backend == "appium"` 分支
2. 删除 `_appium_selector_to_u2()` 中的 Appium import 和兼容逻辑
3. `pyproject.toml` 彻底删除 `appium-python-client`
4. `config.py` 移除 `server_url` / `platform_version` 字段及其校验
5. `config.jsonc` / `config.example.jsonc` 删除注释掉的 `server_url` 行
6. 归档 `start_appium.sh`（重命名为 `start_appium.sh.deprecated` 或直接删除）
7. `conftest.py` 删除 `mock_appium_driver` fixture

---

## 验证检查点（每步完成后执行）

```bash
# 单元测试（无需设备）
poetry run pytest tests/unit/ -v

# 全量测试 + 覆盖率
poetry run pytest --cov-fail-under=80

# 设备冒烟测试（需真机）
./mobile/scripts/start_ticket_grabbing.sh --probe --yes

# 热路径 benchmark（需真机停在演出详情页）
./mobile/scripts/benchmark_hot_path.sh --runs 3
```

---

## 风险点与缓解

| 风险 | 影响范围 | 缓解措施 |
|---|---|---|
| `container.find_elements()` 在 u2 中不直接支持 | Step 5 最复杂，`_collect_descendant_texts` / `_safe_element_texts` 等方法 | 改用 `d.xpath(absolute_xpath)` 绝对路径；或通过 `container.info['bounds']` + `d.xpath` 结合 |
| UiSelector 复杂表达式无法完整解析 | `_parse_uiselector()` 漏掉某些组合 | 遇到 ValueError 时逐一补充解析规则；提供明确的异常信息 |
| `get_attribute("clickable")` 返回类型变化 | 价格卡片选择逻辑（`_click_visible_price_option`） | 改为 `el.info.get('clickable', False)`，直接用 bool，删除 `.lower() == "true"` 转换 |
| ATX Agent 在设备上未安装 | 首次 `u2.connect()` 失败 | u2 会自动推送安装 atx-agent，需要设备联网；离线设备用 `u2.connect(addr)` 手动指定 |
| rush_mode 热路径行为改变 | 抢票成功率敏感 | Step 4/5 改动后必须跑 benchmark，对比迁移前后耗时；rush_mode 逻辑不得引入新的等待 |
| `StepTimelineRecorder` 依赖 logger 名称 | benchmark 步骤计时失效 | u2 版本的 logger 名称需与 `_attach_timeline_recorder()` 中的 `"mobile.damai_app"` / `"damai_app"` 保持一致 |
| `fast_validation_hot_path` 与 `_setup_u2_driver()` 的交互 | benchmark 反复调用 `run_ticket_grabbing` 时的初始化副作用 | `_setup_u2_driver()` 只在构造时执行一次，`app_start(stop=False)` 不会重启 APP；热路径跳过 session 检查的逻辑不变，无需额外处理 |
| `driver.page_source` 在观演人计数逻辑中 | `_attendee_selected_count()` 的 XML fallback 路径 | u2 中改为 `d.dump_hierarchy()`（或 `d.page_source` 别名），返回格式相同（XML 字符串），直接替换 |
| `_click_attendee_checkbox_fast()` 的 `checkbox.click()` 兼容性 | rush+validation 模式下快速勾选观演人 | u2 element 也支持 `.click()`，行为相同；`_click_element_center()` 已通过 `_click_coordinates()` 适配，无需额外处理 |
| `burst_count = 1` 在 validation 模式 | 测试断言 `count=1`，不得恢复为 `count=2` | 迁移时保留 `burst_count = 1 if not self.config.if_commit_order else 2` 逻辑，不改动 |
| `_wait_for_submit_ready()` rush 模式选择器变化 | 确认页就绪检测 | rush 模式已去掉 XPath，改用 `By.ID, "cn.damai:id/checkbox"`，经过 `_has_element` 适配层，u2 迁移后自动生效 |
