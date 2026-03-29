# HaTickets - 大麦抢票自动化

这个仓库不是票务展示站，而是一个“大麦抢票自动化工具箱”。

## 当前状态

- `Mobile`：当前主推方案，也是 README 主要说明对象
- `Web`：保留，适合会调 Selenium 的用户
- `Desktop`：**已被官方渠道限制，当前视为不可用，不再推荐，也不要作为主流程投入时间**

## 方案状态

| 方案 | 目录 | 当前状态 | 说明 |
|------|------|--------|------|
| `Mobile` | `mobile/` | 主推 | 走 Android 大麦 App，最接近真实购票流程 |
| `Web` | `web/` | 可用但次选 | Selenium 控制 Chrome |
| `Desktop` | `desktop/` | 不可用 | 官方渠道和风控已限制，当前不要再作为可执行方案使用 |

> 如果你是第一次用，直接走 `Mobile + 安卓真机`。  
> 如果你只想先验证流程，不想误提交订单，先把 `if_commit_order` 设成 `false`。  
> 如果你看到旧文档里提到 `Desktop`，把它理解成“历史实现”，不要再按它准备环境。

## 一图看懂

![移动端抢票流程](docs/images/tickets-process.png)

上图对应的是当前项目的核心思路：  
先到演出详情页或票档页，再进入确认页；如果配置了 `if_commit_order: false`，脚本会停在“立即提交”之前，不会帮你支付。

## 推荐阅读顺序

1. 先看下面的 `Mobile 真机教程`
2. 再看 [docs/quick-start.md](docs/quick-start.md)
3. 如果你要理解脚本细节，再看 [docs/mobile-ticket-logic.md](docs/mobile-ticket-logic.md)

## 小白推荐路线

推荐你按这 3 个阶段走，不要一步到位：

1. `探测模式`：确认手机、Appium、页面状态都正常
2. `不支付验证`：自动走到“确认购买”页，但不点“立即提交”
3. `正式提交`：确认前两步没问题后，再把自动提交打开

对应配置：

| 目标 | `probe_only` | `if_commit_order` |
|------|--------------|-------------------|
| 只看环境通不通 | `true` | `false` |
| 到确认页但不提交 | `false` | `false` |
| 自动提交订单 | `false` | `true` |

## Mobile 真机教程

这一节是最适合新手的用法。

### 第 1 步：准备环境

你至少需要这些东西：

- 一台 Android 真机
- 手机上已经安装大麦 App，并且已经登录
- Mac 上有 `Python`、`Poetry`、`Node.js`、`Appium`
- 手机已经打开 `开发者选项` 和 `USB 调试`

安装 Python 依赖：

```bash
poetry install
```

安装 Appium：

```bash
npm install -g appium
appium driver install uiautomator2
```

如果你还没有 Android SDK，建议直接安装 Android Studio。

### 第 2 步：连接手机

1. 用数据线把安卓手机连到电脑
2. 手机打开 `开发者选项`
3. 打开 `USB 调试`
4. 手机上如果弹“是否允许这台电脑调试”，点 `允许`

然后执行：

```bash
adb devices
```

你应该能看到类似输出：

```bash
List of devices attached
ABC1234567	device
```

这里的 `ABC1234567` 就是你的 `udid`。

### 第 3 步：启动 Appium

最省事的方式：

```bash
./mobile/scripts/start_appium.sh
```

如果你想自己启动，也可以：

```bash
appium --port 4723
```

### 第 4 步：修改配置

编辑 [mobile/config.jsonc](/Users/andrew/Documents/GitHub/HaTickets/mobile/config.jsonc)。

推荐你第一次先填成这种“安全模式”：

```jsonc
{
  "server_url": "http://127.0.0.1:4723",
  "device_name": "Android",
  "udid": "你的 adb devices 序列号",
  "platform_version": "14",
  "app_package": "cn.damai",
  "app_activity": ".launcher.splash.SplashMainActivity",
  "keyword": "周深",
  "users": ["你的真实观演人姓名"],
  "city": "深圳",
  "date": "12.06",
  "price": "内场1199元",
  "price_index": 5,
  "if_commit_order": false,
  "probe_only": true
}
```

