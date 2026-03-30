# Mobile Hot Path Workflow Design

## 背景

这个方案针对的是移动端实战模式：

- 用户自己先把手机停在目标演出的详情页或 SKU 页
- 脚本不负责从首页导航到演出页
- 脚本只负责最后一段热路径
- 如果失败，优先在当前会话里本地回退再重试，不先重建 driver

核心目标有两个：

1. 压缩 `立即购票 -> 票档 -> 确定购买 -> 立即提交出现` 这一段时间
2. 失败后尽快回到可继续重试的详情页或 SKU 页

配套图文件：

- 可编辑源文件：[`../移动端热路径工作流.drawio`](../移动端热路径工作流.drawio)
- 静态图片：[`../images/移动端热路径工作流.png`](../images/移动端热路径工作流.png)

## 主热路径

```mermaid
flowchart TD
    A[用户手动停在目标演出详情页或 SKU 页] --> B[启动脚本或 run_ticket_grabbing]
    B --> C[建立 Appium 会话]
    C --> D[probe_current_page]

    D -->|detail_page| E[预选日期]
    E --> F[预选城市]
    F --> G{需要等待开售 CTA 吗}
    G -->|是| H[等待 CTA 变成 立即购买/立即预定]
    G -->|否| I[直接进入点击]
    H --> I
    I --> J[点击 立即购票]
    J --> K[_wait_for_purchase_entry_result]

    D -->|sku_page| L[跳过详情页热路径]
    K -->|sku_page| M[进入 SKU 页]
    K -->|order_confirm_page| U[订单确认页]
    L --> M

    M --> N[按 price_index 直点票档]
    N --> O[选择数量]
    O --> P[点击 确定购买]
    P --> Q{立即提交是否已出现}
    Q -->|是| U
    Q -->|否| R[勾选观演人]
    R --> S[_wait_for_submit_ready]
    S --> U

    U --> V{if_commit_order}
    V -->|false| W[停在 立即提交 前]
    V -->|true| X[_submit_order_fast]
    X --> Y[验证支付页/成功页]
```

## 失败后的本地快速回退

```mermaid
flowchart TD
    A[本轮失败] --> B[_fast_retry_from_current_state]
    B --> C{当前页面状态}

    C -->|detail_page / sku_page| D[直接重跑主流程]
    C -->|order_confirm_page| E[走确认页最快路径]
    C -->|other| F[_recover_to_detail_page_for_local_retry]

    F --> G[清一次弹窗]
    G --> H[按 Android Back]
    H --> I[再次探测页面]
    I --> J{是否回到 detail_page / sku_page}
    J -->|是| D
    J -->|否| K{是否达到最大回退次数}
    K -->|否| G
    K -->|是| L[本轮快速重试失败]
```

## Benchmark 工作流

压测脚本入口是 `./mobile/scripts/benchmark_hot_path.sh`，它会强制覆盖成安全参数：

- `if_commit_order=false`
- `auto_navigate=false`
- `rush_mode=true`

压测流程如下：

```mermaid
flowchart TD
    A[用户手动停在抢票界面] --> B[执行 benchmark_hot_path.sh]
    B --> C[优先读取 mobile/config.local.jsonc]
    C --> D[用命令行参数临时覆盖 price / price_index / city / date]
    D --> E[强制安全模式运行]
    E --> F[连续执行 N 轮 run_ticket_grabbing]
    F --> G[记录每轮 elapsed_seconds]
    G --> H[记录 final_state / submit_button_ready]
    H --> I[非最后一轮时统计 recovery_seconds]
    I --> J[输出 avg / min / max / avg_recovery]
```

## 关键设计选择

### 为什么实战时优先相信 `price_index`

因为大麦 SKU 页经常不稳定暴露票档文本：

- 有时只暴露价格数字
- 有时文本完全不进无障碍树
- 有时只能靠 OCR 补读

所以实战热路径里，优先用已经通过验证的 `price_index` 直点票档，速度更快，也更稳定。

### 为什么失败后不先重建 driver

因为手动起跑模式下，用户已经把手机停在正确页面附近了。此时先 `driver.quit()` 再重建：

- 会浪费时间
- 可能把页面打回首页或别的初始态
- 会把“局部失败”扩大成“整链路重走”

所以当前策略是优先在当前会话里做本地回退，只在自动导航模式下才保留重建 driver 的逻辑。
