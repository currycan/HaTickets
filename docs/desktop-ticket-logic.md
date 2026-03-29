# 桌面端抢票逻辑 (Tauri API 直调，已停用)

> 源码目录: `tickets-master/`
>
> **重要说明**
> - 这份文档保留的是 `desktop` 的历史实现
> - 当前官方渠道限制和风控变化，已经让这条路线不再作为实际可用方案推荐
> - 如果你的目标是现在跑通抢票流程，请回到 `mobile/`

## 技术栈

- **前端**: Vue 3 + Arco Design + Vuex + Vue Router
- **后端**: Rust (Tauri v1) + reqwest HTTP 客户端
- **构建**: Vite + Tauri CLI
- **存储**: tauri-plugin-sql（本地 SQLite）+ localStorage

## 核心架构

```
Vue 前端 (表单/UI/定时/签名)
    ↓ invoke() (Tauri IPC)
Rust 后端 (HTTP 请求代理)
    ↓ reqwest
大麦 mtop API (mtop.damai.cn)
```

与 Web/Mobile 方案的根本区别：**不操作任何 UI，直接调用大麦的 H5 API 接口**。

## 模块结构

### Rust 后端 (`src-tauri/src/`)

| 文件 | 职责 |
|------|------|
| `main.rs` | Tauri 入口，注册 5 个 command + 1 个工具函数 |
| `proxy_builder.rs` | HTTP 代理构建器（支持 socks4/5/http/https） |
| `utils.rs` | 文件导出工具 |
| `lib.rs` | 模块声明 |

**Tauri Commands（Rust → 前端可调用的接口）**:

| Command | 对应 API | 用途 |
|---------|----------|------|
| `get_product_info` | `mtop.alibaba.damai.detail.getdetail/1.2` | 获取商品详情 |
| `get_ticket_list` | `mtop.alibaba.detail.subpage.getdetail/2.0` | 获取票档列表 |
| `get_ticket_detail` | `mtop.trade.order.build.h5/4.0` | 构建订单（获取订单详情） |
| `create_order` | `mtop.trade.order.create.h5/4.0` | 创建订单 |
| `get_user_list` | `mtop.damai.wireless.user.customerlist.get/2.0` | 获取观演人列表 |

### Vue 前端 (`src/`)

| 文件 | 职责 |
|------|------|
| `views/dm.vue` | 大麦抢票主页面，加载 baxia 脚本 |
| `components/dm/Form.vue` | 配置表单（cookie/itemId/观演人/重试/代理） |
| `components/dm/Product.vue` | 商品信息展示 + 抢票核心逻辑 |
| `components/dm/VisitUser.vue` | 观演人管理 |
| `utils/dm/index.js` | 签名算法、baxia 集成、订单参数构造 |
| `utils/common/log.js` | 日志工具 |

## 主流程

### 1. 初始化

页面加载时 (`dm.vue::onMounted`):
- 加载阿里 baxia 反爬脚本 (`awsc.js` + `baxiaCommon.js`)
- 2 秒后初始化 baxia，用于生成后续请求所需的 `bx-ua` 和 `bx-umidtoken`

### 2. 用户输入

`Form.vue` 收集:
- **cookie**: 从浏览器 F12 手动复制
- **itemId**: 从商品 URL 自动提取或手动输入
- **token**: 从 cookie 中的 `_m_h5_tk` 字段自动解析
- **观演人**: 通过 API 获取账号下的观演人列表，checkbox 勾选
- **购买张数**: 需与观演人数量一致
- **重试次数**: 默认 5 次，最大 10 次
- **间隔时间**: 失败后重试间隔，默认 1000ms
- **代理**: 可选，支持 socks/http 协议

### 3. 获取商品信息 (`getProductInfo`)

```
前端 getSign(data, token) → [timestamp, sign]
  ↓ invoke("get_product_info", {t, sign, itemid, cookie, ...})
Rust GET mtop.alibaba.damai.detail.getdetail/1.2
  ↓ 返回 JSON
解析 buyBtnStatus:
  303 → 下架
  100 → 不支持该渠道
  106 → 即将开抢（预售），提取 sellStartTime 启动倒计时
```

### 4. 获取票档 (`getSkuInfo`)

用户点击场次后触发：
```
invoke("get_ticket_list", {itemid, performId, ...})
  → Rust GET mtop.alibaba.detail.subpage.getdetail/2.0
  → 返回 skuList（票档名、价格、状态标签）
```

