"""Explicit, pinned adapters for the seven existing public collections."""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import urlsplit, quote, parse_qs
from html.parser import HTMLParser
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

from gallery.updates import SourcePicture

SUPPORTED = {
    "zerolu/awesome-nanobanana-pro": "NanoBanana Pro",
    "callirra-ai/gpt-image-2-5-prompt-atlas": "Callirra Atlas",
    "youart-open-source/awesome-gpt-image-2-5-prompts": "YouArt",
    "vulcaneon/awesome-gpt-image-2.5-prompts": "Vulcan",
    "freestylefly/awesome-gpt-image-2": "Freestylefly",
    "spsrx000000-crypto/awesome-gpt-image-2": "Spsrx",
    "yang0/handraw-style": "Handraw",
    "vigozhao/ai-visual-prompt-cookbook": "Vigo",
}


class GitHubRateLimitError(ValueError):
    def __init__(self, retry_at):
        self.retry_at = retry_at
        local = datetime.fromtimestamp(retry_at).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        super().__init__("GitHub请求额度已用完，预计 " + local + " 后可重试；本地素材未受影响")


_github_retry_at = 0.0


def github_secrets():
    from gallery.translation import WindowsSecrets
    return WindowsSecrets("PersonalGallery/GitHub")


class _NoCredentialRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("GitHub认证请求发生重定向，已停止以保护Token")


def reset_github_cooldown():
    global _github_retry_at
    _github_retry_at = 0.0


def fetch_bytes(url, *, github_token=None):
    global _github_retry_at
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username:
        raise ValueError("来源资源必须使用HTTPS")
    if parsed.hostname == "api.github.com" and github_token is None and time.time() < _github_retry_at:
        raise GitHubRateLimitError(_github_retry_at)
    headers = {"User-Agent": "PersonalGallery/2", "Accept": "application/vnd.github+json"}
    token = ""
    if parsed.hostname == "api.github.com":
        token = github_secrets().get() if github_token is None else github_token
        if token:
            if not token.isascii() or any(c.isspace() for c in token):
                raise ValueError("GitHub Token格式无效，请重新粘贴")
            headers["Authorization"] = "Bearer " + token
    try:
        open_request = build_opener(_NoCredentialRedirect()).open if token else urlopen
        with open_request(Request(url, headers=headers), timeout=60) as response:
            data = response.read(64 * 1024 * 1024 + 1)
            if len(data) > 64 * 1024 * 1024:
                raise ValueError("来源资源超过64MB限制")
            return data
    except HTTPError as exc:
        if parsed.hostname == "api.github.com" and exc.code == 401:
            raise ValueError("GitHub Token无效或已过期，请在GitHub设置中更换") from None
        if parsed.hostname == "api.github.com" and exc.code in (403, 429):
            remaining = exc.headers.get("x-ratelimit-remaining")
            retry_after = exc.headers.get("retry-after")
            if remaining == "0" or retry_after:
                now = time.time()
                try:
                    retry_at = float(exc.headers.get("x-ratelimit-reset", "0")) if remaining == "0" else now + float(retry_after or "60")
                except (ValueError, TypeError):
                    retry_at = now + 60
                _github_retry_at = max(now + 1, retry_at)
                raise GitHubRateLimitError(_github_retry_at) from None
        raise ValueError(f"来源访问失败：HTTP {exc.code}；可能为限流、地址失效或权限不足") from None
    except (URLError, TimeoutError):
        raise ValueError("来源连接失败或超时，请检查网络") from None


def normalize_source(url):
    parsed = urlsplit(url.strip())
    parts = parsed.path.strip("/").removesuffix(".git").split("/")
    if parsed.scheme != "https" or parsed.hostname != "github.com" or len(parts) != 2 or parsed.query or parsed.fragment or parsed.username:
        raise ValueError("请填写受支持的GitHub仓库主页地址")
    repo = "/".join(parts).casefold()
    if repo not in SUPPORTED:
        raise ValueError("该来源暂未适配，请使用已适配的来源仓库")
    return "https://github.com/" + repo


def plain(value):
    if isinstance(value, dict):
        return value.get("en", value.get("zh", ""))
    return str(value or "")


