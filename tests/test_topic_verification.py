import json
import pytest
from gallery.library import Library
from tests.test_library import picture


def test_visual_verifier_sends_image_and_requires_a_decision_for_each_tag(tmp_path):
    from gallery.vision import TopicVerifier
    seen = []
    def request(url, key, payload):
        seen.append(payload)
        result = {"decisions": [{"tag": "人物", "decision": "remove", "reason": "只看到汽车，没有人物"},
                                {"tag": "交通工具", "decision": "keep", "reason": "画面中央有汽车"}]}
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}]}
    verifier = TopicVerifier("https://api.deepseek.com", "deepseek-flash", "test-key", request=request)
    result = verifier(picture(tmp_path / "a.png"), ["人物", "交通工具"])
    assert result["keep"] == ["交通工具"]
    assert result["removed"][0]["tag"] == "人物"
    content = seen[0]["messages"][-1]["content"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert seen[0]["model"] == "deepseek-flash"


def test_update_removes_image_inconsistent_topic_and_uncertainty_keeps_live_library_unchanged(tmp_path):
    from gallery.updates import UpdateService, SourcePicture
    root = tmp_path / "库"
    with Library(root):
        pass
    service = UpdateService(root, tmp_path / "cache")
    image = picture(tmp_path / "car.png").read_bytes()
    row = SourcePicture("one", "a", "Car", "A car", [], "https://example.com/a.png")
    def translate(*args):
        return {"name": "汽车", "prompt": "一辆车", "tags": [
            {"original": "person", "name": "人物", "kind": "topic"},
            {"original": "vehicle", "name": "交通工具", "kind": "topic"}]}
    def uncertain(path, tags):
        raise ValueError("看图核验无法确定")
    before = (root / "gallery.sqlite3").read_bytes()
    with pytest.raises(ValueError, match="无法确定"):
        service.prepare([row], translate, lambda url: image, verify=uncertain)
    assert (root / "gallery.sqlite3").read_bytes() == before
    def verify(path, tags):
        assert path.read_bytes() == image
        assert tags == ["人物", "交通工具"]
        return {"keep": ["交通工具"], "removed": [{"tag": "人物", "reason": "无人物"}]}
    service.prepare([row], translate, lambda url: image, verify=verify)
    service.commit(require_verification=True)
    with Library(root) as lib:
        assert lib.images()[0].tags == ("交通工具",)


@pytest.mark.parametrize("decisions", [
    [],
    [{"tag": "动物", "decision": "keep", "reason": "有猫"}],
    [{"tag": "人物", "decision": "uncertain", "reason": "太小无法确认"}],
])
def test_incomplete_or_uncertain_visual_answers_are_not_accepted(tmp_path, decisions):
    from gallery.vision import TopicVerifier
    def request(*args):
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"decisions": decisions})}}]}
    verifier = TopicVerifier("https://api.deepseek.com", "deepseek-flash", "test-key", request=request)
    with pytest.raises(ValueError, match="核验"):
        verifier(picture(tmp_path / "a.png"), ["人物"])


def test_abstract_image_can_have_no_recognized_subjects(tmp_path):
    from gallery.vision import TopicVerifier
    def request(*args):
        return {"choices": [{"finish_reason": "stop", "message": {"content": '{"topics":[]}'}}]}
    verifier = TopicVerifier("https://api.deepseek.com", "deepseek-flash", "test-key", request=request)
    assert verifier(picture(tmp_path / "a.png"), []) == {"keep": [], "removed": [], "model": "deepseek-flash"}


