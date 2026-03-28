# Desktop URL Security Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove the highest-risk URL-handling issues in the `desktop` app: silent proxy fallback, unsafe remote HTTP content, brittle Damai URL parsing, and unconditional remote Baxia script loading.

**Architecture:** Keep all Damai API traffic pinned to fixed Rust-side hosts, and treat frontend URLs as parse-only input that must be normalized before use. Move proxy validation to Rust so the transport decision is authoritative, replace embedded remote pages with local UI, and lazy-load Baxia only when the order flow actually needs it.

**Tech Stack:** Tauri v1, Rust, reqwest, Vue 3, Arco Design, Vite

---

## Constraints

- The `desktop` package does not currently have a JS unit test runner.
- Prefer Rust unit tests for transport/security logic.
- For pure frontend URL parsing, use a tiny repeatable Node smoke script instead of introducing Vitest in this pass.
- Do not redesign order payload generation in this pass; keep scope on URL/security hardening.

## Rollout Order

1. Fix Damai URL parsing so the UI stops accepting arbitrary hosts and starts extracting the real `id` field.
2. Make proxy validation authoritative in Rust and remove silent direct-connection fallback.
3. Gate proxy usage in the Vue UI so invalid saved proxies cannot leak authenticated traffic.
4. Remove the remote HTTP time webview.
5. Reduce Baxia remote-script exposure and tighten CSP.
6. Update docs and ship with a manual smoke checklist.

### Task 1: Fix Damai Product URL Parsing

**Files:**
- Create: `desktop/scripts/verify-url-parsing.mjs`
- Create: `desktop/src/utils/common/url.js`
- Modify: `desktop/src/utils/common/index.js`
- Modify: `desktop/src/components/dm/Form.vue`
- Test: `desktop/scripts/verify-url-parsing.mjs`

**Step 1: Write the failing smoke test**

Create `desktop/scripts/verify-url-parsing.mjs`:

```js
import assert from "node:assert/strict";
import { extractDamaiItemId } from "../src/utils/common/url.js";

assert.equal(
  extractDamaiItemId("https://detail.damai.cn/item.htm?id=123456"),
  "123456"
);
assert.equal(
  extractDamaiItemId("https://detail.damai.cn/item.htm?itemId=123456"),
  "123456"
);
assert.equal(
  extractDamaiItemId("https://m.damai.cn/shows/item.html?id=123456"),
  "123456"
);
assert.equal(
  extractDamaiItemId("https://example.com/item.htm?id=123456"),
  null
);
assert.equal(extractDamaiItemId("javascript:alert(1)"), null);
```

**Step 2: Run the smoke test and verify it fails**

Run: `cd desktop && node scripts/verify-url-parsing.mjs`
Expected: FAIL because `url.js` does not exist yet and current parsing only looks for `itemId`.

**Step 3: Write the minimal implementation**

Create `desktop/src/utils/common/url.js`:

```js
export function extractDamaiItemId(raw) {
  try {
    const url = new URL(raw);
    if (!["http:", "https:"].includes(url.protocol)) return null;
    if (!(url.hostname === "damai.cn" || url.hostname.endsWith(".damai.cn"))) {
      return null;
    }

    const candidate =
      url.searchParams.get("id") ||
      url.searchParams.get("itemId");

    return /^\d+$/.test(candidate || "") ? candidate : null;
  } catch {
    return null;
  }
}
```

Update `desktop/src/utils/common/index.js` to export the helper, and update `desktop/src/components/dm/Form.vue` to use `extractDamaiItemId(form.url)` instead of `split("html")` and `getQueryString("itemId", search)`.

**Step 4: Run the smoke test and build**

Run: `cd desktop && node scripts/verify-url-parsing.mjs`
Expected: PASS

Run: `cd desktop && yarn build`
Expected: PASS

**Step 5: Commit**

```bash
git add desktop/scripts/verify-url-parsing.mjs desktop/src/utils/common/url.js desktop/src/utils/common/index.js desktop/src/components/dm/Form.vue
git commit -m "fix: validate damai product urls before extracting item id"
```

### Task 2: Make Proxy Validation Authoritative in Rust

**Files:**
- Modify: `desktop/src-tauri/src/proxy_builder.rs`
- Modify: `desktop/src-tauri/src/main.rs`
- Test: `desktop/src-tauri/src/proxy_builder.rs`

**Step 1: Write the failing Rust tests**

Add tests to `desktop/src-tauri/src/proxy_builder.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::ProxyBuilder;

    #[test]
    fn accepts_hostname_proxy() {
        let builder = ProxyBuilder::new(true, "http://proxy.local:7890".into());
        assert!(builder.is_ok());
    }

    #[test]
    fn accepts_ipv6_proxy() {
        let builder = ProxyBuilder::new(true, "socks5://[::1]:1080".into());
        assert!(builder.is_ok());
    }

    #[test]
    fn rejects_unsupported_scheme() {
        let builder = ProxyBuilder::new(true, "ftp://127.0.0.1:21".into());
        assert!(builder.is_err());
    }

    #[test]
    fn invalid_proxy_does_not_fall_back_to_direct_connection() {
        let builder = ProxyBuilder::new(true, "not-a-proxy".into());
        assert!(builder.is_err());
    }
}
```

