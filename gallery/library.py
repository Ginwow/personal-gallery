"""素材库公开操作边界；SQLite 事务与原图副本共同维护收藏。"""
from __future__ import annotations

import hashlib
import sqlite3
import shutil
import json
import re
import tempfile
import zipfile
from contextlib import closing
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageOps


@dataclass(frozen=True)
class Style:
    id: str
    name: str
    prompt: str
    cover: str
    created: float
    used: float
    count: int
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Picture:
    id: str
    style_id: str
    filename: str
    original_prompt: str
    position: int
    tags: tuple[str, ...] = ()


@dataclass
class ImportResult:
    imported: list[str] = field(default_factory=list)
    duplicates: list[tuple[str, str]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


class Library:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        db_path = self.root / "gallery.sqlite3"
        if not db_path.exists() and any(p.name != ".gallery.lock" for p in self.root.iterdir()):
            raise ValueError("请选择空文件夹或已有素材库，避免混入其他文件。")
        (self.root / "originals").mkdir(exist_ok=True)
        (self.root / "thumbnails").mkdir(exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS styles (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, prompt TEXT NOT NULL DEFAULT '',
                cover TEXT NOT NULL DEFAULT '', created REAL NOT NULL, used REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS pictures (
                id TEXT PRIMARY KEY, style_id TEXT NOT NULL REFERENCES styles(id) ON DELETE CASCADE,
                filename TEXT NOT NULL, digest TEXT NOT NULL UNIQUE, extension TEXT NOT NULL,
                original_prompt TEXT NOT NULL DEFAULT '', position INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS pictures_style ON pictures(style_id, position);
            CREATE TABLE IF NOT EXISTS tags (
                kind TEXT NOT NULL CHECK(kind IN ('style','topic')), name TEXT NOT NULL,
                PRIMARY KEY(kind,name));
            CREATE TABLE IF NOT EXISTS style_tags (
                style_id TEXT NOT NULL REFERENCES styles(id) ON DELETE CASCADE,
                name TEXT NOT NULL, PRIMARY KEY(style_id,name));
            CREATE TABLE IF NOT EXISTS picture_tags (
                picture_id TEXT NOT NULL REFERENCES pictures(id) ON DELETE CASCADE,
                name TEXT NOT NULL, PRIMARY KEY(picture_id,name));
        """)
        self._recover_deletes()

    def __enter__(self) -> Library:
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def close(self) -> None:
        self.db.close()

    def styles(self, sort: str = "created") -> list[Style]:
        ordering = {"created": "s.created DESC", "name": "s.name COLLATE NOCASE", "used": "s.used DESC"}[sort]
        rows = self.db.execute(f"""SELECT s.*, COUNT(p.id) AS count FROM styles s
            JOIN pictures p ON p.style_id=s.id GROUP BY s.id ORDER BY {ordering}, s.id""")
        tags = self._tag_map("style")
        return [Style(r["id"], r["name"], r["prompt"], r["cover"], r["created"], r["used"], r["count"], tags.get(r["id"], ())) for r in rows]

    def style(self, style_id: str) -> Style:
        return next(s for s in self.styles() if s.id == style_id)

    def images(self, style_id: str | None = None) -> list[Picture]:
        sql = "SELECT * FROM pictures"
        args = () if style_id is None else (style_id,)
        if style_id is not None:
            sql += " WHERE style_id=?"
        tags = self._tag_map("topic")
        return [Picture(r["id"], r["style_id"], r["filename"], r["original_prompt"], r["position"], tags.get(r["id"], ()))
                for r in self.db.execute(sql + " ORDER BY position, id", args)]

    @staticmethod
    def _tag_table(kind: str) -> tuple[str, str]:
        if kind == "style":
            return "style_tags", "style_id"
        if kind == "topic":
            return "picture_tags", "picture_id"
        raise ValueError("标签类别无效")

    def _tag_map(self, kind: str) -> dict[str, tuple[str, ...]]:
        table, key = self._tag_table(kind)
        groups: dict[str, list[str]] = {}
        for row in self.db.execute(f"SELECT * FROM {table} ORDER BY name"):
            groups.setdefault(row[key], []).append(row["name"])
        return {k: tuple(v) for k, v in groups.items()}

    def tags(self, kind: str) -> list[str]:
        self._tag_table(kind)
        return [r[0] for r in self.db.execute("SELECT name FROM tags WHERE kind=? ORDER BY name", (kind,))]

    def _set_tags(self, kind: str, entity_id: str, tags: list[str], append: bool = False) -> None:
        table, key = self._tag_table(kind)
        if not append:
            self.db.execute(f"DELETE FROM {table} WHERE {key}=?", (entity_id,))
        for name in dict.fromkeys(t.strip() for t in tags if t.strip()):
            self.db.execute("INSERT OR IGNORE INTO tags VALUES(?,?)", (kind, name))
            self.db.execute(f"INSERT OR IGNORE INTO {table} VALUES(?,?)", (entity_id, name))

    def edit_style(self, style_id: str, name: str, prompt: str, tags: list[str]) -> None:
        if not name.strip():
            raise ValueError("画风名称不能为空")
        self.style(style_id)
        with self.db:
            self.db.execute("UPDATE styles SET name=?,prompt=? WHERE id=?", (name.strip(), prompt, style_id))
            self._set_tags("style", style_id, tags)

    def edit_picture(self, picture_id: str, prompt: str, tags: list[str]) -> None:
        self.picture(picture_id)
        with self.db:
            self.db.execute("UPDATE pictures SET original_prompt=? WHERE id=?", (prompt, picture_id))
            self._set_tags("topic", picture_id, tags)

    def add_topic_tags(self, picture_ids: list[str], tags: list[str]) -> None:
        with self.db:
            for pid in picture_ids:
                self.picture(pid)
                self._set_tags("topic", pid, tags, append=True)

    def rename_tag(self, kind: str, old: str, new: str) -> None:
        new = new.strip()
        if not new:
            raise ValueError("标签名称不能为空")
        table, key = self._tag_table(kind)
        if old == new:
            return
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO tags VALUES(?,?)", (kind, new))
            self.db.execute(f"INSERT OR IGNORE INTO {table} SELECT {key},? FROM {table} WHERE name=?", (new, old))
            self.db.execute(f"DELETE FROM {table} WHERE name=?", (old,))
            self.db.execute("DELETE FROM tags WHERE kind=? AND name=?", (kind, old))

    def delete_tag(self, kind: str, name: str) -> None:
        table, _ = self._tag_table(kind)
        with self.db:
            self.db.execute(f"DELETE FROM {table} WHERE name=?", (name,))
            self.db.execute("DELETE FROM tags WHERE kind=? AND name=?", (kind, name))

    def search(self, text: str = "", style_tags: list[str] | None = None,
               topic_tags: list[str] | None = None) -> list[Picture]:
        styles = {s.id: s for s in self.styles()}
        words = text.casefold().split()
        selected_style, selected_topic = set(style_tags or []), set(topic_tags or [])
        matches = []
        for pic in self.images():
            style = styles[pic.style_id]
            haystack = " ".join((style.name, *style.tags, *pic.tags)).casefold()
            if selected_style.issubset(style.tags) and selected_topic.issubset(pic.tags) and all(w in haystack for w in words):
                matches.append(pic)
        return matches

    def prompt_for_copy(self, style_id: str) -> str:
        prompt = self.style(style_id).prompt
        if not prompt.strip():
            raise ValueError("请先添加画风提示词")
        return prompt

    def mark_used(self, style_id: str) -> None:
        with self.db:
            self.db.execute("UPDATE styles SET used=? WHERE id=?", (time.time(), style_id))

    def set_cover(self, style_id: str, picture_id: str) -> None:
        if self.picture(picture_id).style_id != style_id:
            raise ValueError("封面必须属于该画风")
        with self.db:
            self.db.execute("UPDATE styles SET cover=? WHERE id=?", (picture_id, style_id))

    def reorder(self, style_id: str, picture_ids: list[str]) -> None:
        actual = {p.id for p in self.images(style_id)}
        if set(picture_ids) != actual or len(picture_ids) != len(actual):
            raise ValueError("排序必须包含该画风的全部图片且不能重复")
        with self.db:
            for pos, pid in enumerate(picture_ids):
                self.db.execute("UPDATE pictures SET position=? WHERE id=?", (pos, pid))

    def _repair_styles(self, style_ids: set[str]) -> None:
        for sid in style_ids:
            pictures = self.images(sid)
            if not pictures:
                self.db.execute("DELETE FROM styles WHERE id=?", (sid,))
            elif self.style(sid).cover not in {p.id for p in pictures}:
                self.db.execute("UPDATE styles SET cover=? WHERE id=?", (pictures[0].id, sid))

    def move_images(self, picture_ids: list[str], target_style: str) -> None:
        self.style(target_style)
        pictures = [self.picture(pid) for pid in dict.fromkeys(picture_ids)]
        pos = max((p.position for p in self.images(target_style)), default=-1) + 1
        with self.db:
            for pic in pictures:
                if pic.style_id != target_style:
                    self.db.execute("UPDATE pictures SET style_id=?,position=? WHERE id=?", (target_style, pos, pic.id))
                    pos += 1
            self._repair_styles({p.style_id for p in pictures})

    def _recover_deletes(self) -> None:
        pending = self.root / ".pending-delete"
        if not pending.exists():
            return
        manifest = pending / "manifest.json"
        if not manifest.exists():
            # Only a temporary journal can exist before staging begins.
            (pending / "manifest.tmp").unlink(missing_ok=True)
            if not any(pending.iterdir()):
                pending.rmdir()
                return
            raise ValueError("素材库删除记录不完整，请保留文件并检查备份。")
        entries = json.loads(manifest.read_text("utf-8"))
        for entry in entries:
            pid, relative = entry["id"], Path(entry["path"])
            destination = (self.root / relative).resolve()
            staged = (pending / relative).resolve()
            if destination.parent not in {self.root / "originals", self.root / "thumbnails"} or staged.parent not in {pending / "originals", pending / "thumbnails"}:
                raise ValueError("删除记录路径无效")
            if staged.exists():
                if self.db.execute("SELECT 1 FROM pictures WHERE id=?", (pid,)).fetchone():
                    staged.replace(destination)
                else:
                    staged.unlink()
        for directory in (pending / "originals", pending / "thumbnails"):
            if directory.exists():
                directory.rmdir()
        manifest.unlink()
        pending.rmdir()

    def delete_images(self, picture_ids: list[str]) -> None:
        self._recover_deletes()
        pictures = [self.picture(pid) for pid in dict.fromkeys(picture_ids)]
        if not pictures:
            return
        pending = self.root / ".pending-delete"
        pending.mkdir()
        entries = []
        for pic in pictures:
            for path in (self.original(pic.id), self.thumbnail(pic.id)):
                entries.append({"id": pic.id, "path": path.relative_to(self.root).as_posix()})
        temporary = pending / "manifest.tmp"
        try:
            temporary.write_text(json.dumps(entries), "utf-8")
            temporary.replace(pending / "manifest.json")
        except Exception:
            temporary.unlink(missing_ok=True)
            pending.rmdir()
            raise
        try:
            with self.db:
                for entry in entries:
                    path = self.root / entry["path"]
                    if path.exists():
                        staged = pending / entry["path"]
                        staged.parent.mkdir(exist_ok=True)
                        path.replace(staged)
                for pic in pictures:
                    self.db.execute("DELETE FROM pictures WHERE id=?", (pic.id,))
                self._repair_styles({p.style_id for p in pictures})
        except Exception:
            self._recover_deletes()
            raise
        # Database committed: staged files can be removed now or recovered on next open.
        self._recover_deletes()


    def backup(self, destination: Path) -> None:
        destination = destination.resolve()
        if destination.is_relative_to(self.root):
            raise ValueError("请把备份保存在素材库以外的位置")
        self._recover_deletes()
        with tempfile.TemporaryDirectory(prefix="gallery-backup-", dir=destination.parent) as scratch:
            scratch_path = Path(scratch)
            snapshot = scratch_path / "gallery.sqlite3"
            with closing(sqlite3.connect(snapshot)) as connection:
                self.db.backup(connection)
                from gallery.catalog import Catalog
                manifests = Catalog.manifests(self.root)
                with connection:
                    connection.execute("CREATE TABLE IF NOT EXISTS source_manifests(name TEXT PRIMARY KEY,payload TEXT NOT NULL)")
                    connection.execute("DELETE FROM source_manifests")
                    connection.executemany("INSERT INTO source_manifests VALUES(?,?)",
                        [(name, json.dumps(data, ensure_ascii=False)) for name, data in manifests])
            package = scratch_path / "backup.zip"
            with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.write(snapshot, "gallery.sqlite3")
                for pic in self.images():
                    for path in (self.original(pic.id), self.thumbnail(pic.id)):
                        archive.write(path, path.relative_to(self.root).as_posix())
            package.replace(destination)

    @staticmethod
    def restore(backup: Path, destination: Path) -> None:
        destination = destination.resolve()
        if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
            raise ValueError("恢复目标必须是新的空文件夹，不能覆盖或合并已有库")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gallery-restore-", dir=destination.parent) as scratch:
            staged = Path(scratch) / "library"
            staged.mkdir()
            seen: set[str] = set()
            with zipfile.ZipFile(backup) as archive:
                for info in archive.infolist():
                    name = info.filename
                    if name in seen or not (name == "gallery.sqlite3" or re.fullmatch(r"(?:originals|thumbnails)/[0-9a-f]{32}\.(?:jpg|jpeg|png|webp)", name)):
                        raise ValueError("备份格式或文件路径无效")
                    seen.add(name)
                    target = staged / name
                    target.parent.mkdir(exist_ok=True)
                    with archive.open(info) as source, target.open("wb") as out:
                        shutil.copyfileobj(source, out)
            if "gallery.sqlite3" not in seen:
                raise ValueError("备份缺少素材库信息")
            with Library(staged) as restored:
                restored.validate()
            removed_empty = destination.exists()
            if removed_empty:
                destination.rmdir()
            try:
                staged.replace(destination)
            except Exception:
                if removed_empty:
                    destination.mkdir(exist_ok=True)
                raise

    def validate(self) -> None:
        from gallery.catalog import Catalog
        Catalog.manifests(self.root)
        if self.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or self.db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("素材库数据完整性检查失败")
        for row in self.db.execute("SELECT id,extension,digest FROM pictures"):
            if not re.fullmatch(r"[0-9a-f]{32}", row["id"]) or row["extension"] not in {".png", ".jpg", ".jpeg", ".webp"}:
                raise ValueError("素材库图片路径无效")
            path = self.original(row["id"])
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row["digest"] or not self.thumbnail(row["id"]).is_file():
                raise ValueError("备份图片缺失或校验失败")
        for row in self.db.execute("SELECT id,cover FROM styles"):
            pictures = {p.id for p in self.images(row["id"])}
            if not pictures or row["cover"] not in pictures:
                raise ValueError("备份存在空画风或无效封面")
    def picture(self, picture_id: str) -> Picture:
        return next(p for p in self.images() if p.id == picture_id)

    def original(self, picture_id: str) -> Path:
        row = self.db.execute("SELECT extension FROM pictures WHERE id=?", (picture_id,)).fetchone()
        if row is None:
            raise ValueError("图片不存在")
        return self.root / "originals" / f"{picture_id}{row['extension']}"

    def thumbnail(self, picture_id: str) -> Path:
        return self.root / "thumbnails" / f"{picture_id}.jpg"

    def import_images(self, paths: list[Path], *, style_id: str | None = None,
                      new_style: str = "", topic_tags: list[str] | None = None) -> ImportResult:
        if style_id is None and not new_style.strip():
            raise ValueError("导入时必须选择或命名画风")
        if style_id is not None:
            self.style(style_id)
        target = style_id or uuid.uuid4().hex
        result = ImportResult()
        created_files: list[Path] = []
        try:
            with self.db:
                for path in paths:
                    original = thumb = None
                    try:
                        data = path.read_bytes()
                        digest = hashlib.sha256(data).hexdigest()
                        duplicate = self.db.execute("SELECT id FROM pictures WHERE digest=?", (digest,)).fetchone()
                        if duplicate:
                            result.duplicates.append((path.name, duplicate["id"]))
                            continue
                        extension = path.suffix.lower()
                        if extension not in {".jpg", ".jpeg", ".png", ".webp"}:
                            raise ValueError("只支持 JPG、PNG、WebP")
                        with Image.open(path) as img:
                            if img.format not in {"JPEG", "PNG", "WEBP"} or getattr(img, "n_frames", 1) != 1:
                                raise ValueError("只支持静态 JPG、PNG、WebP")
                            preview = ImageOps.exif_transpose(img).convert("RGB")
                            preview.thumbnail((512, 512))
                        image_id = uuid.uuid4().hex
                        original = self.root / "originals" / f"{image_id}{extension}"
                        thumb = self.thumbnail(image_id)
                        original.write_bytes(data)
                        preview.save(thumb, "JPEG", quality=85)
                    except (OSError, ValueError, Image.DecompressionBombError) as exc:
                        for file in (original, thumb):
                            if file:
                                file.unlink(missing_ok=True)
                        result.failed.append((path.name, str(exc)))
                        continue
                    created_files.extend([original, thumb])
                    if not result.imported and style_id is None:
                        self.db.execute("INSERT INTO styles(id,name,created) VALUES(?,?,?)", (target, new_style.strip(), time.time()))
                    pos = self.db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM pictures WHERE style_id=?", (target,)).fetchone()[0]
                    self.db.execute("INSERT INTO pictures(id,style_id,filename,digest,extension,position) VALUES(?,?,?,?,?,?)",
                                    (image_id, target, path.name, digest, extension, pos))
                    self._set_tags("topic", image_id, topic_tags or [])
                    if not result.imported and style_id is None:
                        self.db.execute("UPDATE styles SET cover=? WHERE id=?", (image_id, target))
                    result.imported.append(image_id)
        except Exception:
            for file in created_files:
                file.unlink(missing_ok=True)
            raise
        return result
