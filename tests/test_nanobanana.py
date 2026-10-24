from gallery.nanobanana import parse_readme
from gallery.sources import GitHubSource, normalize_source
import json


def test_reference_inputs_excluded_full_prompt_and_attribution_kept():
    text = """### 1.5. Business Photo
<img alt="Original" src="https://example.com/in.jpg" />
<img alt="Generated" src="https://example.com/out.jpg" />
**Prompt:**
```text
Keep [PERSON] exactly.\nDraw full details.
```
*Source: [Author](https://example.com/post)*
"""
    rows = parse_readme(text, 'a'*40)
    assert len(rows) == 1 and rows[0].url.endswith('/out.jpg')
    assert rows[0].prompt == 'Keep [PERSON] exactly.\nDraw full details.'
    assert 'in.jpg' in rows[0].note and 'https://example.com/post' in rows[0].note


def test_adapter_reads_pinned_readme_and_ignores_advertisement():
    text = '<img src="https://example.com/ad.jpg" />\n### 2.1. Example\n<img src="https://example.com/result.jpg" />\n**Prompt:**\n```text\nDraw a cat.\n```\n'
    def fetch(url):
        if '/git/trees/' in url:return json.dumps({'sha':'a'*40,'tree':[{'path':'README.md','type':'blob'}]}).encode()
        if url.endswith('README.md'):
            assert '/'+ 'a'*40+'/' in url
            return text.encode()
        return b'{"default_branch":"main"}'
    rows = GitHubSource('https://github.com/ZeroLu/awesome-nanobanana-pro', fetch).check()
    assert len(rows)==1 and rows[0].url.endswith('result.jpg')
    assert normalize_source('https://github.com/ZeroLu/awesome-nanobanana-pro').endswith('zerolu/awesome-nanobanana-pro')
