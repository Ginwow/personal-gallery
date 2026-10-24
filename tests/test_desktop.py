import os
import time
import json
from pathlib import Path
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QTimer, QCoreApplication, QEvent, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLineEdit, QMessageBox, QInputDialog, QPushButton

from gallery.library import Library
from gallery.ui import MainWindow
from gallery.dialogs import PictureDialog


def test_style_filter_replaces_selection_while_topics_remain_multiple(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(settings_path=tmp_path / "settings.json")
    pid = window.library.import_images([picture(tmp_path / "a.png")], new_style="测试").imported[0]
    sid = window.library.picture(pid).style_id
    window.library.edit_style(sid, "测试", "", ["水彩", "油画"])
    window.library.edit_picture(pid, "", ["人物", "动物"])
    window.show()
    window.tag_filter("style")
    panel = window.tag_panel
    QTest.qWait(30)
    for name in ("水彩", "油画"):
        box = panel.boxes[name]
        QTest.mouseClick(box, Qt.MouseButton.LeftButton, pos=QPoint(box.width() - 8, box.height() // 2))
    assert window.selected_style_tags == ["油画"]
    assert not panel.boxes["水彩"].isChecked()
    assert panel.boxes["油画"].isChecked()
    assert panel.isVisible()
    panel.boxes["油画"].click()
    assert window.selected_style_tags == []
    window.tag_filter("topic")
    QTest.qWait(30)
    for name in ("人物", "动物"):
        box = panel.boxes[name]
        QTest.mouseClick(box, Qt.MouseButton.LeftButton, pos=QPoint(box.width() - 8, box.height() // 2))
    assert set(window.selected_topic_tags) == {"人物", "动物"}
    window.toggle_tag("style", "水彩", True)
    window.toggle_tag("style", "油画", True)
    assert window.selected_style_tags == ["油画"]
    assert set(window.selected_topic_tags) == {"人物", "动物"}
    window.close()
    app.processEvents()


def test_copy_feedback_is_local_repeatable_and_restores(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(settings_path=tmp_path / "settings.json")
    pid = window.library.import_images([picture(tmp_path / "a.png")], new_style="水彩").imported[0]
    window.library.edit_picture(pid, "【中文提示词】\n水彩猫咪\n【英文原文】\nWatercolor cat", [])
    window.show()
    details = window.show_picture(pid)
    QTest.mouseClick(details.full_copy_button, Qt.MouseButton.LeftButton)
    assert details.full_copy_button.text() == "✓ 已复制中文提示词"
    assert details.full_copy_button.isEnabled()
    QTest.qWait(1200)
    QTest.mouseClick(details.full_copy_button, Qt.MouseButton.LeftButton)
    QTest.qWait(1200)
    assert details.full_copy_button.text() == "✓ 已复制中文提示词"
    assert app.clipboard().text() == "水彩猫咪"
    QTest.qWait(1300)
    assert details.full_copy_button.text() == "复制完整提示词"
    assert details.full_copy_button.styleSheet() == ""
    details.close()
    app.processEvents()
    window.copy_full_prompt(pid)
    from PySide6.QtWidgets import QLabel
    toast = window.findChild(QLabel, "copyToast")
    assert toast and toast.isVisible()
    assert toast.text() == "✓ 已复制中文提示词"
    window.close()
    app.processEvents()


def test_picture_details_show_side_by_side_languages_and_remember_reading_layout(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = tmp_path / "settings.json"
    window = MainWindow(settings_path=settings)
    pid = window.library.import_images([picture(tmp_path / "a.png")], new_style="水彩").imported[0]
    window.library.edit_picture(pid, "【中文提示词】\n中文阅读内容。\n【来源原文】\nOriginal reading text.\n【来源与使用说明】\n来源说明。", [])
    window.show()
    details = window.show_picture(pid)
    QTest.qWait(50)
    assert details.prompt_tabs.count() == 2
    assert details.prompt_tabs.tabText(0) == "中文"
    assert details.prompt_tabs.currentWidget().toPlainText() == "中文阅读内容。"
    details.prompt_tabs.setCurrentIndex(1)
    assert details.prompt_tabs.currentWidget().toPlainText() == "Original reading text."
    assert details.source_notes.isHidden()
    QTest.mouseClick(details.notes_toggle, Qt.MouseButton.LeftButton)
    assert not details.source_notes.isHidden()
    assert details.source_notes.toPlainText() == "来源说明。"
    image_right = details.image.mapTo(details, details.image.rect().topRight()).x()
    text_left = details.prompt_tabs.mapTo(details, details.prompt_tabs.rect().topLeft()).x()
    assert image_right < text_left
    assert details.prompt_tabs.height() > 250
    details.splitter.setSizes([500, 500])
    details.font_size.setValue(18)
    details.close()
    stored_ratio = json.loads(settings.read_text("utf-8"))["picture_view"]["image_ratio"]
    window.close()
    window = MainWindow(settings_path=settings)
    details = window.show_picture(pid)
    QTest.qWait(50)
    assert details.font_size.value() == 18
    assert details.original_prompt.font().pointSize() == 18
    assert abs(details.splitter.sizes()[0] / sum(details.splitter.sizes()) - stored_ratio) < 0.05
    details.close()
    window.close()
    app.processEvents()


@pytest.mark.parametrize("stored,expected", [
    ("【中文提示词】\n[HUMAN] 的黑白肖像海报，文字为 [TEXT]。\n\n【来源原文】\nHigh-contrast portrait poster of [HUMAN].\n\n【来源与使用说明】\n案例：#542", "[HUMAN] 的黑白肖像海报，文字为 [TEXT]。"),
    ("【中文提示词】\n\n【来源原文】\nHigh-contrast portrait poster.\n【来源与使用说明】\n说明", "High-contrast portrait poster."),
    ("【中文提示词｜自动翻译】\n为 VERDE 设计海报。\n\n【英文原文】\nDesign a VERDE poster.\n【来源与使用说明】\n来源链接", "为 VERDE 设计海报。"),
    ("为 VERDE 设计海报。\n\n【提示词来源说明】\n版权说明\n\n【英文提示词】\nDesign a poster.", "为 VERDE 设计海报。"),
    ("【中文提示词】\n\n【英文原文】\nDraw a cat.\n【来源与使用说明】\n说明", "Draw a cat."),
    ("Create a sign that reads \"你好\".\n", "Create a sign that reads \"你好\".\n"),
    ("  Draw a cat.\nKeep the texture.  ", "  Draw a cat.\nKeep the texture.  "),
    ("中文提示词：画一份【食物名称】\nEnglish prompt: Draw food.", "画一份【食物名称】"),
])
def test_copy_full_prompt_prefers_chinese_version_without_source_notes(tmp_path, stored, expected):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(settings_path=tmp_path / "settings.json")
    pid = window.library.import_images([picture(tmp_path / "a.png")], new_style="水彩").imported[0]
    window.library.edit_picture(pid, stored, [])
    details = window.show_picture(pid)
    QTest.mouseClick(details.full_copy_button, Qt.MouseButton.LeftButton)
    assert app.clipboard().text() == expected
    assert window.library.picture(pid).original_prompt == stored
    details.close()
    window.close()
    app.processEvents()


def test_copy_full_prompt_from_details_and_context_menu_only_when_present(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(settings_path=tmp_path / "settings.json")
    a, b = window.library.import_images([picture(tmp_path / "a.png"), picture(tmp_path / "b.png", "blue")], new_style="水彩").imported
    sid = window.library.picture(a).style_id
    full_prompt = "  一只猫，水彩笔触\n保留纸张纹理  "
    window.library.edit_style(sid, "水彩", "共性画风", [])
    window.library.edit_picture(a, full_prompt, [])
    window.library.edit_picture(b, " \n ", [])
    window.current_style = sid
    window.refresh()
    window.show()
    details = window.show_picture(a)
    assert not details.full_copy_button.isHidden()
    QTest.mouseClick(details.full_copy_button, Qt.MouseButton.LeftButton)
    assert app.clipboard().text() == full_prompt
    QTest.mouseClick(details.copy_button, Qt.MouseButton.LeftButton)
    assert app.clipboard().text() == "共性画风"
    details.close()
    empty_details = window.show_picture(b)
    assert empty_details.full_copy_button.isHidden()
    empty_details.close()
    app.processEvents()
    observed = []

    def choose_copy():
        menu = app.activePopupWidget()
        try:
            names = [action.text() for action in menu.actions()]
            observed.append(names)
            if "复制完整提示词" in names:
                action = next(action for action in menu.actions() if action.text() == "复制完整提示词")
                QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(action).center())
        finally:
            menu.close()

    for row in (0, 1):
        QTimer.singleShot(30, choose_copy)
        window.context_menu(window.grid.visualItemRect(window.grid.item(row)).center())
    assert "复制完整提示词" in observed[0]
    assert "复制完整提示词" not in observed[1]
    assert app.clipboard().text() == full_prompt
    window.close()
    app.processEvents()


def test_internal_library_reopens_after_moving_portable_folder(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    original = tmp_path / "便携目录"
    settings = original / "settings.json"
    library = original / "素材库" / "个人"
    window = MainWindow(settings_path=settings, default_library=library)
    window.library.import_images([picture(tmp_path / "sample.png")], new_style="水彩")
    window.close()
    assert not Path(json.loads(settings.read_text('utf-8'))['library']).is_absolute()
    moved = tmp_path / "移动后的目录"
    original.rename(moved)
    monkeypatch.chdir(tmp_path)
    window = MainWindow(settings_path=moved / "settings.json")
    assert window.library.root == (moved / "素材库" / "个人").resolve()
    assert window.library.styles()[0].name == "水彩"
    window.close()
    app.processEvents()


def test_tag_panel_stays_open_for_multiple_choices_and_never_overlaps_results(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(settings_path=tmp_path / "settings.json")
    pid = window.library.import_images([picture(tmp_path / "a.png")], new_style="水彩", topic_tags=["人物", "动物"]).imported[0]
    window.refresh()
    window.show()
    QTest.mouseClick(window.topic_filter, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert window.tag_panel.isVisible()
    first = window.tag_panel.boxes["人物"]
    QTest.mouseClick(first, Qt.MouseButton.LeftButton, pos=QPoint(8, first.height() // 2))
    assert window.tag_panel.isVisible()
    second = window.tag_panel.boxes["动物"]
    QTest.mouseClick(second, Qt.MouseButton.LeftButton, pos=QPoint(8, second.height() // 2))
    assert set(window.selected_topic_tags) == {"人物", "动物"}
    assert window.tag_panel.isVisible()
    assert window.grid.count() == 1
    app.processEvents()
    assert not window.tag_panel.geometry().intersects(window.grid.geometry())
    QTest.mouseClick(window.search_box, Qt.MouseButton.LeftButton)
    assert window.tag_panel.isHidden()
    assert set(window.selected_topic_tags) == {"人物", "动物"}
    window.close()
    app.processEvents()


def test_first_launch_opens_local_default_library_and_reuses_it(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = tmp_path / "便携程序" / "settings.json"
    window = MainWindow(settings_path=settings)
    assert window.library.root == (settings.parent / "素材库").resolve()
    assert window.import_button.isEnabled()
    assert settings.exists()
    window.library.import_images([picture(tmp_path / "a.png")], new_style="水彩")
    window.close()
    window = MainWindow(settings_path=settings)
    assert window.grid.count() == 1
    assert window.library.styles()[0].name == "水彩"
    window.close()
    app.processEvents()


def test_existing_library_choice_is_not_replaced_with_default(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = tmp_path / "程序" / "settings.json"
    window = MainWindow(settings_path=settings)
    window.open_library(tmp_path / "原素材库")
    window.close()
    window = MainWindow(settings_path=settings)
    assert window.library.root == (tmp_path / "原素材库").resolve()
    window.close()
    app.processEvents()


def test_selected_tag_chips_remove_only_clicked_filter_and_keep_search_text(tmp_path):
    app = QApplication.instance() or QApplication([])
    with Library(tmp_path / "库") as lib:
        pid = lib.import_images([picture(tmp_path / "a.png")], new_style="水彩", topic_tags=["人物", "水彩"]).imported[0]
        lib.edit_style(lib.picture(pid).style_id, "水彩", "", ["水彩"])
    window = MainWindow(settings_path=tmp_path / "settings.json")
    window.open_library(tmp_path / "库")
    window.show()
    window.search_box.setText("水彩")
    window.toggle_tag("style", "水彩", True)
    window.toggle_tag("topic", "水彩", True)
    window.toggle_tag("topic", "人物", True)
    app.processEvents()
    chips = [b for b in window.search_bar.findChildren(QPushButton) if b.isVisible()]
    assert {b.accessibleName() for b in chips} == {"取消画风标签：水彩", "取消主题标签：水彩", "取消主题标签：人物"}
    chip = next(b for b in chips if b.accessibleName() == "取消画风标签：水彩")
    QTest.mouseClick(chip, Qt.MouseButton.LeftButton)
    assert window.selected_style_tags == []
    assert window.selected_topic_tags == ["水彩", "人物"]
    assert window.search_box.text() == "水彩"
    assert window.grid.count() == 1
    window.clear_search()
    assert window.search_bar.tag_scroll.isHidden()
    assert window.search_box.text() == ""
    window.close()
    app.processEvents()
from tests.test_library import picture


def test_user_opens_style_and_enlarges_picture(tmp_path):
    app = QApplication.instance() or QApplication([])
    source = picture(tmp_path / "参考.png")
    with Library(tmp_path / "素材库") as lib:
        lib.import_images([source], new_style="水彩")
    window = MainWindow(settings_path=tmp_path / "settings.json")
    window.open_library(tmp_path / "素材库")
    window.show()
    QTest.qWait(80)
    assert window.grid.count() == 1
    QTest.mouseClick(window.grid.viewport(), Qt.MouseButton.LeftButton,
                     pos=window.grid.visualItemRect(window.grid.item(0)).center())
    QTest.qWait(50)
    assert window.heading.text() == "水彩"
    assert window.grid.count() == 1
    window.close()
    app.processEvents()


def test_import_dialog_saves_batch_and_ui_searches_classified_tags(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(settings_path=tmp_path / "settings.json")
    window.open_library(tmp_path / "素材库")
    window.show()
    sources = [picture(tmp_path / "a.png"), picture(tmp_path / "b.webp", "blue")]

    def fill_import():
        dialog = app.activeModalWidget()
        assert isinstance(dialog, QDialog)
        fields = dialog.findChildren(QLineEdit)
        fields[0].setText("Watercolor 水彩")
        fields[1].setText("人物，动物")
        actions = dialog.findChild(QDialogButtonBox)
        QTest.mouseClick(actions.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)

    QTimer.singleShot(30, fill_import)
    window.import_files(sources)
    for _ in range(1000):
        time.sleep(0.01)
        app.processEvents()
        if not window.job:
            break
    assert window.job is None
    assert window.grid.count() == 1
    window.toggle_tag("topic", "人物", True)
    window.toggle_tag("topic", "动物", True)
    window.search_box.setText("water")
    QTest.qWait(250)
    assert window.grid.count() == 2
    window.search_box.setText("不存在")
    QTest.qWait(250)
    assert window.grid.count() == 0
    window.clear_search()
    assert window.grid.count() == 1
    window.close()
    app.processEvents()


def test_user_cancels_then_confirms_last_picture_deletion(tmp_path):
    app = QApplication.instance() or QApplication([])
    source = picture(tmp_path / "a.jpg")
    with Library(tmp_path / "库") as lib:
        pid = lib.import_images([source], new_style="水彩").imported[0]
        sid = lib.picture(pid).style_id
    window = MainWindow(settings_path=tmp_path / "settings.json")
    window.open_library(tmp_path / "库")
    window.show()

    def respond(answer):
        box = app.activeModalWidget()
        assert isinstance(box, QMessageBox)
        assert "最后一张" in box.text()
        QTest.mouseClick(box.button(answer), Qt.MouseButton.LeftButton)

    QTimer.singleShot(30, lambda: respond(QMessageBox.StandardButton.No))
    window.delete_style(sid)
    assert window.grid.count() == 1
    QTimer.singleShot(30, lambda: respond(QMessageBox.StandardButton.Yes))
    window.delete_style(sid)
    assert window.grid.count() == 0
    assert source.is_file()
    window.close()
    app.processEvents()


def test_user_edits_prompts_then_copies_style_prompt(tmp_path):
    app = QApplication.instance() or QApplication([])
    with Library(tmp_path / "素材库") as lib:
        pid = lib.import_images([picture(tmp_path / "a.png")], new_style="水彩").imported[0]
        sid = lib.picture(pid).style_id
    window = MainWindow(settings_path=tmp_path / "settings.json")
    window.open_library(tmp_path / "素材库")
    window.show()
    dialog = window.edit_style(sid)
    dialog.name.setText("复古水彩")
    dialog.tags.setText("水彩，复古")
    dialog.prompt.setPlainText("纸张质感\n暖色")
    QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    details = window.show_picture(pid)
    QTest.mouseClick(details.copy_button, Qt.MouseButton.LeftButton)
    assert app.clipboard().text() == "纸张质感\n暖色"
    assert "复古水彩" in details.windowTitle()
    details.close()
    window.close()
    app.processEvents()


def test_clear_filter_returns_to_style_home_and_closed_previews_are_released(tmp_path):
    app = QApplication.instance() or QApplication([])
    with Library(tmp_path / "库") as lib:
        pid = lib.import_images([picture(tmp_path / "a.png")], new_style="水彩").imported[0]
    window = MainWindow(settings_path=tmp_path / "settings.json")
    window.open_library(tmp_path / "库")
    window.show()
    QTest.mouseClick(window.grid.viewport(), Qt.MouseButton.LeftButton, pos=window.grid.visualItemRect(window.grid.item(0)).center())
    window.search_box.setText("水彩")
    QTest.qWait(250)
    window.clear_search()
    assert window.grid.item(0).data(Qt.ItemDataRole.UserRole)[0] == "style"
    for _ in range(10):
        details = window.show_picture(pid)
        details.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert window.findChildren(PictureDialog) == []
    window.close()
    app.processEvents()


def test_move_cross_style_search_selection_into_existing_source_style(tmp_path):
    app = QApplication.instance() or QApplication([])
    with Library(tmp_path / "库") as lib:
        a = lib.import_images([picture(tmp_path / "a.png")], new_style="A", topic_tags=["人物"]).imported[0]
        b = lib.import_images([picture(tmp_path / "b.png", "blue")], new_style="B", topic_tags=["人物"]).imported[0]
        target = lib.picture(a).style_id
    window = MainWindow(settings_path=tmp_path / "settings.json")
    window.open_library(tmp_path / "库")
    window.show()
    window.toggle_tag("topic", "人物", True)
    window.grid.selectAll()

    def confirm_move():
        box = app.activeModalWidget()
        assert isinstance(box, QMessageBox)
        assert "B" in box.text()
        QTest.mouseClick(box.button(QMessageBox.StandardButton.Yes), Qt.MouseButton.LeftButton)

    def choose_target():
        dialog = app.activeModalWidget()
        assert isinstance(dialog, QInputDialog)
        dialog.setTextValue(next(t for t in dialog.comboBoxItems() if t.startswith("A ·")))
        dialog.accept()
        QTimer.singleShot(30, confirm_move)

    QTimer.singleShot(30, choose_target)
    window.move_selected()
    assert len(window.library.styles()) == 1
    assert window.library.picture(b).style_id == target
    window.close()
    app.processEvents()


def test_details_copy_original_image_pixels_and_preserve_clipboard_on_failure(tmp_path, monkeypatch):
    from PIL import Image
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "transparent.png"
    Image.new("RGBA", (2000, 80), (20, 40, 60, 100)).save(path)
    window = MainWindow(settings_path=tmp_path / "settings.json")
    pid = window.library.import_images([path], new_style="测试").imported[0]
    window.show()
    details = window.show_picture(pid)
    app.clipboard().setText("old text")
    QTest.mouseClick(details.image_copy_button, Qt.MouseButton.LeftButton)
    copied = app.clipboard().image()
    assert (copied.width(), copied.height()) == (2000, 80)
    assert copied.pixelColor(0, 0).getRgb() == (20, 40, 60, 100)
    assert details.image_copy_button.text() == "✓ 已复制图片"
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args))
    details.path = tmp_path / "missing.png"
    QTest.mouseClick(details.image_copy_button, Qt.MouseButton.LeftButton)
    assert errors
    assert app.clipboard().image() == copied
    details.close()
    window.close()
    app.processEvents()


def test_grid_decodes_thumbnails_in_background_and_reuses_sized_cache(tmp_path, monkeypatch):
    import threading
    from PySide6.QtCore import QSize, QThreadPool
    from PySide6.QtGui import QImage, QPixmapCache
    from PySide6.QtWidgets import QListWidgetItem
    from gallery import grid as module
    app = QApplication.instance() or QApplication([])
    gui_thread = threading.get_ident()
    readers = []
    gate = threading.Event()
    class SlowReader:
        def __init__(self, path):
            readers.append(threading.get_ident())
        def setAutoTransform(self, value): pass
        def size(self): return QSize(1000, 1000)
        def setScaledSize(self, size): self.target = size
        def read(self):
            gate.wait(2)
            image = QImage(self.target, QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.red)
            return image
    monkeypatch.setattr(module, "QImageReader", SlowReader)
    QPixmapCache.clear()
    grid = module.GalleryGrid()
    grid.setViewMode(grid.ViewMode.IconMode)
    grid.setIconSize(QSize(100, 80))
    grid.setGridSize(QSize(140, 130))
    item = QListWidgetItem("one")
    item.setData(Qt.ItemDataRole.UserRole + 1, "background-test.jpg")
    grid.addItem(item)
    grid.show()
    try:
        QTest.qWait(50)
        delegate = grid.itemDelegate()
        assert delegate.pending
        assert readers and all(t != gui_thread for t in readers)
        # A blocked decoder must not prevent a GUI timer from firing.
        fired = []
        QTimer.singleShot(0, lambda: fired.append(True))
        app.processEvents()
        assert fired
        gate.set()
        deadline = time.monotonic() + 3
        while delegate.pending and time.monotonic() < deadline:
            QTest.qWait(10)
        assert not delegate.pending
        count = len(readers)
        grid.viewport().repaint()
        app.processEvents()
        assert len(readers) == count
    finally:
        gate.set()
        QThreadPool.globalInstance().waitForDone(3000)
        grid.close()
        app.processEvents()


@pytest.mark.parametrize("key", [Qt.Key.Key_Backspace, Qt.Key.Key_Delete])
def test_delete_key_removes_search_tags_without_deleting_text_or_other_dimension(tmp_path, key):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(settings_path=tmp_path / "settings.json")
    window.show()
    window.toggle_tag("style", "水彩", True)
    window.toggle_tag("topic", "水彩", True)
    window.toggle_tag("topic", "人物", True)
    field = window.search_box
    field.setText("abc")
    field.setCursorPosition(1 if key == Qt.Key.Key_Backspace else 0)
    field.setFocus()
    QTest.keyClick(field, key)
    assert field.text() == "bc"
    assert window.selected_topic_tags == ["水彩", "人物"]
    field.clear()
    QTest.keyClick(field, key)
    assert window.selected_topic_tags == ["水彩"]
    assert window.selected_style_tags == ["水彩"]
    chip = next(b for b in window.search_bar.findChildren(QPushButton)
                if b.isVisible() and b.accessibleName() == "取消画风标签：水彩")
    chip.setFocus()
    QTest.keyClick(chip, key)
    assert window.selected_style_tags == []
    assert window.selected_topic_tags == ["水彩"]
    QTest.keyClick(field, key)
    assert window.selected_topic_tags == []
    QTest.keyClick(field, key)
    assert field.text() == ""
    window.close()
    app.processEvents()
