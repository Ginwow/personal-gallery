from __future__ import annotations

from PySide6.QtWidgets import QDialog, QFormLayout, QLineEdit, QLabel, QDialogButtonBox

from gallery.translation import WindowsSecrets, Translator
from gallery.ui import button
from gallery.jobs import LibraryJob
from gallery.vision import TopicVerifier


class ApiSettingsDialog(QDialog):
    def __init__(self, parent, catalog):
        super().__init__(parent)
        self.setWindowTitle("DeepSeek翻译与看图核验设置")
        self.resize(580, 320)
        self.catalog = catalog
        self.run_operation = parent.run
        self.secrets = WindowsSecrets("PersonalGallery/DeepSeek")
        self.job = None
        config = catalog.read().get("deepseek", {})
        layout = QFormLayout(self)
        self.address = QLineEdit(config.get("address", "https://api.deepseek.com"))
        self.model = QLineEdit(config.get("model", ""))
        self.model.setPlaceholderText("填写账号支持的模型名称")
        self.vision_model = QLineEdit(config.get("vision_model", "deepseek-flash"))
        self.vision_model.setPlaceholderText("留空使用 deepseek-flash")
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("留空保留Windows凭据管理器中已有密钥")
        layout.addRow("API地址", self.address)
        layout.addRow("模型", self.model)
        layout.addRow("看图模型", self.vision_model)
        layout.addRow("API密钥", self.key)
        note = QLabel("导入前翻译并看图核验主题标签，看图默认使用 deepseek-flash。\n"
                      "与实际画面不符的主题标签会被移除；无法确定或调用失败时整批不更新。\n"
                      "测试连接会分别测试翻译及图片核验，API调用依账号计费。\n"
                      "密钥仅存Windows凭据管理器，不随素材库或备份传递。")
        note.setWordWrap(True)
        layout.addRow(note)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        layout.addRow(self.feedback)
        self.test_button = button("测试连接", self.test)
        layout.addRow(self.test_button)
        self.dialog_actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.dialog_actions.accepted.connect(lambda: parent.run(self.save))
        self.dialog_actions.rejected.connect(self.reject)
        layout.addRow(self.dialog_actions)

    def translator(self):
        return Translator(self.address.text().strip(), self.model.text().strip(), self.key.text().strip() or self.secrets.get())

    def test(self):
        if self.job:
            return
        def start():
            translator = self.translator()
            verifier = self.verifier()
            self.feedback.setText("正在测试连接…")
            self.test_button.setEnabled(False)
            self.dialog_actions.setEnabled(False)
            def check():
                import tempfile
                from pathlib import Path
                from PIL import Image, ImageDraw
                translator.translate("Connection test", "Draw a cat.", [])
                with tempfile.TemporaryDirectory(prefix="gallery-vision-test-") as directory:
                    image = Path(directory) / "test.png"
                    canvas = Image.new("RGB", (400, 300), "white")
                    ImageDraw.Draw(canvas).rectangle((80, 80, 320, 220), fill="blue")
                    canvas.save(image)
                    report = verifier(image, ["人物"])
                    if report["keep"]:
                        raise ValueError("看图测试未通过：无人物的几何图被判有人物，请检查视觉模型")
                return True
            job = LibraryJob(check, self)
            self.job = job
            job.completed.connect(lambda result, error: self.feedback.setText("测试失败：" + str(error) if error else "翻译及图片核验测试通过。"))
            def finish():
                self.job = None
                self.test_button.setEnabled(True)
                self.dialog_actions.setEnabled(True)
                job.deleteLater()
            job.finished.connect(finish)
            job.start()
        self.run_operation(start)

    def save(self):
        self.translator()
        self.verifier()
        if self.key.text().strip():
            self.secrets.set(self.key.text().strip())
        data = self.catalog.read()
        data["deepseek"] = {"address": self.address.text().strip(), "model": self.model.text().strip(),
                            "vision_model": self.vision_model.text().strip() or "deepseek-flash"}
        self.catalog.write(data)
        self.accept()

    def verifier(self):
        return TopicVerifier(self.address.text().strip(), self.vision_model.text().strip() or "deepseek-flash",
                             self.key.text().strip() or self.secrets.get())

    def reject(self):
        if not self.job:
            super().reject()

    def closeEvent(self, event):
        if self.job:
            event.ignore()
        else:
            super().closeEvent(event)
