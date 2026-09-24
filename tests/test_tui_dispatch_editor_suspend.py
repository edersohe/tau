"""Direct tests for ``TauTuiApp._dispatch_editor_suspend``.

The dispatcher is the single seam between every internal ``TextArea``
editor and the suspend mechanism. These tests pin its contract:
``$EDITOR`` set runs external (falling back to internal on
missing-command); ``$EDITOR`` unset opens internal; ``on_unchanged``
fires when the editor returns text equal to the input. The
integration tests in ``test_tui_suspend_editor.py`` exercise the same
contract through the prompt, ``/prompts``, and sidebar call sites;
this file locks the dispatcher contract directly so future surfaces
(/lazygit, /jj, …) can build on it.
"""

from __future__ import annotations

import pytest

from tau_coding.tui import app as tui_app
from tau_coding.tui.app import PromptEditorScreen, PromptInput, TauTuiApp
from tau_coding.tui.config import TuiSettings
from test_tui_app import FakeSession


def _settings() -> TuiSettings:
    return TuiSettings()


def _app() -> TauTuiApp:
    return TauTuiApp(FakeSession(), tui_settings=_settings())


class TestDispatchEditorSuspend:
    """``_dispatch_editor_suspend`` is the unified editor-or-internal seam."""

    @pytest.mark.anyio
    async def test_editor_unset_opens_internal_editor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("EDITOR", raising=False)
        app = _app()
        async with app.run_test() as pilot:
            await pilot.pause()

            opened_internal: list[bool] = []

            def open_internal(on_done):  # type: ignore[no-untyped-def]
                opened_internal.append(True)
                app.push_screen(
                    PromptEditorScreen("fallback body"),
                    callback=lambda result: on_done(result),
                )

            applied: list[str] = []

            def apply_external(new_text: str) -> None:
                applied.append(new_text)

            def fake_suspend_to_editor(*args, **kwargs):  # type: ignore[no-untyped-def]
                raise AssertionError("suspend_to_editor must not run when $EDITOR is unset")

            monkeypatch.setattr(tui_app, "suspend_to_editor", fake_suspend_to_editor)
            app._dispatch_editor_suspend(
                "initial",
                open_internal=open_internal,
                apply_external=apply_external,
            )
            await pilot.pause()
            assert isinstance(app.screen, PromptEditorScreen)
            app.screen.dismiss("polished body")
            await pilot.pause()

        assert opened_internal == [True]
        assert applied == ["polished body"]

    @pytest.mark.anyio
    async def test_editor_set_calls_apply_external_with_changes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EDITOR", "vim")
        app = _app()
        async with app.run_test() as pilot:
            await pilot.pause()

            opened_internal: list[bool] = []

            def open_internal(on_done):  # type: ignore[no-untyped-def]
                opened_internal.append(True)

            applied: list[str] = []

            def apply_external(new_text: str) -> None:
                applied.append(new_text)

            def fake_suspend_to_editor(*args, **kwargs):  # type: ignore[no-untyped-def]
                return "edited body"

            monkeypatch.setattr(tui_app, "suspend_to_editor", fake_suspend_to_editor)
            app._dispatch_editor_suspend(
                "initial body",
                open_internal=open_internal,
                apply_external=apply_external,
            )
            await pilot.pause()

        assert opened_internal == []
        assert applied == ["edited body"]

    @pytest.mark.anyio
    async def test_editor_unchanged_text_skips_apply_external(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EDITOR", "true")
        app = _app()
        async with app.run_test() as pilot:
            await pilot.pause()

            applied: list[str] = []

            def apply_external(new_text: str) -> None:
                applied.append(new_text)

            on_unchanged_called: list[bool] = []

            def on_unchanged() -> None:
                on_unchanged_called.append(True)

            def fake_suspend_to_editor(*args, **kwargs):  # type: ignore[no-untyped-def]
                return "unchanged body"  # editor saved nothing

            monkeypatch.setattr(tui_app, "suspend_to_editor", fake_suspend_to_editor)
            app._dispatch_editor_suspend(
                "unchanged body",
                open_internal=lambda on_done: None,
                apply_external=apply_external,
                on_unchanged=on_unchanged,
            )

        assert applied == []
        assert on_unchanged_called == [True]

    @pytest.mark.anyio
    async def test_missing_command_falls_back_to_internal_editor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EDITOR", "definitely-not-installed-xyz")
        app = _app()
        async with app.run_test() as pilot:
            await pilot.pause()

            opened_internal: list[bool] = []
            applied: list[str] = []

            def open_internal(on_done):  # type: ignore[no-untyped-def]
                opened_internal.append(True)
                app.push_screen(
                    PromptEditorScreen("fallback body"),
                    callback=lambda result: on_done(result),
                )

            def apply_external(new_text: str) -> None:
                applied.append(new_text)

            def fake_suspend_to_editor(*args, **kwargs):  # type: ignore[no-untyped-def]
                raise FileNotFoundError(2, "No such file", "definitely-not-installed-xyz")

            monkeypatch.setattr(tui_app, "suspend_to_editor", fake_suspend_to_editor)
            app._dispatch_editor_suspend(
                "initial",
                open_internal=open_internal,
                apply_external=apply_external,
            )
            await pilot.pause()

        assert opened_internal == [True]
        assert applied == []  # user hasn't saved the fallback yet

    @pytest.mark.anyio
    async def test_generic_exception_notifies_and_does_not_open_internal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EDITOR", "vim")
        app = _app()
        async with app.run_test() as pilot:
            await pilot.pause()

            opened_internal: list[bool] = []

            def open_internal(on_done):  # type: ignore[no-untyped-def]
                opened_internal.append(True)

            def fake_suspend_to_editor(*args, **kwargs):  # type: ignore[no-untyped-def]
                raise RuntimeError("editor crashed")

            monkeypatch.setattr(tui_app, "suspend_to_editor", fake_suspend_to_editor)
            app._dispatch_editor_suspend(
                "initial",
                open_internal=open_internal,
                apply_external=lambda new_text: None,
            )

        # Generic failures (not FileNotFoundError) should NOT silently fall
        # back to the internal editor; the dispatcher notifies and the
        # user is back in the chat with no apply.
        assert opened_internal == []

    @pytest.mark.anyio
    async def test_prompt_action_suspend_editor_uses_dispatcher(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Sanity check: the prompt's Ctrl+E binding goes through the
        # dispatcher, not the legacy prompt-only path.
        monkeypatch.setenv("EDITOR", "vim")
        app = _app()
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one(PromptInput)
            prompt.focus()
            prompt.text = "original"

            def fake_suspend_to_editor(*args, **kwargs):  # type: ignore[no-untyped-def]
                return "via dispatcher"

            monkeypatch.setattr(tui_app, "suspend_to_editor", fake_suspend_to_editor)
            await pilot.press("ctrl+e")
            await pilot.pause()

        assert prompt.text == "via dispatcher"

    @pytest.mark.anyio
    async def test_prompt_action_suspend_editor_internal_path_uses_dispatcher(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("EDITOR", raising=False)
        app = _app()
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one(PromptInput)
            prompt.focus()
            prompt.text = "fallback body"

            app.action_suspend_editor()
            await pilot.pause()

            # The dispatcher routed to the internal modal.
            assert isinstance(app.screen, PromptEditorScreen)
            app.screen.dismiss("polished body")
            await pilot.pause()

        assert prompt.text == "polished body"

    @pytest.mark.anyio
    async def test_internal_editor_cancel_does_not_apply(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The dispatcher wraps apply_external so None (cancel) is ignored.
        monkeypatch.delenv("EDITOR", raising=False)
        app = _app()
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one(PromptInput)
            prompt.text = "original"

            app.action_suspend_editor()
            await pilot.pause()
            app.screen.dismiss(None)
            await pilot.pause()

        assert prompt.text == "original"
