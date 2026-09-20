import socket

import pytest
from test_wiki_service import equipment, service_for

from hd2bot.presentation import ChatContext
from hd2bot.rendering.models import CardTile, QueryCard
from hd2bot.rendering.renderer import HtmlRenderer
from hd2bot.wiki.images import validated_svg
from hd2bot.wiki_sync import BASE, MINIMUM_COUNTS, parse_armor_cosmetics, parse_index

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><path fill="#ffd200" d="M0 0 L20 0 L10 20 Z"/></svg>'


def cosmetic(index):
    return equipment(index, id=f"cosmetics:Titles:Rank_{index}", category="cosmetics",
                     subcategory="Titles", code="", aliases=[], image_path="rank.svg")


def test_cosmetic_grids_tables_and_armor_pick_item_art_not_currency(monkeypatch):
    monkeypatch.setitem(MINIMUM_COUNTS, "cosmetics", 1)
    currency = '<img alt="Medals" src="/images/medals.png">'
    box = (f'<div class="hd2-itembox">{currency}'
           '<a href="/wiki/File:Helmet.png"><img src="/images/helmet.png"></a>'
           '<div class="hd2-title"><a href="/wiki/Helmet">Helmet</a></div></div>')
    html = ('<div class="mw-parser-output"><div class="hd2-itemgrid">' + box + '</div>'
            '<h2>Titles</h2><table class="wikitable"><tr><th>Icon</th><th>Title</th><th>Cost</th></tr>'
            '<tr><td><img src="/images/rank.svg"></td><td>Cadet</td><td>' + currency +
            '10</td></tr></table></div>')
    entries = parse_index("cosmetics", '<script>"wgRevisionId":123</script>' + html)
    assert [e["image_url"] for e in entries] == [BASE + "/images/helmet.png", BASE + "/images/rank.svg"]
    assert entries[0]["image_credit"] == BASE + "/wiki/File:Helmet.png"
    armor = '<div class="mw-parser-output">' + ('<div class="hd2-itemgrid">' + box + '</div>') * 2 + '</div>'
    assert all(e["image_url"].endswith("helmet.png") for e in parse_armor_cosmetics('<script>"wgRevisionId":123</script>' + armor))


def test_svg_supports_simple_art_and_internal_references():
    assert b"path" in validated_svg(SVG)
    assert b"#shape" in validated_svg(b'<svg><defs><path id="shape" d="M0 0"/></defs><use href="#shape"/></svg>')


@pytest.mark.parametrize("raw", [
    b'<svg><script>alert(1)</script></svg>',
    b'<svg onload="alert(1)"></svg>',
    b'<svg><use href="https://example.com/a"/></svg>',
    b'<svg><style>@import "https://example.com/a"</style></svg>',
    b'<!DOCTYPE svg [<!ENTITY a "b">]><svg/>',
])
def test_svg_rejects_nonlocal_or_active_content(raw):
    with pytest.raises(ValueError):
        validated_svg(raw)


def test_deep_svg_rejects_safely_and_cosmetic_query_retains_text(tmp_path):
    raw = b'<svg>' + b'<g>' * 1200 + b'</g>' * 1200 + b'</svg>'
    with pytest.raises(ValueError, match="svg_depth_limit"):
        validated_svg(raw)
    service = service_for(tmp_path, [cosmetic(1)])
    images = tmp_path / "wiki_images"
    images.mkdir()
    (images / "rank.svg").write_bytes(raw)
    reply = service.handle("装饰", "cosmetics:Titles:Rank_1")
    assert reply.card is not None and not reply.card.image_data
    assert "暂无可用示意图" in str(reply.card.sections)


def test_cosmetic_gallery_pagination_selection_and_svg_are_offline(tmp_path, monkeypatch):
    service = service_for(tmp_path, [cosmetic(i) for i in range(1, 10)])
    images = tmp_path / "wiki_images"
    images.mkdir()
    (images / "rank.svg").write_bytes(SVG)
    def no_network(*args, **kwargs):
        raise AssertionError("unexpected network")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    context = ChatContext("group", "group", "member")
    first = service.handle("装饰", context=context)
    assert len(first.card.gallery) == 8
    assert [x.number for x in first.card.gallery] == list(range(1, 9))
    assert all(x.image_data.startswith("data:image/svg+xml;base64,") for x in first.card.gallery)
    second = service.handle("下一页", context=context)
    assert len(second.card.gallery) == 1 and "2/2" in second.card.subtitle
    selected = service.handle("选择", "1", context=context)
    assert selected.card.title == second.card.gallery[0].name
    assert selected.card.image_data.startswith("data:image/svg+xml;base64,")


def test_cosmetic_without_art_keeps_an_explicit_image_card(tmp_path):
    reply = service_for(tmp_path, [cosmetic(1)]).handle("装饰", "cosmetics:Titles:Rank_1")
    assert reply.card is not None and not reply.card.image_data
    assert "暂无可用示意图" in str(reply.card.sections)


def test_gallery_escapes_names_and_keeps_global_qr(tmp_path):
    from PIL import Image

    qr = tmp_path / "synthetic-qr.png"
    Image.new("RGB", (32, 32), "white").save(qr)
    html = HtmlRenderer(qr_path=qr).render_html(QueryCard("装饰", gallery=(CardTile(1, '<script>bad</script>'),)))
    assert '<script>bad</script>' not in html
    assert '&lt;script&gt;' in html and 'QQ扫一扫体验' in html
    assert 'catalog-tile' in html and 'catalog-number' in html
