"""Parse numbered NanoBanana examples, separating reference inputs from outputs."""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin
from gallery.updates import SourcePicture

REPOSITORY = "https://github.com/ZeroLu/awesome-nanobanana-pro"


class Images(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "img" and attrs.get("src"):
            self.rows.append(attrs)


def parse_readme(text, revision):
    rows = []
    blocks = re.split(r"^### (\d+\.\d+)\. ([^\n]+)\n", text, flags=re.M)
    for i in range(1, len(blocks), 3):
        case, title, body = blocks[i:i+3]
        body = re.split(r"^## ", body, maxsplit=1, flags=re.M)[0]
        prompts = re.findall(r"^```[^\n]*\n(.*?)^```", body, flags=re.M | re.S)
        if not prompts:
            continue
        prompt = "\n\n".join(p.strip() for p in prompts)
        header = body.split("**Prompt:**", 1)[0]
        parser = Images()
        parser.feed(header)
        references: list[str] = []
        outputs: list[str] = []
        for image in parser.rows:
            url = image['src']
            if 'raw.githubusercontent.com/ZeroLu/awesome-nanobanana-pro/' in url:
                url = f"https://raw.githubusercontent.com/ZeroLu/awesome-nanobanana-pro/{revision}/" + 'assets/' + url.split('/assets/', 1)[1]
            else:
                url = urljoin(f"https://raw.githubusercontent.com/ZeroLu/awesome-nanobanana-pro/{revision}/", url)
            # Exclude only the known mismatched example, so corrected future images can enter.
            if any(media in url for media in ('G7LmGCQWYAAfp47', 'G673aBCWUAAFUGn', 'G7QmCFcXoAAwaet')):
                continue
            alt = image.get('alt', '').casefold()
            if case == '4.4' and 'G63UHDYWoAAD_Hm' in url:
                references.append(url)
                continue
            if case == '1.14' and 'dd48cba7-661a-41c3-998d-e48047ecc6e0' in url:
                references.append(url)
                continue
            is_reference = alt in {'original', 'garment', 'model', 'floor plan', 'source', 'pet'}
            if case == '2.7' and 'replicate.delivery' in url:
                is_reference = True
            (references if is_reference else outputs).append(url)
        provenance = re.findall(r"^\*Source: (.*)\*\s*$", body, flags=re.M)
        source = provenance[-1] if provenance else REPOSITORY
        note = ("来源收录的Nano Banana Pro示例，模型与生成过程未独立验证。\n"
                "来源作者与原帖：" + source + "\n固定版本：" + revision +
                "\n使用要求：部分案例依赖上传参考图、替换占位符或图像编辑输入，请遵循完整原文。")
        if references:
            note += "\n明确列出的参考输入（不作为生成结果导入）：\n" + "\n".join(references)
        for url in dict.fromkeys(outputs):
            rows.append(SourcePicture(case, url, title.strip(), prompt, [], url,
                REPOSITORY + '/blob/' + revision + '/README.md', note, revision))
    return rows
