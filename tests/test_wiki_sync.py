"""Importer checks exercise upstream layout, atomic replacement, and offline safety."""

import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest
from PIL import Image
from test_wiki_service import write_snapshot

from hd2bot import wiki_sync as sync


def html(body):
    return '<script>"wgRevisionId":123</script><div class="mw-parser-output">' + body + '</div>'


def test_stratagem_table_preserves_arrow_codes_and_cooldown(monkeypatch):
    monkeypatch.setitem(sync.MINIMUM_COUNTS, "stratagems", 1)
    value = html('<h3>Offensive Permit</h3><table class="wikitable"><tr>'
                 '<th>Icon</th><th>Name</th><th>Stratagem Code</th><th>Base Cooldown</th></tr>'
                 '<tr><td></td><td><a href="/wiki/Orbital_Precision_Strike">Orbital Precision Strike</a></td>'
                 '<td><img alt="Stratagem Arrow Right.svg"><img alt="Stratagem Arrow Up.svg"></td>'
                 '<td>90s</td></tr></table>')
    entries = sync.parse_index("stratagems", value)
    assert entries[0]["fields"] == [["Stratagem Code", "→ ↑"], ["Base Cooldown", "90s"]]
    assert entries[0]["revision"] == "123"


def test_cosmetic_merged_cells_and_missing_decoration_do_not_drop_entries(monkeypatch):
    monkeypatch.setitem(sync.MINIMUM_COUNTS, "cosmetics", 1)
    value = html('<h2>Patterns</h2><table class="wikitable"><tr>'
                 '<th>Pattern</th><th>Name</th><th>Cost</th><th>Level Needed</th><th>Pattern Icon</th></tr>'
                 '<tr><td></td><td>Default</td><td>0</td><td>0</td></tr>'
                 '<tr><td></td><td>Arctic</td><td rowspan="2">3000</td><td>5</td><td></td></tr>'
                 '<tr><td></td><td>Arctic Camo</td><td>5</td><td></td></tr></table>')
    entries = sync.parse_index("cosmetics", value)
    assert [entry["name"] for entry in entries] == ["Default", "Arctic", "Arctic Camo"]
    assert ["Cost", "3000"] in entries[-1]["fields"]
    assert len({entry["id"] for entry in entries}) == 3


def test_hidden_alignment_zero_is_not_part_of_armor_stat():
    assert sync.DOM('<td><span style="color:gray">0</span>50</td>').root.text() == "50"


def test_malformed_upstream_span_uses_the_displayed_leading_integer():
    table = sync.DOM('<table><tr><td rowspan=2">Name</td><td>A</td></tr>'
                     '<tr><td>B</td></tr></table>').root
    assert [[cell.text() for cell in row] for row in sync.rows(table)] == [["Name", "A"], ["Name", "B"]]


def test_currency_icon_retains_unit_without_duplicating_text_label():
    assert sync.DOM('<td><img alt="Super Credits">150</td>').root.text() == "Super Credits 150"
    assert sync.DOM('<td><img alt="Medals">15 Medals</td>').root.text() == "15 Medals"


def test_model_only_name_never_emits_empty_alias():
    entry = sync.make_entry("cosmetics", "Capes", "Ingress-81",
                            sync.BASE + "/wiki/Ingress-81", [], "1")
    assert entry["aliases"] == ["Ingress-81"]


def test_armor_cosmetics_preserve_supplementary_source_and_do_not_invent_price():
    value = html('<div class="hd2-itemgrid"><div class="hd2-itembox">'
                 '<span class="hd2-title"><a href="/wiki/Helmet">New Helmet</a></span>'
                 '<span>New Warbond</span></div></div>'
                 '<div class="hd2-itemgrid"><div class="hd2-itembox">'
                 '<span class="hd2-title"><a href="/wiki/Cape">New Cape</a></span>'
                 '<span>New Warbond</span></div></div>')
    entries = sync.parse_armor_cosmetics(value)
    assert [entry["subcategory"] for entry in entries] == ["Helmets", "Capes"]
    assert entries[0]["revision_source_url"] == sync.BASE + "/wiki/Armor"
    assert entries[0]["fields"] == [["获取方式", "New Warbond"]]


def test_consumer_schema_failure_preserves_previous_output(tmp_path):
    output = tmp_path / "catalog.json"
    output.write_bytes(b"old catalog")
    with pytest.raises(ValueError):
        sync.publish_catalog(output, {"schema_version": 1, "entries": []})
    assert output.read_bytes() == b"old catalog"


