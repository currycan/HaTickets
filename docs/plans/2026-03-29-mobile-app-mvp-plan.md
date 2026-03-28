# 2026-03-29 Mobile App MVP Plan

## Goal

Validate whether the `mobile/` Appium route is viable for Damai item `1022398072222`, then define the smallest safe code implementation plan based on real-device findings.

## Environment Proven

- Branch: `mobile-mvp-emulator-20260329`
- Emulator: Android 15, `emulator-5554`
- Appium: `3.2.2`
- Driver: `uiautomator2`
- Damai package: `cn.damai`
- Damai version: `8.10.6`
- Damai APK SHA1: `6cf98edf7662ccf424063746c009183bd15cd688`

## Real MVP Findings

### 1. Automation stack is usable

Verified end-to-end:

- `adb` connected to the emulator
- Appium server accepted sessions
- A real Appium session launched Android `Settings`
- Damai app installed successfully and launched successfully

Conclusion: the Android automation environment is not the blocker anymore.

### 2. Damai app cold start is usable

Observed flow:

1. Launch `cn.damai/.launcher.splash.SplashMainActivity`
2. Android full-screen hint appears once
3. Damai privacy agreement appears once
4. After accepting, app lands on `cn.damai/.homepage.MainActivity`

Homepage source was readable through Appium. Key homepage controls exist:

- city selector
- search entry
- scan entry
- homepage content blocks

Conclusion: app cold-start and homepage recognition are viable.

### 3. Current code assumption is still correct

Current mobile code and docs assume:

- user is already logged in
- user has already opened the target performance detail page
- script starts from the detail page and does not include search/navigation

This assumption is explicitly documented in `docs/mobile-ticket-logic.md`, and the runtime selectors in `mobile/damai_app.py` are detail-page selectors rather than homepage-navigation selectors.

Conclusion: the current implementation boundary is still valid and should not be removed.

### 4. Direct detail deeplink is not stable enough

Tested app-registered routes for item `1022398072222`:

- `https://detail.damai.cn/item.htm?id=1022398072222`
- `https://m.damai.cn/damai/perform/item.html?itemId=1022398072222`

Observed behavior:

- `ProjectDetailActivity` is started by Android
- it briefly becomes the top activity
- it is then paused immediately
- the app returns to `Homepage.MainActivity`

Conclusion: direct deeplink into the target item is currently not reliable enough to be the primary MVP navigation path.

### 5. Homepage search path is not yet reliable

Tried to use the homepage search entry and search by `itemId`.

Observed behavior:

- the flow was interrupted by an `AddItemActivity` launcher/widget-style prompt
- result quality for raw numeric `itemId` search was not proven

Conclusion: homepage search is still exploratory, not yet strong enough to become the default route.

### 6. Repo tests for `mobile` passed

Executed:

```bash
poetry run pytest tests/unit/test_mobile_config.py tests/unit/test_mobile_damai_app.py tests/integration/test_mobile_workflow.py -q --cov-fail-under=0
```

Result:

- `48 passed`

Note:

- the first run failed only because global repository coverage gating counts untouched `web/` modules
- the mobile-focused test set itself passed

## Implementation Decision

Do **not** expand the first implementation into full homepage search or deeplink routing yet.

Instead, keep the current contract:

- manual login
- manual open of target detail page
- automation starts from detail page

This is the fastest route to a stable working MVP.

## Code Plan

### Phase 1: Harden current detail-page flow

Add a small state probe before `run_ticket_grabbing()`:

- detect homepage
- detect privacy agreement dialog
- detect Android/system popups
- detect detail page
- detect order confirm page

Expected outcome:

- fail fast with a precise reason instead of timing out on the wrong page

### Phase 2: Add non-destructive probe mode

Add a `probe_only` or `dry_run` mode:

- verify current page state
- locate purchase button
- locate price area
- locate quantity area
- locate viewer selection area
- stop before final submit

Expected outcome:

- allows repeatable environment validation without risky order submission

### Phase 3: Extract page-state helpers

Split page recognition from purchase actions:

- `is_homepage()`
- `is_detail_page()`
- `is_consent_dialog()`
- `is_order_confirm_page()`
- `dismiss_startup_popups()`

Expected outcome:

- selectors become testable
- retries become state-aware instead of blind

### Phase 4: Defer homepage navigation

Do not implement homepage search/deeplink as the main path yet.

Only revisit after one of these is proven:

- a reliable in-app search flow by title
- a stable app deeplink that stays on `ProjectDetailActivity`

## Recommended Next Code Changes

1. Add a startup probe layer in `mobile/damai_app.py`
2. Add `probe_only` to config and main flow
3. Add popup dismissal for first-run agreement and system interstitials
4. Keep detail-page-first workflow as the default path
5. Update docs to explicitly mark deeplink/search navigation as unsupported for now

## Exit Criteria For Implementation

Implementation is good enough when all of the following are true:

1. Script can tell the user they are on homepage vs detail page vs order page
2. Script can dismiss one-time startup blockers
3. Script can run safely in `probe_only` mode without submitting an order
4. Existing detail-page purchase flow remains intact
