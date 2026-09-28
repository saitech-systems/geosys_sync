"""User-facing text in the plugin's five languages. Pure Python, no Qt.

Every string a user can read goes through `tr()`. The English text is the
key; `geosys_sync/i18n/<code>.json` maps it to one other language. English
needs no file. A missing key falls back to English, so an untranslated
string is a cosmetic gap, never a crash - and `tests/unit/test_i18n.py`
fails the build on any gap, so it stays cosmetic only on a user's machine.

Why JSON keyed by English and not Qt's .ts/.qm pipeline: the core and
adapter packages raise these strings from plain Python (unit-tested without
QGIS), and a catalogue that needs `lrelease` at build time is one more thing
a contributor's first patch trips over. QGIS itself picks the language: see
qgis_adapter/locale.py.

`tr()` only looks the text up. Formatting stays with the caller
(`tr('Uploaded {} layer(s).').format(n)`), so a translation must keep the
same placeholders as its key - the guard test checks that too.
"""
import json
import os

LANGUAGES = ('en', 'es', 'de', 'fr', 'nl')
DEFAULT_LANGUAGE = 'en'

_I18N_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'i18n')

_language = DEFAULT_LANGUAGE
_catalog = {}


def normalize_language(value):
    """'fr_FR', 'fr-CA', 'FR' -> 'fr'; anything unsupported -> 'en'."""
    code = str(value or '').strip().replace('-', '_').split('_', 1)[0].lower()
    return code if code in LANGUAGES else DEFAULT_LANGUAGE


def load_catalog(code):
    """The translation dict for `code` ({} for English or a missing file)."""
    if code == DEFAULT_LANGUAGE:
        return {}
    path = os.path.join(_I18N_DIR, '{}.json'.format(code))
    try:
        with open(path, encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def set_language(value):
    """Select the language for every later tr() call. Returns the code used."""
    global _language, _catalog
    _language = normalize_language(value)
    _catalog = load_catalog(_language)
    return _language


def language():
    return _language


def tr(text):
    """The current language's text for the English `text` (itself when
    English, or when the catalogue has no entry)."""
    translated = _catalog.get(text)
    return translated if isinstance(translated, str) and translated else text
