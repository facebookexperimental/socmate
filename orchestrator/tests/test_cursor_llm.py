# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""
Tests for the ClaudeLLM provider abstraction.

Tests:
- Model name mapping (short names -> CLI model IDs)
- Provider detection (always claude_cli)
- Process registry (register, unregister, kill)
- Popen watchdog (stall detection, timeout, heartbeat)
- --dangerously-skip-permissions flag inclusion
"""

from __future__ import annotations

import subprocess
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.langchain.agents.cursor_llm import (
    ClaudeLLM,
    DEFAULT_MODEL,
    _CLI_MODEL_MAP,
    _detect_provider,
    _resolve_model,
    _register_process,
    _unregister_process,
    _active_processes,
    _active_processes_lock,
    kill_active_cli_processes,
)


class TestModelNameMapping:
    def test_opus_46_maps_correctly(self):
        assert _resolve_model("opus-4.6") == "claude-opus-4-6-20250610"

    def test_sonnet_46_maps_correctly(self):
        assert _resolve_model("claude-sonnet-4-6") == "claude-sonnet-4-6-20250514"

    def test_sonnet_45_maps_correctly(self):
        assert _resolve_model("sonnet-4.5") == "claude-sonnet-4-5-20250514"

    def test_haiku_35_maps_correctly(self):
        assert _resolve_model("haiku-3.5") == "claude-3-5-haiku-20241022"

    def test_unknown_model_passes_through(self):
        assert _resolve_model("custom-model-123") == "custom-model-123"

    def test_all_cli_models_have_mappings(self):
        expected_shorts = ["claude-sonnet-4-6", "sonnet-4.5", "sonnet-4", "opus-4.5", "opus-4.6", "haiku-3.5"]
        for short in expected_shorts:
            assert short in _CLI_MODEL_MAP, f"Missing CLI mapping: {short}"

    def test_default_model_constant(self):
        assert DEFAULT_MODEL in _CLI_MODEL_MAP

    def test_empty_model_falls_back_to_default(self, monkeypatch):
        monkeypatch.delenv("SOCMATE_MODEL", raising=False)
        assert _resolve_model("") == _CLI_MODEL_MAP[DEFAULT_MODEL]

    def test_socmate_model_env_overrides_passed_value(self, monkeypatch):
        monkeypatch.setenv("SOCMATE_MODEL", "haiku-3.5")
        assert _resolve_model("opus-4.6") == "claude-3-5-haiku-20241022"

    def test_socmate_model_env_with_full_id_passes_through(self, monkeypatch):
        monkeypatch.setenv("SOCMATE_MODEL", "claude-some-future-model-99")
        assert _resolve_model("opus-4.6") == "claude-some-future-model-99"

    def test_empty_socmate_model_does_not_override(self, monkeypatch):
        monkeypatch.setenv("SOCMATE_MODEL", "")
        assert _resolve_model("opus-4.6") == "claude-opus-4-6-20250610"


class TestProviderDetection:
    def test_always_returns_claude_cli(self):
        assert _detect_provider() == "claude_cli"


class TestProcessRegistry:
    """Test the active subprocess registry (Fix #11)."""

    def setup_method(self):
        with _active_processes_lock:
            _active_processes.clear()

    def teardown_method(self):
        with _active_processes_lock:
            _active_processes.clear()

    def test_register_and_unregister(self):
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.poll.return_value = None
        mock_proc.pid = 12345

        _register_process(mock_proc)
        tid = threading.get_ident()
        with _active_processes_lock:
            assert tid in _active_processes
            assert _active_processes[tid] is mock_proc

        _unregister_process()
        with _active_processes_lock:
            assert tid not in _active_processes

    def test_kill_active_processes(self):
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.poll.return_value = None  # still running
        mock_proc.pid = 99999

        _register_process(mock_proc)

        killed = kill_active_cli_processes()
        assert killed == 1
        mock_proc.kill.assert_called_once()

        with _active_processes_lock:
            assert len(_active_processes) == 0

    def test_kill_skips_already_exited(self):
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.poll.return_value = 0  # already exited
        mock_proc.pid = 11111

        _register_process(mock_proc)

        killed = kill_active_cli_processes()
        assert killed == 0
        mock_proc.kill.assert_not_called()

    def test_unregister_idempotent(self):
        _unregister_process()
        _unregister_process()


class TestCommandConstruction:
    """Verify the CLI command includes --dangerously-skip-permissions."""

    @patch("orchestrator.langchain.agents.cursor_llm._find_claude_binary")
    def test_dangerously_skip_permissions_in_cmd(self, mock_find):
        mock_find.return_value = "/usr/bin/claude"

        model = ClaudeLLM(model="opus-4.6", timeout=10)

        with patch.object(model, "_run_cli_with_watchdog") as mock_watchdog:
            mock_watchdog.return_value = ("test output", "", 0, 1.0, False, False)
            model._generate_via_cli("system prompt", "hello")

            call_args = mock_watchdog.call_args
            cmd = call_args[0][0]  # first positional arg is cmd
            assert "--dangerously-skip-permissions" in cmd

    @patch("orchestrator.langchain.agents.cursor_llm._find_claude_binary")
    def test_print_mode_flags(self, mock_find):
        mock_find.return_value = "/usr/bin/claude"

        model = ClaudeLLM(model="opus-4.6", timeout=10)

        with patch.object(model, "_run_cli_with_watchdog") as mock_watchdog:
            mock_watchdog.return_value = ("test output", "", 0, 1.0, False, False)
            model._generate_via_cli("system prompt", "hello")

            cmd = mock_watchdog.call_args[0][0]
            assert "-p" in cmd
            assert "--output-format" in cmd
            assert "text" in cmd


class TestWatchdogBehaviour:
    """Test stall detection and timeout in _run_cli_with_watchdog."""

    @patch("orchestrator.langchain.agents.cursor_llm._find_claude_binary")
    def test_timeout_returns_partial_output(self, mock_find):
        """When the hard timeout fires, partial output should be captured."""
        mock_find.return_value = "/usr/bin/echo"

        model = ClaudeLLM(model="opus-4.6", timeout=3)

        result = model._generate_via_cli("system prompt", "hello")
        assert isinstance(result, str)

    @patch("orchestrator.langchain.agents.cursor_llm._find_claude_binary")
    def test_stall_detection_with_short_threshold(self, mock_find):
        """A process that produces no output should be killed by stall detection."""
        mock_find.return_value = "/bin/sleep"

        model = ClaudeLLM(model="opus-4.6", timeout=600)
        model._STALL_THRESHOLD_S = 3
        model._POLL_INTERVAL_S = 0.5

        cmd = ["/bin/sleep", "600"]
        t0 = time.monotonic()

        stdout, stderr, rc, elapsed, timed_out, stalled = (
            model._run_cli_with_watchdog(cmd, "", "/tmp", "test", t0)
        )

        assert stalled is True
        assert timed_out is False
        assert elapsed < 30  # should be killed well before timeout
