from PySide6.QtCore import QThread, Signal


class LibraryJob(QThread):
    completed = Signal(object, object)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            result = self.operation()
        except Exception as exc:
            self.completed.emit(None, exc)
        else:
            self.completed.emit(result, None)
