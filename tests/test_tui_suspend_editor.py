"""End-to-end tests for the TUI's suspend-to-editor wiring.

`tau_coding.tui.suspend` is unit-tested in
`tests/test_tui_suspend.py`; here we cover the Textual-side glue:
the `Ctrl+E` keybinding on the prompt, the dispatch to either an internal
modal editor or the external suspend helper, and the prompt-text replacement
after either path completes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tau_coding.tui import app as tui_app
from tau_coding.tui.app import PromptEditorScreen, PromptInput, TauTuiApp
from tau_coding.tui.config import TuiKeybindings, TuiSettings
from test_tui_app import FakeSession


def _settings(suspend_editor: str = "ctrl+e") -> TuiSettings:
    return TuiSettings(
        keybindings=TuiKeybindings(suspend_editor=suspend_editor),
    )


def _app(tui_settings: TuiSettings | None = None) -> TauTuiApp:
    return TauTuiApp(
        FakeSession(),
        tui_settings=tui_settings or _settings(),
    )


class TestPromptSuspendEditorKeybinding:
    """`Ctrl+E` (or its remapped value) on the prompt opens the suspend action."""

    @pytest.mark.anyio
    async def test_ctrl_e_routes_to_app_action(self) -> None:
        app = _app()

        with pytest.MonkeyPatch.context() as mp:
            captured: list[str] = []

            def spy_action(self_or_none=None) -> None:  # type: ignore[no-untyped-def]
                captured.append("called")

            mp.setattr(tui_app.TauTuiApp, "action_suspend_editor", spy_action)

            async with app.run_test() as pilot:
                await pilot.pause()
                prompt = app.query_one(PromptInput)
                prompt.focus()
                await pilot.press("ctrl+e")
                await pilot.pause()

        assert captured == ["called"]

    @pytest.mark.anyio
    async def test_remapped_suspend_editor_key_routes_to_app_action(self) -> None:
        app = _app(_settings(suspend_editor="f4"))

        with pytest.MonkeyPatch.context() as mp:
            captured: list[str] = []

            def spy_action(self_or_none=None) -> None:  # type: ignore[no-untyped-def]
                captured.append("called")

            mp.setattr(tui_app.TauTuiApp, "action_suspend_editor", spy_action)

            async with app.run_test() as pilot:
                await pilot.pause()
                prompt = app.query_one(PromptInput)
                prompt.focus()
                await pilot.press("f4")
                await pilot.pause()

        assert captured == ["called"]

    @pytest.mark.anyio
    async def test_unrelated_keys_do_not_trigger_suspend(self) -> None:
        app = _app()

        with pytest.MonkeyPatch.context() as mp:
            captured: list[str] = []

            def spy_action(self_or_none=None) -> None:  # type: ignore[no-untyped-def]
                captured.append("called")

            mp.setattr(tui_app.TauTuiApp, "action_suspend_editor", spy_action)

            async with app.run_test() as pilot:
                await pilot.pause()
                prompt = app.query_one(PromptInput)
                prompt.focus()
                prompt.text = "abc"
                await pilot.press("a")
                await pilot.pause()

        assert captured == []


class TestActionSuspendEditorExternal:
    """`action_suspend_editor` uses `suspend_to_editor` when `$EDITOR` is set."""

    @pytest.mark.anyio
    async def test_external_editor_replaces_prompt_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EDITOR", "vim")
        app = _app()
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one(PromptInput)
            prompt.text = "original body"
            await pilot.pause()

            called_with: list[str] = []

            def fake_run(
                app_suspend, initial_text, *, environ=None, runner=None, staging_directory=None
            ):  # type: ignore[no-untyped-def]
                called_with.append(initial_text)
                return "rewritten body"

            monkeypatch.setattr(tui_app, "suspend_to_editor", fake_run)

            app.action_suspend_editor()
            await pilot.pause()

        assert called_with == ["original body"]
        assert prompt.text == "rewritten body"

    @pytest.mark.anyio
    async def test_external_editor_unchanged_text_is_a_noop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EDITOR", "true")
        app = _app()
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one(PromptInput)
            prompt.text = "stable body"
            await pilot.pause()

            def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
                return "stable body"  # editor saved nothing

            monkeypatch.setattr(tui_app, "suspend_to_editor", fake_run)

            app.action_suspend_editor()
            await pilot.pause()

        assert prompt.text == "stable body"

    @pytest.mark.anyio
    async def test_external_editor_missing_command_is_handled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EDITOR", "definitely-not-installed-xyz")
        app = _app()
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one(PromptInput)
            prompt.text = "stable body"
            await pilot.pause()

            def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
                raise FileNotFoundError(2, "No such file", "definitely-not-installed-xyz")

            monkeypatch.setattr(tui_app, "suspend_to_editor", fake_run)

            app.action_suspend_editor()
            await pilot.pause()

        # Text unchanged because the editor never ran successfully.
        assert prompt.text == "stable body"


class TestActionSuspendEditorInternal:
    """`action_suspend_editor` falls back to the internal modal editor."""

    @pytest.mark.anyio
    async def test_internal_fallback_opens_modal_when_editor_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("EDITOR", raising=False)
        app = _app()
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one(PromptInput)
            prompt.text = "fallback body"
            await pilot.pause()

            app.action_suspend_editor()
            await pilot.pause()

            assert isinstance(app.screen, PromptEditorScreen)

    @pytest.mark.anyio
    async def test_internal_fallback_dismiss_with_none_leaves_prompt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("EDITOR", raising=False)
        app = _app()
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one(PromptInput)
            prompt.text = "fallback body"
            await pilot.pause()

            app.action_suspend_editor()
            await pilot.pause()

            app.screen.dismiss(None)
            await pilot.pause()

        assert prompt.text == "fallback body"

    @pytest.mark.anyio
    async def test_internal_fallback_dismiss_with_text_replaces_prompt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("EDITOR", raising=False)
        app = _app()
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one(PromptInput)
            prompt.text = "fallback body"
            await pilot.pause()

            app.action_suspend_editor()
            await pilot.pause()

            app.screen.dismiss("polished body")
            await pilot.pause()

        assert prompt.text == "polished body"

    @pytest.mark.anyio
    async def test_internal_fallback_unchanged_text_is_noop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("EDITOR", raising=False)
        app = _app()
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one(PromptInput)
            prompt.text = "fallback body"
            await pilot.pause()

            app.action_suspend_editor()
            await pilot.pause()

            app.screen.dismiss("fallback body")  # user saved without changes
            await pilot.pause()

        assert prompt.text == "fallback body"


class TestEditorSuspendLifecycle:
    """Staging and file plumbing side-effects of the suspend path."""

    @pytest.mark.anyio
    async def test_staging_file_roundtrips_through_helper(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Direct exercise of the staging roundtrip used by the helper; this
        # keeps a TUI test that proves the staging contract still matches
        # what the TUI action depends on (UTF-8 text, .md suffix).
        from tau_coding.tui.suspend import read_staging_file, write_staging_file

        path = write_staging_file("hello\nworld", directory=tmp_path)

        assert path.suffix == ".md"
        assert read_staging_file(path) == "hello\nworld"


class TestPromptTemplateEditorSuspend:
    """`/prompts` Ctrl+E defers to `$EDITOR` when set, falls back otherwise."""

    @pytest.mark.anyio
    async def test_editor_set_saves_template_through_external_editor(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from tau_coding.prompt_templates import PromptTemplate

        monkeypatch.setenv("EDITOR", "vim")
        template_path = tmp_path / "review.md"
        template_path.write_text("Original prompt.\n", encoding="utf-8")
        session = FakeSession()
        session.prompt_templates = (
            PromptTemplate(
                name="review",
                path=template_path,
                content="Original prompt.",
                description="Inspect changes",
            ),
        )
        app = TauTuiApp(session)

        def fake_run(
            app_suspend, initial_text, *, environ=None, runner=None, staging_directory=None
        ):  # type: ignore[no-untyped-def]
            return "Updated prompt.\n"

        monkeypatch.setattr(tui_app, "suspend_to_editor", fake_run)

        async with app.run_test() as pilot:
            prompt = app.query_one(PromptInput)
            prompt.value = "/prompts"
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("ctrl+e")
            await pilot.pause()

            # The template was written through the external editor.
            assert template_path.read_text(encoding="utf-8") == "Updated prompt.\n"
            # The picker reopens so the user can pick another template.
            from tau_coding.tui.app import PromptTemplatePickerScreen

            assert isinstance(app.screen, PromptTemplatePickerScreen)

    @pytest.mark.anyio
    async def test_editor_set_unchanged_text_reopens_picker(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from tau_coding.prompt_templates import PromptTemplate

        monkeypatch.setenv("EDITOR", "true")
        template_path = tmp_path / "review.md"
        template_path.write_text("Stable.\n", encoding="utf-8")
        session = FakeSession()
        session.prompt_templates = (
            PromptTemplate(
                name="review",
                path=template_path,
                content="Stable.",
                description="Inspect changes",
            ),
        )
        app = TauTuiApp(session)

        def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
            # The editor saves nothing, leaving the staging file unchanged.
            initial_text = args[1] if len(args) > 1 else kwargs.get("initial_text", "")
            return initial_text

        monkeypatch.setattr(tui_app, "suspend_to_editor", fake_run)

        async with app.run_test() as pilot:
            prompt = app.query_one(PromptInput)
            prompt.value = "/prompts"
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("ctrl+e")
            await pilot.pause()

            # File untouched, picker reopens.
            assert template_path.read_text(encoding="utf-8") == "Stable.\n"
            from tau_coding.tui.app import PromptTemplatePickerScreen

            assert isinstance(app.screen, PromptTemplatePickerScreen)

    @pytest.mark.anyio
    async def test_editor_set_missing_command_falls_back_to_modal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from tau_coding.prompt_templates import PromptTemplate

        monkeypatch.setenv("EDITOR", "definitely-not-installed-xyz")
        template_path = tmp_path / "review.md"
        template_path.write_text("Original prompt.\n", encoding="utf-8")
        session = FakeSession()
        session.prompt_templates = (
            PromptTemplate(
                name="review",
                path=template_path,
                content="Original prompt.",
                description="Inspect changes",
            ),
        )
        app = TauTuiApp(session)

        def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise FileNotFoundError(2, "No such file", "definitely-not-installed-xyz")

        monkeypatch.setattr(tui_app, "suspend_to_editor", fake_run)

        async with app.run_test() as pilot:
            prompt = app.query_one(PromptInput)
            prompt.value = "/prompts"
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("ctrl+e")
            await pilot.pause()

            # Missing command falls back to the internal modal so the user
            # can still edit.
            from tau_coding.tui.app import PromptTemplateEditorScreen

            assert isinstance(app.screen, PromptTemplateEditorScreen)


class TestSidebarFileEditorSuspend:
    """Sidebar file click defers to `$EDITOR` when set, falls back otherwise."""

    @pytest.mark.anyio
    async def test_editor_set_saves_sidebar_file_through_external_editor(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("EDITOR", "vim")
        context_path = tmp_path / "AGENTS.md"
        context_path.write_text("Original context.\n", encoding="utf-8")
        session = FakeSession()
        session.cwd = tmp_path
        from tau_coding.system_prompt import ProjectContextFile

        session.context_files = (
            ProjectContextFile(path=str(context_path), content="Original context."),
        )
        app = TauTuiApp(session)

        def fake_run(
            app_suspend, initial_text, *, environ=None, runner=None, staging_directory=None
        ):  # type: ignore[no-untyped-def]
            return "Updated context.\n"

        monkeypatch.setattr(tui_app, "suspend_to_editor", fake_run)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.click("#sidebar-context-content .sidebar-file-item")
            # The worker awaits the suspend coroutine; let it drain.
            await pilot.pause()
            await pilot.pause()

            assert context_path.read_text(encoding="utf-8") == "Updated context.\n"

    @pytest.mark.anyio
    async def test_editor_set_unchanged_text_does_not_save(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("EDITOR", "true")
        context_path = tmp_path / "AGENTS.md"
        context_path.write_text("Stable.\n", encoding="utf-8")
        session = FakeSession()
        session.cwd = tmp_path
        from tau_coding.system_prompt import ProjectContextFile

        session.context_files = (ProjectContextFile(path=str(context_path), content="Stable."),)
        app = TauTuiApp(session)

        def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
            initial_text = args[1] if len(args) > 1 else kwargs.get("initial_text", "")
            return initial_text  # editor saved nothing

        monkeypatch.setattr(tui_app, "suspend_to_editor", fake_run)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.click("#sidebar-context-content .sidebar-file-item")
            await pilot.pause()
            await pilot.pause()

            assert context_path.read_text(encoding="utf-8") == "Stable.\n"

    @pytest.mark.anyio
    async def test_editor_set_missing_command_falls_back_to_internal_editor(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("EDITOR", "definitely-not-installed-xyz")
        context_path = tmp_path / "AGENTS.md"
        context_path.write_text("Original context.\n", encoding="utf-8")
        session = FakeSession()
        session.cwd = tmp_path
        from tau_coding.system_prompt import ProjectContextFile

        session.context_files = (
            ProjectContextFile(path=str(context_path), content="Original context."),
        )
        app = TauTuiApp(session)

        def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise FileNotFoundError(2, "No such file", "definitely-not-installed-xyz")

        monkeypatch.setattr(tui_app, "suspend_to_editor", fake_run)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.click("#sidebar-context-content .sidebar-file-item")
            await pilot.pause()
            await pilot.pause()

            from tau_coding.tui.app import SidebarFileEditor

            # Missing command falls back to the in-TUI sidebar editor.
            assert isinstance(app.screen.query_one("#sidebar-file-editor"), SidebarFileEditor)
            assert context_path.read_text(encoding="utf-8") == "Original context.\n"
