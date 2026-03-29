# HaTickets - 大麦网抢票自动化

三套方案、一个目标 —— 在大麦网开售的瞬间帮你抢到票。

## 三种抢票方案

| 方案 | 目录 | 技术栈 | 原理 | 速度 |
|------|------|--------|------|------|
| **Web** | `web/` | Python + Selenium | 自动操控 Chrome 浏览器完成购票流程 | ★★☆ |
| **Mobile** | `mobile/` | Python + Appium | 自动操控 Android 大麦 APP，坐标级手势点击 | ★★★ |
| **Desktop** | `desktop/` | Rust + Vue 3 (Tauri) | 绕过浏览器/APP，直接调用大麦 mtop API | ★★★★ |

> **Desktop 最快**：跳过 UI 渲染，直接发 HTTP 请求；**Mobile 最稳**：走官方 APP 流程，不易触发风控。

## 快速开始

### Web 端

```bash
poetry install
cd web
# 编辑 config.json 填入演出 URL、观演人等
python damai.py
```

首次运行会打开 Chrome 让你扫码登录，Cookie 会自动保存。

### Mobile 端

```bash
# 1. 启动 Appium
appium --port 4723

# 2. 编辑配置
vim mobile/config.jsonc

# 3. 在 Android 设备上打开大麦 APP，进入目标演出页面，然后运行：
cd mobile && poetry run python damai_app.py
```

需要：Android 真机或模拟器 + Appium 3.1+ + Node.js 20.19+

### Desktop 端

```bash
cd desktop
yarn install
yarn tauri dev      # 开发模式
yarn tauri build    # 生产构建
```

需要：Node.js 20+ + Rust toolchain

## 项目结构

```
HaTickets/
├── web/                  # Web 端 (Selenium + ChromeDriver)
│   ├── damai.py         #   入口：加载配置 → 启动 Concert
│   ├── concert.py       #   核心：登录、轮询、选票、下单
│   └── config.json      #   配置：演出 URL、票价、观演人
├── mobile/               # 移动端 (Appium + UIAutomator2)
│   ├── damai_app.py     #   DamaiBot：坐标手势 + 批量点击
│   └── config.jsonc     #   配置：关键词、城市、日期、票价
├── desktop/              # 桌面端 (Tauri v1)
│   ├── src/             #   Vue 3 前端 (Arco Design)
│   ├── src-tauri/src/   #   Rust 后端：直调 mtop API
│   │   ├── main.rs      #     5 个 Tauri command
│   │   └── proxy_builder.rs  # HTTP/SOCKS 代理
│   └── src/utils/dm/    #   签名、反爬、下单参数构造
├── tests/                # Python 测试 (pytest, 80% 覆盖率)
├── docs/                 # 技术文档与流程图
└── pyproject.toml        # Python 依赖与测试配置
```

## 配置示例

<details>
<summary><b>Web 端 — web/config.json</b></summary>

```json
{
  "target_url": "https://detail.damai.cn/item.htm?id=xxx",
  "users": ["张三", "李四"],
  "city": "广州",
  "dates": ["2025-10-28"],
  "prices": ["1039"],
  "if_commit_order": true,
  "max_retries": 10000,
  "fast_mode": true
}
```

</details>

<details>
<summary><b>Mobile 端 — mobile/config.jsonc</b></summary>

```json
{
  "server_url": "http://127.0.0.1:4723",
  "device_name": "Android",
  "udid": "emulator-5554",
  "platform_version": "15",
  "app_package": "cn.damai",
  "app_activity": ".launcher.splash.SplashMainActivity",
  "keyword": "刘若英",
  "users": ["张三", "李四"],
  "city": "泉州",
  "date": "10.04",
  "price": "799元",
  "price_index": 1,
  "if_commit_order": true,
  "probe_only": false
}
```

</details>

首次使用建议把 `probe_only` 设为 `true`，先只验证当前页面是不是目标演出详情页、关键控件是否就绪。当前 `mobile` MVP 仍要求用户先手动打开目标演出详情页，不依赖稳定的 App 内搜索或 deeplink 导航。

真机接入时，先运行 `adb devices`，把手机序列号填到 `udid`。如果只连一台设备，`device_name` 用默认 `Android` 即可。

## 关键设计

- **Mobile 坐标点击**：用 `ultra_fast_click()` 替代 `element.click()`，省去元素查找开销
- **Desktop 直调 API**：Rust 发起 HTTP 请求到 `mtop.damai.cn`，3s 超时，伪装移动端 UA + 反爬 Header
- **Desktop 代理支持**：`ProxyBuilder` 支持 HTTP/SOCKS5 代理，所有 API 请求可走代理
- **Web Cookie 持久化**：登录后 Cookie 序列化到本地，下次跳过登录
- **Web 快速模式**：`fast_mode: true` 将轮询间隔压到最低

## 开发

```bash
# Python 测试
poetry install
poetry run pytest                    # 全部测试
poetry run pytest -k "test_name"     # 单个测试
poetry run pytest -m unit            # 按标签

# Desktop 开发
cd desktop && yarn tauri dev
```

## License

仅供学习和研究使用。
