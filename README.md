# 个人画廊 · Personal Gallery

Windows 本地图片与提示词素材管理工具。支持多个素材库、画风与主题标签、搜索、图片详情、复制图片及提示词、备份恢复，以及受支持公开来源的增量导入。

## 下载使用

1. 在 [Releases](https://github.com/Ginwow/personal-gallery/releases/latest) 下载 `PersonalGallery-Windows-x64.zip`，完整解压。
2. 双击 `个人画廊.exe`，新建素材库并导入图片，或添加已有素材库。

无需安装 Python。请勿单独移动 EXE，保留同级 `_internal` 文件夹。首次启动为空白画廊，**不附带作者的图片、数据库、提示词、配置或密钥**。

本地浏览无需联网。来源导入使用 GitHub，全文翻译和必要的图像识别需要自行配置 DeepSeek，相关 API 可能计费。密钥存于本机 Windows 凭据管理器。

## 开发

需要 Windows 与 Python 3.11。

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe main.py
```

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m mypy gallery main.py
./build.ps1 -Release
```

生成 `dist/个人画廊/` 与 `dist/PersonalGallery-Windows-x64.zip`。更新时替换程序和 `_internal`，保留自己的 `settings.json` 与 `素材库/`。备份可在程序内完成。

## 目录

- `gallery/`：界面、素材库与来源更新逻辑
- `assets/`：程序图标
- `tests/`：自动化测试
- `scripts/`：发行包工具
- `.github/ISSUE_TEMPLATE/`：问题反馈模板

图片与提示词的权利属于各自权利人。公开来源支持不代表素材附带再分发授权；本项目不分发这些内容。
