"""Prepare complete updates away from live data and publish with recovery."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from contextlib import closing
import hashlib
import json
import shutil
import sqlite3
import time
import uuid

from PySide6.QtCore import QLockFile
from gallery.library import Library
from gallery.catalog import Catalog


@dataclass
class SourcePicture:
    case_id: str
    image_id: str
    name: str
    prompt: str
    tags: list[str]
    url: str
    source: str = ""
    note: str = ""
    revision: str = ""


def baseline(root):
    """Read existing importer manifests without modifying a library."""
    records = []
    for _, data in Catalog.manifests(root):
        source = data.get("repository", data.get("source", ""))
        for row in data.get("records", []):
            case = str(row.get("source_id", row.get("case_id", row.get("number", row.get("style_slug", "")))))
            if "youart-open-source/" in source.casefold():
                case = case.rstrip("-")
            pictures = row.get("images", row.get("pictures", []))
            if "picture_id" in row:
                pictures = [dict(row, source_file=row.get("source_image", {}).get("path", ""))]
            for image in pictures:
                pid = image.get("picture_id") or image.get("duplicate_of")
                if not pid:
                    continue
                path = image.get("source_file", image.get("file", ""))
                if not path:
                    detail = image.get("image", image.get("download", row.get("image", row.get("download", {}))))
                    path = detail.get("url", detail.get("path", "")) if isinstance(detail, dict) else str(detail)
                # Case-based collections have a single canonical image.
                if "case_id" in row or "youart-open-source/" in source.casefold():
                    path = "main"
                records.append({"case": case, "image": str(path).replace("\\", "/"), "picture": pid,
                    "style": row.get("style_id", ""), "tags": [], "source": source})
            if row.get("excluded_reason"):
                records.append({"case": case, "image": "main", "picture": "", "style": "", "tags": [], "source": source})
        for skipped in data.get("skipped", []):
            if skipped.get("slug"):
                records.append({"case": skipped["slug"].rstrip("-"), "image": "main", "picture": "", "style": "", "tags": [], "source": source})
    return records


def fingerprint(root):
    root = Path(root)
    digest = hashlib.sha256()
    for file in [root / "gallery.sqlite3", *sorted(root.glob("*来源*json"))]:
        digest.update(file.name.encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


def recover_update(root):
    root = Path(root)
    transaction = root.parent / ("." + root.name + ".gallery-update")
    marker = transaction / "transaction.json"
    if not marker.exists():
        return
    data = json.loads(marker.read_text("utf-8"))
    if data.get("root") != str(root.resolve()):
        raise ValueError("更新恢复记录与素材库路径不一致")
    old = transaction / "old"
    if not root.exists() and old.exists():
        old.rename(root)
    if not root.exists():
        raise ValueError("更新恢复失败：原库和备份均不存在")
    # Once the new root is visible it is a complete prepared directory.
    marker.unlink()


class UpdateService:
    def __init__(self, root: Path, cache: Path):
        self.root = Path(root).resolve()
        self.cache = Path(cache).resolve()
        self.transaction = self.root.parent / ("." + self.root.name + ".gallery-update")
        self.stage = self.transaction / "stage"
        self.plan = self.cache / "prepared.json"

    def state(self):
        with closing(Catalog.connect(self.root)) as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='source_images'").fetchone()
            if exists:
                return [dict(r) for r in db.execute("SELECT * FROM source_images")]
        return baseline(self.root)

    def new_images(self, rows):
        known = {(r["case"], r["image"]) for r in self.state()}
        return [r for r in rows if (r.case_id, r.image_id) not in known]

    def tag_changes(self, rows):
        # Updates classify new images only; existing images retain curated tags.
        return 0

    def ignored(self):
        with closing(Catalog.connect(self.root)) as db:
            existing = {r[0] for r in db.execute("SELECT id FROM pictures")}
        return [r for r in self.state() if r["picture"] not in existing]

    def restore_ignored(self, identities):
        preparation = QLockFile(str(self.root.parent / ("." + self.root.name + ".prepare.lock")))
        preparation.setStaleLockTime(0)
        if not preparation.tryLock(0):
            raise ValueError("另一个实例正在准备该库的更新，请稍后重试")
        try:
            return self._restore_ignored(identities)
        finally:
            preparation.unlock()

    def _restore_ignored(self, identities):
        ignored = {(r["case"], r["image"]) for r in self.ignored()}
        self._prepare([], lambda *args: None, lambda url: b"")
        with closing(sqlite3.connect(self.stage / "gallery.sqlite3")) as db:
            with db:
                for identity in identities:
                    if tuple(identity) in ignored:
                        db.execute('DELETE FROM source_images WHERE "case"=? AND "image"=?', identity)
        self._commit()

    def pending(self):
        return self.plan.exists() and self.stage.is_dir()

    @staticmethod
    def content_tags(result, source_tags):
        # A translated use/category label is not evidence of visible subjects.
        aliases = {
            "人物": {"人物", "person", "people", "portrait", "human"},
            "动物": {"动物", "animal", "animals", "cat", "dog"},
            "食物": {"食物", "food"}, "建筑": {"建筑", "architecture", "building", "buildings"},
            "植物": {"植物", "plant", "plants", "flower", "flowers"},
            "交通工具": {"交通工具", "vehicle", "vehicles", "car", "cars"},
            "自然": {"自然", "nature", "landscape"}, "室内": {"室内", "interior", "interiors"},
            "物品": {"物品", "object", "objects"}, "产品": {"产品", "product", "products"},
        }
        return [t for t in result["tags"] if t["kind"] == "style" or
                (t.get("original") in source_tags and
                 str(t.get("original", "")).strip().casefold() in aliases.get(t["name"], set()))]

    def verify_topics(self, result, image, verify, source_tags=()):
        if source_tags:
            result = {**result, "tags": self.content_tags(result, source_tags)}
        if any(t["kind"] == "topic" and t.get("original") in source_tags for t in result["tags"]):
            return result
        names = list(dict.fromkeys(t["name"] for t in result["tags"] if t["kind"] == "topic"))
        image_digest = hashlib.sha256(image.read_bytes()).hexdigest()
        version = "local-content-topics-v3:" + getattr(verify, "cache_key", "topic-verification-v1")
        identity = hashlib.sha256(json.dumps([version, image_digest, names], ensure_ascii=False).encode()).hexdigest()
        path = self.cache / ("topic-check-" + identity + ".json")
        report = json.loads(path.read_text("utf-8")) if path.exists() else verify(image, names)
        keep, removed = report["keep"], report["removed"]
        if not isinstance(keep, list) or not isinstance(removed, list) or any(not isinstance(t, str) or not t.strip() for t in keep) or len(set(keep)) != len(keep) or (names and sorted([*keep, *[r["tag"] for r in removed]]) != sorted(names)):
            raise ValueError("看图核验结果未完整对应主题标签")
        if any(not isinstance(r.get("reason"), str) or not r["reason"].strip() for r in removed):
            raise ValueError("看图核验移除标签缺少理由")
        path.write_text(json.dumps(report, ensure_ascii=False), "utf-8")
        tags = [t for t in result["tags"] if t["kind"] != "topic" or t["name"] in keep]
        if not names:
            tags.extend({"original": "", "name": name, "kind": "topic"} for name in keep)
        return {**result, "tags": tags,
                "topic_verification": {"image_sha256": image_digest, **report}}

    def prepare(self, rows, translate, download, *, verify=None):
        preparation = QLockFile(str(self.root.parent / ("." + self.root.name + ".prepare.lock")))
        preparation.setStaleLockTime(0)
        if not preparation.tryLock(0):
            raise ValueError("另一个实例正在准备该库的更新，请稍后重试")
        try:
            return self._prepare(rows, translate, download, verify=verify)
        finally:
            preparation.unlock()

    def _prepare(self, rows, translate, download, *, verify=None):
        if (self.transaction / "transaction.json").exists():
            raise ValueError("请先重启完成上次更新恢复")
        self.cache.mkdir(parents=True, exist_ok=True)
        self.plan.unlink(missing_ok=True)
        before = fingerprint(self.root)
        known = self.state()
        fresh = self.new_images(rows)
        # Historical manifests have no source tag baseline. Record today's source
        # tags without changing curated metadata or invoking APIs on old images.
        with closing(Catalog.connect(self.root)) as db:
            tracked = db.execute("SELECT 1 FROM sqlite_master WHERE name='source_images'").fetchone()
        if not tracked:
            current = {(r.case_id, r.image_id): r.tags for r in rows}
            known = [{**r, "tags": current.get((r["case"], r["image"]), r["tags"])} for r in known]
        prepared = []
        for item in fresh:
            identity = hashlib.sha256(json.dumps(asdict(item), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            folder = self.cache / identity
            folder.mkdir(exist_ok=True)
            translated = folder / "translation.json"
            image = folder / "image.bin"
            try:
                if translated.exists():
                    result = json.loads(translated.read_text("utf-8"))
                else:
                    result = translate(item.name, item.prompt, item.tags)
                    translated.write_text(json.dumps(result, ensure_ascii=False), "utf-8")
                if not image.exists():
                    temp = folder / "download.tmp"
                    temp.write_bytes(download(item.url))
                    from PIL import Image
                    with Image.open(temp) as loaded:
                        loaded.verify()
                    temp.replace(image)
                if verify:
                    result = self.verify_topics(result, image, verify, item.tags)
                prepared.append((item, result, image))
            except Exception as exc:
                raise ValueError(f"{item.case_id}/{item.image_id} 处理失败：{exc}") from None
        if self.stage.exists():
            if self.stage.resolve().parent != self.transaction.resolve() or self.stage.is_symlink():
                raise ValueError("更新暂存目录路径异常，未清理任何内容")
            shutil.rmtree(self.stage)
        self.transaction.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.root, self.stage, ignore=shutil.ignore_patterns(".gallery.lock", "*.sqlite3-wal", "*.sqlite3-shm"))
        # Backup API creates a consistent SQLite snapshot even while browsing.
        with closing(Catalog.connect(self.root)) as source, closing(sqlite3.connect(self.stage / "gallery.sqlite3")) as destination:
            source.backup(destination)
        if fingerprint(self.root) != before:
            raise ValueError("准备期间素材库发生变化，请重试；正式库未更新")
        with Library(self.stage) as library:
            library.db.execute('''CREATE TABLE IF NOT EXISTS source_images (
                "case" TEXT NOT NULL,"image" TEXT NOT NULL,picture TEXT NOT NULL,
                style TEXT NOT NULL,tags TEXT NOT NULL DEFAULT '[]',source TEXT NOT NULL DEFAULT '',
                PRIMARY KEY("case","image"))''')
            with library.db:
                for record in known:
                    library.db.execute('INSERT OR IGNORE INTO source_images VALUES(?,?,?,?,?,?)',
                        (record["case"], record["image"], record["picture"], record["style"],
                         json.dumps(record.get("tags", [])) if isinstance(record.get("tags", []), list) else record["tags"], record.get("source", "")))
            styles = {r["case"]: r["style"] for r in known}
            available = {s.id for s in library.styles()}
            for item, result, image in prepared:
                sid = styles.get(item.case_id)
                if sid not in available:
                    sid = None
                # import_images accepts supported extensions; use decoded format.
                from PIL import Image
                with Image.open(image) as loaded:
                    extension = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}.get(loaded.format or "")
                if not extension:
                    raise ValueError("来源图片格式暂不支持")
                local = image.with_suffix(extension)
                if not local.exists():
                    shutil.copy2(image, local)
                imported = library.import_images([local], style_id=sid, new_style="" if sid else result["name"])
                if imported.failed:
                    raise ValueError(str(imported.failed))
                pid = imported.imported[0] if imported.imported else imported.duplicates[0][1]
                actual_style = library.picture(pid).style_id
                if imported.imported:
                    prompt = "【中文提示词｜自动翻译】\n" + result["prompt"] + "\n\n【来源原文】\n" + item.prompt + "\n\n【来源与使用说明】\n" + item.source + "\n" + item.note
                    library.edit_picture(pid, prompt, [t["name"] for t in result["tags"] if t["kind"] == "topic"])
                    style = library.style(actual_style)
                    from urllib.parse import urlsplit, unquote
                    filename = unquote(Path(urlsplit(item.url).path).name)
                    with library.db:
                        library.db.execute("UPDATE pictures SET filename=? WHERE id=?", (filename or item.case_id, pid))
                if imported.imported:
                    style = library.style(actual_style)
                    library.edit_style(actual_style, style.name, style.prompt,
                        list(dict.fromkeys([*style.tags, *[t["name"] for t in result["tags"] if t["kind"] == "style"]])))
                styles[item.case_id] = actual_style
                available.add(actual_style)
                with library.db:
                    library.db.execute('INSERT INTO source_images VALUES(?,?,?,?,?,?)',
                        (item.case_id, item.image_id, pid, actual_style, json.dumps(item.tags), item.source))
            library.validate()
        token = uuid.uuid4().hex
        (self.stage / ".prepared-token").write_text(token, "utf-8")
        self.plan.write_text(json.dumps({"fingerprint": before, "count": len(fresh), "prepared": time.time(), "token": token,
            "topic_verification": "local-content-or-pixels-v3" if verify else ""}), "utf-8")
        return len(fresh)

    def commit(self, *, require_verification=False):
        preparation = QLockFile(str(self.root.parent / ("." + self.root.name + ".prepare.lock")))
        preparation.setStaleLockTime(0)
        if not preparation.tryLock(0):
            raise ValueError("另一个实例正在准备该库的更新，请稍后提交")
        try:
            return self._commit(require_verification=require_verification)
        finally:
            preparation.unlock()

    def _commit(self, *, require_verification=False):
        if not self.pending():
            raise ValueError("没有已完整准备的更新")
        expected = json.loads(self.plan.read_text("utf-8"))
        if require_verification and expected.get("topic_verification") != "local-content-or-pixels-v3":
            raise ValueError("这批暂存更新尚未看图核验，请重新检查并准备更新")
        if not (self.stage / ".prepared-token").exists() or (self.stage / ".prepared-token").read_text("utf-8") != expected["token"]:
            raise ValueError("暂存区已有另一批更新，请重新检查和准备")
        lock = QLockFile(str(self.root / ".gallery.lock"))
        maintenance = QLockFile(str(self.root.parent / ("." + self.root.name + ".maintenance.lock")))
        maintenance.setStaleLockTime(0)
        if not maintenance.tryLock(0):
            raise ValueError("素材库正执行维护，请稍后重试")
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            maintenance.unlock()
            raise ValueError("素材库仍被打开，暂未提交；请关闭该库的其他程序后重试")
        try:
            if fingerprint(self.root) != json.loads(self.plan.read_text("utf-8"))["fingerprint"]:
                raise ValueError("素材库已有本地修改，请重新准备更新；本地内容未覆盖")
            with Library(self.stage) as lib:
                lib.validate()
            old = self.transaction / "old"
            if old.exists():
                # Retain previous complete version as a recoverable backup.
                old.rename(self.transaction / ("backup-" + uuid.uuid4().hex))
            marker = self.transaction / "transaction.json"
            marker.write_text(json.dumps({"root": str(self.root)}), "utf-8")
            lock.unlock()
            self.root.rename(old)
            try:
                self.stage.rename(self.root)
            except BaseException:
                old.rename(self.root)
                marker.unlink(missing_ok=True)
                raise
            marker.unlink()
            self.plan.unlink()
        finally:
            lock.unlock()
            maintenance.unlock()
