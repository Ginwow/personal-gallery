import json
from pathlib import Path
import pytest
from gallery.library import Library
from tests.test_library import picture


def test_prepare_failure_leaves_library_unchanged_and_retry_reuses_cache(tmp_path):
    from gallery.updates import UpdateService, SourcePicture
    root = tmp_path / "库"
    with Library(root) as lib:
        lib.import_images([picture(tmp_path / "old.png")], new_style="原画风")
    calls = []
    def translate(name, prompt, tags):
        calls.append(name)
        if name == "Fail":
            raise ValueError("翻译失败")
        return {"name": "新画风", "prompt": "中文提示词", "tags": []}
    image = picture(tmp_path / "new.png", "blue").read_bytes()
    service = UpdateService(root, tmp_path / "cache")
    rows = [SourcePicture("one", "a", "New", "A prompt", [], "https://example.com/a.png"),
            SourcePicture("two", "b", "Fail", "Another prompt", [], "https://example.com/b.png")]
    before = (root / "gallery.sqlite3").read_bytes()
    with pytest.raises(ValueError, match="翻译失败"):
        service.prepare(rows, translate, lambda url: image)
    assert (root / "gallery.sqlite3").read_bytes() == before
    assert not service.pending()
    service.prepare(rows[:1], translate, lambda url: image)
    assert calls == ["New", "Fail"]
    service.commit()
    with Library(root) as lib:
        assert len(lib.images()) == 2
        assert {s.name for s in lib.styles()} == {"原画风", "新画风"}
    assert service.new_images(rows[:1]) == []


def test_existing_source_tag_changes_never_call_api_and_deleted_images_stay_ignored(tmp_path):
    from gallery.updates import UpdateService, SourcePicture
    root = tmp_path / "库"
    with Library(root):
        pass
    service = UpdateService(root, tmp_path / "cache")
    image = picture(tmp_path / "new.png", "blue").read_bytes()
    translate = lambda name, prompt, tags: {"name": "海报", "prompt": "中文", "tags": [
        {"original": t, "name": "广告" if t == "ad" else "人物", "kind": "topic"} for t in tags]}
    original = SourcePicture("one", "a", "Poster", "Prompt", [], "https://example.com/a.png")
    service.prepare([original], translate, lambda url: image)
    service.commit()
    with Library(root) as lib:
        pid = lib.images()[0].id
        lib.edit_picture(pid, "本地提示词", ["本地标签"])
    changed = SourcePicture("one", "a", "Poster", "Changed prompt", ["ad"], "https://example.com/a.png")
    assert service.tag_changes([changed]) == 0
    def forbidden(*args):
        raise AssertionError("existing image must not call translation, download or vision")
    service.prepare([changed], forbidden, forbidden, verify=forbidden)
    service.commit()
    with Library(root) as lib:
        assert lib.picture(pid).original_prompt == "本地提示词"
        assert set(lib.picture(pid).tags) == {"本地标签"}
        lib.delete_images([pid])
    assert service.new_images([changed]) == []
    assert len(service.ignored()) == 1
    service.restore_ignored([("one", "a")])
    assert service.new_images([changed]) == [changed]


def test_supported_case_source_is_pinned_and_reports_extra_case_images(tmp_path):
    from gallery.sources import GitHubSource
    calls = []
    def fetch(url):
        calls.append(url)
        if url.endswith('/repos/freestylefly/awesome-gpt-image-2'):
            return json.dumps({"default_branch": "main"}).encode()
        if '/git/trees/' in url:
            return json.dumps({"sha": "a" * 40, "tree": [
                {"path": "data/cases.json", "type": "blob"},
                {"path": "data/images/case1.jpg", "type": "blob"}]}).encode()
        if url.endswith('/data/cases.json'):
            return json.dumps({"cases": [{"id": 1, "title": "Poster", "prompt": "Draw a cat", "image": "/images/case1.jpg", "styles": ["watercolor"], "scenes": ["ad"]}]}).encode()
        raise AssertionError(url)
    source = GitHubSource("https://github.com/freestylefly/awesome-gpt-image-2", fetch=fetch)
    rows = source.check()
    assert len(rows) == 1
    assert rows[0].case_id == "1"
    assert rows[0].image_id == "main"
    assert rows[0].tags == ["watercolor", "ad"]
    assert '/' + 'a' * 40 + '/data/cases.json' in calls[-1]
    assert rows[0].url.endswith('/data/images/case1.jpg')


