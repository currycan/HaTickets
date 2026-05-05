# -*- coding: UTF-8 -*-
"""Environment-check tests for missing-dependency guardrails (issue #32).

These tests assert that running ``mobile/damai_app.py`` in an environment
without ``selenium`` raises ``SystemExit`` with a hint pointing the user at
``poetry install`` rather than letting a raw ``ModuleNotFoundError`` bubble up.

The negative case is run in a **subprocess** so that mutating ``sys.modules``
does not leak into the rest of the test suite (reloading ``mobile.damai_app``
in-process detaches its ``logger`` reference from the one other tests patch).
"""

import subprocess
import sys
import textwrap

import pytest


@pytest.mark.unit
def test_damai_app_exits_with_poetry_hint_when_selenium_missing():
    """Importing mobile.damai_app without selenium raises SystemExit with poetry install hint."""
    # ``sys.modules['selenium'] = None`` is the documented trick that forces a
    # ``ModuleNotFoundError`` on subsequent ``import selenium`` (PEP 328 / docs).
    # We also block the submodule the actual import statement names.
    snippet = textwrap.dedent(
        """
        import sys
        sys.modules["selenium"] = None
        sys.modules["selenium.webdriver"] = None
        sys.modules["selenium.webdriver.common"] = None
        sys.modules["selenium.webdriver.common.by"] = None
        try:
            import mobile.damai_app  # noqa: F401
        except SystemExit as exc:
            print("__GUARD_MSG_START__", end="")
            print(exc, end="")
            print("__GUARD_MSG_END__")
            sys.exit(0)
        # If the import unexpectedly succeeded, exit non-zero so the test fails loudly.
        sys.exit(99)
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        "Subprocess should catch SystemExit cleanly. "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )

    start = result.stdout.find("__GUARD_MSG_START__")
    end = result.stdout.find("__GUARD_MSG_END__")
    assert start != -1 and end != -1, (
        "Subprocess never reached the SystemExit branch — guard missing? "
        f"stdout={result.stdout!r}"
    )

    message = result.stdout[start + len("__GUARD_MSG_START__") : end]
    assert "poetry install" in message, (
        "SystemExit message must mention 'poetry install' for actionable guidance, "
        f"got: {message!r}"
    )
    assert "selenium" in message, (
        "SystemExit message should explicitly name the missing dependency, "
        f"got: {message!r}"
    )
