"""Portable catalogue and read-only queries over independently owned libraries."""
from __future__ import annotations

import json
import sqlite3
import uuid
import re
from contextlib import closing
from pathlib import Path
from PySide6.QtCore import QLockFile


class Catalog:
    @classmethod
    def delete_empty_local_library(cls, root):
        root = Path(root).resolve()
        if not (root / "gallery.sqlite3").is_file() or cls.source_info(root).get("sources"):
            return False
        allowed = {"gallery.sqlite3", "originals", "thumbnails", ".gallery.lock"}
        if any(p.name not in allowed or p.is_symlink() for p in root.iterdir()):
            return False
        if any((root / d).exists() and (not (root / d).is_dir() or any((root / d).iterdir())) for d in ("originals", "thumbnails")):
            return False
        maintenance = QLockFile(str(root.parent / ("." + root.name + ".maintenance.lock")))
        lock = QLockFile(str(root / ".gallery.lock"))
        maintenance.setStaleLockTime(0)
        lock.setStaleLockTime(0)
        if not maintenance.tryLock(0):
            return False
        try:
            if not lock.tryLock(0):
                return False
            count, _ = cls.inspect(root)
            if count:
                return False
            # No recursive deletion: only the checked database and empty image folders.
            for directory in (root / "originals", root / "thumbnails"):
                if directory.exists():
                    directory.rmdir()
            (root / "gallery.sqlite3").unlink()
            lock.unlock()
            root.rmdir()
            return True
        finally:
            lock.unlock()
            maintenance.unlock()

    def clean_empty_local_libraries(self):
        candidates = {self.resolve(e["path"]) for e in self.entries()}
        if self.libraries.is_dir():
            candidates.update(p.resolve() for p in self.libraries.iterdir() if p.is_dir() and not p.name.startswith("."))
        removed = set()
        for path in candidates:
            try:
                if self.delete_empty_local_library(path):
                    removed.add(path)
            except (OSError, ValueError, sqlite3.Error):
                continue
        if removed:
            data = self.read()
            data["libraries"] = [e for e in data["libraries"] if self.resolve(e["path"]) not in removed]
            self.write(data)
        return removed

    def __init__(self, settings: Path, libraries: Path):
        self.settings = settings
        self.libraries = libraries
        data = self.read()
        # Recover only registered paths or transactions immediately under the known collection folder.
        from gallery.updates import recover_update
        from gallery.migration import recover_split
        roots = [self.resolve(e["path"]) for e in data.get("libraries", [])]
        if libraries.is_dir():
            for marker in libraries.glob(".*.gallery-*/transaction.json"):
                target = Path(json.loads(marker.read_text("utf-8"))["root"]).resolve()
                if target.parent != libraries.resolve():
                    raise ValueError("素材库恢复记录不在预期目录")
                roots.append(target)
        for root in roots:
            maintenance = QLockFile(str(root.parent / ("." + root.name + ".maintenance.lock")))
            maintenance.setStaleLockTime(0)
            if maintenance.tryLock(0):
                try:
                    recover_split(root)
                    recover_update(root)
                finally:
                    maintenance.unlock()
        if "libraries" not in data:
            data["libraries"] = []
            candidates = sorted(libraries.iterdir()) if libraries.is_dir() else []
            if (libraries / "gallery.sqlite3").is_file():
                candidates.insert(0, libraries)
            previous = data.get("library")
            if previous:
                candidates.append(self.resolve(previous))
            seen = set()
            for path in candidates:
                path = path.resolve()
                if path not in seen and (path / "gallery.sqlite3").is_file():
                    seen.add(path)
                    data["libraries"].append(self.record(path))
            self.write(data)
        changed = False
        for entry in data["libraries"]:
            if "sources" not in entry:
                entry.update(self.source_info(self.resolve(entry["path"])))
                changed = True
        if changed:
            self.write(data)
        self.register_completed_splits()

    def register_completed_splits(self):
        data = self.read()
        roots = [self.resolve(e["path"]) for e in data["libraries"]]
        for root in roots:
            transaction = root.parent / ("." + root.name + ".gallery-split")
            completed = transaction / "completed.json"
            if not completed.exists():
                continue
            maintenance = QLockFile(str(root.parent / ("." + root.name + ".maintenance.lock")))
            maintenance.setStaleLockTime(0)
            if not maintenance.tryLock(0):
                continue
            try:
                result = json.loads(completed.read_text("utf-8"))
                if result.get("root") != str(root):
                    raise ValueError("拆库登记清单路径异常")
                for output in result["outputs"]:
                    name = output["name"]
                    if Path(name).name != name:
                        raise ValueError("拆库登记目标名称异常")
                    path = root.parent / name
                    self.inspect(path)
                    if not any(self.resolve(e["path"]) == path for e in data["libraries"]):
                        data["libraries"].append(self.record(path))
                for entry in data["libraries"]:
                    if self.resolve(entry["path"]) == root:
                        entry.update(source="", sources=[])
                self.write(data)
                completed.unlink()
            finally:
                maintenance.unlock()

    def read(self):
        if not self.settings.exists():
            return {}
        data = json.loads(self.settings.read_text("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("设置文件格式无效")
        return data

    def write(self, data):
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.settings.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        temporary.replace(self.settings)

    def resolve(self, path):
        result = Path(path)
        return (result if result.is_absolute() else self.settings.parent / result).resolve()

    def stored(self, path):
        path = Path(path).resolve()
        base = self.settings.parent.resolve()
        return str(path.relative_to(base)) if path.is_relative_to(base) else str(path)

    def record(self, path):
        return {"id": uuid.uuid4().hex, "name": path.name, "path": self.stored(path), "cover": "", **self.source_info(path)}

    @staticmethod
    def source_info(path):
        from gallery.sources import normalize_source
        urls = set()
        for _, data in Catalog.manifests(path):
            url = data.get("repository", data.get("source", ""))
            if url:
                try:
                    urls.add(normalize_source(url))
                except ValueError:
                    pass
        return {"sources": sorted(urls), "source": next(iter(urls)) if len(urls) == 1 else ""}

    @classmethod
    def manifests(cls, path):
        files = [*Path(path).glob("*来源*json")]
        if (Path(path) / "library-source.json").exists():
            files.append(Path(path) / "library-source.json")
        if files:
            return [(file.name, json.loads(file.read_text("utf-8"))) for file in files]
        if not (Path(path) / "gallery.sqlite3").is_file():
            return []
        with closing(cls.connect(Path(path).resolve())) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='source_manifests'").fetchone():
                return []
            result = []
            for name, payload in db.execute("SELECT name,payload FROM source_manifests"):
                if not isinstance(name, str) or Path(name).name != name or not (
                        name == "library-source.json" or re.fullmatch(r"[^\\/:\r\n]*来源[^\\/:\r\n]*\.json", name)):
                    raise ValueError("来源清单文件名无效")
                result.append((name, json.loads(payload)))
            return result

    def entries(self):
        return self.read().get("libraries", [])

    def entry(self, library_id):
        return next(e for e in self.entries() if e["id"] == library_id)

    def path(self, library_id):
        return self.resolve(self.entry(library_id)["path"])

    def add(self, path):
        path = Path(path).resolve()
        if not (path / "gallery.sqlite3").is_file():
            raise ValueError("该文件夹不是已有素材库")
        count, _ = self.inspect(path)
        if count == 0:
            raise ValueError("素材库必须至少包含一张图片才能添加")
        for entry in self.entries():
            if self.resolve(entry["path"]) == path:
                return entry["id"]
        # Validate before registration, without creating files in the library.
        data = self.read()
        entry = self.record(path)
        data["libraries"].append(entry)
        self.write(data)
        return entry["id"]

    def change(self, library_id, **changes):
        data = self.read()
        entry = next(e for e in data["libraries"] if e["id"] == library_id)
        entry.update(changes)
        self.write(data)

    def remove(self, library_id):
        data = self.read()
        data["libraries"] = [e for e in data["libraries"] if e["id"] != library_id]
        self.write(data)

    def relocate(self, library_id, path):
        path = Path(path).resolve()
        self.inspect(path)
        if any(e["id"] != library_id and self.resolve(e["path"]) == path for e in self.entries()):
            raise ValueError("该素材库已添加")
        self.change(library_id, path=self.stored(path))

    @staticmethod
    def connect(path):
        db = sqlite3.connect((Path(path) / "gallery.sqlite3").as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        return db

    @classmethod
    def inspect(cls, path):
        with closing(cls.connect(path)) as db:
            count = db.execute("SELECT COUNT(*) FROM pictures").fetchone()[0]
            covers = [r[0] for r in db.execute("SELECT id FROM pictures ORDER BY position,id")]
            return count, covers

    def thumbnail(self, library_id, picture_id):
        return self.path(library_id) / "thumbnails" / f"{picture_id}.jpg"

    def search(self, query):
        words = query.casefold().split()
        results, failures = [], []
        for entry in self.entries():
            try:
                with closing(self.connect(self.path(entry["id"]))) as db:
                    rows = db.execute("""SELECT p.id,p.filename,s.name,
                        (SELECT group_concat(name,' ') FROM style_tags WHERE style_id=s.id) AS st,
                        (SELECT group_concat(name,' ') FROM picture_tags WHERE picture_id=p.id) AS pt
                        FROM pictures p JOIN styles s ON s.id=p.style_id ORDER BY s.created DESC,p.position,p.id""")
                    for row in rows:
                        text = " ".join(str(row[k] or "") for k in ("filename", "name", "st", "pt")).casefold()
                        if all(word in text for word in words):
                            results.append((entry["id"], row["id"], row["name"], row["filename"]))
            except (OSError, sqlite3.Error, ValueError):
                failures.append(entry["name"])
        return results, failures
