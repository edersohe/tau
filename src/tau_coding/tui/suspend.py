"""Suspend the TUI so external commands can take the terminal.

The TUI is bound to Textual's alt-screen and key-reading driver, so it
cannot hand the terminal directly to long-running tools. Textual exposes
``App.suspend()`` as a context manager that restores the terminal to its
pre-app cooked mode while a block runs; this module uses that window to
spawn an external command, wait for it to exit, and resume the TUI.

``suspend_to_command`` is the general primitive: any component can use it
to run a command while the TUI is suspended.

- Editors use it with a staging file: ``$EDITOR <staging>`` rewrites the
  staging file, and ``suspend_to_command`` reads it back.
- Full-screen TUI tools (``lazygit``, ``jj``, ``tig``, …) and shell
  commands (``bash -c '...'``) use it without a staging file: the command
  owns the terminal and ``suspend_to_command`` only tracks the return code.

The module deliberately stays small and side-effect-light:
``resolve_command`` is a pure function over an environment mapping, and the
orchestration accepts injected ``app_suspend`` and ``runner`` callables so
tests can substitute fakes without touching the real Textual driver or
subprocess machinery.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

EDITOR_ENV_KEY = "EDITOR"
STAGING_DEFAULT_SUFFIX = ".md"
STAGING_FILE_PREFIX = "tau-payload-"


@dataclass(frozen=True, slots=True)
class SuspendResult:
    """Outcome of suspending the TUI to run an external command.

    ``returncode`` is the command's exit status.
    ``final_text`` is the staging file's contents after the command exits
    when a staging file was used, or ``None`` when the command ran without
    one (e.g. a full-screen TUI tool that manages the terminal itself).
    """

    returncode: int
    final_text: str | None


class _SuspendContext(Protocol):
    """The minimal ``App.suspend`` shape this module depends on."""

    def __call__(self) -> AbstractContextManager[None]: ...


class _SubprocessRunner(Protocol):
    """The minimal ``subprocess.run`` shape this module depends on."""

    def __call__(
        self,
        command: Sequence[str],
        *,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]: ...


def resolve_command(
    env_key: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> Sequence[str] | None:
    """Parse an ``$ENV_KEY`` value into argv tokens, or ``None`` when unset.

    Quoted arguments are honored (``FOO="bar --baz"`` → ``["bar", "--baz"]``).
    A missing, empty, or whitespace-only value returns ``None`` so callers
    can fall back to an internal implementation. Invalid shell quoting is
    treated as a single literal token rather than refusing to launch the
    command at all — the command value is the user's responsibility.
    """
    source = environ if environ is not None else os.environ
    raw = source.get(env_key)
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        tokens = shlex.split(stripped, posix=True)
    except ValueError:
        return [stripped]
    return [token for token in tokens if token] or None


def resolve_editor_command(
    *,
    environ: Mapping[str, str] | None = None,
) -> Sequence[str] | None:
    """Return the argv parsed from ``$EDITOR``, or ``None`` when unset."""
    return resolve_command(EDITOR_ENV_KEY, environ=environ)


def write_staging_file(
    text: str,
    *,
    suffix: str = STAGING_DEFAULT_SUFFIX,
    directory: Path | None = None,
) -> Path:
    """Write ``text`` to a fresh staging file and return its path.

    Atomic-safe: the file is flushed and fsynced before the handle is
    closed so a forked command sees the complete contents even if it opens
    the file with O_RDONLY. The default prefix and suffix help editors
    that pick behaviour by extension (Markdown, JSON, …).
    """
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        prefix=STAGING_FILE_PREFIX,
        suffix=suffix,
        dir=str(directory) if directory is not None else None,
        delete=False,
    ) as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        return Path(handle.name)


def read_staging_file(path: Path) -> str:
    """Read a staging file back as a UTF-8 string."""
    return path.read_text(encoding="utf-8")


def suspend_to_command(
    app_suspend: _SuspendContext,
    *,
    command: Sequence[str],
    initial_text: str | None = None,
    staging_suffix: str = STAGING_DEFAULT_SUFFIX,
    staging_directory: Path | None = None,
    runner: _SubprocessRunner | None = None,
) -> SuspendResult:
    """Suspend the TUI and run ``command``, returning the outcome.

    When ``initial_text`` is provided, write it to a staging file and pass
    the staging file path as the last argument to ``command``. The result's
    ``final_text`` is the staging file's contents after the command exits.
    This is the editor flow: ``$EDITOR <staging>`` reads and rewrites the
    file in place, and the saved text comes back through ``final_text``.

    When ``initial_text`` is ``None``, run ``command`` directly without a
    staging file. This is the full-screen-TUI / shell flow: ``lazygit``,
    ``jj``, or any other tool that manages the terminal itself.
    ``final_text`` is ``None``; ``returncode`` is the only output.

    ``app_suspend`` is the bound ``App.suspend`` method (a context manager
    factory); ``runner`` defaults to ``subprocess.run`` and is overridable
    so tests can substitute a fake that records the command.
    """
    if not command:
        raise ValueError("suspend_to_command requires a non-empty command")
    process_runner = runner if runner is not None else subprocess.run

    if initial_text is None:
        with app_suspend():
            completed = process_runner(list(command), check=False)
        return SuspendResult(returncode=completed.returncode, final_text=None)

    staging_path = write_staging_file(
        initial_text, suffix=staging_suffix, directory=staging_directory
    )
    # The staging path is intentionally not unlinked: most editors rewrite
    # the same inode, and dropping it before the read could erase their save.
    with app_suspend():
        completed = process_runner([*command, str(staging_path)], check=False)
    return SuspendResult(
        returncode=completed.returncode,
        final_text=read_staging_file(staging_path),
    )


def suspend_to_editor(
    app_suspend: _SuspendContext,
    initial_text: str,
    *,
    runner: _SubprocessRunner | None = None,
    staging_directory: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Editor-specific sugar over ``suspend_to_command``.

    Returns the new text when ``$EDITOR`` is configured and runs to
    completion, or ``None`` when ``$EDITOR`` is unset/empty so the caller
    can drop back to an internal editor. The returned text equals the
    original when the editor did not modify the staging file. ``environ``
    defaults to ``os.environ`` and is overridable so tests can drive the
    decision deterministically.
    """
    command = resolve_editor_command(environ=environ)
    if command is None:
        return None
    result = suspend_to_command(
        app_suspend,
        command=command,
        initial_text=initial_text,
        staging_suffix=STAGING_DEFAULT_SUFFIX,
        staging_directory=staging_directory,
        runner=runner,
    )
    return result.final_text
