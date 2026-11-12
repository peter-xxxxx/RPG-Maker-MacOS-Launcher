import json
import logging
import os
import subprocess
from datetime import datetime, timezone

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

import translation


class TranslationDialog(QDialog):
    """Translation export/load/status dialog for an MV/MZ game."""

    def __init__(self, parent=None, game_path=None):
        super().__init__(parent)
        self.setWindowTitle("Translate Game")
        self.resize(760, 760)
        self.game_path = str(game_path) if game_path else ""
        self.layout_info = (
            translation.detect_game_layout(self.game_path) if self.game_path else None
        )
        self._refreshing = False
        self._build_ui()
        self.refresh_state()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)

        self.title_label = QLabel(self)
        self.title_label.setWordWrap(True)
        font = self.title_label.font()
        font.setPointSize(max(font.pointSize() + 2, 13))
        font.setBold(True)
        self.title_label.setFont(font)
        root.addWidget(self.title_label)

        self.info_label = QLabel(self)
        self.info_label.setWordWrap(True)
        root.addWidget(self.info_label)

        status_box = QGroupBox("Status", self)
        status_layout = QFormLayout(status_box)
        self.status_label = QLabel("No translation loaded", status_box)
        self.status_label.setWordWrap(True)
        self.entries_label = QLabel("-", status_box)
        self.path_label = QLabel("-", status_box)
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        status_layout.addRow("Translation:", self.status_label)
        status_layout.addRow("Entries:", self.entries_label)
        status_layout.addRow("Files:", self.path_label)
        root.addWidget(status_box)

        actions_box = QGroupBox("Actions", self)
        actions_layout = QVBoxLayout(actions_box)

        row1 = QHBoxLayout()
        self.export_button = QPushButton("Export ManualTransFile.json...", actions_box)
        self.load_button = QPushButton("Load Translated JSON...", actions_box)
        row1.addWidget(self.export_button)
        row1.addWidget(self.load_button)
        actions_layout.addLayout(row1)

        row1b = QHBoxLayout()
        self.coverage_button = QPushButton("Coverage Report...", actions_box)
        self.missing_button = QPushButton("Export Missing Text JSON...", actions_box)
        row1b.addWidget(self.coverage_button)
        row1b.addWidget(self.missing_button)
        actions_layout.addLayout(row1b)

        row1c = QHBoxLayout()
        self.metadata_button = QPushButton("Export Metadata JSON...", actions_box)
        row1c.addWidget(self.metadata_button)
        actions_layout.addLayout(row1c)

        self.enable_checkbox = QCheckBox("Enable translation on launch", actions_box)
        actions_layout.addWidget(self.enable_checkbox)

        row2 = QHBoxLayout()
        self.remove_button = QPushButton("Remove installed translation", actions_box)
        self.open_folder_button = QPushButton("Open translation folder", actions_box)
        row2.addWidget(self.remove_button)
        row2.addWidget(self.open_folder_button)
        actions_layout.addLayout(row2)
        root.addWidget(actions_box)

        advanced_box = QGroupBox("Advanced", self)
        advanced_layout = QFormLayout(advanced_box)
        self.font_edit = QLineEdit(advanced_box)
        self.font_edit.setPlaceholderText("Optional, e.g. PingFang SC")
        self.wrap_checkbox = QCheckBox("Wrap long dialogue text", advanced_box)
        self.wrap_checkbox.setChecked(True)
        self.debug_checkbox = QCheckBox("Write translation debug logs", advanced_box)
        self.legacy_export_checkbox = QCheckBox(
            "Export exact block keys (legacy/troubleshooting)", advanced_box
        )
        self.identity_export_checkbox = QCheckBox(
            "Use identity values for untranslated keys (MTool mode)", advanced_box
        )
        self.identity_export_checkbox.setChecked(True)
        advanced_layout.addRow("Font face:", self.font_edit)
        advanced_layout.addRow("", self.wrap_checkbox)
        advanced_layout.addRow("", self.debug_checkbox)
        advanced_layout.addRow("", self.legacy_export_checkbox)
        advanced_layout.addRow("", self.identity_export_checkbox)
        root.addWidget(advanced_box)

        title_box = QGroupBox("Image title overlay", self)
        title_layout = QFormLayout(title_box)
        self.title_overlay_checkbox = QCheckBox(
            "Draw the translated title over an image-only title screen",
            title_box,
        )
        self.title_x_spin = QSpinBox(title_box)
        self.title_x_spin.setRange(-2000, 2000)
        self.title_x_spin.setSuffix(" px")
        self.title_x_spin.setToolTip("0 = center automatically")
        self.title_y_spin = QSpinBox(title_box)
        self.title_y_spin.setRange(-2000, 2000)
        self.title_y_spin.setSuffix(" px")
        self.title_y_spin.setToolTip("0 = lower area automatically")
        self.title_font_spin = QSpinBox(title_box)
        self.title_font_spin.setRange(8, 200)
        self.title_font_spin.setValue(48)
        self.title_font_spin.setSuffix(" px")
        self.title_bg_spin = QSpinBox(title_box)
        self.title_bg_spin.setRange(0, 255)
        self.title_bg_spin.setValue(0)
        self.title_bg_spin.setToolTip("0 = transparent background; 255 = opaque")
        title_layout.addRow("", self.title_overlay_checkbox)
        title_layout.addRow("X offset (0=auto):", self.title_x_spin)
        title_layout.addRow("Y position (0=auto):", self.title_y_spin)
        title_layout.addRow("Font size:", self.title_font_spin)
        title_layout.addRow("Background opacity:", self.title_bg_spin)
        root.addWidget(title_box)

        details_box = QGroupBox("Details", self)
        details_layout = QVBoxLayout(details_box)
        self.details_edit = QPlainTextEdit(details_box)
        self.details_edit.setReadOnly(True)
        self.details_edit.setMaximumHeight(130)
        details_layout.addWidget(self.details_edit)
        root.addWidget(details_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)

        self.export_button.clicked.connect(self.export_raw_text)
        self.load_button.clicked.connect(self.load_translated_json)
        self.coverage_button.clicked.connect(self.show_coverage_report)
        self.missing_button.clicked.connect(self.export_missing_text)
        self.metadata_button.clicked.connect(self.export_metadata)
        self.remove_button.clicked.connect(self.remove_translation)
        self.open_folder_button.clicked.connect(self.open_translation_folder)
        self.enable_checkbox.toggled.connect(self.on_enable_toggled)
        self.font_edit.editingFinished.connect(self.save_advanced_settings)
        self.wrap_checkbox.stateChanged.connect(self.save_advanced_settings)
        self.debug_checkbox.stateChanged.connect(self.save_advanced_settings)
        self.title_overlay_checkbox.toggled.connect(self.save_advanced_settings)
        for spin in (
            self.title_x_spin,
            self.title_y_spin,
            self.title_font_spin,
            self.title_bg_spin,
        ):
            spin.valueChanged.connect(self.save_advanced_settings)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def refresh_state(self):
        if not self.layout_info:
            self._refreshing = True
            try:
                self.title_label.setText("Translation unavailable")
                self.info_label.setText(
                    "Translation supports RPG Maker MV/MZ games with an accessible "
                    "js and data folder. Select an MV/MZ game to use this feature."
                )
                self.status_label.setText("Unsupported game")
                self.entries_label.setText("-")
                self.path_label.setText("-")
                self.details_edit.setPlainText(
                    "No rpg_core.js/rmmz_core.js or data folder was found."
                )
                self.enable_checkbox.setChecked(False)
                for widget in (
                    self.export_button,
                    self.load_button,
                    self.coverage_button,
                    self.missing_button,
                    self.metadata_button,
                    self.remove_button,
                    self.open_folder_button,
                    self.enable_checkbox,
                    self.font_edit,
                    self.wrap_checkbox,
                    self.debug_checkbox,
                    self.legacy_export_checkbox,
                    self.identity_export_checkbox,
                    self.title_overlay_checkbox,
                    self.title_x_spin,
                    self.title_y_spin,
                    self.title_font_spin,
                    self.title_bg_spin,
                ):
                    widget.setEnabled(False)
            finally:
                self._refreshing = False
            return

        try:
            status = translation.get_status(self.layout_info)
        except Exception as exc:
            logging.exception("Failed to read translation status")
            self.status_label.setText(f"Error: {exc}")
            status = {}

        config = status.get("config") or {}
        state = status.get("state") or {}
        entry_count = int(status.get("entry_count") or 0)
        active = bool(status.get("active"))
        enabled = bool(status.get("enabled"))
        pending_transaction = bool(status.get("pending_transaction"))
        corrupt_transaction = bool(status.get("corrupt_transaction"))
        can_configure = (
            entry_count > 0 and not pending_transaction and not corrupt_transaction
        )
        has_files = self._has_translation_files()

        self.title_label.setText(self._game_title())
        self.info_label.setText(
            f"Engine: {self.layout_info.engine}    "
            f"Game folder: {self.layout_info.game_path}"
        )

        if entry_count <= 0:
            self.status_label.setText("No translation loaded")
            self.entries_label.setText("0")
        elif active:
            self.status_label.setText("Active")
            self.entries_label.setText(str(entry_count))
        elif enabled:
            self.status_label.setText("Enabled (incomplete)")
            self.entries_label.setText(str(entry_count))
        else:
            self.status_label.setText("Disabled")
            self.entries_label.setText(str(entry_count))

        self.path_label.setText(str(self.layout_info.plugin_dir))

        self._refreshing = True
        try:
            self.enable_checkbox.setEnabled(can_configure)
            self.enable_checkbox.setChecked(
                bool(config.get("enabled")) and can_configure
            )
            self.font_edit.setText(str(config.get("fontFace") or ""))
            self.wrap_checkbox.setChecked(bool(config.get("wrap", True)))
            self.debug_checkbox.setChecked(bool(config.get("debug", False)))
            self.title_overlay_checkbox.setChecked(
                bool(config.get("titleOverlay", False))
            )
            self.title_x_spin.setValue(int(config.get("titleOverlayX", 0) or 0))
            self.title_y_spin.setValue(int(config.get("titleOverlayY", 0) or 0))
            self.title_font_spin.setValue(
                int(config.get("titleOverlayFontSize", 48) or 48)
            )
            self.title_bg_spin.setValue(
                int(config.get("titleOverlayBackground", 0) or 0)
            )
        finally:
            self._refreshing = False

        self.export_button.setEnabled(True)
        self.load_button.setEnabled(True)
        self.coverage_button.setEnabled(True)
        self.missing_button.setEnabled(True)
        self.metadata_button.setEnabled(True)
        self.open_folder_button.setEnabled(True)
        self.remove_button.setEnabled(has_files or entry_count > 0)
        self.font_edit.setEnabled(can_configure)
        self.wrap_checkbox.setEnabled(can_configure)
        self.debug_checkbox.setEnabled(can_configure)
        self.legacy_export_checkbox.setEnabled(True)
        self.identity_export_checkbox.setEnabled(True)
        self.title_overlay_checkbox.setEnabled(can_configure)
        self.title_x_spin.setEnabled(can_configure)
        self.title_y_spin.setEnabled(can_configure)
        self.title_font_spin.setEnabled(can_configure)
        self.title_bg_spin.setEnabled(can_configure)

        details = []
        if status.get("warnings"):
            details.extend(str(w) for w in status["warnings"])
        if state:
            details.append(
                "Loaded from: {}".format(state.get("source_path") or "(unknown)")
            )
            details.append(
                "Last load: {}    Matched: {}    Empty: {}    Unknown: {}".format(
                    state.get("updated_at") or "-",
                    state.get("matched_count", "-"),
                    state.get("empty_count", "-"),
                    state.get("unknown_count", "-"),
                )
            )
        configured_labels = config.get("uiLabels") or []
        if configured_labels:
            installed_data = self._load_installed_dictionary()
            if installed_data is not None:
                missing_labels = [
                    label
                    for label in configured_labels
                    if label not in installed_data.entries
                ]
                if missing_labels:
                    details.append(
                        f"{len(missing_labels)} configured UI label(s) are not "
                        "translated. Use Export Missing Text JSON and load with "
                        "Merge to update menu/plugin labels."
                    )
        if not details:
            details.append("No translation files are installed for this game.")
        self.details_edit.setPlainText("\n".join(details))

    def _has_translation_files(self):
        if not self.layout_info:
            return False
        names = (
            translation.PLUGIN_JS_NAME,
            translation.DICTIONARY_NAME,
            translation.CONFIG_NAME,
            translation.STATE_NAME,
            translation.TXN_NAME,
            translation.TXN_NAME + ".corrupt",
        )
        return any((self.layout_info.plugin_dir / name).exists() for name in names)

    def _game_title(self):
        if not self.layout_info:
            return ""
        system_path = self.layout_info.data_dir / "System.json"
        try:
            raw = system_path.read_text(encoding="utf-8-sig")
            data = json.loads(raw)
            title = data.get("gameTitle")
            if isinstance(title, str) and title.strip():
                return title.strip()
        except Exception:
            pass
        return self.layout_info.game_path.name

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_raw_text(self):
        if not self.layout_info:
            return
        default_name = self._default_export_name()
        start_dir = self.layout_info.game_path
        target, _ = QFileDialog.getSaveFileName(
            self,
            "Export ManualTransFile.json",
            str(start_dir / default_name),
            "JSON files (*.json)",
        )
        if not target:
            return
        if not self._preflight_export(target):
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            scan = translation.scan_game(self.layout_info.game_path)
            identity_values = self.identity_export_checkbox.isChecked()
            if self.legacy_export_checkbox.isChecked():
                result = translation.export_raw_text_result(
                    scan, target, identity_values=identity_values
                )
                mode = "exact block keys (legacy)"
            else:
                result = translation.export_clean_text_result(
                    scan, target, identity_values=identity_values
                )
                mode = "cleaned MTool-style keys"
        except Exception as exc:
            logging.exception("Failed to export translation text")
            QMessageBox.critical(self, "Export Failed", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()

        details = [
            f"Exported {result.count} unique strings ({mode}).",
            f"Output: {result.path}",
        ]
        if result.backup_path:
            details.append(f"Backup: {result.backup_path}")
        if scan.warnings:
            details.append("")
            details.append("Warnings:")
            details.extend(scan.warnings[:20])
            if len(scan.warnings) > 20:
                details.append(f"...and {len(scan.warnings) - 20} more.")
        self.details_edit.setPlainText("\n".join(details))
        if self.legacy_export_checkbox.isChecked():
            instructions = (
                "This legacy export keeps exact block keys and RPG Maker control "
                "codes. Keep the keys and codes unchanged when translating."
            )
        else:
            instructions = (
                "This clean export contains no RPG Maker control codes or YEP "
                "name tags. Translate the values; the launcher will reinsert the "
                "original codes when you load the file."
            )
        if self.identity_export_checkbox.isChecked():
            instructions += (
                " Untranslated keys currently have identity values (key = value), "
                "matching MTool's input convention."
            )
        QMessageBox.information(
            self,
            "Export Complete",
            f"Exported {result.count} unique strings to:\n{result.path}\n\n"
            + instructions,
        )

    def show_coverage_report(self):
        if not self.layout_info:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            scan = translation.scan_game(self.layout_info.game_path)
            data = self._load_installed_dictionary()
            text = translation.coverage_summary(scan, data)
        except Exception as exc:
            logging.exception("Failed to build translation coverage report")
            QMessageBox.critical(self, "Coverage Failed", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._show_details("Translation Coverage", text)

    def export_missing_text(self):
        if not self.layout_info:
            return
        data = self._load_installed_dictionary()
        if data is None or not data.entries:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Select Current Translation JSON",
                str(self.layout_info.game_path),
                "JSON files (*.json)",
            )
            if not path:
                return
            data = translation.load_translation(path)
            if not data.ok:
                QMessageBox.critical(
                    self,
                    "Load Failed",
                    getattr(data, "error", "")
                    or "The translation file could not be loaded.",
                )
                return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            scan = translation.scan_game(self.layout_info.game_path)
            missing = translation.missing_text_keys(scan, data)
        except Exception as exc:
            logging.exception("Failed to find missing translation text")
            QMessageBox.critical(self, "Export Failed", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()

        if not missing:
            QMessageBox.information(
                self,
                "Nothing Missing",
                "Every scanned source string already has a non-empty translation.",
            )
            return

        target, _ = QFileDialog.getSaveFileName(
            self,
            "Export Missing Text JSON",
            str(self.layout_info.game_path / "ManualTransFile_missing.json"),
            "JSON files (*.json)",
        )
        if not target:
            return
        if not self._preflight_export(target):
            return

        try:
            result = translation.export_clean_missing_text_result(
                scan,
                target,
                data,
                identity_values=self.identity_export_checkbox.isChecked(),
            )
        except Exception as exc:
            logging.exception("Failed to export missing translation text")
            QMessageBox.critical(self, "Export Failed", str(exc))
            return

        details = [
            f"Exported {result.count} cleaned untranslated strings.",
            f"Output: {result.path}",
        ]
        if result.backup_path:
            details.append(f"Backup: {result.backup_path}")
        self.details_edit.setPlainText("\n".join(details))
        QMessageBox.information(
            self,
            "Missing Text Exported",
            f"Exported {result.count} untranslated strings to:\n{result.path}\n\n"
            "This is a partial file. After translating it, load it and choose "
            "Merge so the existing dictionary is kept.",
        )

    def export_metadata(self):
        if not self.layout_info:
            return
        target, _ = QFileDialog.getSaveFileName(
            self,
            "Export Metadata JSON",
            str(self.layout_info.game_path / "ManualTransFile_metadata.json"),
            "JSON files (*.json)",
        )
        if not target:
            return
        if not self._preflight_export(target):
            return
        try:
            result = translation.export_metadata_result(self.layout_info, target)
        except Exception as exc:
            logging.exception("Failed to export translation metadata")
            QMessageBox.critical(self, "Export Failed", str(exc))
            return
        details = [
            f"Exported {result.count} metadata strings.",
            f"Output: {result.path}",
            "This file is reference-only and is not injected into the game.",
        ]
        if result.backup_path:
            details.append(f"Backup: {result.backup_path}")
        self.details_edit.setPlainText("\n".join(details))
        QMessageBox.information(
            self,
            "Metadata Exported",
            f"Exported {result.count} editor-only metadata strings to:\n"
            f"{result.path}\n\n"
            "This metadata is for reference only and is not injected into the game.",
        )

    def _load_installed_dictionary(self):
        if not self.layout_info:
            return None
        dict_path = self.layout_info.plugin_dir / translation.DICTIONARY_NAME
        if not dict_path.is_file():
            return None
        data = translation.load_translation(dict_path)
        if not data.ok or not data.entries:
            return None
        return data

    def _preflight_export(self, target):
        info = translation.inspect_export_target(target, self.layout_info)
        if info.blocked_reason:
            QMessageBox.critical(
                self,
                "Protected Export Path",
                info.blocked_reason,
            )
            return False
        if info.is_reserved:
            QMessageBox.critical(
                self,
                "Reserved File Name",
                f"{info.path.name} is reserved for the translation tool or "
                "managed by the launcher, and will not be overwritten by this export.",
            )
            return False
        if not info.exists:
            return True

        try:
            modified = datetime.fromtimestamp(
                info.modified_ns / 1_000_000_000, timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S UTC")
        except (OverflowError, OSError, ValueError):
            modified = "unknown"

        if info.non_empty_count > 0:
            answer = QMessageBox.question(
                self,
                "Replace Existing Translations?",
                f"{info.path.name} contains {info.non_empty_count} non-empty "
                "translation value(s).\n\n"
                "Exporting this file will replace them with the chosen export "
                "values. A timestamped backup will be created first.\n\n"
                "Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return False

        message = (
            f"File: {info.path}\n"
            f"Keys: {info.key_count}\n"
            f"Non-empty values: {info.non_empty_count}\n"
            f"Size: {info.size} bytes\n"
            f"Modified: {modified}\n\n"
            "Overwrite this file? A timestamped backup will be created first."
        )
        if info.error:
            message += f"\n\nWarning: {info.error}"
        answer = QMessageBox.question(
            self,
            "Overwrite Existing File?",
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _default_export_name(self):
        return translation.DEFAULT_EXPORT_NAME

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------
    def load_translated_json(self):
        if not self.layout_info:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Translated JSON",
            str(self.layout_info.game_path),
            "JSON files (*.json)",
        )
        if not path:
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            data = translation.load_translation(path)
            if not getattr(data, "ok", True):
                QMessageBox.critical(
                    self,
                    "Load Failed",
                    getattr(data, "error", "")
                    or "The translation file could not be loaded.",
                )
                return
            if not data.entries:
                QMessageBox.warning(
                    self,
                    "Empty Translation",
                    "This file does not contain any non-empty translations.",
                )
                return

            existing = self._load_installed_dictionary()
            if existing is not None and existing.entries:
                answer = QMessageBox.question(
                    self,
                    "Merge or Replace?",
                    f"This game already has {len(existing.entries)} installed "
                    "translation(s).\n\n"
                    "Yes: merge the loaded file into the installed dictionary "
                    "(existing entries are kept; loaded entries win on conflict).\n"
                    "No: replace the installed dictionary with this file.\n"
                    "Cancel: abort without changing anything.",
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                    QMessageBox.Yes,
                )
                if answer == QMessageBox.Cancel:
                    return
                if answer == QMessageBox.Yes:
                    merged_entries = dict(existing.entries)
                    merged_entries.update(data.entries)
                    data.entries = merged_entries
                    merged_raw = dict(existing.raw_entries)
                    merged_raw.update(data.raw_entries)
                    data.raw_entries = merged_raw
                    data.empty_keys = [
                        key for key in data.empty_keys if key not in merged_entries
                    ]

            scan = translation.scan_game(self.layout_info.game_path)
            compilation = translation.compile_translation(scan, data)
            if not compilation.entries:
                self._show_details(
                    "Translation Summary",
                    compilation.summary()
                    + "\n\nNo exact game strings could be compiled. "
                    "The file may belong to a different game or version.",
                )
                return
            compiled_data = translation.TranslationData(
                path=data.path,
                entries=compilation.entries,
            )
            validation = translation.validate_translation(scan, compiled_data)
        except Exception as exc:
            logging.exception("Failed to validate translation")
            QMessageBox.critical(self, "Load Failed", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()

        summary = compilation.summary() + "\n\n" + validation.summary()
        if scan.warnings:
            summary += "\n\nScan warnings:\n" + "\n".join(
                f"  - {warning}" for warning in scan.warnings[:20]
            )
        if compilation.matched_key_count == 0:
            summary += (
                "\n\nNo static game strings matched. The file may belong to a "
                "different game or version."
            )
        if not self._show_details("Translation Summary", summary, ask=True):
            return

        config = self._config_from_ui(
            scan=scan,
            enabled=True,
        )
        state = {
            "schema_version": 1,
            "source_path": os.path.abspath(path),
            "entry_count": len(compilation.entries),
            "matched_count": compilation.matched_key_count,
            "empty_count": len(data.empty_keys),
            "unknown_count": len(compilation.unknown_keys),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            result = translation.install_translation(
                self.layout_info, compilation.entries, config, state
            )
        except Exception as exc:
            logging.exception("Failed to install translation")
            QMessageBox.critical(self, "Install Failed", str(exc))
            return

        if not result.ok:
            QMessageBox.critical(
                self, "Install Failed", self._with_permission_hint(result.message)
            )
            return

        self.refresh_state()
        message = (
            f"Installed {len(compilation.entries)} exact translations "
            f"(compiled from {compilation.provided_key_count} provided keys) "
            "into the game folder.\n\n"
            "Restart the game from the launcher to apply the translation."
        )
        if result.warnings:
            message += "\n\nWarnings:\n" + "\n".join(result.warnings[:10])
        QMessageBox.information(self, "Translation Installed", message)

    # ------------------------------------------------------------------
    # Enable / remove / folder
    # ------------------------------------------------------------------
    def on_enable_toggled(self, checked):
        if self._refreshing or not self.layout_info:
            return
        try:
            result = translation.set_enabled(self.layout_info, bool(checked))
        except Exception as exc:
            logging.exception("Failed to change translation enabled state")
            QMessageBox.critical(self, "Translation Error", str(exc))
            result = None

        if result is None or not result.ok:
            # The backend may have persisted enabled=false even when loader
            # entry removal failed. Re-read the real config instead of blindly
            # reverting the checkbox.
            self.refresh_state()
            if result is not None:
                QMessageBox.warning(
                    self,
                    "Translation Error",
                    self._with_permission_hint(result.message),
                )
            return

        self.refresh_state()

    def remove_translation(self):
        if not self.layout_info:
            return
        answer = QMessageBox.question(
            self,
            "Remove Translation",
            "Remove the installed translation from this game folder?\n\n"
            "The original game text will be shown again after restarting the game.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            result = translation.remove_translation(self.layout_info)
        except Exception as exc:
            logging.exception("Failed to remove translation")
            QMessageBox.critical(self, "Remove Failed", str(exc))
            return

        if not result.ok:
            QMessageBox.critical(
                self, "Remove Failed", self._with_permission_hint(result.message)
            )
            return

        self.refresh_state()
        QMessageBox.information(
            self,
            "Translation Removed",
            "Translation files were removed. Restart the game to see the "
            "original text.",
        )

    def open_translation_folder(self):
        if not self.layout_info:
            return
        try:
            self.layout_info.plugin_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["open", str(self.layout_info.plugin_dir)],
                check=False,
            )
        except Exception as exc:
            logging.exception("Failed to open translation folder")
            QMessageBox.critical(self, "Error", str(exc))

    # ------------------------------------------------------------------
    # Advanced settings
    # ------------------------------------------------------------------
    def save_advanced_settings(self):
        if self._refreshing or not self.layout_info:
            return
        if not self._has_translation_files():
            return
        try:
            config = translation.read_config(self.layout_info)
            config.update(self._config_from_ui(enabled=config.get("enabled", False)))
            translation.write_config(self.layout_info, config)
        except Exception as exc:
            logging.exception("Failed to save translation settings")
            QMessageBox.warning(self, "Settings Not Saved", str(exc))

    def _config_from_ui(self, scan=None, enabled=False):
        config = {
            "enabled": bool(enabled),
            "fontFace": self.font_edit.text().strip(),
            "wrap": self.wrap_checkbox.isChecked(),
            "debug": self.debug_checkbox.isChecked(),
            "titleOverlay": self.title_overlay_checkbox.isChecked(),
            "titleOverlayX": self.title_x_spin.value(),
            "titleOverlayY": self.title_y_spin.value(),
            "titleOverlayFontSize": self.title_font_spin.value(),
            "titleOverlayBackground": self.title_bg_spin.value(),
        }
        if scan is not None:
            labels = set(scan.category_keys.get("Plugins", set()))
            labels.update(scan.category_keys.get("Actors", set()))
            labels.update(scan.category_keys.get("Classes", set()))
            labels.update(scan.category_keys.get("System", set()))
            try:
                existing_labels = translation.read_config(self.layout_info).get(
                    "uiLabels", []
                )
                labels.update(str(label) for label in existing_labels if label)
            except Exception:
                pass
            config["uiLabels"] = sorted(labels)
        return config

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _with_permission_hint(self, message):
        text = str(message or "")
        lowered = text.lower()
        if any(
            token in lowered
            for token in ("permission", "denied", "read-only", "not writable")
        ):
            text += (
                "\n\nStart the game once from the launcher to let it fix the "
                "game folder's ownership and permissions, then try again."
            )
        return text

    def _show_details(self, title, text, ask=False):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(720, 520)
        layout = QVBoxLayout(dialog)

        edit = QPlainTextEdit(dialog)
        edit.setReadOnly(True)
        edit.setPlainText(text)
        layout.addWidget(edit)

        if ask:
            buttons = QDialogButtonBox(
                QDialogButtonBox.Yes | QDialogButtonBox.No, dialog
            )
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
        else:
            buttons = QDialogButtonBox(QDialogButtonBox.Ok, dialog)
            buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        return dialog.exec() == QDialog.Accepted
