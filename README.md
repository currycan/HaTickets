# HaTickets - 大麦抢票自动化

这个仓库不是票务展示站，而是一个“大麦抢票自动化工具箱”。

## 法律说明

- 开源协议：见 [LICENSE](./LICENSE)
- 版权与商标声明：见 [NOTICE](./NOTICE)
- 免责申明：见 [DISCLAIMER.md](./DISCLAIMER.md)

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

**当前主流程在设计使用`Mobile + 安卓真机`方案**

先定位目标演出，再进入票档页和确认页；如果配置了 `item_url + auto_navigate`，脚本可以从大麦首页自动搜到目标演出。如果配置了 `if_commit_order: false`，脚本会停在“立即提交”之前，不会帮你支付。

注意：`./mobile/scripts/start_ticket_grabbing.sh --yes` 只是“按当前配置执行”。它不会默认直接开始抢票，真正行为由 `probe_only` 和 `if_commit_order` 决定。

如果你希望把“自然语言提示词”也接进来，例如：

```bash
./mobile/scripts/run_from_prompt.sh "帮我抢一张 4 月 6 号张杰的演唱会门票，内场"
```

现在脚本会先自动解析提示词，去大麦 App 搜索目标演出，并输出搜索候选和当前页面可见摘要供你确认。确认后会优先写入本地配置 [mobile/config.local.jsonc](./mobile/config.local.jsonc)，如果该文件不存在才回退到 [mobile/config.jsonc](./mobile/config.jsonc)，也可以继续执行 `probe` / `confirm` 模式。

## 推荐阅读顺序

1. 先看下面的 `五分钟跑通 Mobile`
2. 再看 [docs/quick-start.md](docs/quick-start.md)
3. 需要深入理解脚本时，再看 [docs/mobile-ticket-logic.md](docs/mobile-ticket-logic.md)

## 五分钟跑通 Mobile

按当前代码，最稳定的路线就是这一条：

1. 连接安卓真机，并保持大麦 App 已登录
2. 启动 Appium
3. 复制一份本地配置 `mobile/config.local.jsonc`
4. 先跑一次 `probe_only=true` 的安全探测
5. 探测通过后，再跑“到确认页但不提交”

这 3 个阶段一定要区分清楚：

1. `probe_only=true`
   只是探测。会自动打开目标演出页，但会停在“立即购票/立即预订”之前，不会真正点击。
2. `probe_only=false` 且 `if_commit_order=false`
   会继续进入票档页和确认页，但停在“立即提交”之前，不会支付。
3. `probe_only=false` 且 `if_commit_order=true`
   才是正式提交模式，会尝试提交订单。

### 1. 安装依赖

```bash
poetry install
npm install -g appium
appium driver install uiautomator2
```

如果你还没有 Android SDK，建议直接安装 Android Studio。

### 2. 连接手机

手机前置条件：

- 已打开 `开发者选项`
- 已打开 `USB 调试`
- 已安装并登录大麦 App

连接后执行：

```bash
adb devices
```

输出里类似 `ABC1234567	device` 的这一串，就是你的 `udid`。

### 3. 启动 Appium

```bash
./mobile/scripts/start_appium.sh
```

这一步会启动一个本地 Appium 服务，需要持续保持运行。

建议做法：

1. 用第一个终端窗口执行 `./mobile/scripts/start_appium.sh`
2. 不要关闭这个终端，也不要按 `Ctrl+C`
3. 再打开第二个终端窗口，继续执行后面的抢票命令

这一步会顺带检查：

- Android SDK
- 已连接设备
- 大麦 App 是否安装
- Appium 服务是否成功启动

### 4. 准备本地配置

优先使用 [mobile/config.local.jsonc](./mobile/config.local.jsonc)。如果没有，就先复制模板：

```bash
cp mobile/config.example.jsonc mobile/config.local.jsonc
```

然后把下面这几个字段改成你自己的真实值：

```jsonc
{
  "server_url": "http://127.0.0.1:4723",
  "device_name": "Android",
  "udid": "你的 adb devices 序列号",
  "platform_version": "你的安卓版本",
  "app_package": "cn.damai",
  "app_activity": ".launcher.splash.SplashMainActivity",
  "item_url": "https://m.damai.cn/shows/item.html?itemId=你的 itemId",
  "keyword": null,
  "users": ["你已经在大麦 App 中添加成功的观演人姓名"],
  "city": "你的演出城市",
  "date": "你的场次日期",
  "price": "你的票档原文",
  "price_index": 0,
  "if_commit_order": false,
  "probe_only": true,
  "auto_navigate": true
}
```

字段说明只记最关键的：