def test_hd1_and_non_article_links_are_rejected():
    assert sync.safe_url("/wiki/AR-23_Liberator") == sync.BASE + "/wiki/AR-23_Liberator"
    for url in ("/wiki/Helldivers_1:Weapons", "/wiki/File:Weapon.png", "/api.php",
                "/wiki/Weapons?action=edit", "https://example.com/wiki/Weapon",
                "http://helldivers.wiki.gg/wiki/Weapon"):
        assert sync.safe_url(url) is None


def test_incomplete_index_fails_instead_of_shipping_few_examples():
    with pytest.raises(ValueError, match="Incomplete weapons"):
        sync.parse_index("weapons", html('<p>No equipment table</p>'))


def test_weapon_image_is_taken_from_infobox_not_navigation_icon():
    entry = sync.make_entry("weapons", "Primary", "AR-23 Liberator",
                            sync.BASE + "/wiki/AR-23_Liberator", [], "1")
    value = html('<img src="/images/navigation.png"><div class="druid-main-image">'
                 '<a href="/wiki/File:Liberator.png"><img src="/images/Liberator.png?v=1"></a></div>'
                 '<div class="druid-row"><div class="druid-label">Capacity</div>'
                 '<div class="druid-data">45</div></div>')
    sync.enrich_weapon(entry, value)
    assert entry["image_url"] == sync.BASE + "/images/Liberator.png?v=1"
    assert entry["image_credit"] == sync.BASE + "/wiki/File:Liberator.png"
    assert entry["fields"] == [["Capacity", "45"]]


def test_support_weapon_selects_weapon_tab_instead_of_stratagem_icon():
    entry = sync.make_entry("weapons", "Support Weapons", "MG-43 Machine Gun",
                            sync.BASE + "/wiki/MG-43_Machine_Gun", [], "1")
    value = html('<div class="druid-main-images-file" data-druid-tab-key="Stratagem">'
                 '<img src="/images/Machine_Gun_Stratagem.svg"></div>'
                 '<div class="druid-main-images-file" data-druid-tab-key="Weapon">'
                 '<a href="/wiki/File:Gun.png"><img src="/images/Gun.png"></a></div>'
                 '<div class="druid-row"><div class="druid-label">Capacity</div>'
                 '<div class="druid-data">175</div></div>')
    sync.enrich_weapon(entry, value)
    assert entry["image_url"] == sync.BASE + "/images/Gun.png"


def test_changed_weapon_layout_fails_instead_of_erasing_details():
    entry = sync.make_entry("weapons", "Primary", "AR-23 Liberator",
                            sync.BASE + "/wiki/AR-23_Liberator", [], "1")
    with pytest.raises(ValueError, match="layout changed"):
        sync.enrich_weapon(entry, html('<div class="new-infobox-layout">Capacity 45</div>'))


def test_weapon_procurement_fills_price_absent_from_infobox():
    entry = sync.make_entry("weapons", "Secondary", "CQC-73 Entrenchment Tool",
                            sync.BASE + "/wiki/CQC-73_Entrenchment_Tool", [], "1")
    value = html('<div class="druid-row"><div class="druid-label">Capacity</div>'
                 '<div class="druid-data">1</div></div><h2>Procurement</h2>'
                 '<p>The CQC-73 Entrenchment Tool is unlocked from the '
                 '<a href="/wiki/Entrenched_Division_Premium_Warbond">Entrenched Division</a>'
                 ' Premium Warbond for 20 Medals.</p><h2>Attachments</h2>'
                 '<p>A different upgrade is unlocked for 200 Requisition Slips.</p>')
    sync.enrich_weapon(entry, value)
    assert dict(entry["fields"])["Unlock Cost"] == "20 Medals"
    assert dict(entry["fields"])["Source"] == "Entrenched Division"


def test_field_pickup_does_not_inherit_the_secondary_variant_price():
    entry = sync.make_entry("weapons", "Support Weapons", "CQC-72 Entrenchment Tool",
                            sync.BASE + "/wiki/CQC-72_Entrenchment_Tool", [], "1")
    value = html('<div class="druid-row"><div class="druid-label">Capacity</div>'
                 '<div class="druid-data">1</div></div><h2>Procurement</h2>'
                 '<ul><li>The CQC-72 can be found in various points of interest during missions.</li></ul>'
                 '<ul><li>A secondary weapon variant, CQC-73, is unlocked for 20 Medals.</li></ul>'
                 '<h2>Detailed Weapon Statistics</h2>')
    sync.enrich_weapon(entry, value)
    assert dict(entry["fields"])["Unlock Cost"] == "无需购买"
    assert dict(entry["fields"])["获取方式"] == "任务中的兴趣点拾取"


