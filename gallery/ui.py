from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QLockFile, QPoint
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QVBoxLayout, QWidget,
    QMenu, QSlider, QInputDialog, QProgressDialog,
)

from gallery.library import Library
from gallery.dialogs import EditDialog, PictureDialog, split_tags
from gallery.grid import GalleryGrid
from gallery.jobs import LibraryJob
from gallery.search import TagSearchBar
from gallery.filters import TagFilterPanel
from gallery.prompts import preferred_full_prompt, prompt_sections


THEME = """
QWidget { background: #17191e; color: #e4e6eb; font-family: 'Microsoft YaHei UI'; font-size: 13px; }
QMainWindow { background: #17191e; }
QLabel#heading { font-size: 25px; font-weight: 600; }
QLabel#muted { color: #9299aa; }
QPushButton { background: #292d36; border: 1px solid #383e4a; border-radius: 7px; padding: 9px 14px; }
QPushButton:hover { background: #373e4b; }
QPushButton#primary { background: #bda478; color: #17191e; border: none; font-weight: 600; }
QPushButton:disabled { color: #707787; }
QLineEdit, QTextEdit, QComboBox { background: #20232b; border: 1px solid #3a404c; border-radius: 6px; padding: 8px; }
QListWidget { background: #17191e; border: none; outline: none; }
QListWidget::item { background: #20232a; border: 1px solid #303641; border-radius: 9px; padding: 8px; }
QListWidget::item:selected { background: #38372f; border: 2px solid #bda478; }
QStatusBar { color: #9299aa; }
QMenu { background: #252a32; border: 1px solid #3a404c; padding: 5px; }
QMenu::item { padding: 8px 24px; }
QMenu::item:selected { background: #41495a; }
QScrollBar:vertical { background: #17191e; width: 10px; }
QScrollBar::handle:vertical { background: #414753; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
"""


def button(text, callback, primary=False):
    widget = QPushButton(text)
    if primary:
        widget.setObjectName("primary")
    widget.clicked.connect(callback)
    return widget