class YouArtCards(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cards = {}
        self.current = None
        self.field = None

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "article" and attrs.get("id"):
            self.current = {"id": attrs["id"], "images": [], "title": "", "prompt": ""}
        elif self.current is not None and tag == "img":
            url = attrs.get("src", "")
            url = parse_qs(urlsplit(url).query).get("url", [url])[0]
            if url.startswith("https://static.youart.ai/") and url not in self.current["images"]:
                self.current["images"].append(url)
        elif self.current is not None and tag in ("h3", "pre"):
            self.field = "title" if tag == "h3" else "prompt"

    def handle_data(self, text):
        if self.current is not None and self.field:
            self.current[self.field] += text

    def handle_endtag(self, tag):
        if tag in ("h3", "pre"):
            self.field = None
        if tag == "article" and self.current is not None:
            self.cards[self.current["id"]] = self.current
            self.current = None


class GitHubSource:
    def __init__(self, url, fetch=fetch_bytes, progress=None):
        self.url = normalize_source(url)
        self.repo = self.url.removeprefix("https://github.com/")
        self.progress = progress or (lambda message: None)
        self.request_count = 0
        def tracked_fetch(resource):
            self.request_count += 1
            host = urlsplit(resource).hostname
            stage = "读取GitHub目录" if host == "api.github.com" else "读取来源图文"
            self.progress(f"{stage}，第 {self.request_count} 次请求（单次超时60秒）")
            return fetch(resource)
        self.fetch = tracked_fetch
        self.revision = ""
        self.paths: dict[str, dict] = {}

    def raw_url(self, path):
        return f"https://raw.githubusercontent.com/{self.repo}/{self.revision}/" + quote(path, safe="/")

    def read(self, path):
        data = self.fetch(self.raw_url(path))
        expected = self.paths.get(path, {}).get("sha")
        if expected and hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() != expected:
            raise ValueError("来源文件与固定版本校验不一致：" + path)
        return data.decode("utf-8-sig")

    def json(self, path):
        return json.loads(self.read(path))

    def check(self):
        metadata = json.loads(self.fetch("https://api.github.com/repos/" + self.repo))
        branch = metadata["default_branch"]
        tree = json.loads(self.fetch(f"https://api.github.com/repos/{self.repo}/git/trees/{quote(branch, safe='')}?recursive=1"))
        if tree.get("truncated"):
            raise ValueError("来源目录过大，当前无法完整检查")
        self.revision = tree["sha"]
        self.paths = {r["path"]: r for r in tree["tree"] if r["type"] == "blob"}
        if self.repo.startswith("zerolu/"):
            rows = self.nanobanana()
        elif self.repo.startswith(("freestylefly/", "spsrx000000-crypto/")):
            rows = self.cases()
        elif self.repo.startswith("callirra-ai/"):
            rows = self.atlas()
        elif self.repo.startswith("youart-open-source/"):
            rows = self.youart()
        elif self.repo.startswith("vulcaneon/"):
            rows = self.vulcan()
        elif self.repo.startswith("yang0/"):
            rows = self.handraw()
        else:
            rows = self.vigo()
        identities = [(r.case_id, r.image_id) for r in rows]
        if len(identities) != len(set(identities)):
            raise ValueError("来源包含重复条目标识，停止更新")
        return rows

    def row(self, case, image, name, prompt, tags, *, external=None, note="", source=""):
        if not name or not prompt:
            raise ValueError("来源条目缺少名称或提示词：" + str(case))
        if not external and image not in self.paths:
            raise ValueError("来源图片不存在：" + image)
        return SourcePicture(str(case), image, plain(name), plain(prompt), list(dict.fromkeys(str(t) for t in tags if t)),
            external or self.raw_url(image), source or self.url, note, self.revision)

    def nanobanana(self):
        from gallery.nanobanana import parse_readme
        return parse_readme(self.read("README.md"), self.revision)

    def cases(self):
        data = self.json("data/cases.json")
        result = []
        for r in data["cases"]:
            path = "data" + r["image"]
            item = self.row(r["id"], path, r["title"], r["prompt"], r.get("styles", []) + r.get("scenes", []),
                source=r.get("sourceUrl", self.url), note="作者：" + r.get("sourceLabel", "") + "；来源模型声明未经独立验证。")
            item.image_id = "main"
            result.append(item)
        return result

    def atlas(self):
        rows = []
        for path in sorted(self.paths):
            if not path.endswith("/prompt.txt") or not path.startswith(("showcase/", "cases/")):
                continue
            folder = str(PurePosixPath(path).parent)
            prompt = self.read(path)
            readme = folder + "/README.md"
            title = folder.split("/")[-1]
            note = "源库示例及模型声明未经独立重新生图验证。"
            if readme in self.paths:
                text = self.read(readme)
                match = re.search(r"^#\s+(.+)$", text, re.M)
                title = match[1] if match else title
                note += "\n" + text
            images = [p for p in self.paths if p.startswith(folder + "/") and PurePosixPath(p).parent.as_posix() == folder
                and p.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) and not PurePosixPath(p).name.startswith(("preview", "input", "reference"))]
            for image in sorted(images):
                rows.append(self.row(folder, image, title, prompt, [], note=note, source=self.url + "/tree/" + self.revision + "/" + folder))
        return rows

    def youart(self):
        result = []
        parser = YouArtCards()
        parser.feed(self.fetch("https://youart.ai/gpt-image-2-5-prompts").decode("utf-8"))
        entries = self.json("data/prompts.json")["prompts"]
        known = {r["slug"] for r in entries}
        entries.extend({"slug": k, "title": card["title"], "prompt": card["prompt"]}
            for k, card in parser.cards.items() if k.startswith("template-") and k not in known)
        for r in entries:
            slug = r["slug"]
            card = parser.cards.get(slug) or parser.cards.get(slug.rstrip("-"))
            if not card or not card["images"] or not card["prompt"].strip():
                raise ValueError("YouArt网页图文格式变化或缺少对应卡片：" + slug)
            for index, url in enumerate(card["images"]):
                item = self.row(slug.rstrip("-"), url, r["title"], card["prompt"], [r.get("category", "")], external=url,
                    source=r.get("sourceUrl", r.get("url", self.url)), note="作者：" + r.get("author", "") + "；网站配图及提示词关联，未经独立生图验证；参考输入须按原文准备。")
                item.image_id = "main" if index == 0 else url
                result.append(item)
        return result

    def vulcan(self):
        result = []
        for r in self.json("data/cases.json"):
            url = r["preview"].get("original_url", r["preview"]["url"])
            result.append(self.row(r["id"], url, r["title"], r["prompt"], [r.get("category", "")], external=url,
                source=r.get("source", {}).get("url", self.url), note="提示词性质：" + r.get("prompt_kind", "未说明") + "；第三方示例、模型声明与复现未经独立验证。"))
        for r in self.json("data/official-tutorials.json"):
            for image in r.get("outputs", []):
                url = image["url"]
                result.append(self.row("official/" + r["id"], url, r["title"], r["brief"], [], external=url,
                    source=r.get("source", self.url), note="官方教程示例；请查看来源中的输入与步骤要求。"))
        return result

    def handraw(self):
        paths = [p for p in self.paths if p.endswith("/references/styles.json")]
        if not paths:
            raise ValueError("Handraw风格清单格式已变化")
        rows = []
        for r in self.json(paths[0]):
            number = r["number"]
            images = [p for p in self.paths if p.startswith("images/individual/") and PurePosixPath(p).stem.startswith(number)
                and p.endswith((".webp", ".png", ".jpg"))]
            for image in sorted(images):
                prompt = "Style: " + r["generation_name"] + "\nTraits: " + r["traits"] + "\nSubject: [SUBJECT]"
                rows.append(self.row(number, image, r["generation_name"], prompt, [], note="风格编号：" + number + "；来源参考标识：" + r.get("reference", "")))
        return rows

    def vigo(self):
        rows = []
        for path in sorted(p for p in self.paths if p.startswith("styles/") and p.endswith("/style.json")):
            r = self.json(path)
            folder = str(PurePosixPath(path).parent)
            # Each source style includes previews and generated sample variants.
            images = [p for p in self.paths if (p.startswith(folder + "/preview-") or p.startswith("assets/samples/" + r["style_slug"] + "/"))
                and p.lower().endswith((".jpg", ".png", ".webp"))]
            for image in sorted(images):
                ratio = "16:9" if "16x9" in image else "9:16"
                examples = r.get("examples", [])
                index_match = re.search(r"/(\d+)-", image)
                index = int(index_match[1]) - 1 if index_match else 0
                values = dict(examples[index].get("values", {})) if index < len(examples) else {}
                values.update({"ASPECT_RATIO": ratio,
                    "STYLE_FIDELITY_ANCHORS": " ".join(r.get("style_fidelity_anchors", [])),
                    "SOURCE_CONTENT_TO_AVOID": " ".join(r.get("source_content_to_avoid", [])),
                    "NEGATIVE_PROMPT": r.get("negative_prompt", "")})
                template = r["prompt_template"]
                prompt = re.sub(r"\{([A-Z][A-Z0-9_]*)\}", lambda m: str(values.get(m[1], m[0])), template)
                prompt += "\nAvoid: " + r.get("negative_prompt", "")
                rows.append(self.row(r["style_slug"], image, r["style_name"], prompt, [], note="来源风格模板实例；请按原文准备参考输入。"))
        return rows
