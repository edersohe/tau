"""Tests for ``tau_coding.tui.suspend`` — the TUI's suspend-to-command primitive.

The module is general: any component can use ``suspend_to_command`` to run an
external command while the TUI is suspended. Editors use it with a staging
file (``$EDITOR <staging>``); full-screen TUI tools (``lazygit``, ``jj``) and
shell commands use it without one. These tests cover the helper in
isolation — Textual-driver tests live in ``test_tui_suspend_editor.py``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest

from tau_coding.tui.suspend import (
    EDITOR_ENV_KEY,
    STAGING_DEFAULT_SUFFIX,
    STAGING_FILE_PREFIX,
    SuspendResult,
    read_staging_file,
    resolve_command,
    resolve_editor_command,
    suspend_to_command,
    suspend_to_editor,
    write_staging_file,
)


class TestResolveCommand:
    """`resolve_command` parses any ``$KEY`` into argv tokens."""

    def test_returns_none_when_env_key_unset(self) -> None:
        assert resolve_command("FOO_BAR_BAZ_QUUX") is None

    def test_returns_none_when_env_key_empty(self) -> None:
        assert resolve_command("FOO", environ={"FOO": ""}) is None

    def test_returns_none_when_env_key_whitespace(self) -> None:
        assert resolve_command("FOO", environ={"FOO": "   "}) is None

    def test_returns_single_token_for_simple_command(self) -> None:
        assert resolve_command("FOO", environ={"FOO": "lazygit"}) == ["lazygit"]

    def test_returns_multiple_tokens_for_command_with_args(self) -> None:
        assert resolve_command("FOO", environ={"FOO": "jj status"}) == [
            "jj",
            "status",
        ]

    def test_parses_quoted_args(self) -> None:
        assert resolve_command("FOO", environ={"FOO": 'lazygit --git-dir "/path with spaces"'}) == [
            "lazygit",
            "--git-dir",
            "/path with spaces",
        ]

    def test_trims_surrounding_whitespace(self) -> None:
        assert resolve_command("FOO", environ={"FOO": "  lazygit  "}) == ["lazygit"]

    def test_falls_back_to_single_token_for_invalid_quoting(self) -> None:
        # An unterminated quote would normally raise shlex.ValueError; we
        # degrade to one literal token so the user can fix their config
        # without blocking the suspend entirely.
        assert resolve_command("FOO", environ={"FOO": 'lazygit "unterminated'}) == [
            'lazygit "unterminated'
        ]

    def test_uses_os_environ_when_no_mapping_supplied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FOO_TEST_KEY", "nvim")
        assert resolve_command("FOO_TEST_KEY") == ["nvim"]

    def test_does_not_mutate_os_environ(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FOO_TEST_KEY_MUTATION", raising=False)
        assert resolve_command("FOO_TEST_KEY_MUTATION") is None
        assert "FOO_TEST_KEY_MUTATION" not in __import__("os").environ


class TestResolveEditorCommand:
    """`resolve_editor_command` is sugar for ``resolve_command("EDITOR")``."""

    def test_returns_none_when_editor_unset(self) -> None:
        assert resolve_editor_command(environ={}) is None

    def test_returns_single_token_for_vim(self) -> None:
        assert resolve_editor_command(environ={EDITOR_ENV_KEY: "vim"}) == ["vim"]

    def test_returns_multiple_tokens(self) -> None:
        assert resolve_editor_command(environ={EDITOR_ENV_KEY: "code --wait"}) == [
            "code",
            "--wait",
        ]


class TestStagingFileRoundtrip:
    """`write_staging_file` and `read_staging_file` roundtrip text."""

    def test_roundtrip_preserves_text(self, tmp_path: Path) -> None:
        path = write_staging_file("hello world", directory=tmp_path)

        assert read_staging_file(path) == "hello world"

    def test_roundtrip_preserves_multiline_text(self, tmp_path: Path) -> None:
        text = "first line\nsecond line\n\nlast line\n"
        path = write_staging_file(text, directory=tmp_path)

        assert read_staging_file(path) == text

    def test_roundtrip_preserves_unicode(self, tmp_path: Path) -> None:
        text = "tau · 灵感 — 🚀"
        path = write_staging_file(text, directory=tmp_path)

        assert read_staging_file(path) == text

    def test_roundtrip_preserves_empty_text(self, tmp_path: Path) -> None:
        path = write_staging_file("", directory=tmp_path)

        assert read_staging_file(path) == ""

    def test_default_suffix_is_markdown(self, tmp_path: Path) -> None:
        path = write_staging_file("body", directory=tmp_path)

        assert path.suffix == STAGING_DEFAULT_SUFFIX
        assert path.suffix == ".md"

    def test_custom_suffix_is_honored(self, tmp_path: Path) -> None:
        path = write_staging_file("{}", directory=tmp_path, suffix=".json")

        assert path.suffix == ".json"

    def test_default_prefix_marks_tau_owned(self, tmp_path: Path) -> None:
        path = write_staging_file("body", directory=tmp_path)

        assert path.name.startswith(STAGING_FILE_PREFIX)

    def test_staging_file_survives_after_write(self, tmp_path: Path) -> None:
        # Tools that rewrite the staging file in place depend on the path
        # remaining valid between write and read.
        path = write_staging_file("body", directory=tmp_path)

        assert path.exists()


class FakeCompletedProcess:
    """A minimal stand-in for ``subprocess.CompletedProcess`` for testing."""

    returncode: int = 0


class _RecordingSuspend:
    """Track `__call__` and the lifecycle of the context it returns."""

    def __init__(self) -> None:
        self.call_count = 0
        self.entered = 0
        self.exited = 0

    def __call__(self):
        self.call_count += 1

        @contextmanager
        def cm():
            self.entered += 1
            try:
                yield
            finally:
                self.exited += 1

        return cm()


class TestSuspendToCommand:
    """`suspend_to_command` is the general primitive used by every component."""

    def test_empty_command_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty command"):
            suspend_to_command(_RecordingSuspend(), command=[])

    def test_returns_none_final_text_when_no_initial_text(self) -> None:
        captured: list[Sequence[str]] = []

        def fake_runner(command, *, check=False):  # type: ignore[no-untyped-def]
            captured.append(command)
            return FakeCompletedProcess()

        result = suspend_to_command(
            _RecordingSuspend(),
            command=["lazygit"],
            runner=fake_runner,
        )

        assert isinstance(result, SuspendResult)
        assert result.final_text is None
        assert captured == [["lazygit"]]

    def test_no_staging_file_is_written_when_initial_text_is_none(self, tmp_path: Path) -> None:
        # When initial_text is None the command runs as-is (lazygit, jj, …).
        # No file should appear in the staging directory.
        staging_dir = tmp_path / "stage"
        staging_dir.mkdir()

        def fake_runner(command, *, check=False):  # type: ignore[no-untyped-def]
            assert list(staging_dir.iterdir()) == []
            return FakeCompletedProcess()

        suspend_to_command(
            _RecordingSuspend(),
            command=["jj", "status"],
            runner=fake_runner,
            staging_directory=staging_dir,
        )

        assert list(staging_dir.iterdir()) == []

    def test_writes_staging_then_reads_back(self, tmp_path: Path) -> None:
        def fake_runner(command, *, check=False):  # type: ignore[no-untyped-def]
            staging_path = Path(command[-1])
            staging_path.write_text("edited body", encoding="utf-8")
            return FakeCompletedProcess()

        result = suspend_to_command(
            _RecordingSuspend(),
            command=["vim"],
            initial_text="initial body",
            runner=fake_runner,
            staging_directory=tmp_path,
        )

        assert result.final_text == "edited body"

    def test_returns_original_text_when_unchanged(self, tmp_path: Path) -> None:
        def fake_runner(command, *, check=False):  # type: ignore[no-untyped-def]
            return FakeCompletedProcess()

        result = suspend_to_command(
            _RecordingSuspend(),
            command=["true"],
            initial_text="unchanged",
            runner=fake_runner,
            staging_directory=tmp_path,
        )

        assert result.final_text == "unchanged"

    def test_subprocess_runs_inside_suspend_block(self, tmp_path: Path) -> None:
        suspend = _RecordingSuspend()

        def fake_runner(command, *, check=False):  # type: ignore[no-untyped-def]
            return FakeCompletedProcess()

        suspend_to_command(
            suspend,
            command=["vim"],
            initial_text="body",
            runner=fake_runner,
            staging_directory=tmp_path,
        )

        # Order matters: the suspend block is entered, the runner runs, the
        # block exits. Anything else would mean the editor ran outside the
        # terminal-restore window.
        assert suspend.call_count == 1
        assert suspend.entered == 1
        assert suspend.exited == 1

    def test_command_receives_staging_path_when_initial_text_provided(self, tmp_path: Path) -> None:
        captured_command: list[str] = []

        def fake_runner(command, *, check=False):  # type: ignore[no-untyped-def]
            captured_command[:] = list(command)
            return FakeCompletedProcess()

        suspend_to_command(
            _RecordingSuspend(),
            command=["nvim", "--clean"],
            initial_text="body",
            runner=fake_runner,
            staging_directory=tmp_path,
        )

        # Order matters: argv tokens from the call, then the staging path.
        assert captured_command[:2] == ["nvim", "--clean"]
        assert Path(captured_command[-1]).suffix == ".md"
        assert Path(captured_command[-1]).exists()

    def test_custom_staging_suffix_is_used(self, tmp_path: Path) -> None:
        captured_command: list[str] = []

        def fake_runner(command, *, check=False):  # type: ignore[no-untyped-def]
            captured_command[:] = list(command)
            return FakeCompletedProcess()

        suspend_to_command(
            _RecordingSuspend(),
            command=["jq"],
            initial_text="{}",
            staging_suffix=".json",
            runner=fake_runner,
            staging_directory=tmp_path,
        )

        assert Path(captured_command[-1]).suffix == ".json"

    def test_default_runner_invokes_subprocess_run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The default runner is subprocess.run; override it to assert the
        # orchestration falls back to it when no runner is injected.
        invoked: list[Sequence[str]] = []

        def spy_run(command, *, check=False):  # type: ignore[no-untyped-def]
            invoked.append(command)
            # Simulate the editor rewriting the staging file.
            Path(command[-1]).write_text("spy-edited", encoding="utf-8")
            return subprocess.CompletedProcess(args=command, returncode=0)

        monkeypatch.setattr("tau_coding.tui.suspend.subprocess.run", spy_run)

        result = suspend_to_command(
            _RecordingSuspend(),
            command=["true"],
            initial_text="initial",
            staging_directory=tmp_path,
        )

        assert len(invoked) == 1
        assert result.final_text == "spy-edited"

    def test_returncode_propagates(self, tmp_path: Path) -> None:
        def fake_runner(command, *, check=False):  # type: ignore[no-untyped-def]
            return subprocess.CompletedProcess(args=command, returncode=42)

        result = suspend_to_command(
            _RecordingSuspend(),
            command=["false"],
            initial_text="body",
            runner=fake_runner,
            staging_directory=tmp_path,
        )

        assert result.returncode == 42

    def test_missing_staging_file_raises(self, tmp_path: Path) -> None:
        def fake_runner(command, *, check=False):  # type: ignore[no-untyped-def]
            Path(command[-1]).unlink()  # tool removed the file
            return FakeCompletedProcess()

        with pytest.raises(FileNotFoundError):
            suspend_to_command(
                _RecordingSuspend(),
                command=["rm"],
                initial_text="body",
                runner=fake_runner,
                staging_directory=tmp_path,
            )


class TestSuspendToEditor:
    """`suspend_to_editor` is the editor-sugar layer over `suspend_to_command`."""

    def test_returns_none_when_editor_unset(self, tmp_path: Path) -> None:
        assert suspend_to_editor(_RecordingSuspend(), "initial", staging_directory=tmp_path) is None

    def test_returns_none_when_editor_empty(self, tmp_path: Path) -> None:
        assert (
            suspend_to_editor(
                _RecordingSuspend(),
                "initial",
                staging_directory=tmp_path,
            )
            is None
        )

    def test_writes_staging_then_reads_back(self, tmp_path: Path) -> None:
        def fake_runner(command, *, check=False):  # type: ignore[no-untyped-def]
            Path(command[-1]).write_text("edited body", encoding="utf-8")
            return FakeCompletedProcess()

        result = suspend_to_editor(
            _RecordingSuspend(),
            "initial body",
            environ={EDITOR_ENV_KEY: "vim"},
            runner=fake_runner,
            staging_directory=tmp_path,
        )

        assert result == "edited body"

    def test_returns_original_text_when_unchanged(self, tmp_path: Path) -> None:
        def fake_runner(command, *, check=False):  # type: ignore[no-untyped-def]
            return FakeCompletedProcess()

        result = suspend_to_editor(
            _RecordingSuspend(),
            "unchanged",
            environ={EDITOR_ENV_KEY: "true"},
            runner=fake_runner,
            staging_directory=tmp_path,
        )

        assert result == "unchanged"
