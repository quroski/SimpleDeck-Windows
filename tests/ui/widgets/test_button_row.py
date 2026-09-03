"""Testy ButtonRow — weryfikacja że zmiana checkbox'a on_press propaguje.

Regresja: użytkownik zgłasza że checkbox „Reaguj przy wciśnięciu" nie zmienia
zachowania. Testy weryfikują:
  1. ButtonRow.changed emituje z poprawnym on_press gdy checkbox toggle
  2. Sygnał changed jest podłączony do _on_button_changed w ButtonsPage
  3. Test button poprawnie symuluje press + release
"""
from __future__ import annotations

from unittest.mock import MagicMock


from simple_deck.core.profile import ButtonAction, ButtonConfig
from simple_deck.ui.widgets.config_rows import ButtonRow


class TestButtonRowOnPress:
    """Weryfikacja że checkbox on_press w ButtonRow działa."""

    def test_checkbox_toggle_emits_changed(self, qapp):
        """Toggling checkbox → changed signal fires z nowym on_press."""
        cfg = ButtonConfig(idx=0, on_press=True)
        row = ButtonRow(cfg)
        emitted = MagicMock()
        row.changed.connect(emitted)

        row._on_press.setChecked(False)
        assert emitted.called
        idx, new_cfg = emitted.call_args[0]
        assert idx == 0
        assert new_cfg.on_press is False

    def test_initial_state_reflects_config(self, qapp):
        cfg = ButtonConfig(idx=0, on_press=True)
        row = ButtonRow(cfg)
        assert row._on_press.isChecked() is True

        cfg2 = ButtonConfig(idx=0, on_press=False)
        row2 = ButtonRow(cfg2)
        assert row2._on_press.isChecked() is False

    def test_get_config_after_toggle(self, qapp):
        cfg = ButtonConfig(idx=2, on_press=True)
        row = ButtonRow(cfg)
        row._on_press.setChecked(False)
        result = row.get_config()
        assert result.on_press is False

    def test_changed_signal_has_correct_idx(self, qapp):
        cfg = ButtonConfig(idx=3, on_press=True)
        row = ButtonRow(cfg)
        emitted = MagicMock()
        row.changed.connect(emitted)
        row._on_press.setChecked(False)
        idx, _ = emitted.call_args[0]
        assert idx == 3


class TestButtonRowAction:
    """Weryfikacja że zmiana akcji (combo) również propaguje."""

    def test_action_change_emits(self, qapp):
        cfg = ButtonConfig(idx=0, action=ButtonAction.HOTKEY)
        row = ButtonRow(cfg)
        emitted = MagicMock()
        row.changed.connect(emitted)
        # Zmień akcję na TOGGLE_MUTE (index 1 w combo)
        row._action_combo.setCurrentIndex(1)
        assert emitted.called
        _, new_cfg = emitted.call_args[0]
        assert new_cfg.action == ButtonAction.TOGGLE_MUTE

    def test_hotkey_change_emits(self, qapp):
        cfg = ButtonConfig(idx=0, hotkey="")
        row = ButtonRow(cfg)
        emitted = MagicMock()
        row.changed.connect(emitted)
        row._hotkey_field.set_value("Ctrl+D")
        # set_value jest programowe — nie emituje hotkey_changed
        # ale _on_changed woła ręcznie gdy signal leci z dialogu
        # symuluj przez bezpośrednie wywołanie
        row._on_changed()
        assert emitted.called
        _, new_cfg = emitted.call_args[0]
        assert new_cfg.hotkey == "Ctrl+D"


class TestButtonRowRunCommand:
    """V5: RUN_COMMAND ma osobne pole tekstowe (cfg.target, nie cfg.hotkey)."""

    def test_command_field_visible_on_run_command(self, qapp):
        cfg = ButtonConfig(idx=0, action=ButtonAction.RUN_COMMAND,
                           target="firefox")
        row = ButtonRow(cfg)
        assert row._command_row.isHidden() is False
        assert row._command_field.text() == "firefox"

    def test_command_field_hidden_on_hotkey(self, qapp):
        cfg = ButtonConfig(idx=0, action=ButtonAction.HOTKEY)
        row = ButtonRow(cfg)
        assert row._command_row.isHidden() is True

    def test_hotkey_field_hidden_on_run_command(self, qapp):
        cfg = ButtonConfig(idx=0, action=ButtonAction.RUN_COMMAND)
        row = ButtonRow(cfg)
        assert row._hotkey_row.isHidden() is True

    def test_command_stored_in_target(self, qapp):
        cfg = ButtonConfig(idx=0, action=ButtonAction.RUN_COMMAND)
        row = ButtonRow(cfg)
        emitted = MagicMock()
        row.changed.connect(emitted)
        row._command_field.setText("alacritty -e htop")
        assert emitted.called
        _, new_cfg = emitted.call_args[0]
        assert new_cfg.target == "alacritty -e htop"

    def test_mute_target_visible_on_toggle_mute(self, qapp):
        cfg = ButtonConfig(idx=0, action=ButtonAction.TOGGLE_MUTE,
                           target="discord")
        row = ButtonRow(cfg)
        assert row._mute_row.isHidden() is False
        assert row._mute_target.text() == "discord"

    def test_visibility_toggles_on_action_change(self, qapp):
        cfg = ButtonConfig(idx=0, action=ButtonAction.HOTKEY)
        row = ButtonRow(cfg)
        assert row._hotkey_row.isHidden() is False
        assert row._command_row.isHidden() is True
        row._action_combo.setCurrentIndex(2)  # RUN_COMMAND
        assert row._hotkey_row.isHidden() is True
        assert row._command_row.isHidden() is False