def test_split_source_library_preserves_edits_and_leaves_unknown_content(tmp_path):
    from gallery.migration import split_library
    root = tmp_path / "Atlas"
    with Library(root) as lib:
        ids = lib.import_images([picture(tmp_path / "a.png"), picture(tmp_path / "b.png", "blue")], new_style="本地改名").imported
        lib.edit_picture(ids[0], "已编辑中文", ["本地标签"])
        sid = lib.picture(ids[0]).style_id
    (root / "来源与标签.json").write_text(json.dumps({"repository": "https://github.com/callirra-ai/gpt-image-2-5-prompt-atlas", "records": [
        {"source_id": "case/one", "style_id": sid, "images": [{"picture_id": ids[0], "source_file": "case/one/full.jpg"}]}]}), "utf-8")
    result = split_library(root)
    assert len(result) == 1
    with Library(root) as local:
        assert [p.id for p in local.images()] == [ids[1]]
    with Library(result[0][1]) as source:
        assert source.picture(ids[0]).original_prompt == "已编辑中文"
        assert source.picture(ids[0]).tags == ("本地标签",)
        assert source.style(sid).name == "本地改名"


def test_prepared_batch_refuses_to_overwrite_later_local_edits(tmp_path):
    from gallery.updates import UpdateService, SourcePicture
    root = tmp_path / "库"
    with Library(root) as lib:
        pid = lib.import_images([picture(tmp_path / "a.png")], new_style="原图").imported[0]
    service = UpdateService(root, tmp_path / "cache")
    service.prepare([SourcePicture("one", "a", "New", "Prompt", [], "https://example.com/a.png")],
        lambda *args: {"name": "新", "prompt": "中文", "tags": []},
        lambda url: picture(tmp_path / "b.png", "blue").read_bytes())
    with Library(root) as lib:
        lib.edit_picture(pid, "准备期间的本地修改", [])
    with pytest.raises(ValueError, match="本地修改"):
        service.commit()
    with Library(root) as lib:
        assert len(lib.images()) == 1
        assert lib.picture(pid).original_prompt == "准备期间的本地修改"


def test_commit_interrupted_after_old_directory_move_restores_on_next_open(tmp_path, monkeypatch):
    from gallery.updates import UpdateService, SourcePicture
    from gallery.catalog import Catalog
    root = tmp_path / "素材库" / "库"
    with Library(root) as lib:
        lib.import_images([picture(tmp_path / "a.png")], new_style="旧")
    catalog = Catalog(tmp_path / "settings.json", root.parent)
    service = UpdateService(root, tmp_path / "cache")
    service.prepare([SourcePicture("one", "a", "New", "Prompt", [], "https://example.com/a.png")],
        lambda *args: {"name": "新", "prompt": "中文", "tags": []},
        lambda url: picture(tmp_path / "b.png", "blue").read_bytes())
    # Simulate an OS process exit after old-root rename by retaining its journal.
    marker = service.transaction / "transaction.json"
    marker.write_text(json.dumps({"root": str(root.resolve())}), "utf-8")
    root.rename(service.transaction / "old")
    catalog = Catalog(tmp_path / "settings.json", root.parent)
    with Library(catalog.path(catalog.entries()[0]["id"])) as lib:
        assert len(lib.images()) == 1
        assert lib.styles()[0].name == "旧"


def test_catalog_does_not_restore_an_active_transaction(tmp_path):
    from gallery.updates import UpdateService
    from gallery.catalog import Catalog
    from PySide6.QtCore import QLockFile
    root = tmp_path / "素材库" / "库"
    with Library(root):
        pass
    catalog = Catalog(tmp_path / "settings.json", root.parent)
    service = UpdateService(root, tmp_path / "cache")
    service.transaction.mkdir()
    (service.transaction / "transaction.json").write_text(json.dumps({"root": str(root.resolve())}), "utf-8")
    maintenance = QLockFile(str(root.parent / ("." + root.name + ".maintenance.lock")))
    assert maintenance.tryLock(0)
    root.rename(service.transaction / "old")
    try:
        Catalog(tmp_path / "settings.json", root.parent)
        assert not root.exists()
        assert (service.transaction / "transaction.json").exists()
    finally:
        maintenance.unlock()
    Catalog(tmp_path / "settings.json", root.parent)
    assert root.exists()


def test_one_prepared_plan_cannot_commit_a_different_instances_stage(tmp_path):
    from gallery.updates import UpdateService
    root = tmp_path / "库"
    with Library(root):
        pass
    first = UpdateService(root, tmp_path / "first-cache")
    second = UpdateService(root, tmp_path / "second-cache")
    first.prepare([], lambda *args: None, lambda *args: b"")
    second.prepare([], lambda *args: None, lambda *args: b"")
    with pytest.raises(ValueError, match="另一批更新"):
        first.commit()
    second.commit()


