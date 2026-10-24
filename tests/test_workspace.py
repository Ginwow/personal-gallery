import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from gallery.library import Library
from tests.test_library import picture


def test_source_cards_show_update_date_but_checking_does_not_change_it(tmp_path, monkeypatch):
    from gallery.workspace import WorkspaceWindow
    from PySide6.QtWidgets import QMessageBox
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "素材库" / "甲"
    with Library(root) as lib:
        lib.import_images([picture(tmp_path / "a.png")], new_style="水彩")
    (root / "library-source.json").write_text('{"source":"https://github.com/freestylefly/awesome-gpt-image-2"}', 'utf-8')
    window = WorkspaceWindow(tmp_path / "settings.json", root.parent)
    entry = window.catalog.entries()[0]
    window.catalog.change(entry['id'], updated="2026-10-17")
    window.refresh_home()
    assert "最近一次更新：2026-10-17" in window.home_grid.item(0).toolTip()
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    window.review_updates([(entry['id'], [], 0, 0, None)])
    assert window.catalog.entry(entry['id'])['updated'] == "2026-10-17"
    window.close()
    app.processEvents()


def test_empty_libraries_are_hidden_and_manual_creation_requires_images(tmp_path):
    import pytest
    from gallery.workspace import WorkspaceWindow
    app = QApplication.instance() or QApplication([])
    with Library(tmp_path / "素材库" / "空库"):
        pass
    window = WorkspaceWindow(tmp_path / "settings.json", tmp_path / "素材库")
    assert window.home_grid.count() == 0
    target = tmp_path / "新库"
    with pytest.raises(ValueError, match="至少"):
        window.create_local_library(target, [], "水彩")
    assert not target.exists()
    path = picture(tmp_path / "a.png")
    window.create_local_library(target, [path], "水彩")
    assert window.home_grid.count() == 1
    window.close()
    app.processEvents()


def test_cleanup_only_deletes_empty_library_without_other_files(tmp_path):
    from gallery.catalog import Catalog
    from PySide6.QtCore import QLockFile
    empty = tmp_path / "empty"
    notes = tmp_path / "notes"
    locked = tmp_path / "locked"
    for root in (empty, notes, locked):
        with Library(root):
            pass
    (notes / "个人笔记.txt").write_text("保留", "utf-8")
    lock = QLockFile(str(locked / ".gallery.lock"))
    assert lock.tryLock(0)
    try:
        assert Catalog.delete_empty_local_library(empty)
        assert not empty.exists()
        assert not Catalog.delete_empty_local_library(notes)
        assert (notes / "个人笔记.txt").exists()
        assert not Catalog.delete_empty_local_library(locked)
    finally:
        lock.unlock()


def test_home_discovers_libraries_and_removes_without_deleting(tmp_path):
    from gallery.workspace import WorkspaceWindow
    app = QApplication.instance() or QApplication([])
    for name in ("甲", "乙"):
        with Library(tmp_path / "素材库" / name) as lib:
            lib.import_images([picture(tmp_path / f"{name}.png")], new_style="水彩")
    window = WorkspaceWindow(tmp_path / "settings.json", tmp_path / "素材库")
    window.show()
    assert window.home_grid.count() == 2
    first = window.home_grid.item(0)
    library_id = first.data(Qt.ItemDataRole.UserRole)
    window.enter_library(library_id)
    assert window.current_browser.library.root.name == "乙" or window.current_browser.library.root.name == "甲"
    assert window.current_browser.grid.count() == 1
    window.show_home()
    path = window.catalog.path(library_id)
    window.remove_library(library_id)
    assert path.joinpath("gallery.sqlite3").exists()
    assert window.home_grid.count() == 1
    window.close()
    window = WorkspaceWindow(tmp_path / "settings.json", tmp_path / "素材库")
    assert window.home_grid.count() == 1
    window.close()
    app.processEvents()


