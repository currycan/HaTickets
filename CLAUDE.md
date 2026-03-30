# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Damai.com (大麦网) ticket purchasing automation system with three platform modules:
- **Web** (`web/`): Selenium + ChromeDriver browser automation (Python)
- **Mobile** (`mobile/`): Appium + UIAutomator2 Android app automation (Python)
- **Desktop** (`desktop/`): Tauri v1 desktop app — Vue 3 frontend + Rust backend calling Damai's mtop API directly

Web and Mobile follow the same flow: config → driver init → navigation → ticket selection → order submission. Desktop bypasses the browser entirely, making HTTP requests to Damai's API from the Rust backend via Tauri commands.

Performance is critical — this is competitive ticket-grabbing where milliseconds matter.

## Prerequisites

- **Python**: ^3.8 + Poetry
- **Web**: Chrome browser (ChromeDriver auto-installed by `web/check_environment.py`)
- **Mobile**: Android device/emulator + Appium 3.1+ + Node.js 20.19+
- **Desktop**: Node.js 20+ + Rust toolchain + Yarn

## Commands

```bash
# === Python (Web & Mobile modules) ===
poetry install
poetry run test                              # run all tests (coverage auto-enabled)
poetry run pytest tests/test_setup_validation.py   # single file
poetry run pytest -k "test_name"             # single test
poetry run pytest -m unit                    # by marker (unit | integration | slow)

# Environment check (Web)
web/scripts/check_environment.sh

# Mobile: start Appium then run
mobile/scripts/start_appium.sh
mobile/scripts/start_ticket_grabbing.sh

# === Desktop (Tauri app) ===
cd desktop
yarn install                  # install frontend deps
yarn tauri dev                # dev mode (Vite on :1420 + Tauri window)
yarn tauri build              # production build
cargo test --manifest-path src-tauri/Cargo.toml   # Rust tests
```

## Architecture

### Web (`web/`)
- `damai.py` — Entry point: validates config, loads `Config`, orchestrates `Concert`
- `concert.py` — Core automation: Selenium WebDriver lifecycle, multi-session festival support, ticket selection polling loop. Uses `self.status` state machine (0=init, 2=logged in, 3=selecting)
- `config.py` — Config container (URL, users, city, dates, prices, retry count, fast_mode, page_load_delay)
- `session_manager.py` — Cookie-based auth persistence with 24-hour expiry checks
- `ticket_selector.py` — Selects dates/prices/cities/quantities using fuzzy matching and multiple fallback strategies (PC + mobile layouts)
- `user_selector.py` — Selects attendees on order page via four cascading methods (div, checkbox, click, JS)
- `order_submitter.py` — Finds and clicks submit button with text/attribute/CSS/XPath fallbacks
- `check_environment.py` — ChromeDriver auto-detection/installation; called automatically by `Concert.__init__`
- `quick_diagnosis.py` — Diagnoses Chrome/ChromeDriver version mismatches
- `logger.py` — Unified logging (Shanghai timezone, console INFO+ / file DEBUG+)

### Mobile (`mobile/`)
- `damai_app.py` — `DamaiBot` with coordinate-based gesture clicks (faster than element.click()), aggressive timeout tuning, batch coordinate collection
- `config.py` — Mobile config via `load_config()` reading `config.jsonc`
- `item_resolver.py` — Fetches event metadata (name, venue, dates, prices) from item URLs via Damai mobile API
- `prompt_parser.py` — Parses natural-language prompts into structured intent (quantity, date, city, price) with scoring
- `prompt_runner.py` — CLI entrypoint for natural-language ticket discovery and bot invocation
- `logger.py` — Unified logging (Shanghai timezone, console INFO+ / file DEBUG+)

### Shared (`shared/`)
- `config_validator.py` — Validates URL format, non-empty lists, positive integers (used by both web and mobile)
- `xpath_utils.py` — XPath string literal escaping via `concat()` to handle single/double quotes

### Desktop (`desktop/`)
- **Frontend** (`desktop/src/`): Vue 3 + Vuex + Vue Router + Arco Design UI, bundled by Vite
  - Views: `dm.vue` (ticket operations)
  - Components: `dm/` (Form, Product, VisitUser), `common/` (Header, Proxy, Qa, Tip, Update)
  - Store: Vuex with `state.js`, `mutations.js`, `mutation-types.js`
  - `utils/dm/index.js` (18KB) — heaviest utility file: API calls, signing, anti-spider, order param building
  - `sql/` — SQLite schema/queries (via `tauri-plugin-sql`)
- **Backend** (`desktop/src-tauri/src/`): Rust + reqwest (Tauri 1.3) calling Damai's mtop API
  - `main.rs` — Tauri commands: `get_product_info`, `get_ticket_list`, `get_ticket_detail`, `create_order`, `get_user_list`, `export_sql_to_txt`
  - `proxy_builder.rs` — HTTP/SOCKS proxy support for all API requests
  - `utils.rs` — SQLite export utility; `version.rs` — version management
  - All API requests use 3s timeout, spoofed mobile Chrome UA, and anti-crawl headers

### Configuration
- Web: `web/config.json`
- Mobile: `mobile/config.jsonc`
- Desktop: SQLite database (managed via Tauri plugin)

### Tests (`tests/`)
- `conftest.py` — Shared fixtures: `mock_config`, `mock_selenium_driver`, `mock_appium_driver`, `sample_html_response`, `mock_time`, `temp_dir`
- `test_setup_validation.py` — Environment and setup validation tests
- `unit/` and `integration/` — Initialized but currently empty; new tests go here
- Custom markers auto-applied by file location (unit/integration)
- Coverage threshold: 80% (enforced in pyproject.toml, covers `web/` and `mobile/` only)

### Documentation (`docs/`)
- Per-module logic docs: `web-ticket-logic.md`, `mobile-ticket-logic.md`, `desktop-ticket-logic.md`
- `desktop-usage-guide.md` — Detailed Tauri usage guide
- `大麦抢票流程.drawio` — Visual flow diagram

### CI/CD
- `.github/workflows/release.yml` — Tag-triggered (`v*`) cross-platform Tauri build (macOS/Linux/Windows) with GitHub Release upload

## Key Design Decisions
- Desktop module calls Damai's mtop API directly from Rust (no browser overhead) — the fastest path
- Mobile uses coordinate-based gesture clicks over element.click() for speed
- Proxy support is first-class in the Desktop module (`ProxyBuilder` wraps reqwest with HTTP/SOCKS proxy)
- ChromeDriver auto-detection and auto-installation to prevent version mismatch (Web)
- Cookie persistence for Web login to avoid repeated manual auth
- `fast_mode` config flag reduces polling intervals in Web module
