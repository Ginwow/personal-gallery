import logging
import json
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QImageReader, QIcon

from gallery.ui import MainWindow
from gallery.workspace import WorkspaceWindow


def main():
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    logging.basicConfig(filename=base / "gallery.log", level=logging.ERROR, encoding="utf-8")
    app = QApplication(sys.argv)
    app.setApplicationName("个人画廊")
    resource_root = Path(getattr(sys, "_MEIPASS", base))
    app.setWindowIcon(QIcon(str(resource_root / "assets" / "gallery.ico")))

    def exception_hook(kind, value, traceback):
        logging.error("操作失败", exc_info=(kind, value, traceback))
        QMessageBox.critical(None, "操作未完成", str(value))  # type: ignore[arg-type]  # Qt permits a null parent.

    sys.excepthook = exception_hook
    collection_root = base
    preview = "--preview" in sys.argv and not getattr(sys, "frozen", False)
    settings_path = base / "work" / "multi-preview" / "settings.json" if preview else base / "settings.json"
    default_library = base / "work" / "multi-preview" / "素材库" if preview else collection_root / "素材库" / "个人"
    preview_config = json.loads(settings_path.read_text("utf-8")) if preview and settings_path.exists() else {}
    libraries_path = Path(preview_config["material_root"]) if preview_config.get("material_mode") == "full-local" else (default_library.parent if not preview else default_library)
    window = WorkspaceWindow(settings_path, libraries_path)
    if not getattr(sys, "frozen", False):
        label = "（完整本地素材）" if preview_config.get("material_mode") == "full-local" else "（测试素材副本）" if preview else ""
        window.setWindowTitle("个人画廊 · 开发版" + label)
    if "--smoke-test" in sys.argv:
        index = sys.argv.index("--smoke-test")
        report = Path(sys.argv[index + 1])
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)

        def verify():
            report.write_text(json.dumps({"frozen": bool(getattr(sys, "frozen", False)),
                                          "platform": app.platformName(), "visible": window.isVisible(),
                                          "width": window.width(), "height": window.height(),
                                          "has_icon": not app.windowIcon().isNull(),
                                          "image_formats": [bytes(f.data()).decode() for f in QImageReader.supportedImageFormats()]}), "utf-8")
            window.close()
        QTimer.singleShot(800, verify)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
