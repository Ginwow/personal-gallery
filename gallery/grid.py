from typing import Any

from PySide6.QtCore import Qt, Signal, Slot, QMimeData, QObject, QRunnable, QThreadPool
from PySide6.QtGui import QIcon, QPixmap, QPixmapCache, QDrag, QImage, QImageReader
from PySide6.QtWidgets import QListWidget, QAbstractItemView, QStyledItemDelegate, QStyleOptionViewItem, QStyle, QApplication


class ThumbnailSignals(QObject):
    ready = Signal(str, QImage)


class ThumbnailTask(QRunnable):
    def __init__(self, key, path, size):
        super().__init__()
        self.key, self.path, self.size = key, path, size
        self.signals = ThumbnailSignals()

    def run(self):
        reader = QImageReader(self.path)
        reader.setAutoTransform(True)
        original = reader.size()
        if original.isValid():
            reader.setScaledSize(original.scaled(self.size, Qt.AspectRatioMode.KeepAspectRatio))
        image = reader.read()
        if not image.isNull():
            image = image.scaled(self.size, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        self.signals.ready.emit(self.key, image)


class ThumbnailDelegate(QStyledItemDelegate):
    """Painting never reads image files; bounded background work fills the cache."""
    def __init__(self, parent):
        super().__init__(parent)
        self.view = parent
        self.pending = {}
        self.failed = set()

    @Slot(str, QImage)
    def loaded(self, key, image):
        self.pending.pop(key, None)
        if image.isNull():
            if len(self.failed) >= 256:
                self.failed.clear()
            self.failed.add(key)
        else:
            QPixmapCache.insert(key, QPixmap.fromImage(image))
        self.view.viewport().update()

    def paint(self, painter, option, index):
        styled: Any = QStyleOptionViewItem(option)  # Qt stubs omit option data fields.
        self.initStyleOption(styled, index)
        path = index.data(Qt.ItemDataRole.UserRole + 1)
        if path:
            ratio = styled.widget.devicePixelRatioF() if styled.widget else 1.0
            size = styled.decorationSize * ratio
            key = f"gallery-thumb-v2:{path}:{size.width()}x{size.height()}:{ratio}"
            pixmap = QPixmap()
            if QPixmapCache.find(key, pixmap):
                pixmap.setDevicePixelRatio(ratio)
                styled.icon = QIcon(pixmap)
            elif key not in self.pending and key not in self.failed and len(self.pending) < 24:
                task = ThumbnailTask(key, path, size)
                task.signals.ready.connect(self.loaded)
                self.pending[key] = task
                QThreadPool.globalInstance().start(task)
            styled.features |= QStyleOptionViewItem.ViewItemFeature.HasDecoration
        style = styled.widget.style() if styled.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, styled.widget)


class GalleryGrid(QListWidget):
    filesDropped = Signal(list)
    orderChanged = Signal()

    def __init__(self):
        super().__init__()
        QPixmapCache.setCacheLimit(32 * 1024)
        self.setItemDelegate(ThumbnailDelegate(self))
        self.setUniformItemSizes(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.reorder_enabled = False
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def startDrag(self, supported_actions):
        if not self.reorder_enabled or not self.selectedItems():
            return
        # Own the drag so QAbstractItemView cannot delete source rows after our reorder.
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText("个人画廊图片排序")
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or (event.source() is self and self.reorder_enabled):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            if paths:
                self.filesDropped.emit(paths)
                event.acceptProposedAction()
            return
        if event.source() is not self or not self.reorder_enabled:
            event.ignore()
            return
        target = self.itemAt(event.position().toPoint())
        target_row = self.row(target) if target else self.count()
        selected = sorted(self.row(item) for item in self.selectedItems())
        if not selected:
            return
        target_row -= sum(row < target_row for row in selected)
        moved = [self.takeItem(row) for row in reversed(selected)]
        for offset, item in enumerate(reversed(moved)):
            self.insertItem(target_row + offset, item)
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        self.orderChanged.emit()


