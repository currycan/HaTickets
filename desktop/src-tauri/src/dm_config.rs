// ============================================================
// 大麦平台 API 参数配置
// 若大麦更新导致接口调用失败，首先检查并更新此文件
// ============================================================

// mtop 协议版本，出现在所有接口 URL 的 jsv= 参数中
pub const JSV: &str = "2.7.5";

// 接口鉴权 appKey，需与 JS 端 dm-config.js 中的 DM_APP_KEY 保持一致
pub const APP_KEY: &str = "12574478";

// 接口宿主域名
pub const MTOP_HOST: &str = "mtop.damai.cn";

// User-Agent：模拟移动端浏览器，版本过旧可能被风控
// Chrome 版本可参考 https://chromestatus.com/roadmap 适当更新
pub const USER_AGENT: &str =
    "Mozilla/5.0 (Linux; Android 10; K) \
     AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Mobile Safari/537.36";

pub const SEC_CH_UA: &str =
    "\"Google Chrome\";v=\"146\", \"Chromium\";v=\"146\", \";Not A Brand\";v=\"24\"";

// 各接口独立版本号（出现在 URL path 中）
pub const API_VERSION_DETAIL: &str = "1.2"; // mtop.alibaba.damai.detail.getdetail
pub const API_VERSION_SKU: &str = "2.0"; // mtop.alibaba.detail.subpage.getdetail
pub const API_VERSION_ORDER_BUILD: &str = "1.0"; // mtop.damai.trade.order.build.h5
pub const API_VERSION_ORDER_CREATE: &str = "1.0"; // mtop.damai.trade.order.create.h5
pub const API_VERSION_USER_LIST: &str = "2.0"; // mtop.damai.wireless.user.customerlist.get

/// 构建 mtop URL 公共前缀，避免各接口函数重复拼接
/// 返回: https://{host}/h5/{api}/{version}/?jsv=...&appKey=...&t={t}&sign={sign}
pub fn build_base_url(api: &str, version: &str, t: usize, sign: &str) -> String {
    format!(
        "https://{}/h5/{}/{}/?jsv={}&appKey={}&t={}&sign={}",
        MTOP_HOST, api, version, JSV, APP_KEY, t, sign
    )
}
