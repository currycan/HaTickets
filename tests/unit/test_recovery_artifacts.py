# -*- coding: UTF-8 -*-
"""Tests for mobile.damai_app.recovery_strategies.capture_failure_artifacts.

The helper is exercised standalone (no real device, no real Damai package);
fakes simulate a uiautomator2 device's ``dump_hierarchy()`` and
``screenshot()`` to keep these tests hermetic and fast.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mobile.damai_app.recovery_strategies import (
    _config_hash,
    _slugify_scene,
    capture_failure_artifacts,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeConfig:
    def __init__(self, *, serial="device-A", rush_mode=True, sell_start_time=None):
        self.serial = serial
        self.rush_mode = rush_mode
        self.sell_start_time = sell_start_time


class _FakeImage:
    """Minimal stand-in for a uiautomator2 ``screenshot()`` PIL Image."""

    def __init__(self):
        self.saved_to = None

    def save(self, path):
        self.saved_to = path
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n-fake-")


class _FakeDevice:
    def __init__(self, *, xml="<hierarchy/>", screenshot_kind="image"):
        self._xml = xml
        self._screenshot_kind = screenshot_kind
        self.dump_calls = 0
        self.screenshot_calls = 0

    def dump_hierarchy(self):
        self.dump_calls += 1
        if isinstance(self._xml, BaseException):
            raise self._xml
        return self._xml

    def screenshot(self):
        self.screenshot_calls += 1
        kind = self._screenshot_kind
        if isinstance(kind, BaseException):
            raise kind
        if kind == "image":
            return _FakeImage()
        if kind == "bytes":
            return b"\x89PNG\r\n\x1a\n-fake-bytes"
        if kind == "none":
            return None
        if kind == "weird":
            return object()
        raise AssertionError(f"unhandled fake screenshot kind: {kind}")


class _FakeBot:
    def __init__(self, *, device=None, config=None):
        self.d = device
        self.config = config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_version_detect():
    """Force detect_damai_app_version to return a stable string in tests."""
    with patch(
        "mobile.damai_app.recovery_strategies.detect_damai_app_version",
        return_value="10.6.1",
    ) as mocked:
        yield mocked


# ---------------------------------------------------------------------------
# capture_failure_artifacts — happy paths
# ---------------------------------------------------------------------------


class TestCaptureFailureArtifactsHappyPath:
    def test_writes_xml_png_and_metadata(self, tmp_path: Path, patched_version_detect):
        device = _FakeDevice(xml="<hierarchy>ok</hierarchy>")
        bot = _FakeBot(device=device, config=_FakeConfig())
        result = capture_failure_artifacts(
            bot,
            "run_ticket_grabbing",
            error=RuntimeError("boom"),
            extra={"step": "submit_order"},
            root=tmp_path,
        )

        # All three artifacts produced.
        assert result["xml"] is not None
        assert result["png"] is not None
        assert result["json"] is not None

        xml_path = Path(result["xml"])
        png_path = Path(result["png"])
        json_path = Path(result["json"])

        # File contents land where expected.
        assert xml_path.read_text(encoding="utf-8") == "<hierarchy>ok</hierarchy>"
        assert png_path.read_bytes().startswith(b"\x89PNG")

        meta = json.loads(json_path.read_text(encoding="utf-8"))
        assert meta["scene"] == "run_ticket_grabbing"
        assert meta["device"] == "device-A"
        assert meta["damai_version"] == "10.6.1"
        assert meta["error_type"] == "RuntimeError"
        assert meta["error_message"] == "boom"
        assert meta["screenshot_failed"] is False
        assert meta["step"] == "submit_order"
        assert "config_hash" in meta and len(meta["config_hash"]) == 12

    def test_screenshot_bytes_path_writes_png(
        self, tmp_path: Path, patched_version_detect
    ):
        device = _FakeDevice(screenshot_kind="bytes")
        bot = _FakeBot(device=device, config=_FakeConfig())
        result = capture_failure_artifacts(bot, "scene-X", root=tmp_path)
        assert result["png"] is not None
        assert Path(result["png"]).read_bytes().startswith(b"\x89PNG")

    def test_filename_pattern_includes_timestamp_and_scene(
        self, tmp_path: Path, patched_version_detect
    ):
        bot = _FakeBot(device=_FakeDevice(), config=_FakeConfig())
        result = capture_failure_artifacts(bot, "Run Ticket / Grabbing!", root=tmp_path)
        # Slug is lowercased, non-alnum collapsed to underscores.
        for art in (result["xml"], result["png"], result["json"]):
            stem = Path(art).stem
            assert "run_ticket___grabbing" in stem
            # Timestamp prefix YYYYMMDD-HHMMSS-mmm
            assert stem[8] == "-"
            assert stem[15] == "-"


# ---------------------------------------------------------------------------
# capture_failure_artifacts — degraded paths
# ---------------------------------------------------------------------------


class TestCaptureFailureArtifactsDegraded:
    def test_screenshot_failure_records_flag_and_no_png(
        self, tmp_path: Path, patched_version_detect
    ):
        device = _FakeDevice(screenshot_kind=RuntimeError("camera blocked"))
        bot = _FakeBot(device=device, config=_FakeConfig())
        result = capture_failure_artifacts(bot, "scene-broken-cam", root=tmp_path)

        assert result["xml"] is not None
        assert result["png"] is None
        assert result["json"] is not None

        meta = json.loads(Path(result["json"]).read_text(encoding="utf-8"))
        assert meta["screenshot_failed"] is True
        assert meta["png_path"] is None

    def test_screenshot_returns_none_records_flag(
        self, tmp_path: Path, patched_version_detect
    ):
        device = _FakeDevice(screenshot_kind="none")
        bot = _FakeBot(device=device, config=_FakeConfig())
        result = capture_failure_artifacts(bot, "scene-none", root=tmp_path)
        meta = json.loads(Path(result["json"]).read_text(encoding="utf-8"))
        assert result["png"] is None
        assert meta["screenshot_failed"] is True

    def test_screenshot_unexpected_type_records_flag(
        self, tmp_path: Path, patched_version_detect
    ):
        device = _FakeDevice(screenshot_kind="weird")
        bot = _FakeBot(device=device, config=_FakeConfig())
        result = capture_failure_artifacts(bot, "scene-weird", root=tmp_path)
        meta = json.loads(Path(result["json"]).read_text(encoding="utf-8"))
        assert result["png"] is None
        assert meta["screenshot_failed"] is True

    def test_xml_dump_failure_still_writes_metadata(
        self, tmp_path: Path, patched_version_detect
    ):
        device = _FakeDevice(xml=RuntimeError("uiautomator dead"))
        bot = _FakeBot(device=device, config=_FakeConfig())
        result = capture_failure_artifacts(bot, "scene-no-xml", root=tmp_path)

        assert result["xml"] is None
        assert result["json"] is not None
        meta = json.loads(Path(result["json"]).read_text(encoding="utf-8"))
        assert meta["xml_path"] is None

    def test_no_device_attached_only_metadata_written(
        self, tmp_path: Path, patched_version_detect
    ):
        bot = _FakeBot(device=None, config=_FakeConfig(serial=None))
        result = capture_failure_artifacts(bot, "no-device", root=tmp_path)

        assert result["xml"] is None
        assert result["png"] is None
        assert result["json"] is not None
        meta = json.loads(Path(result["json"]).read_text(encoding="utf-8"))
        assert meta["device"] == "unknown"
        assert meta["screenshot_failed"] is True

    def test_unknown_damai_version_when_detect_returns_none(self, tmp_path: Path):
        bot = _FakeBot(device=_FakeDevice(), config=_FakeConfig())
        with patch(
            "mobile.damai_app.recovery_strategies.detect_damai_app_version",
            return_value=None,
        ):
            result = capture_failure_artifacts(bot, "vmiss", root=tmp_path)
        meta = json.loads(Path(result["json"]).read_text(encoding="utf-8"))
        assert meta["damai_version"] == "unknown"


# ---------------------------------------------------------------------------
# Helpers — _slugify_scene / _config_hash
# ---------------------------------------------------------------------------


class TestSlugifyScene:
    def test_lowercases_and_replaces_non_alphanum(self):
        assert _slugify_scene("Run Ticket!") == "run_ticket"

    def test_collapses_to_default_when_all_punctuation(self):
        assert _slugify_scene("///") == "scene"

    def test_truncates_long_input(self):
        long = "a" * 200
        assert len(_slugify_scene(long)) == 48


class TestConfigHash:
    def test_none_config_returns_constant(self):
        assert _config_hash(None) == "none"

    def test_same_config_produces_same_hash(self):
        cfg = _FakeConfig(serial="X", rush_mode=True)
        h1 = _config_hash(cfg)
        h2 = _config_hash(_FakeConfig(serial="X", rush_mode=True))
        assert h1 == h2
        assert len(h1) == 12

    def test_different_config_produces_different_hash(self):
        a = _config_hash(_FakeConfig(serial="X"))
        b = _config_hash(_FakeConfig(serial="Y"))
        assert a != b

    def test_falls_back_to_repr_when_vars_unavailable(self):
        class NoDict:
            __slots__ = ()

        h = _config_hash(NoDict())
        assert isinstance(h, str) and len(h) == 12