def test_style_tags_survive_topic_verification(tmp_path):
    from gallery.updates import UpdateService, SourcePicture
    root = tmp_path / "库"
    with Library(root):
        pass
    service = UpdateService(root, tmp_path / "cache")
    file = picture(tmp_path / "poster.png")
    row = SourcePicture("one", "a", "Poster", "Poster", [], "https://example.com/a.png")
    def translate(name, prompt, tags):
        return {"name": "海报", "prompt": "海报", "tags": [
            {"original": "poster", "name": "海报", "kind": "style"},
            {"original": "person", "name": "人物", "kind": "topic"}]}
    def verify(path, tags):
        assert tags == ["人物"]
        return {"keep": [], "removed": [{"tag": "人物", "reason": "画面里没有人"}]}
    service.prepare([row], translate, lambda url: file.read_bytes(), verify=verify)
    service.commit(require_verification=True)
    with Library(root) as lib:
        item = lib.images()[0]
        assert item.tags == ()
        assert "海报" in lib.style(item.style_id).tags


def test_topic_verification_cache_is_reused_on_retry(tmp_path):
    from gallery.updates import UpdateService, SourcePicture
    root = tmp_path / "库"
    with Library(root):
        pass
    service = UpdateService(root, tmp_path / "cache")
    file = picture(tmp_path / "a.png")
    row = SourcePicture("one", "a", "Car", "A car", [], "https://example.com/a.png")
    calls = []
    def translate(name, prompt, tags):
        return {"name": "汽车", "prompt": "一辆车", "tags": [
            {"original": "person", "name": "人物", "kind": "topic"}]}
    def first(path, tags):
        calls.append(tags)
        return {"keep": [], "removed": [{"tag": "人物", "reason": "只看到汽车"}]}
    service.prepare([row], translate, lambda url: file.read_bytes(), verify=first)
    assert calls == [["人物"]]
    def second(path, tags):
        raise AssertionError("cached verification must be reused")
    service.prepare([row], translate, lambda url: file.read_bytes(), verify=second)
    assert calls == [["人物"]]


def test_update_ignores_added_source_tags_and_keeps_manual_tags(tmp_path):
    from gallery.updates import UpdateService, SourcePicture
    root = tmp_path / "库"
    with Library(root):
        pass
    service = UpdateService(root, tmp_path / "cache")
    file = picture(tmp_path / "cat.png")
    row = SourcePicture("cat", "a", "Cat", "Cat", [], "https://example.com/a.png")
    translate = lambda name, prompt, tags: {"name": "猫", "prompt": "猫", "tags": [
        {"original": "person", "name": "人物", "kind": "topic"}] if tags else []}
    service.prepare([row], translate, lambda url: file.read_bytes())
    with pytest.raises(ValueError, match="尚未看图核验"):
        service.commit(require_verification=True)
    service.commit()
    with Library(root) as lib:
        pid = lib.images()[0].id
        lib.edit_picture(pid, "本地提示词", ["手动标签"])
    row.tags = ["person"]
    def verify(path, tags):
        raise AssertionError("source topics must skip vision")
    service.prepare([row], translate, lambda url: b"", verify=verify)
    service.commit(require_verification=True)
    with Library(root) as lib:
        assert set(lib.picture(pid).tags) == {"手动标签"}
        assert lib.picture(pid).original_prompt == "本地提示词"

def test_source_topic_tags_are_trusted_without_visual_api(tmp_path):
    from gallery.updates import UpdateService, SourcePicture
    root = tmp_path / '库'
    with Library(root): pass
    row = SourcePicture('one','a','Poster','Prompt',['person'],'https://example.com/a.png')
    service = UpdateService(root,tmp_path/'cache')
    def translate(*args):
        return {'name':'海报','prompt':'海报','tags':[{'original':'person','name':'人物','kind':'topic'}]}
    def forbidden(*args): raise AssertionError('source topic tags must skip vision API')
    service.prepare([row],translate,lambda url: picture(tmp_path/'a.png').read_bytes(),verify=forbidden)
    service.commit(require_verification=True)
    with Library(root) as lib: assert lib.images()[0].tags == ('人物',)

