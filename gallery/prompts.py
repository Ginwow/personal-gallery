"""Choose a stored prompt language without translating or editing the source."""
import re


LANGUAGE = re.compile(
    r"^(中文(?:提示词|版本|原文)?|Chinese(?:\s+(?:prompt|version))?|"
    r"英文(?:提示词|原文|版本)?|English(?:\s+(?:prompt|version|original))?|来源原文)"
    r"(?:[｜|]\s*自动翻译)?\s*(?:[：:]\s*(.*))?$", re.IGNORECASE,
)
NOTES = {"来源与使用说明", "提示词来源说明", "来源说明", "来源与版权", "版权说明"}


def prompt_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {"plain": [], "zh": [], "en": [], "notes": []}
    current = "plain"
    recognized = False
    source_container = False
    for line in text.splitlines(keepends=True):
        heading = re.sub(r"^#{1,6}\s+", "", line.strip()).strip()
        outer_heading = heading.startswith("【") and heading.endswith("】")
        if source_container and not outer_heading:
            sections[current].append(line)
            continue
        if (heading.startswith("【") and heading.endswith("】")) or (heading.startswith("[") and heading.endswith("]")):
            heading = heading[1:-1].strip()
        match = LANGUAGE.fullmatch(heading)
        if match:
            current = "zh" if match[1].lower().startswith(("中文", "chinese")) else "en"
            source_container = heading == "来源原文"
            recognized = True
            if match[2]:
                sections[current].append(match[2] + "\n")
        elif heading.rstrip("：:") in NOTES:
            current = "notes"
            source_container = False
            recognized = True
        elif current in sections:
            sections[current].append(line)
    if not recognized:
        return {"chinese": "", "original": text, "notes": ""}
    chinese = "".join(sections["zh"]).strip()
    leading = "".join(sections["plain"]).strip()
    english = "".join(sections["en"]).strip()
    if not chinese and leading and re.search(r"[\u3400-\u9fff]", leading):
        chinese = leading
    return {"chinese": chinese, "original": english or (leading if not chinese else ""),
            "notes": "".join(sections["notes"]).strip()}


def preferred_full_prompt(text: str) -> str:
    parts = prompt_sections(text)
    return parts["chinese"] or parts["original"]
