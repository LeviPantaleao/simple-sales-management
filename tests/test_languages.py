"""Tests for the i18n table in ``languages.py`` — locale canonicalisation and
the ``t()`` lookup with its English-msgid fallback.
"""

from __future__ import annotations

import pytest

import languages as L


# --------------------------------------------------------------------------------------
# canon_locale — shared by server.py (request locale) and viewer.py (OS locale)
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, "en"),
        ("", "en"),
        ("   ", "en"),
        ("123 !!!", "en"),             # nothing alphabetic left after cleaning
        ("auto", "auto"),
        ("AUTO", "auto"),              # sentinel is case-insensitive, preserved
        ("en", "en"),
        ("en-US", "en_US"),
        ("pt_BR", "pt_BR"),
        ("pt", "pt"),
        ("en_US.UTF-8", "en_US"),      # encoding suffix dropped
        ("pt_BR@latin", "pt_BR"),      # @modifier dropped
        ("en-US:en", "en_US"),         # :fallback dropped
        ("Portuguese_Brazil", "pt_BR"),   # Windows long form
        ("Portuguese", "pt"),             # long form, no region -> generic pt
        ("English_United States", "en_US"),
        ("Spanish", "es"),
        ("es-ES", "es_ES"),
        ("xx", "xx"),                  # unknown 2-letter code passes through
    ],
)
def test_canon_locale(raw, expected):
    assert L.canon_locale(raw) == expected


def test_canon_locale_is_idempotent():
    for tag in ("en", "pt_BR", "es_ES", "auto", "en_US", "xx"):
        assert L.canon_locale(L.canon_locale(tag)) == L.canon_locale(tag)


# --------------------------------------------------------------------------------------
# t(msgid, locale)
# --------------------------------------------------------------------------------------

def test_t_english_returns_the_msgid_itself():
    # English has no built-in table; the msgid is the English string.
    assert L.t("Cancel", "en") == "Cancel"
    assert L.t("Cancel", None) == "Cancel"


def test_t_translates_pt_and_es():
    assert L.t("Select all", "pt") == "Selecionar todos"
    assert L.t("Select all", "pt_BR") == "Selecionar todos"
    assert L.t("Select all", "es") == "Seleccionar todo"


def test_t_unknown_key_falls_back_to_the_msgid():
    assert L.t("this key certainly does not exist 123", "pt") == \
        "this key certainly does not exist 123"


def test_t_all_caps_msgid_yields_all_caps_translation():
    # "SELECT ALL" is matched case-foldedly against "Select all" then upper()ed.
    assert L.t("SELECT ALL", "pt") == "SELECIONAR TODOS"


def test_t_coerces_non_string_msgid():
    assert L.t(123, "en") == "123"


# --------------------------------------------------------------------------------------
# misc
# --------------------------------------------------------------------------------------

def test_is_rtl_is_always_false():
    for tag in (None, "en", "pt_BR", "es", "ar", "he"):
        assert L.is_rtl(tag) is False


def test_os_locale_tag_returns_a_canonical_tag():
    tag = L.os_locale_tag()
    assert isinstance(tag, str) and tag
    assert L.canon_locale(tag) == tag
