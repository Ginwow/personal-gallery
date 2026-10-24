from pathlib import Path

from PIL import Image
import pytest
import zipfile

from gallery.library import Library


def picture(path: Path, color: str = "red") -> Path:
    Image.new("RGB", (160, 120), color).save(path)
    return path


def test_import_survives_source_removal_and_reopen(tmp_path):
    source = picture(tmp_path / "水彩.png")
    root = tmp_path / "素材库"
    with Library(root) as lib:
        result = lib.import_images([source], new_style="复古水彩")
        assert len(result.imported) == 1
        style = lib.styles()[0]
        assert style.name == "复古水彩"
        assert style.cover == result.imported[0]
    source.unlink()
    with Library(root) as lib:
        images = lib.images(style.id)
        assert len(images) == 1
        assert lib.original(images[0].id).is_file()
        assert lib.thumbnail(images[0].id).is_file()


def test_tags_search_same_picture_and_prompt_ownership(tmp_path):
    with Library(tmp_path / "库") as lib:
        result = lib.import_images([picture(tmp_path / "a.png"), picture(tmp_path / "b.png", "blue")], new_style="Watercolor 水彩")
        a, b = result.imported
        sid = lib.picture(a).style_id
        lib.edit_style(sid, "Watercolor 水彩", "共同笔触\n低饱和", ["复古"])
        lib.edit_picture(a, "一位人物的完整提示词", ["人物"])
        lib.edit_picture(b, "一只动物的完整提示词", ["动物"])
        assert lib.search(topic_tags=["人物", "动物"]) == []
        lib.edit_picture(a, "人物与猫", ["人物", "动物"])
        assert [p.id for p in lib.search(text="water 复古", style_tags=["复古"], topic_tags=["人物", "动物"])] == [a]
        assert lib.search(text="低饱和") == []
        assert lib.style(sid).prompt == "共同笔触\n低饱和"
        assert lib.picture(a).original_prompt == "人物与猫"
        lib.rename_tag("topic", "人物", "人像")
        assert [p.id for p in lib.search(topic_tags=["人像"])] == [a]
        lib.rename_tag("topic", "人像", "动物")
        assert lib.picture(a).tags == ("动物",)
        lib.delete_tag("topic", "动物")
        assert len(lib.images()) == 2
        assert lib.picture(a).tags == ()


def test_failed_import_and_duplicate_batch_never_create_empty_style(tmp_path, monkeypatch):
    a = picture(tmp_path / "a.png")
    broken = tmp_path / "坏图.png"
    broken.write_text("not an image")
    with Library(tmp_path / "库") as lib:
        first = lib.import_images([a], new_style="水彩", topic_tags=["人物"]).imported[0]
        result = lib.import_images([a, broken], new_style="不应该出现")
        assert result.duplicates == [("a.png", first)]
        assert len(result.failed) == 1
        assert [s.name for s in lib.styles()] == ["水彩"]
        assert lib.picture(first).tags == ("人物",)
        source = picture(tmp_path / "b.jpg", "blue")
        original_write = Path.write_bytes

        def disk_failure(path, data):
            if path.parent.name == "originals":
                original_write(path, data[:8])
                raise OSError("disk write failed")
            return original_write(path, data)

        monkeypatch.setattr(Path, "write_bytes", disk_failure)
        failed = lib.import_images([source], new_style="失败画风")
        assert len(failed.failed) == 1
        assert len(lib.styles()) == 1
        assert len(list((lib.root / "originals").iterdir())) == 1


def test_delete_file_failure_restores_images_and_style(tmp_path, monkeypatch):
    source = picture(tmp_path / "a.png")
    with Library(tmp_path / "库") as lib:
        pid = lib.import_images([source], new_style="水彩").imported[0]
        original_replace = Path.replace
        failed_once = False

        def fail_thumbnail_stage(path, target):
            nonlocal failed_once
            if path.parent.name == "thumbnails" and not failed_once:
                failed_once = True
                raise OSError("file is locked")
            return original_replace(path, target)

        monkeypatch.setattr(Path, "replace", fail_thumbnail_stage)
        with pytest.raises(OSError):
            lib.delete_images([pid])
        assert len(lib.styles()) == 1
        assert lib.original(pid).read_bytes() == source.read_bytes()
        assert lib.thumbnail(pid).is_file()