**Step 2: Run the failing Rust tests**

Run: `cargo test --manifest-path desktop/src-tauri/Cargo.toml proxy_builder -- --nocapture`
Expected: FAIL because `ProxyBuilder::new()` currently returns a builder and silently disables proxying on invalid input.

**Step 3: Write the minimal implementation**

Refactor `desktop/src-tauri/src/proxy_builder.rs` so `ProxyBuilder::new()` returns `Result<ProxyBuilder, String>` when `is_proxy == true` and the address is invalid. Replace the regex-only IPv4 extraction with structured URL parsing:

```rust
pub fn new(is_proxy: bool, address: String) -> Result<ProxyBuilder, String> {
    if !is_proxy {
        return Ok(ProxyBuilder { is_proxy: false, address });
    }

    let url = reqwest::Url::parse(&address).map_err(|e| e.to_string())?;
    match url.scheme() {
        "http" | "https" | "socks4" | "socks5" => {}
        other => return Err(format!("unsupported proxy scheme: {}", other)),
    }

    if url.host_str().is_none() {
        return Err("proxy host is required".into());
    }

    if url.port().is_none() {
        return Err("proxy port is required".into());
    }

    Ok(ProxyBuilder { is_proxy: true, address: url.to_string() })
}
```

Add a real `check_proxy` Tauri command in `desktop/src-tauri/src/main.rs` that validates proxy syntax and client construction without making a remote network call:

```rust
#[tauri::command]
fn check_proxy(proxy: &str) -> Result<String, String> {
    let builder = ProxyBuilder::new(true, proxy.to_string())?;
    builder.get_client().map_err(|e| e.to_string())?;
    Ok("ok".into())
}
```

Update every command in `desktop/src-tauri/src/main.rs` to return a proxy error instead of falling back to direct traffic:

```rust
let client = ProxyBuilder::new(is_proxy, address)
    .map_err(|e| format!("[get_product_info] 无效代理: {}", e))?
    .get_client()?;
```

Register `check_proxy` in `generate_handler!`.

**Step 4: Re-run Rust tests**

Run: `cargo test --manifest-path desktop/src-tauri/Cargo.toml proxy_builder -- --nocapture`
Expected: PASS

**Step 5: Commit**

```bash
git add desktop/src-tauri/src/proxy_builder.rs desktop/src-tauri/src/main.rs
git commit -m "fix: reject invalid proxies instead of silently bypassing them"
```

### Task 3: Gate Proxy Usage in the Vue UI

**Files:**
- Modify: `desktop/src/App.vue`
- Modify: `desktop/src/components/dm/Form.vue`
- Test: build + manual smoke

**Step 1: Reproduce the current broken flow**

Manual check:
- Save an invalid proxy in settings.
- Enable "使用代理".
- Submit the form.

Expected: current app either fails with a missing command (`check_proxy`) or proceeds with misleading state.

**Step 2: Wire the frontend to the real command**

Update `desktop/src/App.vue` so saving a non-empty proxy first calls:

```js
await invoke("check_proxy", { proxy: form.proxy.trim() });
```

Only persist the proxy if validation succeeds.

Update `desktop/src/components/dm/Form.vue` so:
- `proxyStatus` becomes `success` or `error`, not only `validating`
- turning on `isUseProxy` with an invalid saved proxy immediately shows an error
- form submission is blocked when `isUseProxy === true` and `proxy` is empty or invalid
- proxy strings are trimmed before sending to Rust

**Step 3: Build and manually verify**

Run: `cd desktop && yarn build`
Expected: PASS

Manual smoke:
- `http://127.0.0.1:7890` saves successfully
- `socks5://proxy.local:1080` saves successfully
- `ftp://127.0.0.1:21` is rejected
- leaving "使用代理" unchecked still allows normal form submission

**Step 4: Commit**

```bash
git add desktop/src/App.vue desktop/src/components/dm/Form.vue
git commit -m "fix: validate proxy settings before desktop requests"
```

### Task 4: Remove the Remote HTTP Time Webview

**Files:**
- Modify: `desktop/src/components/common/Header.vue`
- Update: `docs/desktop-usage-guide.md`
- Test: build + manual smoke

**Step 1: Verify the insecure behavior**

Manual check:
- Click the current "北京时间" button.

Expected: it opens a `WebviewWindow` pointing to `http://time.tianqi.com/`.

**Step 2: Replace the remote webview with local UI**

Remove `WebviewWindow` usage from `desktop/src/components/common/Header.vue`. Replace it with a local modal or popover that renders system time and refreshes every `100ms`:

```js
const now = ref(Date.now());
let timer;

function showClock() {
  visible.value = true;
  timer = window.setInterval(() => {
    now.value = Date.now();
  }, 100);
}
```