每个字段是什么意思：

- `udid`：你的手机序列号，来自 `adb devices`
- `users`：必须写你大麦账号里真实存在的观演人姓名
- `city`：演出城市
- `date`：场次日期文本，按页面上看到的写
- `price`：票档文本，尽量按页面原文填
- `price_index`：如果文本匹配失败，就按索引兜底
- `if_commit_order`：是否自动点“立即提交”
- `probe_only`：是否只做页面探测

下面这张图能帮你理解 `city / date / price` 这些值通常从哪里看：

![参数示意图](docs/images/example_detail.png)

### 第 5 步：先做探测，不提交

先在手机上手动做两件事：

1. 打开大麦 App
2. 进入目标演出详情页，或者已经点进票档页

然后执行：

```bash
./mobile/scripts/start_ticket_grabbing.sh
```

如果这一步成功，说明：

- 手机已连接
- Appium 已启动
- 大麦 App 能被正常控制
- 当前页面可以被脚本识别

### 第 6 步：做“不支付验证”

把 [mobile/config.jsonc](/Users/andrew/Documents/GitHub/HaTickets/mobile/config.jsonc) 改成：

```jsonc
"probe_only": false,
"if_commit_order": false
```

然后再次运行：

```bash
./mobile/scripts/start_ticket_grabbing.sh
```

这一步的预期结果是：

- 脚本自动选票
- 自动进入“确认购买”页
- 停在“立即提交”之前
- 不会支付

### 第 7 步：确认没问题后再正式提交

只有当你已经完成上一步，并确认流程没问题时，才把：

```jsonc
"if_commit_order": true
```

打开自动提交。

## 常见问题

### 1. `adb: command not found`

说明 Android SDK 的 `platform-tools` 没进环境变量。  
最直接的办法是：

```bash
export ANDROID_HOME="$HOME/Library/Android/sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$ANDROID_HOME/platform-tools:$PATH"
```

### 2. `adb devices` 看不到手机

先检查：

1. 手机有没有打开 `USB 调试`
2. 数据线是不是只能充电不能传数据
3. 手机上有没有点“允许调试”

### 3. 打开大麦后提示“访问被拒绝”

这通常是风控，不一定是代码问题。  
模拟器比真机更容易触发，所以推荐用真机。

### 4. 脚本找不到观演人

最常见原因是：

- [mobile/config.jsonc](/Users/andrew/Documents/GitHub/HaTickets/mobile/config.jsonc) 里的 `users` 写的是占位符，不是真实名字
- 你的大麦账号里还没有配置对应观演人

### 5. 脚本没有进入确认页

通常先查这几项：

1. `price` 文本是不是填错了
2. `price_index` 是不是和实际票档不一致
3. 当前票档是不是“缺货登记”而不是可买状态
4. 当前项目是不是只支持 App，不支持 H5 / Web

## 其他方案

### Web 端

适合已经熟悉 Selenium 的人。它还在仓库里，但不再是默认推荐路线。

```bash
cd web
python damai.py
```

首次运行会打开 Chrome 登录，配置文件是 [web/config.json](/Users/andrew/Documents/GitHub/HaTickets/web/config.json)。

### Desktop 端

`Desktop` 方案保留代码和历史文档，但当前已经不作为可用方案推荐。

原因很简单：

- 这条路线依赖大麦 H5 / mtop 接口
- 当前官方渠道限制和风控已经让这条方案失去稳定可用性
- 继续折腾 `desktop` 的投入产出很差

如果你只是想真正跑通抢票流程，请回到上面的 `Mobile 真机教程`。

```bash
cd desktop
yarn install
yarn tauri dev
```

## 项目结构

```text
HaTickets/
├── mobile/                  # Android App 自动化
├── web/                     # Selenium 浏览器自动化
├── desktop/                 # Tauri + Rust 桌面端
├── docs/                    # 文档、流程图、说明图
├── tests/                   # pytest 测试
└── pyproject.toml           # Python 依赖
```

## 开发与测试

```bash
poetry install
poetry run pytest
```

## 免责声明

仅供学习和研究使用。请自行承担使用风险，并遵守平台规则。