def test_initial_equipment_is_explicitly_free_without_inventing_numeric_price():
    entry = {"fields": [["Source", "Starter Equipment"]]}
    sync.enrich_weapon_acquisition(entry, sync.article(html('<p>Available to all Helldivers.</p>')))
    assert dict(entry["fields"])["Unlock Cost"] == "免费"


def test_gift_under_acquisition_heading_does_not_become_unknown_cost():
    entry = {"fields": [["Source", "Liberty Day"]]}
    body = sync.article(html('<h2>Acquisition</h2><p>Distributed to all present and future Helldivers '
                             'to celebrate Liberty Day.</p><h2>Statistics</h2>'))
    sync.enrich_weapon_acquisition(entry, body)
    assert dict(entry["fields"])["Unlock Cost"] == "免费发放"


def warbond_html(*, item="R-63 Diligence", item_type="Marksman Rifle", cost="8",
                 threshold="8", warbond_cost="1,000 Super Credits", page=2):
    return html('<div class="druid-title">Test Warbond</div>'
                '<div class="druid-row"><span class="druid-label">Cost</span>'
                f'<span class="druid-data">{warbond_cost}</span></div>'
                f'<h3>Page {page}</h3><div class="hd2-acq-stat-row">Total page cost: 320</div>'
                + (f'<div class="hd2-acq-stat-row">Medals spent to unlock: {threshold}</div>'
                   if threshold else '')
                + '<table class="wikitable"><tr><th>Icon</th><th>Item</th><th>Type</th><th>Cost</th></tr>'
                f'<tr><td></td><td><a href="/wiki/{item.replace(" ", "_")}">{item}</a></td>'
                f'<td>{item_type}</td><td><img alt="Medals">{cost}</td></tr></table>')


def test_warbond_item_cost_page_threshold_and_premium_currency_remain_separate():
    item = sync.parse_warbond(warbond_html(), sync.BASE + "/wiki/Test_Warbond")[0]
    assert (item["cost"], item["threshold"], item["warbond_cost"], item["page"]) == (
        "8 Medals", "8 Medals", "1,000 Super Credits", 2)
    assert "320" not in (item["cost"], item["threshold"])
    assert item["revision"] == "123"


def test_missing_warbond_page_threshold_is_not_guessed_from_page_total():
    item = sync.parse_warbond(warbond_html(threshold=""), sync.BASE + "/wiki/Test_Warbond")[0]
    assert item["threshold"] == ""


def test_armor_and_helmet_sharing_wiki_url_get_their_own_reward_rows():
    item = sync.parse_warbond(warbond_html(item="CPG-48 Sapper", item_type="Helmet", cost="35"),
                              sync.BASE + "/wiki/Test_Warbond")[0]
    helmet = sync.make_entry("cosmetics", "Helmets", "CPG-48 Sapper", item["url"], [], "1")
    armor = sync.make_entry("armor", "Medium", "CPG-48 Sapper", item["url"], [], "1")
    assert sync.warbond_item_matches(helmet, item)
    assert not sync.warbond_item_matches(armor, item)


def test_warbond_enrichment_is_repeatable_and_retains_missing_price_as_unknown(monkeypatch):
    url = sync.BASE + "/wiki/Test_Warbond"
    entry = sync.make_entry("weapons", "Primary", "R-63 Diligence",
                            sync.BASE + "/wiki/R-63_Diligence", [["Unlock Cost", "? Medals"]], "1")
    monkeypatch.setattr(sync, "warbond_urls", lambda _: [url])
    class Cached:
        def fetch(self, url):
            return warbond_html(cost=""), "digest"
    first = sync.enrich_warbond_acquisitions([entry], Cached())
    before = json.dumps(entry, sort_keys=True)
    assert sync.enrich_warbond_acquisitions([entry], Cached()) == first
    assert json.dumps(entry, sort_keys=True) == before
    assert dict(entry["fields"])["Unlock Cost"] == "? Medals"
    assert entry["source_urls"] == [entry["source_url"], url]
    assert first["cost_conflicts"] == []


