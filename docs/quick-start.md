# 大麦抢票快速开始

`Desktop` 方案已经不可用。当前主推方案是 `Mobile + 安卓真机`。

## 最短路径

1. 连接安卓真机，并保持大麦 App 已登录
2. 启动 Appium
3. 创建 `mobile/config.local.jsonc`
4. 先跑 `probe_only=true` 的安全探测
5. 探测通过后，再跑“到确认页但不提交”

这 3 个阶段不要混淆：

1. `probe_only=true`
   只探测。会停在“立即购票/立即预订”之前，不会真正点击。
2. `probe_only=false` 且 `if_commit_order=false`
   会继续进入票档页和确认页，但停在“立即提交”之前。
3. `probe_only=false` 且 `if_commit_order=true`
   才是正式提交模式。

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

优先编辑 `mobile/config.local.jsonc`。

如果文件不存在，先复制模板：

```bash
cp mobile/config.example.jsonc mobile/config.local.jsonc
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
- `probe_only=true`：只探测，不下单，也不会点击“立即购票”
- `if_commit_order=false`：到确认页也不提交
- `auto_navigate=true`：允许脚本从首页自动进入目标演出

## 5. 先跑安全探测

如果配置了 `item_url + auto_navigate=true`，手机停在大麦首页即可。

注意：下面这个命令请在**第二个终端**里执行，第一个终端里的 Appium 要保持运行。

这里要特别注意：

- 这一步不是正式抢票
- 这一步不会点击“立即购票”
- 如果脚本停在详情页，不代表脚本坏了；这正是 `probe_only=true` 的预期行为

```bash
./mobile/scripts/start_ticket_grabbing.sh --yes
```

通过的标志是：

- 脚本能自动控制大麦 App
- 能自动定位到目标演出页
- 在购票点击前停止

如果你想让脚本真正点击“立即购票/立即预订”并继续往后走，下一步必须把配置改成：

```jsonc
"probe_only": false,
"if_commit_order": false
```

## 6. 再跑“不支付验证”

把配置改成：

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
- 自动进入“确认购买”页
- 停在“立即提交”之前
- 不会支付

## 可选：自然语言入口

如果你不想手改配置，也可以用自然语言入口：

```bash
./mobile/scripts/run_from_prompt.sh --mode summary --yes "帮我抢一张 4 月 6 号张杰的演唱会门票，内场"
```

模式说明：

- `summary`：只搜索并输出候选和当前页面可见摘要，不写配置
- `apply`：写配置，不执行抢票
- `probe`：写配置后直接做安全探测
- `confirm`：写配置后验证到确认页前，不提交订单

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

最常见原因是配置里仍然是：

```jsonc
"probe_only": true
```

这是安全探测模式，脚本会故意停在购票按钮前。

如果你要继续跑到确认页但不支付，请改成：

```jsonc
"probe_only": false,
"if_commit_order": false
```