class MainWindow(QMainWindow):
    def __init__(self, settings_path: Path, default_library: Path | None = None, *, auto_open=True):
        super().__init__()
        # The offscreen Qt plugin has no system font discovery on Windows.
        if not QFontDatabase.families():
            local_font = Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts/msyh.ttc"
            if local_font.exists():
                QFontDatabase.addApplicationFont(str(local_font))
        self.settings_path = settings_path
        self.library: Library | None = None
        self.current_style: str | None = None
        self.selected_style_tags: list[str] = []
        self.selected_topic_tags: list[str] = []
        self.library_lock: QLockFile | None = None
        self.dialogs: list[QDialog] = []
        self.job: LibraryJob | None = None
        self.setWindowTitle("个人画廊 · 画风收藏库")
        self.resize(1240, 840)
        self.setMinimumSize(850, 580)
        self.setStyleSheet(THEME)
        self.setAcceptDrops(True)
        central = QWidget()
        self.setCentralWidget(central)
        self.content_layout = QVBoxLayout(central)
        self.content_layout.setContentsMargins(28, 24, 28, 18)
        self.content_layout.setSpacing(16)
        top = QHBoxLayout()
        self.heading = QLabel("个人画廊")
        self.heading.setObjectName("heading")
        top.addWidget(self.heading)
        top.addStretch()
        self.open_library_button = button("打开素材库", self.choose_library)
        top.addWidget(self.open_library_button)
        self.import_button = button("＋ 导入图片", self.choose_import, True)
        self.import_button.setEnabled(False)
        top.addWidget(self.import_button)
        self.content_layout.addLayout(top)
        self.caption = QLabel("收集画风，让下一次创作有迹可循。")
        self.caption.setObjectName("muted")
        self.content_layout.addWidget(self.caption)
        self.controls = QHBoxLayout()
        self.back = button("← 全部画风", self.go_home)
        self.controls.addWidget(self.back)
        self.search_bar = TagSearchBar()
        self.search_box = self.search_bar.input
        self.search_bar.tagRemoved.connect(lambda kind, name: self.toggle_tag(kind, name, False))
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(200)
        self.search_timer.timeout.connect(self.refresh)
        self.search_box.textChanged.connect(lambda: self.search_timer.start())
        self.controls.addWidget(self.search_bar, 1)
        self.style_filter = button("画风标签", lambda: self.tag_filter("style"))
        self.topic_filter = button("主题标签", lambda: self.tag_filter("topic"))
        self.controls.addWidget(self.style_filter)
        self.controls.addWidget(self.topic_filter)
        self.controls.addWidget(button("清除", self.clear_search))
        self.sort = QComboBox()
        self.sort.addItem("最近添加", "created")
        self.sort.addItem("名称", "name")
        self.sort.addItem("最近使用", "used")
        self.sort.currentIndexChanged.connect(self.refresh)
        self.controls.addWidget(self.sort)
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(130, 320)
        self.size_slider.setValue(220)
        self.size_slider.setFixedWidth(90)
        self.size_slider.setToolTip("缩略图大小")
        self.size_slider.valueChanged.connect(self.resize_tiles)
        self.controls.addWidget(self.size_slider)
        self.controls.addStretch()
        self.content_layout.addLayout(self.controls)
        self.grid = GalleryGrid()
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setSpacing(14)
        self.grid.setIconSize(QSize(220, 180))
        self.grid.setGridSize(QSize(250, 242))
        self.grid.setWordWrap(True)
        self.grid.itemClicked.connect(self.activate_item)
        self.grid.filesDropped.connect(lambda files: self.import_files([Path(f) for f in files]))
        self.grid.orderChanged.connect(self.save_order)
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self.context_menu)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        body = QHBoxLayout()
        self.tag_panel = TagFilterPanel([self.style_filter, self.topic_filter])
        self.tag_panel.tagToggled.connect(self.toggle_tag)
        results = QVBoxLayout()
        results.addWidget(self.grid, 1)
        self.empty = QLabel("还没有画风\n\n打开一个空文件夹作为素材库，然后拖入你的第一组参考图。")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setObjectName("muted")
        results.addWidget(self.empty, 1)
        body.addLayout(results, 1)
        body.addWidget(self.tag_panel)
        self.content_layout.addLayout(body, 1)
        self.footer_actions = QHBoxLayout()
        self.edit_button = button("编辑画风", lambda: self.edit_style(self.current_style))
        self.copy_button = button("复制画风提示词", lambda: self.copy_prompt(self.current_style), True)
        self.footer_actions.addWidget(self.edit_button)
        self.footer_actions.addWidget(self.copy_button)
        self.footer_actions.addStretch()
        self.footer_actions.addWidget(button("批量主题标签", self.batch_tags))
        self.footer_actions.addWidget(button("标签管理", self.manage_tags))
        self.footer_actions.addWidget(button("更多操作 ▾", self.more_menu))
        self.content_layout.addLayout(self.footer_actions)
        self.statusBar().showMessage("本地素材 · 离线可用")
        self.refresh()
        if not auto_open:
            return
        if settings_path.exists():
            try:
                root = Path(json.loads(settings_path.read_text("utf-8"))["library"])
                if not root.is_absolute():
                    root = settings_path.parent / root
                if (root / "gallery.sqlite3").is_file():
                    self.open_library(root)
            except (OSError, ValueError, KeyError):
                self.statusBar().showMessage("上次素材库无法打开，请重新选择。")
        else:
            try:
                self.open_library(default_library or settings_path.parent / "素材库")
            except (OSError, ValueError):
                self.statusBar().showMessage("默认素材库无法打开，请通过“打开素材库”选择文件夹。")

    def choose_library(self):
        path = QFileDialog.getExistingDirectory(self, "选择空文件夹或已有素材库")
        if path:
            self.run(lambda: self.open_library(Path(path)))

    def open_library(self, root: Path):
        root = root.resolve()
        if self.library and root == self.library.root:
            return
        root.parent.mkdir(parents=True, exist_ok=True)
        maintenance = QLockFile(str(root.parent / ("." + root.name + ".maintenance.lock")))
        maintenance.setStaleLockTime(0)
        if not maintenance.tryLock(0):
            raise ValueError("素材库正在维护，请稍后打开")
        from gallery.updates import recover_update
        recover_update(root)
        root.mkdir(parents=True, exist_ok=True)
        # Refuse arbitrary non-library folders before creating the lock file.
        if not (root / "gallery.sqlite3").exists() and any(root.iterdir()):
            raise ValueError("请选择空文件夹或已有素材库")
        lock = QLockFile(str(root / ".gallery.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            raise ValueError("这个素材库已由另一个程序打开，请先关闭另一个窗口。")
        try:
            new_library = Library(root)
        except Exception:
            lock.unlock()
            raise
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.settings_path.with_suffix(".tmp")
            settings_root = self.settings_path.parent.resolve()
            stored_root = str(root.relative_to(settings_root)) if root.is_relative_to(settings_root) else str(root)
            settings = self.read_settings()
            settings["library"] = stored_root
            temp.write_text(json.dumps(settings, ensure_ascii=False), "utf-8")
            temp.replace(self.settings_path)
        except Exception:
            new_library.close()
            lock.unlock()
            raise
        if self.library:
            self.close_dialogs()
            self.library.close()
        if self.library_lock:
            self.library_lock.unlock()
        self.library_lock = lock
        maintenance.unlock()
        self.library = new_library
        self.current_style = None
        self.import_button.setEnabled(True)
        self.clear_search()

    def go_home(self):
        self.current_style = None
        self.clear_search()

    def refresh(self):
        self.grid.clear()
        self.search_bar.set_tags(self.selected_style_tags, self.selected_topic_tags)
        if self.library and self.tag_panel.isVisible():
            selected = self.selected_style_tags if self.tag_panel.kind == "style" else self.selected_topic_tags
            self.tag_panel.sync(self.library.tags(self.tag_panel.kind), selected)
        self.filtering = bool(self.search_box.text().strip() or self.selected_style_tags or self.selected_topic_tags)
        self.grid.reorder_enabled = self.current_style is not None and not self.filtering
        if self.library and self.current_style not in {s.id for s in self.library.styles()}:
            self.current_style = None
        self.grid.reorder_enabled = self.current_style is not None and not self.filtering
        self.grid.setMovement(QListWidget.Movement.Snap if self.grid.reorder_enabled else QListWidget.Movement.Static)
        self.back.setVisible(self.current_style is not None)
        self.edit_button.setVisible(self.current_style is not None and not self.filtering)
        self.copy_button.setVisible(self.current_style is not None and not self.filtering)
        self.sort.setVisible(self.current_style is None and not self.filtering)
        self.style_filter.setText(f"画风标签 ({len(self.selected_style_tags)})" if self.selected_style_tags else "画风标签")
        self.topic_filter.setText(f"主题标签 ({len(self.selected_topic_tags)})" if self.selected_topic_tags else "主题标签")
        self.heading.setText("个人画廊")
        if self.library:
            if self.filtering:
                matches = self.library.search(self.search_box.text(), self.selected_style_tags, self.selected_topic_tags)
                self.heading.setText(f"匹配图片 · {len(matches)}")
                names = {s.id: s.name for s in self.library.styles()}
                entries = [(p.id, f"{names[p.style_id]}\n{p.filename}", p.id, "picture") for p in matches]
            elif self.current_style:
                style = self.library.style(self.current_style)
                self.heading.setText(style.name)
                entries = [(p.id, p.filename, p.id, "picture") for p in self.library.images(style.id)]
            else:
                entries = [(s.id, f"{s.name}\n{s.count} 张图片", s.cover, "style") for s in self.library.styles(self.sort.currentData())]
            for entry_id, title, image_id, kind in entries:
                item = QListWidgetItem(title)
                item.setData(Qt.ItemDataRole.UserRole, (kind, entry_id))
                item.setData(Qt.ItemDataRole.UserRole + 1, str(self.library.thumbnail(image_id)))
                item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
                size = self.size_slider.value()
                item.setSizeHint(QSize(size + 18, size + 10))
                self.grid.addItem(item)
            self.caption.setText(f"{len(self.library.styles())} 个画风 · {len(self.library.images())} 张图片 · {self.library.root}")
        self.empty.setText("没有匹配图片\n\n试试减少标签或清除搜索条件。" if self.filtering else
                           "还没有画风\n\n拖入你的第一组参考图，或点击“导入图片”。" if self.library else
                           "还没有画风\n\n打开素材库，然后拖入你的第一组参考图。")
        self.empty.setVisible(self.grid.count() == 0)
        self.grid.setVisible(self.grid.count() > 0)

    def activate_item(self, item):
        kind, entry_id = item.data(Qt.ItemDataRole.UserRole)
        if kind == "style":
            self.current_style = entry_id
            self.refresh()
        elif not QApplication.keyboardModifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            self.show_picture(entry_id)

    def choose_import(self):
        files, _ = QFileDialog.getOpenFileNames(self, "导入参考图片", "", "图片 (*.jpg *.jpeg *.png *.webp)")
        if files:
            self.import_files([Path(f) for f in files])

    def import_files(self, files: list[Path]):
        if not self.library:
            self.choose_library()
            if not self.library:
                return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"导入 {len(files)} 张图片")
        form = QFormLayout(dialog)
        combo = QComboBox()
        combo.addItem("新建画风", None)
        for style in self.library.styles():
            combo.addItem(style.name, style.id)
        if self.current_style:
            combo.setCurrentIndex(combo.findData(self.current_style))
        name = QLineEdit()
        name.setPlaceholderText("新画风名称（必填）")
        combo.currentIndexChanged.connect(lambda: name.setEnabled(combo.currentData() is None))
        name.setEnabled(combo.currentData() is None)
        form.addRow("归入画风", combo)
        form.addRow("画风名称", name)
        tags = QLineEdit()
        tags.setPlaceholderText("选填，多个标签用逗号分隔")
        form.addRow("统一主题标签", tags)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        actions.accepted.connect(dialog.accept)
        actions.rejected.connect(dialog.reject)
        form.addRow(actions)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.run(lambda: self.perform_import(files, combo.currentData(), name.text(), split_tags(tags.text())))

    def perform_import(self, files, style_id, name, tags=None):
        assert self.library
        root = self.library.root

        def operation():
            with Library(root) as lib:
                return lib.import_images(files, style_id=style_id, new_style=name, topic_tags=tags or [])

        self.run_job("正在导入并生成缩略图…", operation, self.import_finished)

    def import_finished(self, result):
        assert self.library
        self.refresh()
        self.statusBar().showMessage(f"已导入 {len(result.imported)} 张 · 重复 {len(result.duplicates)} 张 · 失败 {len(result.failed)} 张")
        if result.failed or result.duplicates:
            dialog = QDialog(self)
            dialog.setWindowTitle("导入结果")
            dialog.resize(640, 380)
            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel(f"已导入 {len(result.imported)} 张 · 重复 {len(result.duplicates)} 张 · 失败 {len(result.failed)} 张"))
            entries = QListWidget()
            for name, error in result.failed:
                entries.addItem(f"失败：{name} — {error}")
            for name, pid in result.duplicates:
                item = QListWidgetItem(f"重复：{name} — 已在 {self.library.style(self.library.picture(pid).style_id).name}")
                item.setData(Qt.ItemDataRole.UserRole, pid)
                entries.addItem(item)
            layout.addWidget(entries)

            def locate():
                item = entries.currentItem()
                if item and item.data(Qt.ItemDataRole.UserRole):
                    self.show_picture(item.data(Qt.ItemDataRole.UserRole))

            layout.addWidget(button("查看选中的已收录图片", locate))
            entries.itemDoubleClicked.connect(locate)
            self.keep_dialog(dialog)

    def run_job(self, title, operation, on_success):
        if self.job:
            raise ValueError("请等待当前操作完成")
        self.close_dialogs()
        self.search_timer.stop()
        progress = QProgressDialog(title, "", 0, 0, self)
        progress.setWindowTitle("个人画廊")
        progress.setCancelButton(None)  # type: ignore[arg-type]  # Qt accepts nullptr; 6.8 stub omits it.
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        self.centralWidget().setEnabled(False)
        job = LibraryJob(operation, self)
        self.job = job

        def complete(result, error):
            progress.close()
            self.centralWidget().setEnabled(True)
            if error:
                QMessageBox.critical(self, "操作未完成", str(error))
            else:
                self.run(lambda: on_success(result))

        def finished():
            self.job = None
            job.deleteLater()

        job.completed.connect(complete)
        job.finished.connect(finished)
        progress.show()
        job.start()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def clear_search(self):
        self.current_style = None
        self.search_box.clear()
        self.selected_style_tags.clear()
        self.selected_topic_tags.clear()
        self.refresh()

    def save_order(self):
        if not self.library or not self.current_style or self.filtering:
            return
        ids = [self.grid.item(i).data(Qt.ItemDataRole.UserRole)[1] for i in range(self.grid.count())]
        lib, sid = self.library, self.current_style
        self.run(lambda: lib.reorder(sid, ids))
        self.refresh()

    def tag_filter(self, kind):
        if not self.library:
            return
        selected = self.selected_style_tags if kind == "style" else self.selected_topic_tags
        self.tag_panel.open_category(kind, self.library.tags(kind), selected)

    def toggle_tag(self, kind, name, checked):
        selected = self.selected_style_tags if kind == "style" else self.selected_topic_tags
        if kind == "style" and checked:
            selected.clear()
        if checked and name not in selected:
            selected.append(name)
        elif not checked and name in selected:
            selected.remove(name)
        self.refresh()

    def resize_tiles(self, size):
        self.grid.setIconSize(QSize(size, max(90, size - 40)))
        self.grid.setGridSize(QSize(size + 30, size + 22))
        for index in range(self.grid.count()):
            self.grid.item(index).setSizeHint(QSize(size + 18, size + 10))

    def keep_dialog(self, dialog):
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.dialogs.append(dialog)
        dialog.finished.connect(lambda: self.dialogs.remove(dialog) if dialog in self.dialogs else None)
        dialog.show()
        return dialog

    def close_dialogs(self):
        for dialog in self.dialogs[:]:
            dialog.close()

    def edit_style(self, style_id):
        if not self.library or not style_id:
            return None
        style = self.library.style(style_id)

        def save(dialog):
            def commit():
                assert self.library
                self.library.edit_style(style_id, dialog.name.text(), dialog.prompt.toPlainText(), split_tags(dialog.tags.text()))
                dialog.accept()
                self.refresh()
            self.run(commit)

        return self.keep_dialog(EditDialog(self, title="编辑画风", name=style.name, tags=style.tags, prompt=style.prompt, on_save=save))

    def edit_picture(self, picture_id, details=None):
        if not self.library:
            return None
        pic = self.library.picture(picture_id)

        def save(dialog):
            def commit():
                assert self.library
                self.library.edit_picture(picture_id, dialog.prompt.toPlainText(), split_tags(dialog.tags.text()))
                dialog.accept()
                self.refresh()
                if details:
                    details.close()
                    self.show_picture(picture_id)
            self.run(commit)

        return self.keep_dialog(EditDialog(self, title="编辑图片信息", tags=pic.tags, prompt=pic.original_prompt, on_save=save))

    def show_picture(self, picture_id):
        assert self.library
        pic = self.library.picture(picture_id)
        style = self.library.style(pic.style_id)
        metadata = f"{style.name}  ·  {pic.filename}\n画风标签：{'、'.join(style.tags) or '未填写'}    主题标签：{'、'.join(pic.tags) or '未填写'}"
        dialog: PictureDialog
        dialog = PictureDialog(self, self.library.original(pic.id), title=f"{style.name} · {pic.filename}",
                               metadata=metadata, prompt=pic.original_prompt,
                               copy_callback=lambda: self.copy_prompt(style.id, notify=False),
                               full_copy_callback=lambda: self.copy_full_prompt(pic.id, notify=False),
                               edit_callback=lambda: self.edit_picture(pic.id, dialog),
                               preferences=self.read_settings().get("picture_view", {}),
                               save_preferences=lambda preferences: self.run(lambda: self.save_picture_preferences(preferences)))
        return self.keep_dialog(dialog)

    def read_settings(self):
        try:
            data = json.loads(self.settings_path.read_text("utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_picture_preferences(self, preferences):
        settings = self.read_settings()
        settings["picture_view"] = preferences
        temporary = self.settings_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(settings, ensure_ascii=False), "utf-8")
        temporary.replace(self.settings_path)

    def show_copy_feedback(self, message):
        window = QApplication.activeWindow()
        if window is None or isinstance(window, QMenu):
            window = self
        previous = window.findChild(QLabel, "copyToast")
        if previous:
            previous.hide()
            previous.deleteLater()
        toast = QLabel("✓ " + message, window)
        toast.setObjectName("copyToast")
        toast.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        toast.setStyleSheet("background: #286747; color: white; border: 1px solid #67bb8b; border-radius: 8px; padding: 12px 24px; font-size: 15px;")
        toast.adjustSize()
        toast.move(max(0, (window.width() - toast.width()) // 2), max(0, window.height() - toast.height() - 60))
        toast.show()
        toast.raise_()
        QTimer.singleShot(2200, toast, toast.deleteLater)

    def copy_full_prompt(self, picture_id, notify=True):
        if not self.library:
            return

        def copy():
            assert self.library
            stored = self.library.picture(picture_id).original_prompt
            prompt = preferred_full_prompt(stored)
            if not prompt.strip():
                self.statusBar().showMessage("该图片未填写完整提示词。", 4000)
                return
            QApplication.clipboard().setText(prompt)
            self.statusBar().showMessage("完整提示词已复制。", 6000)
            parts = prompt_sections(stored)
            message = "已复制中文提示词" if parts["chinese"] else "已复制完整提示词"
            if notify:
                self.show_copy_feedback(message)
            return message

        return self.run(copy)

    def copy_prompt(self, style_id, notify=True):
        if not self.library or not style_id:
            return
        if not self.library.style(style_id).prompt.strip():
            self.edit_style(style_id)
            self.statusBar().showMessage("请先添加画风提示词")
            return

        def copy():
            assert self.library
            QApplication.clipboard().setText(self.library.prompt_for_copy(style_id))
            self.library.mark_used(style_id)
            self.statusBar().showMessage("画风提示词已复制，可粘贴到 ChatGPT 或 Codex。", 6000)
            message = "已复制画风提示词"
            if notify:
                self.show_copy_feedback(message)
            return message
        return self.run(copy)

    def selected_pictures(self):
        return [item.data(Qt.ItemDataRole.UserRole)[1] for item in self.grid.selectedItems()
                if item.data(Qt.ItemDataRole.UserRole)[0] == "picture"]

    def batch_tags(self):
        if not self.library:
            return
        ids = self.selected_pictures()
        if not ids:
            self.statusBar().showMessage("请进入画风或搜索结果，按住 Ctrl / Shift 选择图片。", 6000)
            return
        text, ok = QInputDialog.getText(self, "批量主题标签", f"给 {len(ids)} 张图片追加标签（逗号分隔）")
        if ok:
            self.run(lambda: self.library.add_topic_tags(ids, split_tags(text)))
            self.refresh()

    def manage_tags(self):
        if not self.library:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("标签管理")
        dialog.resize(460, 500)
        layout = QVBoxLayout(dialog)
        category = QComboBox()
        category.addItem("画风标签", "style")
        category.addItem("主题标签", "topic")
        layout.addWidget(category)
        items = QListWidget()
        layout.addWidget(items)
        layout.addWidget(QLabel("重命名为同类别已有标签时，将合并所有关联。"))

        def reload():
            items.clear()
            if self.library:
                items.addItems(self.library.tags(category.currentData()))

        def rename():
            if not items.currentItem():
                return
            old = items.currentItem().text()
            new, ok = QInputDialog.getText(dialog, "重命名 / 合并标签", "新名称", text=old)
            if ok:
                def commit():
                    assert self.library
                    self.library.rename_tag(category.currentData(), old, new)
                    self.selected_style_tags.clear()
                    self.selected_topic_tags.clear()
                    reload()
                    self.refresh()
                self.run(commit)

        def delete():
            if items.currentItem() and self.confirm("删除标签只解除关联，不会删除图片。是否继续？"):
                def commit():
                    assert self.library
                    self.library.delete_tag(category.currentData(), items.currentItem().text())
                    self.selected_style_tags.clear()
                    self.selected_topic_tags.clear()
                    reload()
                    self.refresh()
                self.run(commit)

        category.currentIndexChanged.connect(reload)
        row = QHBoxLayout()
        row.addWidget(button("重命名 / 合并", rename))
        row.addWidget(button("删除标签", delete))
        layout.addLayout(row)
        reload()
        self.keep_dialog(dialog)

    def context_menu(self, pos):
        item = self.grid.itemAt(pos)
        if not item or not self.library:
            return
        kind, entry_id = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        if kind == "style":
            menu.addAction("编辑画风", lambda: self.edit_style(entry_id))
            menu.addAction("复制画风提示词", lambda: self.copy_prompt(entry_id))
            menu.addAction("删除整个画风…", lambda: self.delete_style(entry_id))
        else:
            if not item.isSelected():
                self.grid.clearSelection()
                item.setSelected(True)
            menu.addAction("放大查看", lambda: self.show_picture(entry_id))
            menu.addAction("编辑图片信息", lambda: self.edit_picture(entry_id))
            if self.library.picture(entry_id).original_prompt.strip():
                menu.addAction("复制完整提示词", lambda: self.copy_full_prompt(entry_id))
            if self.current_style and not self.filtering:
                menu.addAction("设为画风封面", lambda: self.set_cover(entry_id))
            menu.addAction("批量主题标签", self.batch_tags)
            menu.addAction("移动所选图片…", self.move_selected)
            menu.addAction("删除所选图片…", self.delete_selected)
        menu.exec(self.grid.viewport().mapToGlobal(pos))

    def set_cover(self, picture_id):
        if not self.library or not self.current_style:
            return
        lib, sid = self.library, self.current_style
        self.run(lambda: lib.set_cover(sid, picture_id))
        self.refresh()

    def confirm(self, text):
        return QMessageBox.question(self, "确认操作", text,
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def removal_warning(self, ids, moving=False):
        assert self.library
        selected = set(ids)
        removed = [s.name for s in self.library.styles() if {p.id for p in self.library.images(s.id)}.issubset(selected)]
        text = f"{'移动' if moving else '永久删除'} {len(ids)} 张图片？"
        if removed:
            text += "\n\n以下画风将失去最后一张图片并被删除，共性提示词及标签关联也将移除：\n" + "、".join(removed)
        if not moving:
            text += "\n\n没有回收站。导入前的原文件不会被删除。"
        return text

    def delete_selected(self):
        if not self.library:
            return
        lib = self.library
        ids = self.selected_pictures()
        if ids and self.confirm(self.removal_warning(ids)):
            self.run(lambda: lib.delete_images(ids))
            self.close_dialogs()
            self.refresh()

    def delete_style(self, style_id):
        assert self.library
        ids = [p.id for p in self.library.images(style_id)]
        if self.confirm(self.removal_warning(ids)):
            self.run(lambda: self.library.delete_images(ids))
            self.close_dialogs()
            self.refresh()

    def move_selected(self):
        if not self.library:
            return
        ids = self.selected_pictures()
        if not ids:
            return
        source_ids = {self.library.picture(pid).style_id for pid in ids}
        targets = [s for s in self.library.styles() if any(source != s.id for source in source_ids)]
        if not targets:
            QMessageBox.information(self, "移动图片", "请先创建另一个带图片的画风作为目标。")
            return
        labels = [f"{s.name} · {s.id[:6]}" for s in targets]
        chosen, ok = QInputDialog.getItem(self, "移动图片", "目标画风", labels, 0, False)
        if ok:
            target = targets[labels.index(chosen)].id
            movers = [pid for pid in ids if self.library.picture(pid).style_id != target]
            if not self.confirm(self.removal_warning(movers, moving=True)):
                return
            self.run(lambda: self.library.move_images(movers, target))
            self.close_dialogs()
            self.refresh()

    def more_menu(self):
        menu = QMenu(self)
        menu.addAction("移动所选图片…", self.move_selected)
        menu.addAction("删除所选图片…", self.delete_selected)
        if self.current_style:
            menu.addAction("删除当前画风…", lambda: self.delete_style(self.current_style))
        menu.addSeparator()
        menu.addAction("备份素材库…", self.backup_library)
        menu.addAction("恢复备份…", self.restore_library)
        menu.exec(self.footer_actions.itemAt(self.footer_actions.count() - 1).widget().mapToGlobal(QPoint(0, 0)))

    def backup_library(self):
        if not self.library:
            return
        path, _ = QFileDialog.getSaveFileName(self, "备份素材库", "个人画廊备份.zip", "ZIP 备份 (*.zip)")
        if path:
            root = self.library.root

            def backup():
                with Library(root) as lib:
                    lib.backup(Path(path))

            self.run_job("正在备份素材库…", backup, lambda _: self.statusBar().showMessage("备份已完成。", 6000))

    def restore_library(self):
        backup, _ = QFileDialog.getOpenFileName(self, "选择备份文件", "", "ZIP 备份 (*.zip)")
        if not backup:
            return
        path = QFileDialog.getExistingDirectory(self, "选择新的空文件夹恢复")
        if path:
            def restore():
                Library.restore(Path(backup), Path(path))

            def success(_):
                if self.confirm("恢复已完成，是否打开恢复后的独立素材库？"):
                    self.open_library(Path(path))
            self.run_job("正在恢复并校验素材库…", restore, success)

    def dropEvent(self, event):
        files = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if files:
            self.import_files(files)
            event.acceptProposedAction()

    def run(self, operation):
        try:
            return operation()
        except Exception as exc:
            QMessageBox.critical(self, "操作未完成", str(exc))
            return None

    def closeEvent(self, event):
        if self.job and self.job.isRunning():
            self.statusBar().showMessage("正在保存文件，请等待当前操作完成后再关闭。")
            event.ignore()
            return
        if self.library:
            self.close_dialogs()
            self.library.close()
            self.library = None
        if self.library_lock:
            self.library_lock.unlock()
            self.library_lock = None
        event.accept()

