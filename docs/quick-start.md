# 大麦抢票快速开始

`Desktop` 方案已经不可用。当前主推方案是 `Mobile + 安卓真机`。

## 推荐环境

| 组件      | 推荐版本                                  |
| --------- | ----------------------------------------- |
| Python    | 3.10 ~ 3.13（CI 已覆盖；3.8/3.9 受限支持） |
| Poetry    | 1.7+                                      |
| Android   | 12 ~ 14 真机（模拟器易被风控）             |

> ⚠ Python 3.14 暂不支持：`uiautomator2` 与 `selenium` 上游尚未发布预编译 wheel（issue #21）。

## 最短路径

1. 连接安卓真机，并保持大麦 App 已登录
2. 自动配置并直接做一次安全探测
3. 探测通过后，再进入正式抢票

这 2 个用户阶段不要混淆：

1. `./mobile/scripts/start_ticket_grabbing.sh --probe`
   只探测。会停在“立即购票/立即预订”之前，不会真正点击。（自动化场景可加 `--yes` 跳过确认）
2. `./mobile/scripts/start_ticket_grabbing.sh --commit --yes`
   才是正式提交模式，启动前会打印下单摘要并倒数 3 秒。

三个旗标的语义先记牢：

- `--probe`：安全探测，绝不下单
- `--commit`：唯一会真实下单的旗标；不带 `--yes` 时还需手动输入确认词
- `--yes`：仅跳过普通确认；**漏敲 `--probe` 不再会真下单，脚本会直接报错退出**

也就是说：

