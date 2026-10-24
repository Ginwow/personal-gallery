"""Search text and removable, categorized tag filters in one field."""
from PySide6.QtCore import Qt, Signal, QEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QPushButton, QScrollArea, QWidget


class TagSearchBar(QFrame):
    tagRemoved = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.setObjectName("tagSearchBar")
        self.setMinimumWidth(210)
        self.setStyleSheet("""
            QFrame#tagSearchBar { background: #20232b; border: 1px solid #3a404c; border-radius: 6px; }
            QLineEdit#searchText { background: transparent; border: none; padding: 6px 4px; }
            QWidget#tagStrip, QScrollArea#tagScroll { background: transparent; border: none; }
            QPushButton#searchTag { background: #37372f; color: #e7d3ad; border: 1px solid #6c6048;
                border-radius: 5px; padding: 4px 8px; }
            QPushButton#searchTag:hover { background: #514737; }
            QScrollBar:horizontal { height: 5px; background: transparent; }
            QScrollBar::handle:horizontal { background: #70654e; min-width: 20px; border-radius: 2px; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
        """)
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 3, 6, 3)
        row.setSpacing(6)
        self.strip = QWidget()
        self.strip.setObjectName("tagStrip")
        self.chips = QHBoxLayout(self.strip)
        self.chips.setContentsMargins(0, 0, 0, 0)
        self.chips.setSpacing(5)
        self.tag_scroll = QScrollArea()
        self.tag_scroll.setObjectName("tagScroll")
        self.tag_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.tag_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tag_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.tag_scroll.setWidget(self.strip)
        self.tag_scroll.viewport().setStyleSheet("background: transparent;")
        self.tag_scroll.setFixedHeight(36)
        row.addWidget(self.tag_scroll)
        self.input = QLineEdit()
        self.input.setObjectName("searchText")
        self.input.installEventFilter(self)
        self.input.setMinimumWidth(100)
        self.input.setPlaceholderText("搜索画风名称或标签…")
        row.addWidget(self.input, 1)
        self.setFocusProxy(self.input)
        self._selection: tuple[tuple[str, str], ...] = ()
        self.tag_scroll.hide()

    def set_tags(self, style_tags: list[str], topic_tags: list[str]):
        selection = tuple(("style", name) for name in style_tags) + tuple(("topic", name) for name in topic_tags)
        if selection == self._selection:
            return
        self._selection = selection
        while self.chips.count():
            item = self.chips.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.deleteLater()
        for kind, name in selection:
            category = "画风" if kind == "style" else "主题"
            chip = QPushButton(f"{category} · {name}  ×")
            chip.setObjectName("searchTag")
            chip.setAccessibleName(f"取消{category}标签：{name}")
            chip.setToolTip(f"点击取消{category}标签：{name}；聚焦后也可按退格键或 Delete 删除")
            chip.setProperty("tagIdentity", (kind, name))
            chip.installEventFilter(self)
            chip.clicked.connect(lambda _checked=False, k=kind, n=name: self.tagRemoved.emit(k, n))
            self.chips.addWidget(chip)
            chip.show()
        self.chips.invalidate()
        self.chips.activate()
        self.strip.setFixedSize(self.chips.sizeHint())
        self.tag_scroll.setVisible(bool(selection))
        self._fit_tags()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            identity = None
            if watched is self.input:
                if not self.input.text() and self._selection:
                    identity = self._selection[-1]
            elif isinstance(watched, QPushButton):
                identity = watched.property("tagIdentity")
            if identity and tuple(identity) in self._selection:
                if not event.isAutoRepeat():
                    self.input.setFocus()
                    self.tagRemoved.emit(*identity)
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _fit_tags(self):
        self.tag_scroll.setFixedWidth(min(self.strip.width() + 2, max(60, int(self.width() * 0.6))))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_tags()

