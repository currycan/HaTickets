// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use reqwest::header::{self, HeaderMap};
use std::collections::HashMap;
use std::error::Error;
use std::time::Duration;
use tauri::Manager;
use tickets::dm_config;
use tickets::proxy_builder::ProxyBuilder;
use tickets::utils;

#[tauri::command]
async fn get_product_info(
    t: usize,
    sign: &str,
    itemid: &str,
    cookie: &str,
    is_proxy: bool,
    address: String,
) -> Result<String, String> {
    let res = get_info(t, sign, itemid, cookie, is_proxy, address).await;
    match res {
        Ok(s) => Ok(s),
        Err(e) => Err(format!("[get_product_info] 请求失败: {}", e)),
    }
}

// SAFETY: All header values in this function are static strings guaranteed to parse successfully.
fn get_common_headers() -> HeaderMap {
    let mut headers = HeaderMap::new();

    headers.insert("authority", "mtop.damai.cn".parse().unwrap());
    headers.insert("accept", "application/json".parse().unwrap());
    headers.insert("accept-language", "zh-CN,zh;q=0.9".parse().unwrap());
    headers.insert("globalcode", "ali.china.damai".parse().unwrap());
    headers.insert("origin", "https://m.damai.cn".parse().unwrap());
    headers.insert("referer", "https://m.damai.cn/".parse().unwrap());
    headers.insert(
        "sec-ch-ua",
        dm_config::SEC_CH_UA.parse().unwrap(),
    );
    headers.insert("sec-ch-ua-mobile", "?1".parse().unwrap());
    headers.insert("sec-ch-ua-platform", "\"Android\"".parse().unwrap());
    headers.insert("sec-fetch-dest", "empty".parse().unwrap());
    headers.insert("sec-fetch-mode", "cors".parse().unwrap());
    headers.insert("sec-fetch-site", "same-site".parse().unwrap());
    headers.insert("user-agent", dm_config::USER_AGENT.parse().unwrap());

    headers
}

async fn get_info(
    t: usize,
    sign: &str,
    itemid: &str,
    cookie: &str,
    is_proxy: bool,
    address: String,
) -> Result<String, Box<dyn Error>> {
    let base = dm_config::build_base_url(
        "mtop.alibaba.damai.detail.getdetail",
        dm_config::API_VERSION_DETAIL,
        t, sign,
    );
    let url = format!(
        "{}&type=originaljson&dataType=json&v=2.0&H5Request=true\
         &AntiCreep=true&forceAntiCreep=true&timeout=10000\
         &api=mtop.alibaba.damai.detail.getdetail\
         &data=%7B%22itemId%22%3A%22{}%22%2C%22bizCode%22%3A%22ali.china.damai%22\
         %2C%22scenario%22%3A%22itemsku%22%2C%22exParams%22%3A%22%7B%5C%22dataType%5C%22%3A4\
         %2C%5C%22dataId%5C%22%3A%5C%22%5C%22%2C%5C%22privilegeActId%5C%22%3A%5C%22%5C%22%7D%22\
         %2C%22platform%22%3A%228%22%2C%22comboChannel%22%3A%222%22\
         %2C%22dmChannel%22%3A%22damai%40damaih5_h5%22%7D",
        base, itemid
    );

    let mut headers = get_common_headers();
    headers.insert(
        "content-type",
        "application/x-www-form-urlencoded".parse().unwrap(),
    );
    headers.insert(header::COOKIE, cookie.parse().map_err(|e: reqwest::header::InvalidHeaderValue| Box::new(e) as Box<dyn Error>)?);

    let client = ProxyBuilder::new(is_proxy, address).get_client()?;
    let res = client
        .get(url)
        .headers(headers)
        .timeout(Duration::from_secs(3))
        .send()
        .await?
        .text()
        .await?;

    Ok(res)
}

#[tauri::command]
async fn get_ticket_list(
    t: usize,
    sign: &str,
    itemid: &str,
    cookie: &str,
    dataid: &str,
    is_proxy: bool,
    address: String,
) -> Result<String, String> {
    let res = get_ticket_list_res(t, sign, itemid, cookie, dataid, is_proxy, address).await;
    match res {
        Ok(s) => Ok(s),
        Err(e) => Err(format!("[get_ticket_list] 请求失败: {}", e)),
    }
}