def test_failed_sync_leaves_last_good_catalog_untouched(tmp_path, monkeypatch):
    output = tmp_path / "catalog.json"
    output.write_text('{"schema_version": 1, "entries": []}', "utf-8")
    original = output.read_bytes()
    def fail(*args):
        raise OSError("upstream unavailable")
    monkeypatch.setattr(sync.Fetcher, "fetch", fail)
    with pytest.raises(OSError, match="upstream unavailable"):
        sync.sync_catalog(output, tmp_path / "cache")
    assert output.read_bytes() == original


def test_offline_missing_cache_does_not_make_network_request(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("offline import must not access network")
    monkeypatch.setattr(sync, "urlopen", unexpected)
    with pytest.raises(ValueError, match="No cached article"):
        sync.Fetcher(tmp_path, offline=True).fetch(sync.BASE + "/wiki/Weapons")


class Response:
    def __init__(self, url, data, headers=None):
        self.url, self.data, self.headers = url, data, headers or {}
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, limit):
        return self.data[:limit]


def test_conditional_refresh_uses_etag_and_keeps_304_body(tmp_path, monkeypatch):
    url = sync.BASE + "/wiki/Weapons"
    value = html('<p>Equipment</p>').encode()
    monkeypatch.setattr(sync.time, "sleep", lambda *_: None)
    monkeypatch.setattr(sync, "urlopen", lambda *a, **kw: Response(url, value, {"ETag": '"abc"'}))
    first, digest = sync.Fetcher(tmp_path).fetch(url)
    def not_modified(request, **kwargs):
        assert request.headers["If-none-match"] == '"abc"'
        raise HTTPError(url, 304, "Not Modified", {}, None)
    monkeypatch.setattr(sync, "urlopen", not_modified)
    assert sync.Fetcher(tmp_path, refresh=True).fetch(url) == (first, digest)


def test_missing_cached_body_does_not_send_stale_validator(tmp_path, monkeypatch):
    import hashlib
    url = sync.BASE + "/wiki/Weapons"
    key = hashlib.sha256(url.encode()).hexdigest()
    sync.atomic_json(tmp_path / (key + ".json"), {"etag": '"stale"', "checked_at": 1})
    def fetch_full(request, **kwargs):
        assert "If-none-match" not in request.headers
        return Response(url, html('<p>Fresh article</p>').encode())
    monkeypatch.setattr(sync, "urlopen", fetch_full)
    assert "Fresh article" in sync.Fetcher(tmp_path, refresh=True).fetch(url)[0]


def test_article_byte_limit_rejects_truncated_multibyte_documents(tmp_path, monkeypatch):
    url = sync.BASE + "/zh/wiki/Weapons"
    document = html("<p>中文装备</p>").encode("utf8") + b" " * 8_000_001
    monkeypatch.setattr(sync, "urlopen", lambda *a, **kw: Response(url, document))
    with pytest.raises(ValueError, match="8 MB limit"):
        sync.Fetcher(tmp_path).fetch(url)
    assert not list(tmp_path.glob("*.html"))


def test_429_retries_are_bounded_and_preserve_error(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sync.time, "sleep", lambda *_: None)
    def throttled(request, **kwargs):
        calls.append(request)
        raise HTTPError(request.full_url, 429, "Too many requests", {"Retry-After": "1"}, None)
    monkeypatch.setattr(sync, "urlopen", throttled)
    with pytest.raises(HTTPError):
        sync.Fetcher(tmp_path).fetch(sync.BASE + "/wiki/Weapons")
    assert len(calls) == 3


def test_html_disguised_as_weapon_image_is_rejected(tmp_path, monkeypatch):
    url = sync.BASE + "/images/Fake.png"
    monkeypatch.setattr(sync, "urlopen", lambda *a, **kw: Response(url, b"<html>Challenge</html>"))
    with pytest.raises(OSError):
        sync.Fetcher(tmp_path / "cache").image(url, tmp_path / "images")
    assert not (tmp_path / "images").exists()


