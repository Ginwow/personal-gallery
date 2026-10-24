from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QEvent, QTimer
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMessageBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTextEdit, QVBoxLayout, QSplitter, QWidget, QTabWidget, QSpinBox, QSizePolicy,
)
from gallery.prompts import prompt_sections


def split_tags(text: str) -> list[str]:
    return list(dict.fromkeys(t.strip() for t in re.split(r"[,，\n]", text) if t.strip()))


class EditDialog(QDialog):
    def __init__(self, parent, *, title, name=None, tags=(), prompt="", on_save):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(580, 450)
        form = QFormLayout(self)
        self.name = QLineEdit(name or "")
        if name is not None:
            form.addRow("画风名称", self.name)
        self.tags = QLineEdit("，".join(tags))
        self.tags.setPlaceholderText("多个标签用逗号分隔")
        form.addRow("画风标签" if name is not None else "主题标签", self.tags)
        self.prompt = QTextEdit()
        self.prompt.setAcceptRichText(False)
        self.prompt.setPlainText(prompt)
        self.prompt.setPlaceholderText("可以留空，稍后再补充")
        form.addRow("画风共性提示词" if name is not None else "该图原始完整提示词", self.prompt)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.save_button = actions.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText("保存")
        actions.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        actions.rejected.connect(self.reject)
        actions.accepted.connect(lambda: on_save(self))
        form.addRow(actions)


class PictureDialog(QDialog):
    def __init__(self, parent, path: Path, *, title, metadata, prompt, copy_callback, full_copy_callback, edit_callback,
                 preferences=None, save_preferences=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1200, 820)
        self.setMinimumSize(850, 550)
        preferences = preferences or {}
        self.save_preferences = save_preferences
        self.path = path
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(200, 200)
        self.image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.image.installEventFilter(self)
        self.image.setStyleSheet("background: #101217; border-radius: 9px;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(8)
        self.splitter.setStyleSheet("QSplitter::handle { background: #383e4a; border-radius: 3px; }")
        self.splitter.addWidget(self.image)
        panel = QWidget()
        panel.setMinimumWidth(320)
        right = QVBoxLayout(panel)
        right.setContentsMargins(12, 0, 0, 0)
        self.splitter.addWidget(panel)
        layout.addWidget(self.splitter, 1)
        self.metadata = QLabel(metadata)
        self.metadata.setWordWrap(True)
        right.addWidget(self.metadata)
        tools = QHBoxLayout()
        tools.addWidget(QLabel("完整提示词"), 1)
        tools.addWidget(QLabel("字号"))
        self.font_size = QSpinBox()
        self.font_size.setRange(10, 26)
        self.font_size.setValue(int(preferences.get("font_size", 14)))
        tools.addWidget(self.font_size)
        right.addLayout(tools)
        self.prompt_tabs = QTabWidget()
        self.prompt_tabs.setStyleSheet("QTabWidget::pane { border: 1px solid #383e4a; } QTabBar::tab { padding: 7px 14px; }")
        self.readers: list[QTextEdit] = []
        parts = prompt_sections(prompt)
        for title, text in (("中文", parts["chinese"]), ("原文", parts["original"])):
            if text.strip():
                reader = QTextEdit()
                reader.setReadOnly(True)
                reader.setPlainText(text)
                self.prompt_tabs.addTab(reader, title)
                self.readers.append(reader)
        if not self.readers:
            reader = QTextEdit()
            reader.setReadOnly(True)
            reader.setPlainText("未填写该图原始完整提示词")
            self.prompt_tabs.addTab(reader, "提示词")
            self.readers.append(reader)
        self.original_prompt = self.readers[0]
        right.addWidget(self.prompt_tabs, 1)
        self.notes_toggle = self._button("来源说明 ▸", lambda: None)
        self.notes_toggle.setCheckable(True)
        self.notes_toggle.setVisible(bool(parts["notes"]))
        self.source_notes = QTextEdit()
        self.source_notes.setReadOnly(True)
        self.source_notes.setPlainText(parts["notes"])
        self.source_notes.setMaximumHeight(160)
        self.source_notes.hide()
        self.notes_toggle.toggled.connect(self.toggle_notes)
        right.addWidget(self.notes_toggle)
        right.addWidget(self.source_notes)
        self.font_size.valueChanged.connect(self.set_reader_font)
        self.set_reader_font(self.font_size.value())
        row = QHBoxLayout()
        hint = QLabel("画风提示词属于整组图片；完整提示词仅属于此图。")
        hint.setWordWrap(True)
        right.addWidget(hint)
        self.full_copy_button = self._copy_button("复制完整提示词", full_copy_callback)
        self.full_copy_button.setVisible(bool(prompt.strip()))
        row.addWidget(self.full_copy_button)
        self.copy_button = self._copy_button("复制画风提示词", copy_callback)
        self.copy_button.setObjectName("primary")
        row.addWidget(self.copy_button)
        right.addLayout(row)
        self.image_copy_button = self._copy_button("复制图片", self.copy_image)
        right.addWidget(self.image_copy_button)
        right.addWidget(self._button("编辑图片信息", edit_callback))
        ratio = max(0.2, min(0.8, float(preferences.get("image_ratio", 0.65))))
        self.splitter.setSizes([int(1000 * ratio), int(1000 * (1 - ratio))])
        self.splitter.splitterMoved.connect(self._fit_image)
        self._load_image()

    def toggle_notes(self, checked):
        self.source_notes.setVisible(checked)
        self.notes_toggle.setText("来源说明 ▾" if checked else "来源说明 ▸")

    def eventFilter(self, watched, event):
        if watched is self.image and event.type() == QEvent.Type.Resize:
            self._fit_image()
        return super().eventFilter(watched, event)

    def set_reader_font(self, size):
        for reader in [*self.readers, self.source_notes]:
            reader.setStyleSheet(f"QTextEdit {{ font-size: {size}pt; }}")

    def closeEvent(self, event):
        if self.save_preferences:
            sizes = self.splitter.sizes()
            self.save_preferences({"image_ratio": sizes[0] / max(1, sum(sizes)), "font_size": self.font_size.value()})
        super().closeEvent(event)

    @staticmethod
    def _button(text, callback):
        widget = QPushButton(text)
        widget.clicked.connect(callback)
        return widget

    def _copy_button(self, text, callback):
        button = QPushButton(text)
        timer = QTimer(button)
        timer.setSingleShot(True)

        def restore():
            button.setText(text)
            button.setStyleSheet("")

        def copy():
            message = callback()
            if message:
                button.setText("✓ " + message)
                button.setStyleSheet("QPushButton { background: #286747; color: #ffffff; border: 1px solid #67bb8b; }")
                timer.start(2200)

        timer.timeout.connect(restore)
        button.clicked.connect(copy)
        return button

    def copy_image(self):
        reader = QImageReader(str(self.path))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            QMessageBox.warning(self, "复制图片失败", "图片无法读取，请检查素材库文件是否完整。")
            return None
        QApplication.clipboard().setImage(image)
        return "已复制图片"

    def _load_image(self):
        reader = QImageReader(str(self.path))
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid():
            reader.setScaledSize(size.scaled(QSize(1800, 1400), Qt.AspectRatioMode.KeepAspectRatio))
        self.pixmap = QPixmap.fromImage(reader.read())
        self._fit_image()

    def _fit_image(self, *_args):
        if hasattr(self, "pixmap") and not self.pixmap.isNull():
            self.image.setPixmap(self.pixmap.scaled(self.image.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        elif hasattr(self, "pixmap"):
            self.image.setText("图片无法读取，请检查素材库文件是否完整。")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_image()