- 安全探测见 [3.2 推荐：用现有精确配置做只读 prompt 探测](#32-推荐用现有精确配置做只读-prompt-探测)
- 正式抢票见 [4. 真正开始抢票](#4-真正开始抢票)

## 1. 安装依赖

先准备 Python 3.8 ~ 3.13（推荐 3.12 或 3.13）和
[Poetry](https://python-poetry.org/docs/#installation)，再在仓库根目录执行：

```bash
poetry env use python3.12  # 已经是受支持版本时可省略
poetry install
```

已有有效的项目 `.venv` 时，启动脚本不再要求 Poetry 命令必须存在；否则会使用
Poetry 已创建的环境。启动脚本**不会自动安装依赖**，缺依赖时会立即退出并提示先
完成 `poetry install`，不要等到开抢前再安装。

如果你还没有 Android SDK，建议直接安装 Android Studio（需要 `adb` 命令可用）。

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

这里的 `ABC1234567` 就是你的 `serial`（配置文件中的设备序列号字段）。

安卓版本可以这样取：

```bash
adb shell getprop ro.build.version.release
```

## 3. 准备本地配置

开始前先确认：

- 真机里已经安装并登录大麦 App
- 你要用到的观演人已经在大麦 App 里添加成功

普通用户优先编辑 `mobile/config.jsonc`。

如果文件不存在，先复制模板：

```bash
cp mobile/config.example.jsonc mobile/config.jsonc
```

然后至少改这几个字段：

<!-- CONFIG_EXAMPLE:BEGIN -->
```jsonc
{
  // adb devices 显示的设备序列号
  "serial": "你的设备序列号",
  // 在大麦 App 内搜索目标演出的关键词（必填，不能为 null）
  "keyword": "张杰 演唱会",
  // 必须是你已经在大麦 App 中添加成功的观演人；人数 = 购票张数
  "users": ["你的真实观演人姓名"],
  "city": "演出城市",
  "date": "场次日期",
  "price": "票档原文",
  "price_index": 0,
  "probe_only": true,
  "if_commit_order": false,
  "auto_navigate": true
}
```
<!-- CONFIG_EXAMPLE:END -->

关键说明：

- `serial`：`adb devices` 输出的设备序列号
- `keyword`：必填，不能为空或 `null`；脚本用它在大麦 App 内搜索目标演出
- `users`：必须是已经在大麦 App 里添加成功的真实观演人；人数就是购票数量
- `city / date / price`：尽量按 App 页面原文填写
- `price_index`：文本匹配失败时的兜底索引，从 `0` 开始
- `probe_only=true`：脚本内部使用的探测标记；普通用户优先用 `--probe`
- `if_commit_order=false`：脚本会继续到确认页并执行观演人勾选校验，但会停在“立即提交”前；正式抢票时 `start_ticket_grabbing.sh --commit` 只会在本次进程中覆盖为 `true`，不会写回配置文件
- `auto_navigate=true`：允许脚本从首页自动进入目标演出

如果你是开发者，也可以额外创建 `mobile/config.local.jsonc` 作为本地覆盖配置。它不会提交到 GitHub，但默认不会自动生效；只有显式通过 `--config mobile/config.local.jsonc` 或 `HATICKETS_CONFIG_PATH=mobile/config.local.jsonc` 才会启用。

运行期环境变量（详见 [README「退出码与运行摘要」](../README.md#退出码与运行摘要)）：

- `HATICKETS_SERIAL`：覆盖配置中的 `serial`（不改写配置文件），同一份 config 可复用到多台设备；脚本参数 `--serial <serial>` 就是它的透传通道
- `HATICKETS_RESULT_JSON`：机器可读运行摘要 JSON 的输出路径（默认 `mobile/tmp/run_summary.json`）；脚本参数 `--result-json <path>` 同义
- `HATICKETS_RUN_MODE`：由启动脚本为本次子进程注入 `probe/commit`；不要手工持久化
- `HATICKETS_COMMIT_AUTH`：`--commit` 确认闸门通过后由启动脚本临时注入，进程结束即失效

脚本与 `python -m damai_app` 现在会返回语义化退出码（`0` 成功 / `10` 重试耗尽 / `11` 不可重试失败 / `12` 配置或设备错误 / `13` 同设备实例冲突 / `130` 用户中断），供 cron/systemd 等编排消费，退出码表见 README。

## 3.1 开抢前多久启动脚本

如果开抢时间是 `12:00`，不要等到 `11:59:59` 才运行：

- 你已经手动停在目标详情页或票档页：提前 **1 到 2 分钟**
- 你还要依赖自动导航、自动搜索：提前 **3 到 5 分钟**
- 第一次跑或者想更稳：提前 **5 分钟以上**

实操上，推荐在 **11:55 到 11:58** 之间启动。

如果你知道精确开抢时间，推荐这样配：

```jsonc
"sell_start_time": "2026-04-06T12:00:00+08:00",
"countdown_lead_ms": 3000,
"presale_prewarm_enabled": true,
"wait_cta_ready_timeout_ms": 0
```

含义是：

- 精确等到 `12:00`
- 从 `11:59:57` 开始紧密轮询
- 在倒计时窗口开始时做一次只读页面/CTA 预热
- 不额外走 CTA 长等待

如果你不知道精确时间，但会手动停在倒计时详情页，再考虑这样配：

```jsonc
"sell_start_time": null,
"wait_cta_ready_timeout_ms": 60000
```

这表示“最多等 60 秒 CTA 变成可购”，更适合蹲详情页，不适合作为普通默认配置。

## 3.2 推荐：用现有精确配置做只读 prompt 探测

先用 `--mode apply` 生成并检查配置，再手动亮屏、解锁，在大麦里打开配置
对应的精确目标详情页，然后执行：

```bash
./mobile/scripts/run_from_prompt.sh --mode probe --yes "给张三和李四抢4 月 6 号张杰的北京站演唱会内场门票，票价 1680 元"
```

这一步会：

- 校验提示词中的演出、城市和日期与现有精确配置一致
- 不修改 `mobile/config.jsonc`
- 只读检查当前前台详情页
- 停在购票点击前，不会直接下单

它不会自动返回、搜索、输入、点击搜索结果或点击购票入口；当前页面不匹配时
会直接停止并要求手动导航。

如果你是手动配置用户，完成 [3. 准备本地配置](#3-准备本地配置) 后，先手动亮屏、解锁，并在大麦里打开配置对应的目标演出详情页、处理完弹窗，再运行：

```bash
./mobile/scripts/start_ticket_grabbing.sh --probe
```

通过的标志是：

- 脚本确认当前前台是大麦和配置对应的目标演出页
- 能只读识别详情页关键控件
- 在购票点击前停止

`--probe` 不会唤醒手机、启动 App、关闭弹窗、自动搜索或切换页面。停在详情页正是预期行为；不在目标页时请手动导航后重试。

## 4. 真正开始抢票

[3. 准备本地配置](#3-准备本地配置) 的探测通过后，直接执行：

```bash
./mobile/scripts/start_ticket_grabbing.sh --commit --yes
```

这一步才会真正点击”立即提交”。如果当前配置里还是探测模式，脚本会先提醒你，把配置切到正式抢票模式，并在打印下单摘要、倒数 3 秒后继续执行。不带 `--yes` 时还需要手动输入确认词（`GO` 或配置里的 `keyword` 原文）。如果下单成功，通常会进入支付页；后续支付需要你自己完成。

> ⚠️ 旧命令 `./mobile/scripts/start_ticket_grabbing.sh --yes`（不带 `--commit`）已不再触发真实下单，会直接报错并给出迁移指引。

再提醒一次：

- 第 4 步不要等到最后一秒再启动
- 已经验证过流程时，提前 **1 到 2 分钟**
- 需要自动导航时，提前 **3 到 5 分钟**

## 可选：自然语言入口

如果你不想手改配置，也可以用自然语言入口：

先记住这几条：

- 提示词里要写清楚观演人姓名
- 如果没写观演人，脚本会立即停止
- 如果已经写了多个观演人但没额外写“2张”，脚本会自动按观演人数推断购票数量
- 只有当你手动写了张数、且和观演人数不一致时，脚本才会停止
- 这种情况下不会继续搜索、连接设备，也不会写配置
- 脚本会直接打印一条或两条“可复制的正确命令”，你按输出重试即可
- 如果当前只连接了一台安卓设备，脚本会自动识别 `serial`
- 在 `apply` 模式下，设备序列号也会一起写回 `mobile/config.jsonc`
- 推荐格式：`给张三和李四抢4 月 6 号张杰的北京站演唱会内场门票，票价 1680 元`
- 使用时请把 `张三`、`李四` 替换成你自己已经在大麦 App 中添加成功的真实观演人姓名

```bash
./mobile/scripts/run_from_prompt.sh --mode summary --yes "给张三和李四抢4 月 6 号张杰的北京站演唱会内场门票，票价 1680 元"
```

模式说明：

- `summary`：只搜索并输出候选和当前页面可见摘要，不写配置
- `apply`：写配置，不执行抢票
- `probe`：不写配置、不自动导航，只读检查当前精确目标详情页

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

### 设备连接失败

确认 `adb devices` 能正常识别设备，且手机已开启 USB 调试。

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
./mobile/scripts/start_ticket_grabbing.sh --commit --yes
```

如果当前配置里还是探测模式，这条命令会先提醒你，把配置切到正式抢票模式，并在打印下单摘要、倒数 3 秒后继续执行。
