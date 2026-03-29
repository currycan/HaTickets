# 项目概览

大麦网 (damai.cn) 抢票自动化系统，仓库里保留了三套实现，但当前真正主推的是 `Mobile`。

## 当前结论

- `Mobile`：当前主推路线
- `Web`：保留，可作为补充方案
- `Desktop`：历史实现，当前已不再视为可用方案

如果你的目标是“现在就把流程跑通”，优先看 `mobile/`，不要从 `desktop/` 开始。

## 当前主推方案

### Mobile 端 — Appium Android 自动化 (`mobile/`)

- **状态**: 主推
- **技术栈**: Python + Appium + UIAutomator2
- **原理**: 控制 Android 真机/模拟器操作大麦 APP
- **登录**: `noReset=true` 保持 APP 登录态
- **特点**: 坐标级点击优化、支持真机、最接近真实购票链路
- **适合**: 想按 README 直接上手的新用户

## 其他保留方案

### 1. Web 端 — Selenium 浏览器自动化 (`damai/`)

- **状态**: 次选
- **技术栈**: Python + Selenium + ChromeDriver
- **原理**: 控制 Chrome 浏览器模拟人工操作，在大麦网页面上完成选票、下单
- **登录**: Cookie 持久化（pickle 序列化），首次需手动扫码
- **特点**: 支持选座、有快速模式、ChromeDriver 自动安装

### 3. 桌面端 — Tauri API 直调 (`tickets-master/`)

- **状态**: 不可用 / 历史实现
- **技术栈**: Tauri v1 + Rust + Vue 3 + Arco Design
- **原理**: 跳过 UI，直接调用大麦 H5 mtop API 接口
- **登录**: 用户手动从浏览器复制 Cookie
- **历史特点**: 速度快、支持预售倒计时、支持代理、有反爬对抗
- **当前说明**: 因官方渠道限制和风控变化，这条路线已经不再作为实际可执行方案推荐

## 方案对比

| | Web (Selenium) | Mobile (Appium) | 桌面 (Tauri API) |
|---|---|---|---|
| **当前状态** | 可用但次选 | **主推** | 不可用 |
| **技术路线** | 浏览器 UI 自动化 | Android APP UI 自动化 | 直接调用 HTTP API |
| **抢票速度** | 慢（需渲染页面） | 中（坐标点击优化） | **最快**（无 UI 开销） |
| **登录方式** | Cookie / 扫码 | APP 保持登录态 | 手动复制 Cookie |
| **选座支持** | 有（手动） | 无 | 不支持 |
| **反爬处理** | 禁用 automation 标记 | 无特殊处理 | baxia 凭证 + UA 伪造 |
| **预售定时** | 无（手动轮询） | 无 | 有（毫秒级倒计时） |
| **代理支持** | 无 | 无 | 有（socks/http） |
| **风控感知** | 无 | 无 | 有（滑块/订单冲突检测） |
| **重试策略** | 无限刷新 | 固定 3 次 | 可配置次数 + 间隔 |
| **运行平台** | 跨平台（有 Chrome） | 仅 Android | 跨平台桌面 |
| **风控风险** | 中 | 低（真实设备） | 高（直接调 API） |
| **当前推荐度** | 中 | **高** | 低 |

## 共同的抢票流程

三套方案虽然技术实现不同，但核心流程一致：

```
登录/认证 → 获取商品信息 → 选择场次 → 选择票档 → 选择数量/观演人 → 提交订单
```

## 构建与运行

### Web 端

```bash
poetry install
cd damai && python damai.py
```

### Mobile 端（推荐）

```bash
poetry install
./start_appium.sh        # 启动 Appium 服务器
./start_ticket_grabbing.sh  # 执行抢票
```

### 桌面端（仅历史参考）

```bash
cd tickets-master
yarn install
yarn tauri dev    # 开发模式
yarn tauri build  # 构建发布包
```

## 测试

```bash
poetry run test              # 运行测试
poetry run pytest --cov      # 带覆盖率
poetry run pytest -k "name"  # 按名称运行单个测试
poetry run pytest -m unit    # 按标记运行
```
