import json
from io import BytesIO
import pytest
from gallery import sources


def test_token_only_sent_to_api_and_explicit_test_ignores_old_cooldown(monkeypatch):
    calls = []
    class Opener:
        def open(self, request, **kwargs):
            calls.append(request)
            return BytesIO(b"{}")
    monkeypatch.setattr(sources, "build_opener", lambda *a: Opener())
    monkeypatch.setattr(sources, "urlopen", Opener().open)
    monkeypatch.setattr(sources, "_github_retry_at", 9999999999)
    sources.fetch_bytes("https://api.github.com/rate_limit", github_token="test-secret")
    sources.fetch_bytes("https://raw.githubusercontent.com/a/b/main/a.png", github_token="test-secret")
    assert calls[0].get_header("Authorization") == "Bearer test-secret"
    assert calls[1].get_header("Authorization") is None


def test_authenticated_redirect_is_refused():
    from urllib.request import Request
    with pytest.raises(ValueError, match="重定向"):
        sources._NoCredentialRedirect().redirect_request(Request("https://api.github.com/test"), None, 302, "", {}, "https://other.example/")


def test_token_dialog_saves_and_deletes_without_catalog(monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QWidget, QLineEdit
    from gallery import github_settings
    app = QApplication.instance() or QApplication([])
    class Parent(QWidget):
        def run(self, operation):
            return operation()
    class Secrets:
        value = ""
        def get(self): return self.value
        def set(self, value): self.value = value
        def delete(self): self.value = ""
    secrets = Secrets()
    monkeypatch.setattr(github_settings, "github_secrets", lambda: secrets)
    parent = Parent()
    dialog = github_settings.GitHubSettingsDialog(parent)
    assert dialog.token.echoMode() == QLineEdit.EchoMode.Password
    dialog.token.setText("test-secret")
    monkeypatch.setattr(sources, "_github_retry_at", 9999999999)
    dialog.save()
    assert secrets.value == "test-secret"
    assert sources._github_retry_at == 0
    again = github_settings.GitHubSettingsDialog(parent)
    assert again.token.text() == ""
    again.remove.setChecked(True)
    again.save()
    assert secrets.value == ""


def test_saved_token_used_and_invalid_token_has_safe_message(monkeypatch):
    from types import SimpleNamespace
    from urllib.error import HTTPError
    seen = []
    class Opener:
        def open(self, request, **kwargs):
            seen.append(request.get_header("Authorization"))
            raise HTTPError(request.full_url, 401, "secret", {}, BytesIO(b"private"))
    monkeypatch.setattr(sources, "build_opener", lambda *a: Opener())
    monkeypatch.setattr(sources, "github_secrets", lambda: SimpleNamespace(get=lambda: "saved-test-token"))
    monkeypatch.setattr(sources, "_github_retry_at", 0)
    with pytest.raises(ValueError, match="Token无效或已过期") as error:
        sources.fetch_bytes("https://api.github.com/repos/a/b")
    assert seen == ["Bearer saved-test-token"]
    assert "saved-test-token" not in str(error.value)
