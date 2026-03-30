# 🚀 大麦抢票快速开始指南

> `Desktop` 方案已经不可用。当前主推方案是 `Mobile + 安卓真机`。
> 下面提到的大麦 H5 页面只用于查找演出详情页和 `itemId`，不是用于 H5 下单。

## 📋 一键检查环境

```bash
poetry run python web/check_environment.py
```

## 🎯 三步开始抢票

### 第1步：启动Appium服务器
```bash
./mobile/scripts/start_appium.sh
```

### 第2步：准备抢票环境
1. 在Android设备上打开大麦APP
2. 保持大麦账号登录
3. 如果配置了 `item_url + auto_navigate`，可以直接停留在首页；否则再手动进入演出详情页

### 第3步：开始抢票
```bash
./mobile/scripts/start_ticket_grabbing.sh --yes
```

## ⚙️ 配置抢票参数

优先编辑 `mobile/config.local.jsonc` 文件：

如果这个文件还不存在，先执行：

```bash
cp mobile/config.example.jsonc mobile/config.local.jsonc
```

```json
{
  "server_url": "http://127.0.0.1:4723",
  "device_name": "Android",
  // 通过 adb devices 获取
  "udid": "emulator-5554",
  // 通过 adb shell getprop ro.build.version.release 获取
  "platform_version": "15",
  "app_package": "cn.damai",
  "app_activity": ".launcher.splash.SplashMainActivity",
  "item_url": "https://m.damai.cn/shows/item.html?itemId=1016133935724",
  "keyword": null,
  "users": [
    "观演人1",
    "观演人2"
  ],
  // 尽量使用 App 当前页面上的原文或稳定子串
  "city": "泉州",
  // 尽量使用 App 当前页面上的原文或稳定子串
  "date": "10.04",
  // 尽量使用 App 当前页面上的原文或稳定子串
  "price": "799元",
  // 文本匹配失败时的兜底索引，按 0 开始计数
  "price_index": 1,
  "if_commit_order": true,
  "probe_only": false,
  "auto_navigate": true
}
```

首次使用建议先把 `probe_only` 设为 `true`，确认脚本能自动定位到目标演出页，且购票按钮和票档区域都已出现。
如果改用真机，把 `adb devices` 里显示的序列号填到 `udid`。

建议你从 H5 详情页里同步确认这些字段：

- `item_url`：演出详情页链接
- `city`：演出城市
- `date`：场次日期
- `price`：票档原文
- `price_index`：票档列表兜底索引，按 `0` 开始计数；如果暂时无法可靠判断，优先保留现有值，新建配置时可先用 `0`

补充两点：

- `users` 的人数就是要买的票数
- 如果配置了 `item_url` 和 `"auto_navigate": true`，脚本会自动导航到详情页，无需填 `keyword`
- 如果没有特别指定开售时间，`sell_start_time`、`countdown_lead_ms`、`fast_retry_count`、`fast_retry_interval_ms` 保持默认值即可

如果你想让 AI 尽量自己把事情做完，可以直接把下面这段提示词发给它：

```text
请帮我完整处理 HaTickets 的 mobile 方案配置和验证。

前提：
1. 我已经在大麦 App 里手动添加好了观演人
2. 我已经确认这些观演人保存成功
3. 手机已经连接电脑，并且 adb devices 可以识别
4. 大麦 App 已登录
5. 你不要帮我创建观演人，只使用我提供的已存在姓名

要求：
1. 如果本机没有项目，请先从 https://github.com/currycan/HaTickets 下载，并进入 master 分支
2. 自行检查并准备依赖环境；如果 Appium 服务没启动，请先启动
3. 如果 mobile/config.local.jsonc 不存在，先从 mobile/config.example.jsonc 复制一份
4. 如果当前配置里已有 udid、platform_version、app_package，就尽量保留；如果没有，就自己通过 adb 获取
5. 根据我提供的大麦详情页链接，提取 itemId，并填充到 item_url
6. city、date、price 尽量使用大麦 App 当前页面上实际可见的原文或稳定子串
7. 用我给你的观演人姓名更新 users，users 的人数就是购票数量
8. 设置一个可用的 price_index，按 0 开始计数；如果暂时无法可靠判断，就保留现有值，新建配置时先用 0，并在结果里说明
9. 先把 probe_only 设为 true
10. 先把 if_commit_order 设为 false
11. 确保 auto_navigate 设为 true，keyword 可设为 null
12. 修改 mobile/config.local.jsonc 后，先检查字段和类型是否正确
13. 运行验证时，如果 ./mobile/scripts/start_ticket_grabbing.sh 需要交互确认，请你自己处理确认输入；也可以直接进入 mobile 目录运行 poetry run python damai_app.py
14. 先做一次页面探测
15. 如果探测通过，再继续做一次“到确认页但不提交订单、不支付”的验证
16. 不要创建观演人，不要提交订单，不要支付
17. 把你修改了哪些字段和验证结果告诉我

演出详情页链接：
https://m.damai.cn/shows/item.html?itemId=1016133935724

我已经在大麦 App 中添加成功的观演人姓名：
张三
李四
```

如果探测通过，再把 `probe_only` 改成 `false`，继续做“到确认页但不提交”的验证。

## 🔧 常见问题解决

### 问题1：Node.js版本不兼容
```bash
# 升级Node.js
brew upgrade node
```

### 问题2：Android设备未连接
```bash
# 启动模拟器
$ANDROID_HOME/emulator/emulator -avd <your_avd_name>

# 检查设备
$ANDROID_HOME/platform-tools/adb devices
```

真机模式：
```bash
adb devices
# 把输出里的设备序列号填进 mobile/config.local.jsonc 的 udid
```

### 问题3：Appium服务器未启动
```bash
# 设置环境变量并启动
export ANDROID_HOME="$HOME/Library/Android/sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
appium --port 4723
```

## 📱 移动端抢票完整流程

1. **环境检查**：`poetry run python web/check_environment.py`
2. **启动服务**：`./mobile/scripts/start_appium.sh`
3. **准备设备**：在模拟器上打开大麦APP
也可以改成安卓真机，前提是已经打开 USB 调试并通过 `adb devices` 识别
4. **配置参数**：编辑 `config.local.jsonc`
5. **开始抢票**：`./mobile/scripts/start_ticket_grabbing.sh --yes`

## ⚠️ 重要提醒

- 确保在开售时间前准备好所有环境
- 提前测试脚本运行是否正常
- 建议使用专门的测试账号
- 遵守大麦网使用条款
- 正式提交前，先完成 `probe_only=true` 和 `if_commit_order=false` 两轮验证

## 🆘 获取帮助

如果遇到问题，请：
1. 运行 `poetry run python web/check_environment.py` 检查环境
2. 查看 `README.md` 详细文档
3. 检查控制台错误信息
4. 确认所有依赖已正确安装

---

**祝您抢票成功！** 🎫✨
