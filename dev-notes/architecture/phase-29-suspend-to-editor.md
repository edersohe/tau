---
title: "Phase 29: Suspend-to-External-Command"
---

Phase 29 introduces a generic `suspend_to_command` primitive that any
component can use to hand the terminal to an external tool while the TUI
is suspended. The editors (`$EDITOR`) build on it; future full-screen
tools (`lazygit`, `jj`, `tig`) and shell commands will build on it the
same way. Every internal `TextArea` editor in Tau defers to the suspend
flow when its underlying command is configured, and falls back to the
in-TUI editor otherwise — through one shared dispatcher.

The implementation lives in:

```text
src/tau_coding/tui/suspend.py
src/tau_coding/tui/app.py
src/tau_coding/tui/config.py
tests/test_tui_suspend.py
tests/test_tui_suspend_editor.py
website/content/reference/keybindings.md
website/content/reference/configuration.md
website/content/guides/tui.md
```

## What was added

- `tau_coding.tui.suspend.resolve_command(env_key, *, environ=None)` — pure
  parser that turns any `$KEY` value into argv tokens (`shlex` semantics,
  graceful fallback on invalid quoting).
- `tau_coding.tui.suspend.resolve_editor_command(*, environ=None)` —
  sugar for `resolve_command("EDITOR")`.
- `tau_coding.tui.suspend.write_staging_file(text, *, suffix, directory)`
  and `read_staging_file(path)` — fsynced UTF-8 staging file with a
  configurable suffix (default `.md`) and `tau-payload-` prefix.
- `tau_coding.tui.suspend.suspend_to_command(app_suspend, *, command,
  initial_text=None, …)` — the general primitive. With `initial_text`
  set it writes a staging file and passes it as the last argument (editor
  flow); with `initial_text=None` it runs the command bare (full-screen
  tool flow). Returns `SuspendResult(returncode, final_text)`.
- `tau_coding.tui.suspend.suspend_to_editor(app_suspend, initial_text, …)`
  — editor sugar over `suspend_to_command`. Returns the new text or `None`
  when `$EDITOR` is unset so callers can fall back.
- `TauTuiApp._dispatch_editor_suspend(initial_text, *, open_internal,
  apply_external, on_unchanged)` — single dispatch method that handles
  the `$EDITOR`-set check, the suspend run, the missing-command fallback,
  the unchanged-text hook, and the generic-exception notification.
- `TuiKeybindings.suspend_editor: str = "ctrl+e"` — new configurable key
  with JSON round-trip support.
- `PromptInput.on_key` route + `PromptInput.action_suspend_editor` for
  the configured key, plus matching entries on the `CompletionActionTarget`
  protocol.
- Internal fallback surfaces:
  - `PromptEditorScreen` (modal) for the prompt suspend path.
  - Existing `PromptTemplateEditorScreen` (modal) for the `/prompts`
    template edit path.
  - Existing `SidebarFileEditor` (main-area widget) for the sidebar file
    click path. The dispatcher's `on_done` callback is intentionally
    unused here because `SidebarFileEditor` owns its own save action.
- `tests/conftest.py` autouse isolation of `$EDITOR` so existing TUI tests
  keep exercising the internal-fallback paths unless they explicitly opt
  into the suspend path.

## Why it exists

The TUI's `TextArea`-based editors (prompt, `/prompts` template, sidebar
files) are great for quick edits but no substitute for a real editor when
content is long. Pi exposes `$EDITOR` as the override: any "edit this"
affordance hands the terminal to the user's configured editor. Tau's prior
Textual frontend had no equivalent, and the previous refactor narrowly
bound the primitive to the editor case (`run_external_editor`), which
made it unusable for non-editor tools (`lazygit`, `jj`, `bash -c …`).

Phase 29 splits the primitive cleanly:

- The general `suspend_to_command` is the only place that talks to the
  Textual driver (`App.suspend()`) and `subprocess.run`. Any component
  can call it.
- The editor-specific `suspend_to_editor` is sugar on top, so the three
  editor call sites stay small.
- `_dispatch_editor_suspend` is the one place that decides "use external
  or open internal" — every internal editor surface uses the same
  dispatch, so the rule lives in one method.

## How Phase 29 supports later phases

```text
Phase 30+ — /lazygit, /jj, /tig
  Reuse `suspend_to_command(app.suspend, command=[...])` to hand the
  terminal to a full-screen VCS tool. The `final_text is None` /
  `returncode` shape carries through unchanged.

Phase 30+ — Persistent editor preference
  A future `tui.json` "default_editor" override can replace the
  `$EDITOR` lookup in `resolve_command` without touching call sites.

Phase 30+ — Diff-before-save for sidebar file edits
  The current sidebar `$EDITOR` path auto-saves on exit; a future phase
  can add an inline diff confirmation modal so concurrent external
  changes are reviewed before the file is overwritten. The same
  `apply_external(new_text)` callback is the seam.
```

## Design rule

Suspending the TUI is a UI affordance that crosses the `App.suspend()`
driver boundary. `tau_coding.tui.suspend` is the only place that runs
external commands while the TUI is suspended and the only place that owns
the staging-file lifecycle; `tau_coding.tui.app` only orchestrates via the
dispatcher. The reusable agent harness in `tau_agent` and the provider
adapters in `tau_ai` do not import `suspend` and gain no dependency on
`subprocess` or `tempfile`.

## Tests

- `tests/test_tui_suspend.py` — 41 tests covering the helper module in
  isolation: `resolve_command` (any `$KEY`, unset, empty, whitespace,
  quoted args, invalid quoting, `os.environ` fallback), `write_staging_file`
  / `read_staging_file` roundtrips and default suffix / prefix,
  `suspend_to_command` with and without `initial_text` (no-staging file
  is written when bare), `subprocess.run` fallback, return-code
  propagation, missing-staging-file error, and `suspend_to_editor`
  unset/empty/unchanged paths.
- `tests/test_tui_suspend_editor.py` — 17 TUI integration tests: Ctrl+E
  dispatch on the prompt, keybinding remap, no-op when `$EDITOR` is
  unset, internal modal open / dismiss / text-replace, external replace
  / no-op / missing-command, `/prompts` template picker suspend + save /
  unchanged / fallback, sidebar file click suspend + save / unchanged /
  fallback, and a TUI-side smoke test of the staging file contract.
- `tests/test_tui_config.py` — covers the new `suspend_editor` default
  (`ctrl+e`) and JSON round-trip.
- `tests/conftest.py` autouse isolation of `$EDITOR` keeps the
  developer's shell environment from changing TUI behavior in any
  existing test.

## Next phase

A natural follow-up is a `/lazygit` slash command (or equivalent) that
uses `suspend_to_command(app.suspend, command=["lazygit"])` to hand the
terminal to the Git TUI without a staging file. That work belongs to a
phase that adds slash commands for full-screen external tools; Phase 29
only ships the primitive and the editor dispatch.