def test_missing_source_topics_are_recognized_from_pixels(tmp_path):
    from gallery.updates import UpdateService, SourcePicture
    from gallery.vision import TopicVerifier
    root=tmp_path/'库'
    with Library(root): pass
    service=UpdateService(root,tmp_path/'cache')
    row=SourcePicture('car','a','Car','Misleading prompt about a person',[],'https://example.com/a.png')
    seen=[]
    def request(url,key,payload):
        seen.append(payload)
        return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'topics':['交通工具']})}}]}
    verifier=TopicVerifier('https://api.deepseek.com','deepseek-flash','test-key',request=request)
    translate=lambda *args:{'name':'汽车','prompt':'误导提示词','tags':[]}
    service.prepare([row],translate,lambda url:picture(tmp_path/'a.png').read_bytes(),verify=verifier)
    service.commit(require_verification=True)
    with Library(root) as lib: assert lib.images()[0].tags==('交通工具',)
    assert len(seen)==1
    assert 'Misleading' not in json.dumps(seen)

@pytest.mark.parametrize('answer', [{'uncertain':True,'reason':'图片不可读取'}, {'topics':'人物'}])
def test_missing_topics_recognition_failure_does_not_publish(tmp_path, answer):
    from gallery.updates import UpdateService, SourcePicture
    from gallery.vision import TopicVerifier
    root=tmp_path/'库'
    with Library(root): pass
    before=(root/'gallery.sqlite3').read_bytes()
    def request(*args):
        return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}]}
    verifier=TopicVerifier('https://api.deepseek.com','deepseek-flash','test-key',request=request)
    row=SourcePicture('one','a','Poster','Prompt',['watercolor'],'https://example.com/a.png')
    translate=lambda *args:{'name':'水彩','prompt':'水彩','tags':[{'original':'watercolor','name':'水彩','kind':'style'}]}
    service=UpdateService(root,tmp_path/'cache')
    with pytest.raises(ValueError,match='核验'):
        service.prepare([row],translate,lambda url:picture(tmp_path/'a.png').read_bytes(),verify=verifier)
    assert not service.pending()
    assert (root/'gallery.sqlite3').read_bytes()==before


@pytest.mark.parametrize("category", ["Commerce", "Education", "Tech", "Fashion"])
def test_use_categories_do_not_replace_visible_subjects(tmp_path, category):
    from gallery.updates import UpdateService, SourcePicture
    root = tmp_path / "library"
    with Library(root): pass
    row = SourcePicture("new", "a", "Poster", "Person in misleading prompt", [category], "https://example.com/a")
    translate = lambda *args: {"name": "海报", "prompt": "人物", "tags": [{"original": category, "name": "商业", "kind": "topic"}]}
    def verify(path, tags):
        assert tags == []
        return {"keep": ["食物"], "removed": []}
    service = UpdateService(root, tmp_path / "cache")
    service.prepare([row], translate, lambda url: picture(tmp_path / "a.png").read_bytes(), verify=verify)
    service.commit(require_verification=True)
    with Library(root) as lib: assert lib.images()[0].tags == ("食物",)


def test_historical_baseline_keeps_curated_data_without_api(tmp_path):
    from gallery.updates import UpdateService, SourcePicture
    root = tmp_path / "library"
    with Library(root) as lib:
        pid = lib.import_images([picture(tmp_path / "old.png")], new_style="手工画风").imported[0]
        sid = lib.picture(pid).style_id
        lib.edit_picture(pid, "本地完整提示词", ["食物"])
    (root / "来源.json").write_text(json.dumps({"records": [{"case_id": "old", "picture_id": pid, "style_id": sid}]}), "utf-8")
    service = UpdateService(root, tmp_path / "cache")
    row = SourcePicture("old", "main", "Changed", "Changed", ["Commerce"], "https://example.com/a")
    def forbidden(*args): raise AssertionError("old curated image must not call APIs")
    service.prepare([row], forbidden, forbidden, verify=forbidden)
    service.commit(require_verification=True)
    with Library(root) as lib:
        assert lib.picture(pid).tags == ("食物",)
        assert lib.picture(pid).original_prompt == "本地完整提示词"
    assert service.tag_changes([row]) == 0