用户在 UI 中点击选择具体票档。

### 5. 抢票触发

两种模式：

**非预售商品**: 点击"购票"按钮 → 直接调用 `buy()`

**预售商品**: 点击"抢票"按钮 → 前端倒计时
- 倒计时基于 `sellStartTime`（服务端返回的开售时间戳）
- 支持**修正时间**（毫秒级，补偿本地时钟与服务器的偏差）
- 最终时间 = sellStartTime + timeFix
- 倒计时归零 → 自动调用 `buy()`
- 超过 1 小时提醒更换 cookie

### 6. 获取订单详情 (`getOrderDetail`)

```
getHeaderUaAndUmidtoken()  → 获取 baxia 反爬凭证（每个值只能用 2 次）
getSign(data, token)       → 计算签名
  ↓ invoke("get_ticket_detail", {t, sign, cookie, data, ua, umidtoken, ...})
Rust POST mtop.trade.order.build.h5/4.0
  ↓ 返回订单构建数据
```

失败时自动重试一次（`isDetailRetry` 标志防止无限重试）。

### 7. 创建订单 (`createOrder`)

**参数构造** (`combinationOrderParams()`):
- 从订单详情中提取 data（支付方式、观演人、配送、联系信息等）
- 注入选中的观演人（设置 `isUsed: true`）
- 提取 linkage（签名、提交参数）
- 提取 hierarchy（页面结构）
- 使用自定义 JSON 序列化 `cusJSON()` —— 非标准 JSON.stringify，可能是为了匹配大麦的签名校验格式

```
combinationOrderParams(orderDetail, selectUserList)
  → encode 为 form data
  → 附加 bx-ua, bx-umidtoken
  ↓ invoke("create_order", {t, sign, cookie, data, submitref, ...})
Rust POST mtop.trade.order.create.h5/4.0
```

**结果处理**:

| 响应 | 行为 |
|------|------|
| SUCCESS | 播放成功音频，弹窗引导去订单页支付 |
| "您还有未支付订单" | 停止抢票 |
| FAIL_SYS_USER_VALIDATE | 滑块/风控触发，停止并提示重新登录 |
| 其他失败 | 按间隔时间重试，直到用完重试次数 |

## API 签名机制

`getSign()` (`utils/dm/index.js`):

```javascript
// 标准阿里 mtop 签名算法
timestamp = Date.now()
appKey = "12574478"
sign = MD5(token + "&" + timestamp + "&" + appKey + "&" + data)
return [timestamp, sign]
```

- `token` 从 Cookie 的 `_m_h5_tk` 字段解析
- `data` 是请求的 JSON 数据
- MD5 实现内联在代码中（非依赖外部库）

## 反爬对抗

### baxia 凭证

阿里系的反爬系统，通过加载外部脚本生成动态凭证：
- `bx-ua`: 浏览器指纹 token
- `bx-umidtoken`: 设备标识 token
- 每组凭证**只能使用两次**
- 仅 `order.build` 和 `order.create` 接口需要

### 请求头伪造

Rust 端构造完整的移动端 Chrome 请求头：
- `user-agent`: Android Nexus 5 + Chrome 113
- `sec-ch-ua-platform`: "Android"
- `origin` / `referer`: m.damai.cn
- `globalcode`: ali.china.damai

### 代理

`ProxyBuilder` 支持：
- 协议: socks4 / socks5 / http / https
- IP 提取: 正则解析代理地址
- 每个 Tauri command 都接收 `is_proxy` + `address` 参数

## 数据持久化

| 数据 | 存储位置 |
|------|----------|
| 观演人列表 | localStorage (`visitUserList`) |
| 代理设置 | SQLite (tauri-plugin-sql) |
| 操作日志 | Log 类（内存） |

## 与 UI 自动化方案的关键差异

1. **无浏览器渲染**: 不打开任何网页，HTTP 请求直达 API
2. **签名自算**: 需要自行实现 mtop 签名算法
3. **反爬自处理**: 需要加载 baxia 脚本获取凭证
4. **无选座能力**: API 不支持选座流程
5. **Cookie 有效期短**: 需要用户手动更新，不能像 Selenium 那样自动刷新
6. **风控风险更高**: 非浏览器环境的请求更容易被识别
