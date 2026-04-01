# 2026-04-01 u2 Migration Failures

## Context

- Branch: `feature/u2-migration-20260331`
- Command:
  - `poetry run pytest tests/unit/test_mobile_config.py tests/unit/test_mobile_damai_app.py -q`

## Failures Captured

1. `tests/unit/test_mobile_config.py::TestMobileConfigInit::test_config_init_stores_all_attributes`
   - Assertion mismatch:
   - expected `cfg.driver_backend == "appium"`
   - actual `cfg.driver_backend == "u2"`

2. Multiple `tests/unit/test_mobile_damai_app.py::TestRunTicketGrabbing::*` failures
   - Runtime error:
   - `抢票过程发生错误: 'Mock' object is not iterable`

3. `tests/unit/test_mobile_damai_app.py::TestSkuInspectionHelpers::test_get_visible_price_options_returns_empty_when_cards_are_not_a_sequence`
   - TypeError:
   - `'Mock' object is not iterable`

## Initial Root Cause Analysis

- New container/query adapters assume iterables in several paths.
- Existing tests intentionally return `Mock()` (non-iterable) for some container APIs; old code tolerated this.
- One config assertion was not updated for new default backend behavior.

## Resolution Plan

1. Update config test expectation to default `u2`.
2. Harden adapter methods to gracefully handle non-list/non-tuple/non-iterable mocks.
3. Restore Appium fallback behavior in price-selection backup path (`self.wait.until`) to keep old tests and behavior stable.
4. Re-run focused tests and then full test suite.

## Additional Failures (2nd Round)

- Command:
  - `poetry run pytest tests/unit tests/integration -q -o addopts=''`

1. `tests/unit/test_mobile_hot_path_benchmark.py` (5 failures)
   - `_fast_check_detail_page` switched to `bot._find_all(...)`, while test helper only mocked `bot.driver.find_elements`.
   - Result: fast-check no longer bypassed in tests, causing assertion mismatches.

2. `tests/integration/test_mobile_workflow.py::TestConfigToBotInit::test_load_config_to_bot_init`
   - Config JSON omitted `driver_backend`, defaulting to `u2` and hitting mocked `uiautomator2.connect`.
   - Mock settings object did not support dict assignment.

## Additional Fixes Applied

1. `_fast_check_detail_page` now:
   - supports both `bot._find_all(...)` and legacy `bot.driver.find_elements(...)`,
   - and safely returns `None` when result is non-iterable.
2. Integration fixture/config updated to set `driver_backend="appium"` where test intends Appium path.
3. `_setup_u2_driver()` made tolerant to non-mapping `settings` objects in mocked environments.
4. Added dedicated adapter coverage tests in:
   - `tests/unit/test_mobile_damai_app_u2_adapter.py`

## Tooling Error (3rd Round)

- Command:
  - `poetry lock --no-update`
- Error:
  - `The option "--no-update" does not exist`
- Root Cause:
  - Local Poetry version does not support this flag.
- Resolution:
  - Use compatible command `poetry lock` to regenerate lock file.

## Final Verification

- `poetry run pytest tests/unit/test_mobile_config.py tests/unit/test_mobile_damai_app.py -q -o addopts=''`
  - `255 passed`
- `poetry run pytest tests/unit/test_mobile_hot_path_benchmark.py tests/integration/test_mobile_workflow.py -q -o addopts=''`
  - `44 passed`
- `poetry run pytest -q -o addopts=''`
  - `897 passed`
- `poetry lock`
  - success, lock file written
- `poetry run pytest`
  - `897 passed`
  - coverage `80.21%` (threshold `>=80%` satisfied)

## Prompt Probe Runtime Errors (4th Round)

- Command:
  - `./mobile/scripts/run_from_prompt.sh --mode probe --yes "给张志涛抢4 月 6 号张杰的北京站演唱会内场门票，票价 1680 元"`

### Failures Captured

1. Environment import failure
   - `ModuleNotFoundError: No module named 'uiautomator2'`

2. Search discovery false negative
   - Logs repeatedly showed:
   - `本屏搜索结果未找到明确匹配项，已扫描: 0 条`
   - even when real search list existed on-device.

3. Click crash after switching to XPath-based u2 results
   - `TypeError: tuple indices must be integers or slices, not str`
   - Source: DeviceXMLElement `rect` is tuple, while click path expected dict.

4. Temporary coverage regression
   - `poetry run pytest` reported coverage `78.95%` (below required `80%`) after adding new runtime logic.

### Root Cause

1. Local virtual env had stale deps before reinstall.
2. u2 adapter used selector existence checks that could be false-positive and child-text extraction that missed XML attribute text.
3. DeviceXMLElement geometry shape differs from Appium element shape.
4. New branches lacked enough targeted unit tests.

### Fixes Applied

1. Reinstalled lockfile deps:
   - `poetry install`
2. Reworked u2 search-result adapter path:
   - `_find_all(By.ID/By.CLASS_NAME)` now prefers XPath result objects.
   - `_container_find_elements(...)` now supports XPath XML node containers.
   - `_read_element_text(...)` now supports XML attribute-based text (`attrib["text"]`) and skips empty early-returns.
3. Stabilized search submission:
   - wait condition switched to real list materialization (`_find_all`) instead of existence-only probe.
   - tab-switch and result wait ordering improved.
4. Normalized tuple-rect handling:
   - `_element_rect(...)` now converts tuple rects to dict form.
5. Added/updated focused tests:
   - `tests/unit/test_mobile_damai_app_u2_adapter.py`
   - `tests/unit/test_mobile_prompt_parser.py`
   - `tests/unit/test_mobile_prompt_runner.py`

### Re-Verification

- `poetry run pytest`
  - `911 passed`
  - coverage `81.12%` (threshold `>=80%` satisfied)
- `./mobile/scripts/run_from_prompt.sh --mode probe --yes "给张志涛抢4 月 6 号张杰的北京站演唱会内场门票，票价 1680 元"`
  - success
  - step timing logs:
    - `搜索页输入并提交关键词` = `1.70s` (manual baseline `6.00s`)
    - `搜索结果扫描并打开目标` = `2.23s` (manual baseline `12.00s`)
