"""Library home and cross-library results; each browser owns its own library."""
from __future__ import annotations

import sqlite3
import json
import datetime
import tempfile
import time
from typing import Any
from pathlib import Path

from PySide6.QtCore import Qt, QSize, Signal, QTimer
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QProgressBar, QStackedWidget, QFileDialog, QInputDialog, QMessageBox, QListWidgetItem, QMenu, QPushButton,
)

from gallery.catalog import Catalog
from gallery.grid import GalleryGrid
from gallery.library import Library
from gallery.ui import MainWindow, THEME, button
from gallery.jobs import LibraryJob
from gallery.sources import GitHubSource, normalize_source, fetch_bytes, SUPPORTED
from gallery.updates import UpdateService
from gallery.translation import Translator, WindowsSecrets
from gallery.vision import TopicVerifier


def source_update_time():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")


class WorkspaceWindow(QMainWindow):
    source_progress = Signal(str)
    source_completed = Signal(int)

    def __init__(self, settings_path: Path, libraries_path: Path):
        super().__init__()
        self.setWindowTitle("个人画廊")
        self.resize(1240, 840)
        self.setMinimumSize(850, 580)
        # Initialize Qt fonts through the existing browser without opening data.
        font_host = MainWindow(settings_path, auto_open=False)
        font_host.deleteLater()
        self.setStyleSheet(THEME)
        self.catalog = Catalog(settings_path, libraries_path)
        self.catalog.clean_empty_local_libraries()
        self.browsers: dict[str, MainWindow] = {}
        self.current_browser: MainWindow | None = None
        self.query = ""
        self.job: LibraryJob | None = None
        self.busy_ids: set[str] = set()
        self.check_bar = QProgressBar()
        self.check_bar.setFixedWidth(180)
        self.check_bar.setFormat("%v/%m 个来源 · %p%")
        self.check_bar.hide()
        self.statusBar().addPermanentWidget(self.check_bar)
        self.source_completed.connect(self.check_bar.setValue)
        self.check_started: float | None = None
        self.check_detail = ""
        self.progress_timer = QTimer(self)
        self.progress_timer.setInterval(1000)
        self.progress_timer.timeout.connect(self.show_check_progress)
        self.source_progress.connect(self.set_check_progress)
        self.scroll_position = 0
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        nav = QHBoxLayout()
        self.home_button = button("← 素材库首页", self.show_home)
        nav.addWidget(self.home_button)
        self.title = QLabel("个人画廊 · 素材库")
        self.title.setObjectName("heading")
        nav.addWidget(self.title)
        nav.addStretch()
        nav.addWidget(button("翻译设置", self.api_settings))
        nav.addWidget(button("GitHub设置", self.github_settings))
        nav.addWidget(button("新增素材库", self.add_source))
        nav.addWidget(button("检查全部更新", self.check_all))
        self.source_button = button("当前库来源 ▾", self.current_source_menu)
        self.source_button.hide()
        nav.addWidget(self.source_button)
        layout.addLayout(nav)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages)
        self.home = QWidget()
        self.pages.addWidget(self.home)
        home_layout = QVBoxLayout(self.home)
        actions = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("跨素材库搜索名称、画风或标签，按 Enter 搜索…")
        self.search_box.returnPressed.connect(self.search)
        actions.addWidget(self.search_box, 1)
        actions.addWidget(button("搜索", self.search))
        actions.addWidget(button("清除", self.clear_search))
        actions.addWidget(button("新建本地库", self.create_library))
        actions.addWidget(button("添加已有库", self.add_library))
        home_layout.addLayout(actions)
        self.caption = QLabel()
        home_layout.addWidget(self.caption)
        self.home_grid = GalleryGrid()
        self.home_grid.setViewMode(GalleryGrid.ViewMode.IconMode)
        self.home_grid.setResizeMode(GalleryGrid.ResizeMode.Adjust)
        self.home_grid.setIconSize(QSize(220, 180))
        self.home_grid.setGridSize(QSize(260, 270))
        self.home_grid.setSpacing(12)
        self.home_grid.setWordWrap(True)
        self.home_grid.setAcceptDrops(False)
        self.home_grid.setDragEnabled(False)
        self.home_grid.itemClicked.connect(self.activate)
        self.home_grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.home_grid.customContextMenuRequested.connect(self.context_menu)
        home_layout.addWidget(self.home_grid, 1)
        self.empty = QLabel("尚未添加素材库，可新建本地库或添加已有库。")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        home_layout.addWidget(self.empty)
        self.refresh_home()

    def github_settings(self):
        from gallery.github_settings import GitHubSettingsDialog
        self.run(lambda: GitHubSettingsDialog(self).exec())

    def api_settings(self):
        from gallery.api_settings import ApiSettingsDialog
        dialog = ApiSettingsDialog(self, self.catalog)
        dialog.exec()

    def run(self, operation):
        try:
            return operation()
        except Exception as exc:
            QMessageBox.critical(self, "操作未完成", str(exc))
            return None

    def refresh_home(self):
        self.home_grid.clear()
        self.home_button.setVisible(bool(self.query) or self.pages.currentWidget() is not self.home)
        if self.query:
            matches, skipped = self.catalog.search(self.query)
            for library_id, pid, style, filename in matches:
                name = self.catalog.entry(library_id)["name"]
                self.add_card(f"{style}\n{filename}\n来自：{name}", (library_id, pid), self.catalog.thumbnail(library_id, pid))
            self.caption.setText(f"匹配图片 · {len(matches)}" + (" · 未参与搜索：" + "、".join(skipped) if skipped else ""))
        else:
            for entry in self.catalog.entries():
                cover = None
                try:
                    count, ids = self.catalog.inspect(self.catalog.path(entry["id"]))
                    if count == 0:
                        continue
                    pid = entry.get("cover") if entry.get("cover") in ids else next(iter(ids), "")
                    cover = self.catalog.thumbnail(entry["id"], pid) if pid else None
                    text = f"{entry['name']}\n{count} 张图片"
                    if entry.get("source"):
                        text += "\n最近一次更新：" + entry.get("updated", "尚未更新")
                except (OSError, sqlite3.Error, ValueError):
                    text = f"{entry['name']}\n无法访问 · 右键重新定位"
                self.add_card(text, entry["id"], cover)
            self.caption.setText(f"{self.home_grid.count()} 个素材库 · 右键卡片管理")
        self.empty.setText("没有匹配图片，请调整关键词。" if self.query else "尚未添加素材库，可新建本地库或添加已有库。")
        self.empty.setVisible(self.home_grid.count() == 0)

    def add_card(self, text, identity, thumbnail):
        metrics = QFontMetrics(self.home_grid.font())
        item = QListWidgetItem("\n".join(metrics.elidedText(line, Qt.TextElideMode.ElideRight, 225) for line in text.splitlines()))
        item.setToolTip(text)
        item.setData(Qt.ItemDataRole.UserRole, identity)
        if thumbnail:
            item.setData(Qt.ItemDataRole.UserRole + 1, str(thumbnail))
        item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
        item.setSizeHint(QSize(245, 260))
        self.home_grid.addItem(item)

    def search(self):
        self.query = self.search_box.text().strip()
        self.scroll_position = 0
        self.refresh_home()

    def clear_search(self):
        self.search_box.clear()
        self.search()

    def activate(self, item):
        identity = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(identity, tuple):
            self.run(lambda: self.show_picture(*identity))
        else:
            self.run(lambda: self.enter_library(identity))

    def browser(self, library_id):
        if library_id not in self.browsers:
            path = self.catalog.path(library_id)
            if not (path / "gallery.sqlite3").is_file():
                raise ValueError("素材库无法访问，请右键卡片重新定位")
            browser = MainWindow(self.catalog.settings, auto_open=False)
            try:
                browser.open_library(path)
            except Exception:
                browser.deleteLater()
                raise
            # Library selection belongs to the catalogue, not an individual browser.
            browser.open_library_button.hide()
            self.browsers[library_id] = browser
            self.pages.addWidget(browser)
        return self.browsers[library_id]

    def enter_library(self, library_id):
        self.scroll_position = self.home_grid.verticalScrollBar().value()
        self.current_browser = self.browser(library_id)
        self.current_browser.refresh()
        self.pages.setCurrentWidget(self.current_browser)
        self.title.setText(self.catalog.entry(library_id)["name"])
        self.home_button.show()
        self.source_button.show()

    def show_picture(self, library_id, picture_id):
        return self.browser(library_id).show_picture(picture_id)

    def show_home(self):
        if self.pages.currentWidget() is self.home:
            self.clear_search()
        self.pages.setCurrentWidget(self.home)
        self.current_browser = None
        self.source_button.hide()
        self.title.setText("个人画廊 · 素材库")
        self.refresh_home()
        self.home_grid.verticalScrollBar().setValue(self.scroll_position)

    def create_library(self):
        files, _ = QFileDialog.getOpenFileNames(self, "新建素材库：至少选择一张图片", "", "图片 (*.jpg *.jpeg *.png *.webp)")
        if not files:
            return
        style, ok = QInputDialog.getText(self, "初始画风", "为首批图片指定画风名称")
        if not ok or not style.strip():
            return
        path = QFileDialog.getExistingDirectory(self, "选择空文件夹作为新库")
        if path:
            self.run(lambda: self.create_local_library(Path(path), [Path(f) for f in files], style))

    def create_local_library(self, folder, files, style):
        if not files:
            raise ValueError("必须至少添加一张图片才能建库")
        if not style.strip():
            raise ValueError("必须指定图片所属画风")
        folder = Path(folder).resolve()
        if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
            raise ValueError("新建库请选择空文件夹；已有库请使用添加已有库")
        folder.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".gallery-create-", dir=folder.parent) as scratch:
            staged = Path(scratch) / "library"
            with Library(staged) as library:
                result = library.import_images(files, new_style=style)
                if not result.imported or result.failed:
                    raise ValueError("图片导入失败，未建库：" + str(result.failed))
                library.validate()
            if folder.exists():
                folder.rmdir()
            staged.rename(folder)
        self.catalog.add(folder)
        self.refresh_home()

    def add_library(self):
        path = QFileDialog.getExistingDirectory(self, "选择已有素材库")
        if path:
            self.run(lambda: self.catalog.add(Path(path)))
            self.refresh_home()

    def remove_library(self, library_id):
        if library_id in self.busy_ids:
            raise ValueError("该库正在检查或准备更新，请稍后移除")
        browser = self.browsers.get(library_id)
        if browser:
            if browser.job and browser.job.isRunning():
                raise ValueError("请等待该库操作完成后再移除")
            if not browser.close():
                return
            self.pages.removeWidget(browser)
            browser.deleteLater()
            del self.browsers[library_id]
        self.catalog.remove(library_id)
        self.refresh_home()

    def context_menu(self, point):
        item = self.home_grid.itemAt(point)
        if not item or self.query:
            return
        library_id = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.addAction("打开素材库", lambda: self.run(lambda: self.enter_library(library_id)))
        menu.addAction("选择封面", lambda: self.run(lambda: self.choose_cover(library_id)))
        menu.addAction("重命名", lambda: self.rename_library(library_id))
        menu.addAction("重新定位", lambda: self.relocate(library_id))
        self.source_actions(menu, library_id)
        menu.addAction("从首页移除（保留文件）", lambda: self.run(lambda: self.remove_library(library_id)))
        menu.exec(self.home_grid.mapToGlobal(point))

    def rename_library(self, library_id):
        name, ok = QInputDialog.getText(self, "素材库名称", "显示名称", text=self.catalog.entry(library_id)["name"])
        if ok and name.strip():
            self.run(lambda: self.catalog.change(library_id, name=name.strip()))
            self.refresh_home()

    def relocate(self, library_id):
        path = QFileDialog.getExistingDirectory(self, "重新定位已有素材库")
        if path:
            if library_id in self.browsers:
                QMessageBox.information(self, "素材库仍已打开", "请重启开发版后再重新定位该库。")
                return
            self.run(lambda: self.catalog.relocate(library_id, Path(path)))
            self.refresh_home()

    def choose_cover(self, library_id):
        browser = self.browser(library_id)
        assert browser.library
        pictures = browser.library.images()
        names = [f"{i + 1}. {p.filename}" for i, p in enumerate(pictures)]
        if not names:
            raise ValueError("该库尚无图片")
        name, ok = QInputDialog.getItem(self, "素材库封面", "选择图片", names, editable=False)
        if ok:
            self.catalog.change(library_id, cover=pictures[names.index(name)].id)
            self.refresh_home()

    def closeEvent(self, event):
        if self.job or any(b.job and b.job.isRunning() for b in self.browsers.values()):
            QMessageBox.information(self, "正在处理", "请等待素材库操作完成。")
            event.ignore()
            return
        for browser in self.browsers.values():
            browser.close()
        event.accept()

    def background(self, title, operation, callback):
        if self.job:
            raise ValueError("请等待当前来源任务完成")
        self.statusBar().showMessage(title)
        job = LibraryJob(operation, self)
        self.job = job
        outcome = []
        job.completed.connect(lambda result, error: outcome.extend([result, error]))
        def finished():
            self.job = None
            if self.check_started is not None:
                self.progress_timer.stop()
                self.check_started = None
            job.deleteLater()
            self.busy_ids.clear()
            if len(outcome) != 2:
                self.statusBar().showMessage("后台任务未返回结果")
                return
            result, error = outcome
            if error:
                self.statusBar().showMessage("处理失败，正式素材库未更新")
                QMessageBox.warning(self, "处理失败", str(error))
            else:
                self.run(lambda: callback(result))
        job.finished.connect(finished)
        job.start()

    def translator(self):
        config = self.catalog.read().get("deepseek", {})
        return Translator(config.get("address", "https://api.deepseek.com"), config.get("model", ""), WindowsSecrets("PersonalGallery/DeepSeek").get())

    def topic_verifier(self):
        config = self.catalog.read().get("deepseek", {})
        return TopicVerifier(config.get("address", "https://api.deepseek.com"), config.get("vision_model") or "deepseek-flash",
                             WindowsSecrets("PersonalGallery/DeepSeek").get())

    def service(self, library_id):
        return UpdateService(self.catalog.path(library_id), self.catalog.settings.parent / "work" / "updates" / library_id)

    def source_actions(self, menu, library_id):
        entry = self.catalog.entry(library_id)
        for url in entry.get("sources", []):
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            menu.addAction("访问源库：" + url.split("/")[-2], lambda checked=False, u=url: QDesktopServices.openUrl(QUrl(u)))
        if len(entry.get("sources", [])) > 1:
            menu.addAction("按来源拆分素材库", lambda: self.run(lambda: self.split_sources(library_id)))
        elif entry.get("source"):
            menu.addAction("检查更新", lambda: self.run(lambda: self.check_updates([library_id])))
            if self.service(library_id).pending():
                menu.addAction("应用已准备的更新", lambda: self.run(lambda: self.apply_pending(library_id)))
            menu.addAction("恢复已忽略图例", lambda: self.run(lambda: self.restore_ignored(library_id)))
        menu.addAction("设置来源地址", lambda: self.set_source(library_id))

    def current_source_menu(self):
        library_id = next((key for key, value in self.browsers.items() if value is self.current_browser), None)
        if not library_id:
            QMessageBox.information(self, "来源操作", "请进入素材库，或右键首页卡片操作。")
            return
        menu = QMenu(self)
        self.source_actions(menu, library_id)
        from PySide6.QtGui import QCursor
        menu.exec(QCursor.pos())

    def set_source(self, library_id):
        if self.job:
            QMessageBox.information(self, "任务进行中", "请等待当前来源任务完成。")
            return
        url, ok = QInputDialog.getText(self, "来源地址", "受支持的GitHub仓库主页", text=self.catalog.entry(library_id).get("source", ""))
        if not ok:
            return
        def save():
            normalized = normalize_source(url)
            if len(self.catalog.entry(library_id).get("sources", [])) > 1:
                raise ValueError("混合来源素材库必须先按来源拆分，不能直接替换为单个来源")
            current = self.catalog.entry(library_id).get("source")
            if current and current != normalized:
                raise ValueError("已有来源映射的库请通过“新增素材库”创建新库，避免错配旧条目")
            if any(e["id"] != library_id and normalized in e.get("sources", []) for e in self.catalog.entries()):
                raise ValueError("此来源已对应现有素材库，请打开该库")
            self.catalog.change(library_id, source=normalized, sources=[normalized])
            self.refresh_home()
        self.run(save)

    def release_browser(self, library_id):
        browser = self.browsers.get(library_id)
        if not browser:
            return
        if browser.job:
            raise ValueError("素材库仍有导入/保存任务，请完成后重试更新")
        if self.current_browser is browser:
            self.show_home()
        if not browser.close():
            raise ValueError("素材库暂时无法关闭，未执行更新")
        self.pages.removeWidget(browser)
        del self.browsers[library_id]
        browser.deleteLater()

    def check_all(self):
        ids = [e["id"] for e in self.catalog.entries() if e.get("source")]
        if not ids:
            QMessageBox.information(self, "没有可更新来源", "请先拆分混合来源库，或从受支持来源添加素材库。")
            return
        self.run(lambda: self.check_updates(ids))

    def set_check_progress(self, detail):
        if self.check_started is not None:
            self.check_detail = detail
            self.show_check_progress()

    def show_check_progress(self):
        if self.check_started is not None:
            elapsed = int(time.monotonic() - self.check_started)
            self.statusBar().showMessage(f"{self.check_detail} · 已耗时 {elapsed} 秒；可继续浏览")

    def check_updates(self, library_ids):
        if self.job:
            raise ValueError("请等待当前来源任务完成")
        self.busy_ids = set(library_ids)
        entries = [self.catalog.entry(i) for i in library_ids]
        self.check_started = time.monotonic()
        self.check_detail = f"准备检查 {len(entries)} 个来源"
        self.check_bar.setRange(0, len(entries))
        self.check_bar.setValue(0)
        self.check_bar.show()
        self.progress_timer.start()
        def check():
            results: list[Any] = []
            for index, entry in enumerate(entries, 1):
                prefix = f"检查 {index}/{len(entries)}：{entry['name']}"
                self.source_progress.emit(prefix + " · 正在连接GitHub")
                try:
                    rows = GitHubSource(entry["source"], progress=lambda stage, p=prefix: self.source_progress.emit(p + " · " + stage)).check()
                    service = self.service(entry["id"])
                    results.append((entry["id"], rows, len(service.new_images(rows)), 0, None))
                except Exception as exc:
                    results.append((entry["id"], [], 0, 0, str(exc)))
                self.source_completed.emit(index)
            return results
        self.background("正在检查来源更新，可继续浏览与搜索…", check, self.review_updates)

    def review_updates(self, results):
        failed = sum(bool(r[4]) for r in results)
        self.statusBar().showMessage(f"来源检查完成：{len(results) - failed}/{len(results)} 个成功，{failed} 个失败；素材库尚未更新")
        lines, selected = [], []
        for library_id, rows, new, tags, error in results:
            name = self.catalog.entry(library_id)["name"]
            if error:
                lines.append(name + "：检查失败，" + error)
            else:
                self.catalog.change(library_id, checked=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
                lines.append(f"{name}：新增 {new} 张" if new else f"{name}：暂无新增图片")
                if new:
                    selected.append((library_id, rows))
        self.refresh_home()
        if not selected:
            QMessageBox.information(self, "检查结果", "\n".join(lines))
            return
        answer = QMessageBox.question(self, "确认更新", "\n".join(lines) + "\n\n是否下载新增图片？新增图片将翻译提示词并自动分类，优先使用可靠来源标签，必要时看图识别。每库整批成功后写入，DeepSeek调用可能计费。")
        if answer == QMessageBox.StandardButton.Yes:
            self.run(lambda: self.prepare_updates(selected))

    def prepare_updates(self, selected):
        if self.job:
            raise ValueError("请等待当前来源任务完成")
        translator = self.translator()
        verifier = self.topic_verifier()
        self.busy_ids = {i for i, _ in selected}
        services = [(i, rows, self.service(i)) for i, rows in selected]
        def prepare():
            results: list[Any] = []
            for library_id, rows, service in services:
                try:
                    count = service.prepare(rows, translator.translate, fetch_bytes, verify=verifier)
                    results.append((library_id, service, count, None))
                except Exception as exc:
                    results.append((library_id, service, 0, str(exc)))
            return results
        self.background("正在暂存下载和翻译；正式素材库尚未更新，可继续浏览…", prepare, self.publish_updates)

    def publish_updates(self, results):
        lines = []
        for library_id, service, count, error in results:
            name = self.catalog.entry(library_id)["name"]
            if error:
                lines.append(name + "：整批未更新，" + error)
                continue
            try:
                browser = self.browsers.get(library_id)
                if browser and (browser is self.current_browser or browser.dialogs or browser.job):
                    lines.append(name + "：已准备完成；当前仍在浏览，请从来源菜单选择“应用已准备的更新”")
                    continue
                self.release_browser(library_id)
                service.commit(require_verification=True)
                self.catalog.change(library_id, updated=source_update_time())
                lines.append(f"{name}：整批成功，新增 {count} 张")
            except Exception as exc:
                lines.append(name + "：未提交，" + str(exc))
        # Do not unexpectedly replace a search result set while the user browses.
        self.statusBar().showMessage("更新任务完成。返回首页或重新搜索可刷新结果。")
        QMessageBox.information(self, "更新结果", "\n".join(lines))

    def apply_pending(self, library_id):
        if self.job:
            raise ValueError("请等待来源任务完成")
        answer = QMessageBox.question(self, "应用更新", "应用时会关闭该库的详情并返回首页。是否现在应用？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.release_browser(library_id)
        self.service(library_id).commit(require_verification=True)
        self.catalog.change(library_id, updated=source_update_time())
        self.refresh_home()
        QMessageBox.information(self, "更新完成", "整批更新已应用。")

    def split_sources(self, library_id):
        if self.job:
            raise ValueError("请等待当前来源任务完成")
        answer = QMessageBox.question(self, "备份并拆分", "将在同级目录按来源建立独立库，保留完整原库备份，并将来源不明内容留在原库。是否执行？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.release_browser(library_id)
        from gallery.migration import split_library
        self.busy_ids = {library_id}
        def finished(targets):
            for source, path in targets:
                new_id = self.catalog.add(path)
                self.catalog.change(new_id, source=source, sources=[source])
            self.catalog.change(library_id, source="", sources=[])
            self.refresh_home()
            QMessageBox.information(self, "拆分完成", "已验证图片和记录。完整原库备份保留在原库同级隐藏的 gallery-split 目录中。")
        root = self.catalog.path(library_id)
        self.background("正在备份并预演拆库，请勿在其他程序编辑该库…", lambda: split_library(root), finished)

    def restore_ignored(self, library_id):
        service = self.service(library_id)
        records = service.ignored()
        if not records:
            QMessageBox.information(self, "忽略记录", "没有已删除的来源图例。")
            return
        names = [r["case"] + " / " + r["image"] for r in records]
        text, ok = QInputDialog.getItem(self, "恢复导入", "选择图例，下次检查将重新导入", names, editable=False)
        if ok:
            self.release_browser(library_id)
            record = records[names.index(text)]
            service.restore_ignored([(record["case"], record["image"])])

    def add_source(self):
        url, ok = QInputDialog.getText(self, "新增素材库", "受支持的GitHub仓库主页地址")
        if not ok:
            return
        def start():
            source = normalize_source(url)
            existing = next((e for e in self.catalog.entries() if source in e.get("sources", [])), None)
            if existing:
                self.enter_library(existing["id"])
                return
            self.background("正在读取来源目录…", lambda: GitHubSource(source).check(), lambda rows: self.confirm_new_source(source, rows))
        self.run(start)

    def confirm_new_source(self, source, rows):
        name = SUPPORTED[source.removeprefix("https://github.com/")]
        answer = QMessageBox.question(self, "导入来源", f"{name}：可导入 {len(rows)} 张图例。\n确认后下载，通过DeepSeek翻译；可靠的源画面元素标签映射到本地主题；新图缺少可靠内容标签时按图识别，保留旧图校对结果，无法识别则整批不导入。可能产生API费用。是否继续？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        parent = QFileDialog.getExistingDirectory(self, "选择新素材库的父文件夹", str(self.catalog.libraries))
        if not parent:
            return
        target = Path(parent) / name
        if target.exists():
            raise ValueError("目标目录已存在，不会覆盖")
        translator = self.translator()
        verifier = self.topic_verifier()
        staging = target.parent / ("." + name + ".new-library")
        if staging.exists():
            marker = staging / "library-source.json"
            if not marker.exists() or json.loads(marker.read_text("utf-8")).get("source") != source:
                raise ValueError("暂存目录来源不符，不会覆盖")
        def prepare():
            from PySide6.QtCore import QLockFile
            from gallery.updates import recover_update
            maintenance = QLockFile(str(staging.parent / ("." + staging.name + ".maintenance.lock")))
            maintenance.setStaleLockTime(0)
            if not maintenance.tryLock(0):
                raise ValueError("该来源库正在恢复或提交，请稍后重试")
            try:
                recover_update(staging)
                marker = staging / "library-source.json"
                if staging.exists() and marker.exists() and json.loads(marker.read_text("utf-8")).get("source") != source:
                    raise ValueError("恢复后的暂存来源不匹配")
            finally:
                maintenance.unlock()
            with Library(staging):
                pass
            (staging / "library-source.json").write_text(json.dumps({"source": source}), "utf-8")
            service = UpdateService(staging, self.catalog.settings.parent / "work" / "new-source-cache" / name)
            service.prepare(rows, translator.translate, fetch_bytes, verify=verifier)
            service.commit(require_verification=True)
            (staging / "library-source.json").write_text(json.dumps({"source": source}), "utf-8")
            return staging
        def finish(stage):
            stage.rename(target)
            new_id = self.catalog.add(target)
            self.catalog.change(new_id, updated=source_update_time())
            self.refresh_home()
            QMessageBox.information(self, "来源导入完成", "已完成整批翻译和校验，新库已添加到首页。")
        self.background("正在建立来源库，全部成功后才显示到首页…", prepare, finish)
