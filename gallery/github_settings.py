"""Local GitHub credential entry; tokens never enter catalog or library files."""
import json
from datetime import datetime
from PySide6.QtWidgets import QDialog, QFormLayout, QLineEdit, QLabel, QDialogButtonBox, QCheckBox
from gallery.ui import button
from gallery.jobs import LibraryJob
from gallery.sources import github_secrets, fetch_bytes, reset_github_cooldown


class GitHubSettingsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("GitHub设置")
        self.resize(560, 320)
        self.run_operation = parent.run
        self.secrets = github_secrets()
        self.job = None
        layout = QFormLayout(self)
        self.status = QLabel("已保存Token" if self.secrets.get() else "尚未配置Token，当前使用匿名额度")
        layout.addRow(self.status)
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("粘贴新Token；留空保留已保存的Token")
        layout.addRow("GitHub Token", self.token)
        self.remove = QCheckBox("删除已保存的Token，恢复匿名访问")
        self.remove.toggled.connect(lambda checked: self.token.setEnabled(not checked))
        layout.addRow(self.remove)
        note = QLabel("仅需公开仓库只读访问，无需写入权限。\nToken仅存Windows凭据管理器，不随素材库或备份传递。\n测试使用当前输入，点击保存后才会用于检查更新。")
        note.setWordWrap(True)
        layout.addRow(note)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        layout.addRow(self.feedback)
        self.test_button = button("测试连接与额度", lambda: self.run_operation(self.test))
        layout.addRow(self.test_button)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(lambda: self.run_operation(self.save))
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)

    def selected_token(self):
        token = "" if self.remove.isChecked() else self.token.text().strip() or self.secrets.get()
        if token and (not token.isascii() or any(c.isspace() for c in token)):
            raise ValueError("Token格式无效，请重新粘贴")
        return token

    def save(self):
        token = self.selected_token()
        if self.remove.isChecked():
            self.secrets.delete()
        elif self.token.text().strip():
            self.secrets.set(token)
        reset_github_cooldown()
        self.token.clear()
        self.accept()

    def test(self):
        if self.job:
            return
        token = self.selected_token()
        self.feedback.setText("正在测试GitHub连接…")
        self.buttons.setEnabled(False)
        self.test_button.setEnabled(False)
        self.token.setEnabled(False)
        self.remove.setEnabled(False)
        job = LibraryJob(lambda: json.loads(fetch_bytes("https://api.github.com/rate_limit", github_token=token)), self)
        self.job = job
        def completed(result, error):
            if error:
                self.feedback.setText("测试失败：" + str(error))
                return
            core = result["resources"]["core"]
            reset = datetime.fromtimestamp(core["reset"]).strftime("%Y-%m-%d %H:%M:%S")
            self.feedback.setText(("Token连接成功" if token else "匿名连接成功") +
                f"；剩余 {core['remaining']} / {core['limit']} 次；重置时间：{reset}（本地时间）。")
        def finish():
            self.job = None
            self.buttons.setEnabled(True)
            self.test_button.setEnabled(True)
            self.remove.setEnabled(True)
            self.token.setEnabled(not self.remove.isChecked())
            job.deleteLater()
        job.completed.connect(completed)
        job.finished.connect(finish)
        job.start()

    def reject(self):
        if not self.job:
            self.token.clear()
            super().reject()

    def closeEvent(self, event):
        if self.job:
            event.ignore()
        else:
            super().closeEvent(event)
