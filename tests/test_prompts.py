from gallery.prompts import preferred_full_prompt, prompt_sections


def test_nested_source_languages_stay_in_original_tab():
    chinese = "2000 年代面向中学生的日剧喜剧场景"
    original = f"[中文]\n{chinese}\n\n[English]\nA school comedy scene."
    stored = f"【中文提示词】\n{chinese}\n\n【来源原文】\n{original}\n\n【来源与使用说明】\n测试来源说明。"
    parts = prompt_sections(stored)
    assert parts == {"chinese": chinese, "original": original, "notes": "测试来源说明。"}
    assert preferred_full_prompt(stored) == chinese


def test_distinct_alternate_prompt_after_source_notes_is_preserved():
    stored = "【中文提示词】\n第一版\n【来源原文】\n[中文]\n第一版\n[English]\nFirst\n【来源与使用说明】\n来源一\n【中文提示词】\n第二版\n【来源原文】\nSecond"
    parts = prompt_sections(stored)
    assert parts["chinese"] == "第一版\n第二版"
    assert "[中文]\n第一版" in parts["original"]
    assert parts["original"].endswith("Second")