def test_restore_rejects_escaping_or_incomplete_archives(tmp_path):
    archive = tmp_path / "坏备份.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("../escape.txt", "bad")
    with pytest.raises(ValueError):
        Library.restore(archive, tmp_path / "target")
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "target").exists()


def test_delete_rollback_keeps_jpeg_original_distinct_from_thumbnail(tmp_path, monkeypatch):
    a = picture(tmp_path / "a.jpg")
    b = picture(tmp_path / "b.jpg", "blue")
    with Library(tmp_path / "库") as lib:
        first, second = lib.import_images([a, b], new_style="水彩").imported
        original_replace = Path.replace

        def fail_second_picture(path, target):
            if path == lib.original(second):
                raise OSError("second picture locked")
            return original_replace(path, target)

        monkeypatch.setattr(Path, "replace", fail_second_picture)
        with pytest.raises(OSError):
            lib.delete_images([first, second])
        assert lib.original(first).read_bytes() == a.read_bytes()
        assert lib.original(second).read_bytes() == b.read_bytes()
        assert len(lib.images()) == 2


def test_failed_delete_journal_write_does_not_block_reopening(tmp_path, monkeypatch):
    source = picture(tmp_path / "a.png")
    root = tmp_path / "库"
    original_write = Path.write_text
    with Library(root) as lib:
        pid = lib.import_images([source], new_style="水彩").imported[0]

        def partial_manifest(path, text, *args, **kwargs):
            if path.parent.name == ".pending-delete":
                original_write(path, "{", *args, **kwargs)
                raise OSError("disk full")
            return original_write(path, text, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", partial_manifest)
        with pytest.raises(OSError):
            lib.delete_images([pid])
    with Library(root) as reopened:
        assert len(reopened.styles()) == 1
        assert reopened.original(pid).read_bytes() == source.read_bytes()


def test_reorder_move_and_delete_keep_nonempty_styles_and_sources(tmp_path):
    sources = [picture(tmp_path / "a.png"), picture(tmp_path / "b.png", "blue"), picture(tmp_path / "c.webp", "green")]
    with Library(tmp_path / "库") as lib:
        a, b = lib.import_images(sources[:2], new_style="水彩").imported
        c = lib.import_images(sources[2:], new_style="油画").imported[0]
        first, second = lib.picture(a).style_id, lib.picture(c).style_id
        lib.reorder(first, [b, a])
        lib.set_cover(first, b)
        assert [p.id for p in lib.images(first)] == [b, a]
        assert lib.style(first).cover == b
        lib.move_images([b], second)
        assert lib.style(first).cover == a
        lib.move_images([a], second)
        assert [s.id for s in lib.styles()] == [second]
        assert len(lib.images(second)) == 3
        with pytest.raises(ValueError):
            lib.reorder(second, [a])
        lib.delete_images([a, b, c])
        assert lib.styles() == []
        assert lib.images() == []
        assert all(p.is_file() for p in sources)
        assert not any((lib.root / "originals").iterdir())


def test_backup_restores_all_metadata_to_independent_library(tmp_path):
    backup = tmp_path / "收藏备份.zip"
    with Library(tmp_path / "原库") as lib:
        a, b = lib.import_images([picture(tmp_path / "a.png"), picture(tmp_path / "b.jpg", "blue")], new_style="水彩").imported
        sid = lib.picture(a).style_id
        lib.edit_style(sid, "复古水彩", "纸张质感\n暖色", ["水彩", "复古"])
        lib.edit_picture(a, "人物原始提示词", ["人物", "动物"])
        lib.set_cover(sid, b)
        lib.reorder(sid, [b, a])
        lib.backup(backup)
        restored_root = tmp_path / "恢复库"
        Library.restore(backup, restored_root)
        with Library(restored_root) as restored:
            assert restored.style(sid) == lib.style(sid)
            assert restored.images() == lib.images()
            assert restored.prompt_for_copy(sid) == "纸张质感\n暖色"
            assert [p.id for p in restored.search(topic_tags=["人物", "动物"])] == [a]
            assert restored.original(a).read_bytes() == lib.original(a).read_bytes()
        with pytest.raises(ValueError):
            Library.restore(backup, lib.root)
        assert len(lib.images()) == 2