def test_catalog_relative_paths_survive_move_and_unavailable_library_is_skipped(tmp_path):
    import shutil
    from gallery.workspace import WorkspaceWindow
    app = QApplication.instance() or QApplication([])
    original = tmp_path / "original"
    with Library(original / "素材库" / "甲") as lib:
        lib.import_images([picture(tmp_path / "a.png")], new_style="广告")
    window = WorkspaceWindow(original / "settings.json", original / "素材库")
    library_id = window.catalog.entries()[0]["id"]
    window.close()
    moved = tmp_path / "moved"
    shutil.move(str(original), moved)
    window = WorkspaceWindow(moved / "settings.json", moved / "素材库")
    assert window.catalog.path(library_id) == (moved / "素材库" / "甲").resolve()
    (moved / "素材库" / "甲").rename(moved / "素材库" / "甲-moved")
    window.refresh_home()
    assert "无法访问" in window.home_grid.item(0).text()
    window.search_box.setText("广告")
    window.search()
    assert window.home_grid.count() == 0
    assert "未参与搜索" in window.caption.text()
    window.catalog.relocate(library_id, moved / "素材库" / "甲-moved")
    window.search()
    assert window.home_grid.count() == 1
    window.close()
    app.processEvents()


def test_cross_library_search_preserves_context_and_copies_correct_library(tmp_path):
    from gallery.workspace import WorkspaceWindow
    app = QApplication.instance() or QApplication([])
    for name, prompt in (("甲", "甲库提示词"), ("乙", "乙库提示词")):
        with Library(tmp_path / "素材库" / name) as lib:
            pid = lib.import_images([picture(tmp_path / f"{name}.png")], new_style="广告设计").imported[0]
            lib.edit_picture(pid, prompt, ["人物"])
    window = WorkspaceWindow(tmp_path / "settings.json", tmp_path / "素材库")
    window.show()
    window.search_box.setText("广告 人物")
    QTest.qWait(250)
    assert window.home_grid.count() == 2
    # Until submitted, these are still library cards rather than image results.
    assert isinstance(window.home_grid.item(0).data(Qt.ItemDataRole.UserRole), str)
    QTest.keyClick(window.search_box, Qt.Key.Key_Return)
    assert window.home_grid.count() == 2
    identity = window.home_grid.item(0).data(Qt.ItemDataRole.UserRole)
    details = window.show_picture(*identity)
    QTest.mouseClick(details.full_copy_button, Qt.MouseButton.LeftButton)
    assert app.clipboard().text() == window.catalog.entry(identity[0])["name"] + "库提示词"
    other = next(e["id"] for e in window.catalog.entries() if e["id"] != identity[0])
    window.enter_library(other)
    QTest.mouseClick(details.full_copy_button, Qt.MouseButton.LeftButton)
    assert app.clipboard().text() == window.catalog.entry(identity[0])["name"] + "库提示词"
    details.close()
    window.show_home()
    assert window.search_box.text() == "广告 人物"
    assert window.home_grid.count() == 2
    window.close()
    app.processEvents()


def test_source_progress_bar_and_final_status(tmp_path, monkeypatch):
    from gallery.workspace import WorkspaceWindow
    from PySide6.QtWidgets import QMessageBox
    import time
    app = QApplication.instance() or QApplication([])
    window = WorkspaceWindow(tmp_path / "settings.json", tmp_path / "素材库")
    window.check_bar.setRange(0, 7)
    window.check_bar.show()
    window.check_started = time.monotonic() - 12
    window.source_completed.emit(2)
    window.source_progress.emit("检查 3/7：Spsrx · 读取来源图文")
    assert window.check_bar.value() == 2
    assert "3/7：Spsrx" in window.statusBar().currentMessage()
    assert "12 秒" in window.statusBar().currentMessage()
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    window.review_updates([])
    assert "来源检查完成" in window.statusBar().currentMessage()
    assert "正在检查" not in window.statusBar().currentMessage()
    window.check_started = None
    window.close()