def test_verified_image_uses_content_hash_and_is_resized(tmp_path, monkeypatch):
    url = sync.BASE + "/images/Weapon.png"
    buffer = io.BytesIO()
    Image.new("RGBA", (1600, 900), (1, 2, 3, 255)).save(buffer, format="PNG")
    monkeypatch.setattr(sync, "urlopen", lambda *a, **kw: Response(url, buffer.getvalue()))
    name = sync.Fetcher(tmp_path / "cache").image(url, tmp_path / "images")
    assert len(Path(name).stem) == 64
    with Image.open(tmp_path / "images" / name) as image:
        assert image.format == "WEBP"
        assert image.size == (800, 450)


def test_only_observed_wiki_raster_image_paths_are_allowed():
    assert sync.safe_image_url(sync.BASE + "/images/thumb/A.png/600px-A.png?abc")
    assert not sync.safe_image_url(sync.BASE + "/images/Icon.svg")
    assert not sync.safe_image_url("https://example.com/images/A.png")


def test_atomic_json_replaces_complete_document(tmp_path):
    output = tmp_path / "nested" / "catalog.json"
    sync.atomic_json(output, {"名称": "解放者"})
    assert json.loads(output.read_text("utf-8")) == {"名称": "解放者"}
    assert list(output.parent.iterdir()) == [output]


def test_unchanged_article_uses_parsed_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "enrich_warbond_acquisitions", lambda *args: {})
    value = html('<table class="wikitable"><tr><th>Booster</th><th>Description</th></tr>'
                 '<tr><td><a href="/wiki/Vitality_Enhancement">Vitality Enhancement</a></td>'
                 '<td>Minor damage reduction.</td></tr></table>')
    monkeypatch.setattr(sync, "INDEXES", {"boosters": "Boosters"})
    monkeypatch.setattr(sync, "BUNDLED_CATALOG", tmp_path / "missing-bundle.json")
    monkeypatch.setitem(sync.MINIMUM_COUNTS, "boosters", 1)
    monkeypatch.setattr(sync.Fetcher, "fetch", lambda *a: (value, "unchanged-body-hash"))
    output, cache = tmp_path / "catalog.json", tmp_path / "cache"
    assert sync.sync_catalog(output, cache, include_chinese=False)["count"] == 1
    def unexpected_parse(*args):
        pytest.fail("identical upstream content must reuse its parsed cache")
    monkeypatch.setattr(sync, "parse_index", unexpected_parse)
    assert sync.sync_catalog(output, cache, include_chinese=False)["count"] == 1


def test_offline_rebuild_preserves_last_remote_sync_time(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "enrich_warbond_acquisitions", lambda *args: {})
    value = html('<table class="wikitable"><tr><th>Booster</th><th>Description</th></tr>'
                 '<tr><td><a href="/wiki/Vitality_Enhancement">Vitality Enhancement</a></td>'
                 '<td>Minor damage reduction.</td></tr></table>')
    monkeypatch.setattr(sync, "INDEXES", {"boosters": "Boosters"})
    monkeypatch.setitem(sync.MINIMUM_COUNTS, "boosters", 1)
    monkeypatch.setattr(sync.Fetcher, "fetch", lambda *a: (value, "old-body"))
    output = tmp_path / "catalog.json"
    write_snapshot(output, synced_at="2020-01-01T00:00:00+00:00")
    sync.sync_catalog(output, tmp_path / "cache", offline=True, include_chinese=False)
    result = json.loads(output.read_text("utf-8"))
    assert result["synced_at"] == "2020-01-01T00:00:00+00:00"
    assert result["built_at"] != result["synced_at"]


@pytest.mark.parametrize("damaged", ["{truncated", "[]", '{"entries": [null]}'])
def test_sync_recovers_damaged_snapshot_using_bundled_coverage(tmp_path, monkeypatch, damaged):
    value = html('<table class="wikitable"><tr><th>Booster</th><th>Description</th></tr>'
                 '<tr><td><a href="/wiki/Vitality_Enhancement">Vitality Enhancement</a></td>'
                 '<td>Minor damage reduction.</td></tr></table>')
    bundled = tmp_path / "bundled.json"
    coverage = {category: {"count": 10} for category in sync.INDEXES}
    write_snapshot(bundled, coverage=coverage)
    monkeypatch.setattr(sync, "BUNDLED_CATALOG", bundled)
    monkeypatch.setattr(sync, "INDEXES", {"boosters": "Boosters"})
    monkeypatch.setitem(sync.MINIMUM_COUNTS, "boosters", 1)
    monkeypatch.setattr(sync.Fetcher, "fetch", lambda *a: (value, "recovered-body"))
    monkeypatch.setattr(sync, "enrich_warbond_acquisitions", lambda *a: {})
    output = tmp_path / "catalog.json"
    output.write_text(damaged, encoding="utf8")
    with pytest.raises(ValueError, match="unexpectedly decreased"):
        sync.sync_catalog(output, tmp_path / "cache", include_chinese=False)
    assert output.read_text("utf8") == damaged
    coverage["boosters"]["count"] = 1
    write_snapshot(bundled, coverage=coverage)
    assert sync.sync_catalog(output, tmp_path / "cache", include_chinese=False)["count"] == 1
    from hd2bot.wiki.service import _read_snapshot
    assert len(_read_snapshot(output).entries) == 1


