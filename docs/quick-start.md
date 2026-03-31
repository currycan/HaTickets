# 大麦抢票快速开始

`Desktop` 方案已经不可用。当前主推方案是 `Mobile + 安卓真机`。

## 最短路径

1. 连接安卓真机，并保持大麦 App 已登录
2. 启动 Appium
3. 自动配置并直接做一次安全探测
4. 探测通过后，再进入正式抢票

这 2 个用户阶段不要混淆：

1. `./mobile/scripts/start_ticket_grabbing.sh --probe --yes`
   只探测。会停在“立即购票/立即预订”之前，不会真正点击。
2. `./mobile/scripts/start_ticket_grabbing.sh --yes`
   才是正式提交模式。

也就是说：

- 第 4 步是测试“能不能找到正确演出页”
- 第 5 步是真正开始抢票

## 1. 安装依赖

```bash
poetry install
npm install -g appium
appium driver install uiautomator2
```

如果你还没有 Android SDK，建议直接安装 Android Studio。

## 2. 连接手机

手机需要先打开：

- `开发者选项`
- `USB 调试`

然后执行：

```bash
adb devices
```

你会看到类似：

```bash
List of devices attached
ABC1234567	device
```

这里的 `ABC1234567` 就是你的 `udid`。

安卓版本可以这样取：

```bash
adb shell getprop ro.build.version.release
```

## 3. 启动 Appium

```bash
./mobile/scripts/start_appium.sh
```

这一步会启动一个本地 Appium 服务，需要持续保持运行。

建议做法：

1. 第一个终端运行 `./mobile/scripts/start_appium.sh`
2. 保持这个终端不要关闭
3. 第二个终端再去执行抢票脚本

这一步会检查：

- Android SDK
- 已连接设备
- 大麦 App 是否安装
- Appium 服务是否成功启动

## 4. 准备本地配置

开始前先确认：

- 真机里已经安装并登录大麦 App
- 你要用到的观演人已经在大麦 App 里添加成功

普通用户优先编辑 `mobile/config.jsonc`。

如果文件不存在，先复制模板：

```bash
cp mobile/config.example.jsonc mobile/config.jsonc
```

然后至少改这几个字段：

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

关键说明：

- `item_url`：推荐填大麦详情页链接，脚本会自动提取 `itemId`
- `keyword`：如果 `item_url` 已可用，可以填 `null`
- `users`：必须是已经在大麦 App 里添加成功的真实观演人；人数就是购票数量
- `city / date / price`：尽量按 App 页面原文填写
- `price_index`：文本匹配失败时的兜底索引，从 `0` 开始
- `probe_only=true`：脚本内部使用的探测标记；普通用户优先用 `--probe`
- `if_commit_order=false`：脚本会继续到确认页并执行观演人勾选校验，但会停在“立即提交”前；正式抢票时 `start_ticket_grabbing.sh --yes` 会自动改成 `true`
- `auto_navigate=true`：允许脚本从首页自动进入目标演出

如果你是开发者，也可以额外创建 `mobile/config.local.jsonc` 作为本地覆盖配置。它不会提交到 GitHub，但默认不会自动生效；只有显式通过 `--config mobile/config.local.jsonc` 或 `HATICKETS_CONFIG_PATH=mobile/config.local.jsonc` 才会启用。

## 4.1 开抢前多久启动脚本

如果开抢时间是 `12:00`，不要等到 `11:59:59` 才运行：

- 你已经手动停在目标详情页或票档页：提前 **1 到 2 分钟**
- 你还要依赖自动导航、自动搜索：提前 **3 到 5 分钟**
- 第一次跑或者想更稳：提前 **5 分钟以上**

实操上，推荐在 **11:55 到 11:58** 之间启动。

如果你知道精确开抢时间，推荐这样配：

```jsonc
"sell_start_time": "2026-04-06T12:00:00+08:00",
"countdown_lead_ms": 3000,
"wait_cta_ready_timeout_ms": 0
```

含义是：

- 精确等到 `12:00`
- 从 `11:59:57` 开始紧密轮询
- 不额外走 CTA 长等待

如果你不知道精确时间，但会手动停在倒计时详情页，再考虑这样配：

```jsonc
"sell_start_time": null,
"wait_cta_ready_timeout_ms": 60000
```

