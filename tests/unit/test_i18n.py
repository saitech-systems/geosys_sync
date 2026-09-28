"""Every tr('...') literal in the package has a translation in every
language, with the same placeholders, and nothing else is in a catalogue.

Keyed on the source rather than on a hand-kept list, so adding a user-facing
string without translating it fails here rather than shipping English to
four of the five languages.
"""
import ast
import os
import re

import pytest

from geosys_sync.core import i18n

PACKAGE = os.path.join(os.path.dirname(__file__), '..', '..', 'geosys_sync')
TRANSLATED = [code for code in i18n.LANGUAGES if code != i18n.DEFAULT_LANGUAGE]
# tr() is also called on these runtime values (layer kinds shown in tables).
DYNAMIC_KEYS = {'vector', 'raster', 'unsupported'}
_PLACEHOLDER = re.compile(r'\{[^{}]*\}')


def source_keys():
    keys = set()
    for dirpath, _dirs, files in os.walk(PACKAGE):
        for name in files:
            if not name.endswith('.py'):
                continue
            with open(os.path.join(dirpath, name), encoding='utf-8') as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and getattr(node.func, 'id', None) == 'tr'
                        and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)):
                    keys.add(node.args[0].value)
    return keys | DYNAMIC_KEYS


def test_source_has_user_facing_strings():
    assert len(source_keys()) > 50


@pytest.mark.parametrize('code', TRANSLATED)
def test_catalogue_is_complete_and_exact(code):
    catalog = i18n.load_catalog(code)
    keys = source_keys()
    missing = sorted(keys - set(catalog))
    stale = sorted(set(catalog) - keys)
    assert not missing, '{}: untranslated: {}'.format(code, missing)
    assert not stale, '{}: no longer in the source: {}'.format(code, stale)
    for key, value in catalog.items():
        assert isinstance(value, str) and value.strip(), (code, key)
        assert (sorted(_PLACEHOLDER.findall(key))
                == sorted(_PLACEHOLDER.findall(value))), (code, key, value)


def test_tr_falls_back_to_english_and_switches_language():
    try:
        assert i18n.set_language('xx') == 'en'
        assert i18n.tr('Log out') == 'Log out'
        assert i18n.set_language('fr_FR') == 'fr'
        assert i18n.tr('Log out') == 'Se déconnecter'
        assert i18n.tr('never translated text') == 'never translated text'
        assert i18n.set_language('nl-BE') == 'nl'
        assert i18n.tr('Log out') == 'Uitloggen'
    finally:
        i18n.set_language('en')


def test_formatting_after_translation_keeps_the_placeholders_usable():
    try:
        for code in TRANSLATED:
            i18n.set_language(code)
            assert '3' in i18n.tr('Uploaded {} layer(s).').format(3)
            both = i18n.tr('Connected to {} as {}').format('srv', 'usr')
            assert 'srv' in both and 'usr' in both
    finally:
        i18n.set_language('en')