async fn get_ticket_list_res(
    t: usize,
    sign: &str,
    itemid: &str,
    cookie: &str,
    dataid: &str,
    is_proxy: bool,
    address: String,
) -> Result<String, Box<dyn Error>> {
    let base = dm_config::build_base_url(
        "mtop.alibaba.detail.subpage.getdetail",
        dm_config::API_VERSION_SKU,
        t, sign,
    );
    let url = format!(
        "{}&type=originaljson&dataType=json&v=2.0&H5Request=true\
         &AntiCreep=true&forceAntiCreep=true&timeout=10000&valueType=original\
         &api=mtop.alibaba.detail.subpage.getdetail\
         &data=%7B%22itemId%22%3A%22{}%22%2C%22bizCode%22%3A%22ali.china.damai%22\
         %2C%22scenario%22%3A%22itemsku%22%2C%22exParams%22%3A%22%7B%5C%22dataType%5C%22%3A2\
         %2C%5C%22dataId%5C%22%3A%5C%22{}%5C%22%2C%5C%22privilegeActId%5C%22%3A%5C%22%5C%22%7D%22\
         %2C%22dmChannel%22%3A%22damai%40damaih5_h5%22%7D",
        base, itemid, dataid
    );

    let mut headers = get_common_headers();
    headers.insert(
        "content-type",
        "application/x-www-form-urlencoded".parse().unwrap(),
    );
    headers.insert(header::COOKIE, cookie.parse().map_err(|e: reqwest::header::InvalidHeaderValue| Box::new(e) as Box<dyn Error>)?);

    let client = ProxyBuilder::new(is_proxy, address).get_client()?;
    let res = client
        .get(url)
        .headers(headers)
        .timeout(Duration::from_secs(3))
        .send()
        .await?
        .text()
        .await?;

    Ok(res)
}

#[tauri::command]
async fn get_ticket_detail(
    t: usize,
    sign: &str,
    cookie: &str,
    data: &str,
    ua: &str,
    umidtoken: &str,
    is_proxy: bool,
    address: String,
) -> Result<String, String> {
    let res = get_ticket_detail_res(t, sign, cookie, data, ua, umidtoken, is_proxy, address).await;
    match res {
        Ok(s) => Ok(s),
        Err(e) => Err(format!("[get_ticket_detail] 请求失败: {}", e)),
    }
}

async fn get_ticket_detail_res(
    t: usize,
    sign: &str,
    cookie: &str,
    data: &str,
    ua: &str,
    umidtoken: &str,
    is_proxy: bool,
    address: String,
) -> Result<String, Box<dyn Error>> {
    let base = dm_config::build_base_url(
        "mtop.damai.trade.order.build.h5",
        dm_config::API_VERSION_ORDER_BUILD,
        t, sign,
    );
    let url = format!(
        "{}&type=originaljson&dataType=json&v=1.0&H5Request=true\
         &AntiCreep=true&forceAntiCreep=true\
         &api=mtop.damai.trade.order.build.h5&method=POST\
         &ttid=%23t%23ip%23%23_h5_2014&globalCode=ali.china.damai",
        base
    );
    let mut params = HashMap::new();
    params.insert("data", data);
    params.insert("bx-ua", ua);
    params.insert("bx-umidtoken", umidtoken);

    let mut headers = get_common_headers();
    headers.insert(
        "content-type",
        "application/x-www-form-urlencoded".parse().unwrap(),
    );
    headers.insert(header::COOKIE, cookie.parse().map_err(|e: reqwest::header::InvalidHeaderValue| Box::new(e) as Box<dyn Error>)?);

    let client = ProxyBuilder::new(is_proxy, address).get_client()?;
    let res = client
        .post(url)
        .form(&params)
        .headers(headers)
        .timeout(Duration::from_secs(3))
        .send()
        .await?
        .text()
        .await?;

    Ok(res)
}