- `item_url`：推荐填大麦详情页链接，脚本会自动提取 `itemId`
- `keyword`：如果 `item_url` 已可用，可以填 `null`
- `users`：必须是你已经在大麦 App 里添加成功的真实观演人；人数就是购票张数
- `city / date / price`：尽量按 App 页面上的原文填写
- `price_index`：文本匹配失败时的兜底索引，从 `0` 开始
- `probe_only=true`：只探测，不下单，也不会点击“立即购票”
- `if_commit_order=false`：到确认页也不提交
- `auto_navigate=true`：允许脚本从首页/搜索页自动进入目标演出

下面这张图可以帮助你确认 `city / date / price` 的来源：

![参数示意图](docs/images/example_detail.png)

### 5. 先跑安全探测

如果你配置了 `item_url + auto_navigate=true`，手机停在大麦首页就可以。

注意：下面这个命令请在**第二个终端窗口**里执行，第一个终端里的 Appium 要保持运行。

这里非常容易误解：

- 这一步不是正式抢票
- 这一步不会自动点击“立即购票”
- 这一步的目标只是确认脚本能不能自动找到目标演出页
- 如果你执行后看到脚本停在详情页，这是 `probe_only=true` 的正常行为，不是卡死

然后执行：

```bash
./mobile/scripts/start_ticket_grabbing.sh --yes
```

我本地实际跑过这条链路，当前脚本会：

- 自动解析 `item_url`
- 自动生成搜索关键词
- 自动拉起大麦 App
- 在 `probe_only=true` 时停在购票点击前

只有当你把配置改成下面这样，脚本才会真的点击“立即购票/立即预订”并继续往后走：

```jsonc
"probe_only": false,
"if_commit_order": false
```

### 6. 再跑“不支付验证”

把本地配置改成：

```jsonc
"probe_only": false,
"if_commit_order": false
```

然后再次执行：

```bash
./mobile/scripts/start_ticket_grabbing.sh --yes
```

预期结果：

- 会点击“立即购票/立即预订”
- 自动选票
- 自动进入“确认购买”页
- 停在“立即提交”之前
- 不会支付

### 7. 正式提交前再确认一次

只有前两轮都通过后，才考虑把：

```jsonc
"if_commit_order": true
```

打开。

## 自然语言入口

如果你不想手改配置，也可以用自然语言入口：

```bash
./mobile/scripts/run_from_prompt.sh --mode summary --yes "帮我抢一张 4 月 6 号张杰的演唱会门票，内场"
```

模式说明：

- `summary`：只搜索并输出候选和当前页面可见摘要，不写配置
- `apply`：写配置，不执行抢票
- `probe`：写配置后直接做安全探测
- `confirm`：写配置后验证到确认页前，不提交订单

注意：

- `summary` 能稳定给出搜索候选
- 日期和票档摘要取决于页面当前是否已经展开到可识别状态，显示 `未识别` 也算正常，不代表脚本失效

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

- [mobile/config.local.jsonc](./mobile/config.local.jsonc) 里的 `users` 写的是占位符，不是真实名字
- 你的大麦账号里还没有配置对应观演人

### 5. 脚本没有进入确认页

通常先查这几项：

1. `price` 文本是不是填错了
2. `price_index` 是不是和实际票档不一致
3. 当前票档是不是“缺货登记”而不是可买状态

### 6. 为什么脚本停在详情页，没有继续点“立即购票”

最常见的原因不是脚本坏了，而是当前还在安全探测模式。

先检查配置：

```jsonc
"probe_only": true
```

如果是这个值，脚本会故意停在详情页购票按钮前，不会真正点击。

如果你想继续跑到确认页，但又不想支付，请改成：

```jsonc
"probe_only": false,
"if_commit_order": false
```

另外再检查：

1. `wait_cta_ready_timeout_ms` 是否设置得过大，导致脚本还在等待 CTA 就绪
2. `city` 是否和详情页上的实际文本不一致，导致预选失败
3. 当前项目是否其实还是“预约/预售”流程，而不是真正可下单流程
4. 当前项目是不是只支持 App，不支持 H5 / Web

## 其他方案

### Web 端

适合已经熟悉 Selenium 的人。它还在仓库里，但不再是默认推荐路线。

```bash
cd web
python damai.py
```

首次运行会打开 Chrome 登录，配置文件是 [web/config.json](./web/config.json)。

### Desktop 端

`Desktop` 方案保留代码和历史文档，但当前已经不作为可用方案推荐。

原因很简单：

- 这条路线依赖大麦 H5 / mtop 接口
- 当前官方渠道限制和风控已经让这条方案失去稳定可用性
- 继续折腾 `desktop` 的投入产出很差

如果你只是想真正跑通抢票流程，请回到上面的 `五分钟跑通 Mobile`。

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

仅供学习和研究使用。请自行承担使用风险，并遵守平台规则。更完整的说明见 [DISCLAIMER.md](./DISCLAIMER.md)。