这表示“最多等 60 秒 CTA 变成可购”，更适合蹲详情页，不适合作为普通默认配置。

## 4.2 推荐：直接用 prompt 做安全探测

如果你已经在用自然语言入口，最推荐的做法是直接执行：

```bash
./mobile/scripts/run_from_prompt.sh --mode probe --yes "给张三和李四抢4 月 6 号张杰的北京站演唱会内场门票，票价 1680 元"
```

这一步会：

- 自动解析提示词
- 自动写入 `mobile/config.jsonc`
- 自动做一次安全探测
- 停在购票点击前，不会直接下单

也就是说，对普通用户来说，这一步已经覆盖了原来独立的“第 5 步安全探测”。

如果你是手动配置用户，完成第 4 步后，也可以直接用下面这条命令做安全探测：

```bash
./mobile/scripts/start_ticket_grabbing.sh --probe --yes
```

通过的标志是：

- 脚本能自动控制大麦 App
- 能自动定位到目标演出页
- 在购票点击前停止

如果脚本停在详情页，不代表脚本坏了；这正是 `--probe` 的预期行为。

## 5. 真正开始抢票

第 4 步探测通过后，直接执行：

```bash
./mobile/scripts/start_ticket_grabbing.sh --yes
```

这一步才会真正点击“立即提交”。如果当前配置里还是探测模式，脚本会先提醒你，再自动把配置切到正式抢票模式，然后继续执行。如果下单成功，通常会进入支付页；后续支付需要你自己完成。

再提醒一次：

- 第 5 步不要等到最后一秒再启动
- 已经验证过流程时，提前 **1 到 2 分钟**
- 需要自动导航时，提前 **3 到 5 分钟**

## 可选：自然语言入口

如果你不想手改配置，也可以用自然语言入口：

先记住这几条：

- 提示词里要写清楚观演人姓名
- 如果没写观演人，脚本会立即停止
- 如果已经写了多个观演人但没额外写“2张”，脚本会自动按观演人数推断购票数量
- 只有当你手动写了张数、且和观演人数不一致时，脚本才会停止
- 这种情况下不会继续搜索、连接 Appium，也不会写配置
- 脚本会直接打印一条或两条“可复制的正确命令”，你按输出重试即可
- 如果当前只连接了一台安卓设备，脚本会自动识别 `udid / platform_version`
- 在 `apply / probe` 模式下，设备字段也会一起写回 `mobile/config.jsonc`
- 推荐格式：`给张三和李四抢4 月 6 号张杰的北京站演唱会内场门票，票价 1680 元`
- 使用时请把 `张三`、`李四` 替换成你自己已经在大麦 App 中添加成功的真实观演人姓名

```bash
./mobile/scripts/run_from_prompt.sh --mode summary --yes "给张三和李四抢4 月 6 号张杰的北京站演唱会内场门票，票价 1680 元"
```

模式说明：

- `summary`：只搜索并输出候选和当前页面可见摘要，不写配置
- `apply`：写配置，不执行抢票
- `probe`：写配置后直接做安全探测

说明：

- `summary` 一定会尽量给出搜索候选
- 日期和票档摘要如果当前页面还没展开，可能显示 `未识别`

## 常见问题

### `adb: command not found`

把 Android SDK 的 `platform-tools` 加进环境变量。

### `adb devices` 看不到手机

检查：

1. 手机是否打开 `USB 调试`
2. 数据线是否支持传输
3. 手机上是否点了“允许调试”

### Appium 服务器未启动

先执行：

```bash
./mobile/scripts/start_appium.sh
```

### 脚本找不到观演人

检查：

1. `users` 是否写成了占位符
2. 这些名字是否已经在大麦 App 里添加成功
3. `users` 的人数是否和你要买的票数一致

### 脚本停在详情页，没有继续点击“立即购票”

最常见原因是你执行的是探测命令：

```bash
./mobile/scripts/start_ticket_grabbing.sh --probe --yes
```

这是预期行为。`--probe` 会故意停在购票按钮前。

如果你想正式开始抢票，直接执行：

```bash
./mobile/scripts/start_ticket_grabbing.sh --yes
```

如果当前配置里还是探测模式，这条命令会先提醒你，再自动把配置切到正式抢票模式。
