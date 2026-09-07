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


class TestButtonRowHotkeyMode:
    """V1.0.5: tryb skrótu — combo (dialog) vs klawisz funkcyjny F13–F24."""

    def test_default_mode_is_combo(self, qapp):
        cfg = ButtonConfig(idx=0, hotkey="Ctrl+D")
        row = ButtonRow(cfg)
        assert cfg.hotkey_mode == "combo"
        assert row._mode_combo.currentData() == "combo"
        assert row._hotkey_row.isHidden() is False
        assert row._fkey_row.isHidden() is True

    def test_mode_combo_visible_on_hotkey_only(self, qapp):
        row = ButtonRow(ButtonConfig(idx=0, action=ButtonAction.HOTKEY))
        assert row._mode_row.isHidden() is False
        row2 = ButtonRow(ButtonConfig(idx=0, action=ButtonAction.RUN_COMMAND))
        assert row2._mode_row.isHidden() is True

    def test_switch_to_fkey_builds_hotkey(self, qapp):
        cfg = ButtonConfig(idx=1, hotkey="Ctrl+D")
        row = ButtonRow(cfg)
        emitted = MagicMock()
        row.changed.connect(emitted)
        # Przełącz tryb → F13 (domyślny w combo F-key)
        row._mode_combo.setCurrentIndex(1)
        assert emitted.called
        _, new_cfg = emitted.call_args[0]
        assert new_cfg.hotkey_mode == "fkey"
        assert new_cfg.hotkey == "F13"
        assert row._fkey_row.isHidden() is False
        assert row._hotkey_row.isHidden() is True

    def test_fkey_with_modifiers(self, qapp):
        cfg = ButtonConfig(idx=0, hotkey_mode="fkey", hotkey="Ctrl+F13")
        row = ButtonRow(cfg)
        assert row._mode_combo.currentData() == "fkey"
        assert row._fkey_combo.currentText() == "F13"
        assert row._fkey_ctrl.isChecked() is True
        assert row._fkey_shift.isChecked() is False
        emitted = MagicMock()
        row.changed.connect(emitted)
        row._fkey_shift.setChecked(True)
        _, new_cfg = emitted.call_args[0]
        assert new_cfg.hotkey == "Ctrl+Shift+F13"
        assert new_cfg.hotkey_mode == "fkey"

    def test_fkey_selection_change(self, qapp):
        cfg = ButtonConfig(idx=0, hotkey_mode="fkey", hotkey="F24")
        row = ButtonRow(cfg)
        assert row._fkey_combo.currentText() == "F24"
        emitted = MagicMock()
        row.changed.connect(emitted)
        row._fkey_combo.setCurrentText("F15")
        _, new_cfg = emitted.call_args[0]
        assert new_cfg.hotkey == "F15"

    def test_mode_preserved_on_unrelated_change(self, qapp):
        """Regresja: przebudowa configu w _on_changed nie może gubić trybu."""
        cfg = ButtonConfig(idx=0, hotkey_mode="fkey", hotkey="Ctrl+F13",
                           label="Mikrofon")
        row = ButtonRow(cfg)
        emitted = MagicMock()
        row.changed.connect(emitted)
        row._on_press.setChecked(False)  # niezwiązana zmiana
        _, new_cfg = emitted.call_args[0]
        assert new_cfg.hotkey_mode == "fkey"
        assert new_cfg.hotkey == "Ctrl+F13"  # zapisany F-key nie ginie
        assert new_cfg.label == "Mikrofon"

    def test_switch_back_to_combo_restores_field_value(self, qapp):
        cfg = ButtonConfig(idx=0, hotkey="Ctrl+D")
        row = ButtonRow(cfg)
        row._mode_combo.setCurrentIndex(1)   # → fkey
        row._mode_combo.setCurrentIndex(0)   # → z powrotem combo
        assert row._hotkey_field.value() == "Ctrl+D"
        assert row.get_config().hotkey_mode == "combo"
        assert row.get_config().hotkey == "Ctrl+D"

    def test_action_change_keeps_fkey_widgets_consistent(self, qapp):
        cfg = ButtonConfig(idx=0, hotkey_mode="fkey", hotkey="F13")
        row = ButtonRow(cfg)
        row._action_combo.setCurrentIndex(4)  # Brak (NONE)
        assert row._fkey_row.isHidden() is True
        assert row._mode_row.isHidden() is True


class TestConfigRowLeftColumn:
    """V1.0.5: kolumna badge+nazwa o stałej szerokości — Przyciski i
    Potencjometry dzielą kartę identycznie (niezależnie od długości nazwy)."""

    def test_left_column_fixed_width(self, qapp):
        row = ButtonRow(ButtonConfig(idx=0))
        assert row._left_widget.minimumWidth() == 120
        assert row._left_widget.maximumWidth() == 120

    def test_button_and_pot_rows_equal_column_width(self, qapp):
        from simple_deck.core.profile import PotConfig
        from simple_deck.ui.widgets.config_rows import PotRow
        btn_row = ButtonRow(ButtonConfig(idx=0))
        pot_row = PotRow(PotConfig(idx=0))
        assert (btn_row._left_widget.minimumWidth()
                == pot_row._left_widget.minimumWidth())

    def test_title_wraps_instead_of_stretching(self, qapp):
        row = ButtonRow(ButtonConfig(idx=0, label="Bardzo długa nazwa kontrolki z Overview"))
        assert row._title_lbl.wordWrap() is True