@pytest.mark.parametrize("both_markers", [False, True])
def test_completed_split_is_registered_after_restart_even_if_ui_callback_did_not_run(tmp_path, both_markers):
    from gallery.migration import split_library
    from gallery.catalog import Catalog
    root = tmp_path / "素材库" / "Atlas"
    with Library(root) as lib:
        pid = lib.import_images([picture(tmp_path / "a.png")], new_style="画风").imported[0]
        sid = lib.picture(pid).style_id
    (root / "来源与标签.json").write_text(json.dumps({"repository": "https://github.com/callirra-ai/gpt-image-2-5-prompt-atlas", "records": [
        {"source_id": "case/one", "style_id": sid, "images": [{"picture_id": pid, "source_file": "case/one/full.jpg"}]}]}), "utf-8")
    catalog = Catalog(tmp_path / "settings.json", root.parent)
    targets = split_library(root)
    if both_markers:
        transaction = root.parent / ("." + root.name + ".gallery-split")
        (transaction / "transaction.json").write_text(json.dumps({"root": str(root.resolve()),
            "outputs": [p.name for _, p in targets]}), "utf-8")
    catalog = Catalog(tmp_path / "settings.json", root.parent)
    assert len(catalog.entries()) == 2
    assert sum(bool(e.get("source")) for e in catalog.entries()) == 1


def test_backup_restores_source_mapping_without_external_manifest_files(tmp_path):
    from gallery.updates import baseline
    from gallery.catalog import Catalog
    root = tmp_path / "库"
    with Library(root) as lib:
        pid = lib.import_images([picture(tmp_path / "a.png")], new_style="画风").imported[0]
        sid = lib.picture(pid).style_id
        (root / "来源与标签.json").write_text(json.dumps({"repository": "https://github.com/callirra-ai/gpt-image-2-5-prompt-atlas", "records": [
            {"source_id": "case/one", "style_id": sid, "images": [{"picture_id": pid, "source_file": "case/one/full.jpg"}]}]}), "utf-8")
        lib.backup(tmp_path / "backup.zip")
    restored = tmp_path / "restored"
    Library.restore(tmp_path / "backup.zip", restored)
    assert baseline(restored)[0]["picture"] == pid
    assert Catalog.source_info(restored)["source"] == "https://github.com/callirra-ai/gpt-image-2-5-prompt-atlas"


def test_restore_rejects_source_manifest_paths_that_escape_the_library(tmp_path):
    import sqlite3
    import zipfile
    from contextlib import closing
    root = tmp_path / "库"
    with Library(root) as lib:
        lib.backup(tmp_path / "normal.zip")
    snapshot = tmp_path / "malicious.sqlite3"
    with zipfile.ZipFile(tmp_path / "normal.zip") as archive:
        snapshot.write_bytes(archive.read("gallery.sqlite3"))
    with closing(sqlite3.connect(snapshot)) as db:
        with db:
            db.execute("INSERT INTO source_manifests VALUES(?,?)", ("../escaped来源.json", '{}'))
    with zipfile.ZipFile(tmp_path / "bad.zip", "w") as archive:
        archive.write(snapshot, "gallery.sqlite3")
    with pytest.raises(ValueError, match="来源清单文件名"):
        Library.restore(tmp_path / "bad.zip", tmp_path / "restored")
    assert not (tmp_path / "escaped来源.json").exists()


def test_github_rate_limit_reports_reset_and_pauses_api_only(monkeypatch):
    from types import SimpleNamespace
    from gallery import sources
    monkeypatch.setattr(sources, "github_secrets", lambda: SimpleNamespace(get=lambda: ""))
    from urllib.error import HTTPError
    from email.message import Message
    from io import BytesIO
    from gallery import sources
    headers = Message()
    headers["x-ratelimit-remaining"] = "0"
    headers["x-ratelimit-reset"] = "2000000000"
    calls = []
    def limited(request, **kwargs):
        calls.append(request.full_url)
        raise HTTPError(request.full_url, 403, "Forbidden", headers, BytesIO(b"{}"))
    monkeypatch.setattr(sources, "_github_retry_at", 0)
    monkeypatch.setattr(sources.time, "time", lambda: 1900000000)
    monkeypatch.setattr(sources, "urlopen", limited)
    with pytest.raises(sources.GitHubRateLimitError, match="额度已用完"):
        sources.fetch_bytes("https://api.github.com/repos/a/b")
    with pytest.raises(sources.GitHubRateLimitError):
        sources.fetch_bytes("https://api.github.com/repos/c/d")
    assert len(calls) == 1
    with pytest.raises(ValueError, match="HTTP 403"):
        sources.fetch_bytes("https://raw.githubusercontent.com/a/b/main/image.png")
    assert len(calls) == 2
    monkeypatch.setattr(sources.time, "time", lambda: 2000000001)
    with pytest.raises(sources.GitHubRateLimitError):
        sources.fetch_bytes("https://api.github.com/repos/a/b")
    assert len(calls) == 3