UI copy should say:
- this is the local machine clock
- if the user needs exact synchronization, sync the OS clock before抢票

**Step 3: Build and manually verify**

Run: `cd desktop && yarn build`
Expected: PASS

Manual smoke:
- clicking the button no longer opens any external page
- time updates locally in the app

**Step 4: Commit**

```bash
git add desktop/src/components/common/Header.vue docs/desktop-usage-guide.md
git commit -m "fix: replace remote time webview with local system clock"
```

### Task 5: Lazy-Load Baxia and Tighten CSP

**Files:**
- Modify: `desktop/src/views/dm.vue`
- Modify: `desktop/src/components/dm/Product.vue`
- Modify: `desktop/src/utils/dm/baxia.js`
- Modify: `desktop/src/utils/dm/dm-config.js`
- Modify: `desktop/src-tauri/tauri.conf.json`
- Update: `docs/desktop-ticket-logic.md`

**Step 1: Reproduce the current eager-loading behavior**

Manual check:
- launch the desktop app
- open DevTools network panel

Expected: `awsc.js` / `baxiaCommon.js` are requested immediately on page load, before the user starts the order flow.

**Step 2: Add single-flight lazy loading**

Refactor `desktop/src/utils/dm/baxia.js` to export `ensureBaxiaReady()`:

```js
let baxiaReadyPromise = null;

export function ensureBaxiaReady() {
  if (window.baxiaCommon && window.__baxia__) {
    return Promise.resolve(true);
  }

  if (!baxiaReadyPromise) {
    baxiaReadyPromise = loadBaxiaScript().then(() => {
      if (!initBaxia()) {
        throw new Error("Baxia init failed");
      }
      return true;
    });
  }

  return baxiaReadyPromise;
}
```

Inside `loadBaxiaScript()`:
- reject any non-HTTPS URL
- reject any URL whose hostname is not exactly `g.alicdn.com`
- dedupe script tags by `id`
- keep the existing timeout, but return structured errors

**Step 3: Remove eager loading from the page**

Update `desktop/src/views/dm.vue` so it no longer calls `loadBaxiaScript()` on `onMounted`.

Update `desktop/src/components/dm/Product.vue` so `await ensureBaxiaReady()` runs immediately before `getHeaderUaAndUmidtoken()` in the order-build/order-create path, not before product info fetches.

**Step 4: Tighten CSP**

Update `desktop/src-tauri/tauri.conf.json`:
- keep `script-src` limited to `'self' https://g.alicdn.com`
- keep `connect-src` limited to `'self' https://mtop.damai.cn https://api.github.com`
- do not add any `http:` allowance
- do not widen shell or dialog allowlists during this change

**Step 5: Build and manually verify**

Run: `cd desktop && yarn build`
Expected: PASS

Manual smoke:
- app startup does not request Baxia scripts
- "获取商品信息" still works without Baxia
- starting the order flow loads Baxia once
- Baxia load failure produces a clear user-visible error instead of silent breakage

**Step 6: Commit**

```bash
git add desktop/src/views/dm.vue desktop/src/components/dm/Product.vue desktop/src/utils/dm/baxia.js desktop/src/utils/dm/dm-config.js desktop/src-tauri/tauri.conf.json docs/desktop-ticket-logic.md
git commit -m "refactor: lazy-load baxia and tighten desktop csp"
```

### Task 6: Update Docs and Ship Checklist

**Files:**
- Modify: `README.md`
- Modify: `docs/desktop-ticket-logic.md`
- Modify: `docs/desktop-usage-guide.md`

**Step 1: Update usage docs**

Document:
- accepted Damai URL shapes: `?id=` and `?itemId=`
- Damai-only host restriction
- accepted proxy schemes: `http`, `https`, `socks4`, `socks5`
- invalid proxy behavior: fail closed, never silent direct fallback
- local system clock behavior replacing the remote time page
- Baxia is loaded only during order creation, not at page mount

**Step 2: Run repo-level verification**

Run: `cargo test --manifest-path desktop/src-tauri/Cargo.toml`
Expected: PASS

Run: `cd desktop && yarn build`
Expected: PASS

**Step 3: Manual release smoke**

Manual checklist:
1. `https://detail.damai.cn/item.htm?id=123456` auto-fills `itemId`
2. `https://detail.damai.cn/item.htm?itemId=123456` auto-fills `itemId`
3. `https://example.com/item.htm?id=123456` is rejected
4. invalid proxy strings cannot be saved
5. valid hostname and IPv6 proxies are accepted
6. turning proxy off still allows the form to submit
7. clicking the time button never opens a remote page
8. Baxia loads only when the buy/create-order path starts

**Step 4: Commit**

```bash
git add README.md docs/desktop-ticket-logic.md docs/desktop-usage-guide.md
git commit -m "docs: describe hardened desktop url and proxy behavior"
```

## Non-Goals

- Do not migrate the desktop frontend to a new test runner in this pass.
- Do not redesign the Damai order payload format.
- Do not add automatic update downloads; current work is limited to validation and URL-handling safety.
