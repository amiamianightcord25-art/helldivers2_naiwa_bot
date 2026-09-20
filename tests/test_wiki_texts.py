"""Keep the maintained Chinese descriptions complete and faithful to source numbers."""

import json
import re
from collections import Counter
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "src/hd2bot/assets"


def no_duplicate_keys(pairs):
    values = {}
    for key, value in pairs:
        assert key not in values, f"Duplicate translation key: {key}"
        values[key] = value
    return values


def translations():
    payload = json.loads((ASSETS / "wiki_zh_texts.json").read_text(encoding="utf8"),
                         object_pairs_hook=no_duplicate_keys)
    assert payload["schema_version"] == 1
    assert payload["license"] == "CC BY-NC-SA 4.0"
    assert payload["source"] == "Helldivers Wiki contributors; translations for this bot"
    return payload["texts"]


def is_english(value):
    return bool(re.search(r"[A-Za-z]", value) and not re.search(r"[\u3400-\u9fff]", value))


def test_all_bundled_summaries_descriptions_passives_and_traits_have_chinese_text():
    catalog = json.loads((ASSETS / "wiki_catalog.json").read_text(encoding="utf8"))
    texts = translations()
    required = set()
    for entry in catalog["entries"]:
        if is_english(entry.get("summary", "")):
            required.add(entry["summary"])
        for label, value in entry.get("fields", []):
            if label in {"Description", "Passive", "Traits"} and is_english(value):
                required.add(value)
    assert required  # Native Chinese additions may reduce the number requiring translation.
    assert required <= texts.keys(), "Missing translations: " + repr(sorted(required - texts.keys()))
    for source in required:
        assert re.search(r"[\u3400-\u9fff]", texts[source]), source
        assert "待翻译" not in texts[source]
        assert texts[source].strip() == texts[source]


def test_translations_preserve_every_literal_source_number_and_add_none():
    number = re.compile(r"\d+(?:\.\d+)?%?")
    for source, translated in translations().items():
        assert Counter(number.findall(source)) == Counter(number.findall(translated)), source


def test_different_trench_tools_keep_their_secondary_and_support_roles():
    texts = translations()
    support = "The CQC-72 Entrenchment Tool , is a Melee Support Weapon that doubles as a shovel."
    secondary = "The CQC-73 Entrenchment Tool is a Melee Secondary Weapon that doubles as a shovel."
    assert "近战支援武器" in texts[support]
    assert "近战副武器" in texts[secondary]


def test_booster_limits_and_exclusions_are_not_lost_in_translation():
    texts = translations()
    exclusion = ("Allows all Helldivers to recover faster after being slowed by an attack, "
                 "such as acid . Does not mitigate “area effects,” such as EMS strikes .")
    limit = "Large enemies now have a chance of dropping Samples on death. Capped at 10 drops per mission."
    assert "不会减轻" in texts[exclusion] and "区域效果" in texts[exclusion]
    assert "一定概率" in texts[limit] and "每次任务最多掉落 10 次" in texts[limit]
