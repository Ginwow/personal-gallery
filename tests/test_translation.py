import pytest
from gallery.library import Library


def test_translation_preserves_placeholders_and_rejects_partial_reply(tmp_path):
    from gallery.translation import Translator
    with Library(tmp_path / "library") as library:
        translator = Translator("https://api.deepseek.com", "configured-model", "test-key",
            request=lambda url, key, payload: {"choices": [{"finish_reason": "stop", "message": {"content":
                '{"name":"广告","prompt":"画一个 [PRODUCT]，--ar 4:5","tags":[{"original":"poster","name":"海报","kind":"topic"}]}'}}]})
        result = translator.translate("Ad", "Draw [PRODUCT], --ar 4:5", ["poster"])
        assert result["name"] == "广告"
        assert result["prompt"] == "画一个 [PRODUCT]，--ar 4:5"
        broken = Translator("https://api.deepseek.com", "configured-model", "test-key",
            request=lambda *args: {"choices": [{"finish_reason": "stop", "message": {"content":
                '{"name":"广告","prompt":"画一个产品","tags":[]}'}}]})
        with pytest.raises(ValueError, match="占位符|标签"):
            broken.translate("Ad", "Draw [PRODUCT], --ar 4:5", ["poster"])


def test_http_error_reports_api_message(monkeypatch):
    import io
    import json
    from urllib.error import HTTPError
    from gallery import translation

    def fail(request, timeout):
        body = json.dumps({"error": {"message": "model deepseek-chat does not support image input"}}).encode()
        raise HTTPError(request.full_url, 400, "Bad Request", {}, io.BytesIO(body))

    monkeypatch.setattr(translation, "urlopen", fail)
    with pytest.raises(ValueError, match="does not support image input"):
        translation.chat_request("https://api.deepseek.com/chat/completions", "test-key", {})


def test_json_prompt_allows_translated_array_prose_but_preserves_keys_and_numbers(tmp_path):
    from gallery.translation import Translator
    import json
    source = '{"items":["soft light","paper texture"],"width":1024}'
    def response(*args):
        result = {"name": "海报", "prompt": '{"items":["柔光","纸张纹理"],"width":1024}', "tags": []}
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}]}
    translator = Translator("https://api.deepseek.com", "configured-model", "test-key", request=response)
    assert json.loads(translator.translate("Poster", source, [])["prompt"])["items"] == ["柔光", "纸张纹理"]
    with pytest.raises(ValueError, match="结构或参数"):
        translator.validate_json_prompt(source, '{"items":["柔光","纸张纹理"],"width":2048}')