#[tauri::command]
async fn create_order(
    t: usize,
    sign: &str,
    cookie: &str,
    data: &str,
    submitref: &str,
    is_proxy: bool,
    address: String,
) -> Result<String, String> {
    let res = create_order_res(t, sign, cookie, data, submitref, is_proxy, address).await;
    match res {
        Ok(s) => Ok(s),
        Err(e) => Err(format!("[create_order] 请求失败: {}", e)),
    }
}

async fn create_order_res(
    t: usize,
    sign: &str,
    cookie: &str,
    data: &str,
    submitref: &str,
    is_proxy: bool,
    address: String,
) -> Result<String, Box<dyn Error>> {
    let base = dm_config::build_base_url(
        "mtop.damai.trade.order.create.h5",
        dm_config::API_VERSION_ORDER_CREATE,
        t, sign,
    );
    let url = format!(
        "{}&v=1.0&post=1&type=originaljson&timeout=15000&dataType=json\
         &isSec=1&ecode=1&AntiCreep=true&forceAntiCreep=true\
         &ttid=%23t%23ip%23%23_h5_2014&globalCode=ali.china.damai\
         &H5Request=true&api=mtop.damai.trade.order.create.h5&method=POST&{}",
        base, submitref
    );

    let mut headers = get_common_headers();
    headers.insert(
        "content-type",
        "application/x-www-form-urlencoded".parse().unwrap(),
    );
    headers.insert(header::COOKIE, cookie.parse().map_err(|e: reqwest::header::InvalidHeaderValue| Box::new(e) as Box<dyn Error>)?);

    let client = ProxyBuilder::new(is_proxy, address).get_client()?;
    let res = client
        .post(url)
        .body(data.to_string())
        .headers(headers)
        .timeout(Duration::from_secs(3))
        .send()
        .await?
        .text()
        .await?;

    Ok(res)
}

#[tauri::command]
async fn get_user_list(
    t: usize,
    sign: &str,
    cookie: &str,
    data: &str,
    is_proxy: bool,
    address: String,
) -> Result<String, String> {
    let res = get_user_list_res(t, sign, cookie, data, is_proxy, address).await;

    match res {
        Ok(s) => Ok(s),
        Err(e) => Err(format!("[get_user_list] 请求失败: {}", e)),
    }
}

async fn get_user_list_res(
    t: usize,
    sign: &str,
    cookie: &str,
    data: &str,
    is_proxy: bool,
    address: String,
) -> Result<String, Box<dyn Error>> {
    let base = dm_config::build_base_url(
        "mtop.damai.wireless.user.customerlist.get",
        dm_config::API_VERSION_USER_LIST,
        t, sign,
    );
    let url = format!(
        "{}&type=originaljson&dataType=json&v=2.0&H5Request=true\
         &AntiCreep=true&forceAntiCreep=true\
         &api=mtop.damai.wireless.user.customerlist.get\
         &hasToast=true&needTbLogin=true&data={}",
        base, data
    );

    let mut headers = get_common_headers();
    headers.insert(
        "content-type",
        "application/x-www-form-urlencoded".parse().unwrap(),
    );
    headers.insert(header::COOKIE, cookie.parse().map_err(|e: reqwest::header::InvalidHeaderValue| Box::new(e) as Box<dyn Error>)?);

    let client = ProxyBuilder::new(is_proxy, address).get_client()?;
    let res = client
        .get(url)
        .headers(headers)
        .timeout(Duration::from_secs(3))
        .send()
        .await?
        .text()
        .await?;

    Ok(res)
}

fn main() {
    tauri::Builder::default()
        .setup(|_app| {
            #[cfg(debug_assertions)] // only include this code on debug builds
            {
                if let Some(window) = _app.get_window("main") {
                    window.open_devtools();
                }
            }
            Ok(())
        })
        .plugin(tauri_plugin_sql::Builder::default().build())
        .invoke_handler(tauri::generate_handler![
            get_product_info,
            get_ticket_list,
            get_ticket_detail,
            create_order,
            get_user_list,
            // version::get_repo_version,
            utils::export_sql_to_txt,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