def test_first_external_snapshot_compares_against_bundled_baseline(tmp_path, monkeypatch):
    value = html('<table class="wikitable"><tr><th>Booster</th><th>Description</th></tr>'
                 '<tr><td><a href="/wiki/Vitality_Enhancement">Vitality Enhancement</a></td>'
                 '<td>Minor damage reduction.</td></tr></table>')
    bundled = tmp_path / "bundled.json"
    sync.atomic_json(bundled, {"schema_version": 1, "entries": [], "coverage": {
        category: {"count": 10} for category in sync.INDEXES}})
    monkeypatch.setattr(sync, "BUNDLED_CATALOG", bundled)
    monkeypatch.setattr(sync, "INDEXES", {"boosters": "Boosters"})
    monkeypatch.setitem(sync.MINIMUM_COUNTS, "boosters", 1)
    monkeypatch.setattr(sync.Fetcher, "fetch", lambda *a: (value, "reduced-body"))
    output = tmp_path / "external.json"
    with pytest.raises(ValueError, match="unexpectedly decreased"):
        sync.sync_catalog(output, tmp_path / "cache")
    assert not output.exists()


def test_chinese_names_match_exact_longest_model_code_and_keep_english(monkeypatch):
    monkeypatch.setattr(sync, "ZH_MINIMUM_WEAPON_NAMES", 1)
    value = html('<div class="gallerytext"><a href="/zh/wiki/AR-23P_%E7%A9%BF%E7%94%B2?action=edit&amp;redlink=1">'
                 'AR-23P 穿甲“解放者”</a></div>')
    class Cached:
        def optional_article(self, url):
            return value if url.endswith("%E6%AD%A6%E5%99%A8") else None
    entries = [sync.make_entry("weapons", "Primary", name, sync.BASE + "/wiki/" + name.replace(" ", "_"), [], "1")
               for name in ("AR-23 Liberator", "AR-23P Liberator Penetrator")]
    ids = [entry["id"] for entry in entries]
    result = sync.merge_chinese_catalog(entries, Cached())
    assert entries[0]["name"] == "AR-23 Liberator"
    assert entries[1]["name"] == 'AR-23P 穿甲“解放者”'
    assert entries[1]["english_name"] == "AR-23P Liberator Penetrator"
    assert [entry["id"] for entry in entries] == ids
    assert entries[1]["name_revision"] == "123"
    assert result["chinese_name_entries"] == 1


def test_chinese_name_containing_english_model_is_not_mistaken_for_chinese_prose():
    assert not sync.is_chinese_prose("The AC-8 机炮 is a Support Weapon that fires explosive projectiles.")
    assert sync.is_chinese_prose("一次性的武器，发射高当量导弹。用后即弃。")


def test_chinese_model_crosswalk_and_unspaced_names():
    assert sync.known_code("AC-8机炮", {"AC-8"}) == "AC-8"
    assert sync.known_code("M6C/特种作战 手枪", {"M6C/SOCOM"}) == "M6C/SOCOM"
    assert sync.known_code("AR-23P 穿甲解放者", {"AR-23"}) == ""


def test_chinese_optional_404_is_cached_and_rechecked_on_refresh(tmp_path, monkeypatch):
    calls = []
    url = sync.BASE + "/zh/wiki/Boosters"
    def absent(request, **kwargs):
        calls.append(request)
        raise HTTPError(url, 404, "Not Found", {}, None)
    monkeypatch.setattr(sync, "urlopen", absent)
    assert sync.Fetcher(tmp_path).optional_article(url) is None
    assert sync.Fetcher(tmp_path, offline=True).optional_article(url) is None
    assert len(calls) == 1
    assert sync.Fetcher(tmp_path, refresh=True).optional_article(url) is None
    assert len(calls) == 2
