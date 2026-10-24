"""Non-overlapping multi-select tag panel with outside-click dismissal."""
from PySide6.QtCore import QEvent, QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QApplication, QCheckBox, QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget


class TagCheckBox(QCheckBox):
    """The entire tag row is a click target, including its trailing space."""

    def hitButton(self, pos):
        return self.rect().contains(pos)


class TagFilterPanel(QFrame):
    tagToggled = Signal(str, str, bool)

    def __init__(self, anchors):
        super().__init__()
        self.anchors = anchors
        self.kind = ""
        self.names: tuple[str, ...] | None = None
        self.boxes: dict[str, QCheckBox] = {}
        self.setFixedWidth(210)
        self.setStyleSheet("QFrame#tagPanel { background: #20232b; border: 1px solid #383e4a; border-radius: 7px; }")
        self.setObjectName("tagPanel")
        layout = QVBoxLayout(self)
        self.title = QLabel()
        layout.addWidget(self.title)
        self.hint = QLabel()
        self.hint.setObjectName("muted")
        layout.addWidget(self.hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        contents = QWidget()
        self.options = QVBoxLayout(contents)
        self.options.setContentsMargins(4, 6, 4, 6)
        scroll.setWidget(contents)
        layout.addWidget(scroll, 1)
        self.hide()

    def open_category(self, kind, names, selected):
        if self.isVisible() and self.kind == kind:
            self.hide()
            return
        self.kind = kind
        self.names = None
        self.sync(names, selected)
        self.title.setText("画风标签" if kind == "style" else "主题标签")
        self.hint.setText(("单选 · 可取消选择" if kind == "style" else "可连续多选") + "\n点击面板外收起")
        self.show()

    def sync(self, names, selected):
        names = tuple(names)
        if names != self.names or not self.options.count():
            self.names = names
            self.boxes.clear()
            while self.options.count():
                item = self.options.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.hide()
                    widget.deleteLater()
            for name in names:
                box: QCheckBox = TagCheckBox(name)
                box.setMinimumHeight(36)
                box.setCursor(Qt.CursorShape.PointingHandCursor)
                box.setStyleSheet("QCheckBox { padding: 6px; border-radius: 5px; } QCheckBox:hover { background: #373e4b; }")
                box.setToolTip(name)
                box.toggled.connect(lambda checked, n=name: self.tagToggled.emit(self.kind, n, checked))
                self.boxes[name] = box
                self.options.addWidget(box)
            if not names:
                self.options.addWidget(QLabel("暂无标签"))
            self.options.addStretch()
        for name, box in self.boxes.items():
            with QSignalBlocker(box):
                box.setChecked(name in selected)

    def showEvent(self, event):
        super().showEvent(event)
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)

    def hideEvent(self, event):
        app = QApplication.instance()
        if app:
            app.removeEventFilter(self)
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        if self.isVisible():
            if event.type() == QEvent.Type.MouseButtonPress:
                point = event.globalPosition().toPoint()
                inside = self.rect().contains(self.mapFromGlobal(point))
                anchor = any(b.rect().contains(b.mapFromGlobal(point)) for b in self.anchors)
                if not inside and not anchor:
                    self.hide()
            elif event.type() == QEvent.Type.ApplicationDeactivate:
                self.hide()
        return False
