# Third-party components

The portable package bundles Python, PySide6/Qt, Shiboken6, Pillow and their runtime libraries. Their respective licenses remain applicable.

- Python 3.11: PSF license and bundled library notices in `licenses/Python.txt`; source: https://www.python.org/downloads/source/
- PySide6 Essentials / Shiboken6 / Qt 6.8.3: LGPLv3; license texts in `licenses/`. Corresponding source: https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.8.3-src/ and https://download.qt.io/archive/qt/6.8/6.8.3/single/
- Pillow: license in `licenses/Pillow.txt`; source: https://github.com/python-pillow/Pillow

Qt/PySide6 are dynamically linked under `_internal`. Users may replace these shared libraries with compatible modified versions and reverse-engineer this application solely to debug modifications to those libraries as permitted by LGPLv3. Build instructions and application source are in the repository.

Imported images and prompts are not bundled. They remain subject to their original authors' rights and terms.
