"""Real browser geometry for readable Chinese query cards and intact source text."""

import os
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from hd2bot.rendering import CardRow, CardSection, HtmlRenderer, QueryCard
from hd2bot.rendering.typography import text_parts
from hd2bot.wiki.service import CatalogService


@pytest.mark.parametrize("value", [
    "", " \t\n ", "AC-8 自动加农炮", "支持武器 · 背包 · 重甲穿透 · 静止装填",
    "AR-23 Liberator、AR-23、Liberator、解放者、解放者突击步枪",
    "4秒 3.55秒 (升级后) 0.66秒 (每弹夹协助装填)",
    "射速 640 发/分钟，生命恢复 1,234.56 HP/秒，解放进度 99.50%。",
    "重甲 (穿甲高爆曳光弹) 中甲 (近炸弹)\n区域 #0 / 区域 #123",
    "中文长段落保持原有标点、空格和换行。\n" * 8,
    "https://helldivers.wiki.gg/wiki/" + "VeryLongWeaponName" * 15,
    '<img src="https://invalid.example/leak" onerror="alert(1)">&特殊字符',
])
def test_readable_fragments_preserve_exact_original_text(value):
    assert "".join(fragment for fragment, _ in text_parts(value)) == value


def test_readable_fragments_remain_escaped_in_html():
    attack = '<img src="https://invalid.example/leak" onerror="alert(1)">'
    html = HtmlRenderer().render_html(QueryCard(
        "文字安全", sections=(CardSection("字段", (CardRow(attack, attack, attack),)),),
    ))
    assert attack not in html
    assert html.count("&lt;img") == 3
    assert '<img src="https://invalid.example/' not in html
    assert "<script" not in html


_LONG_WORD = "Supercalifragilisticexpialidocious" * 12
_LONG_URL = "https://helldivers.wiki.gg/wiki/" + "Long_Equipment_Name_" * 20


def _mixed_card():
    return QueryCard(
        "中英文排版回归",
        sections=(
            CardSection("装备资料", (
                CardRow("特性", "支持武器 · 背包 · 重甲穿透 · 静止装填"),
                CardRow("穿甲", "重甲 (穿甲高爆曳光弹) 中甲 (近炸弹)"),
                CardRow("完全换弹时间", "4秒 3.55秒 (升级后) 0.66秒 (每弹夹协助装填)"),
                CardRow("射速", "640 发/分钟"),
                CardRow("型号", "AC-8"),
            )),
            CardSection("任务与区域", (
                CardRow("任务时长累计值", "190"),
                CardRow("阿斯佩洛斯主星 · 区域 #0", "1,234 人"),
                CardRow("别名", "AR-23 Liberator、AR-23、Liberator、解放者、解放者突击步枪"),
            )),
            CardSection("完整长文", (
                CardRow("原始单词", _LONG_WORD),
                CardRow("原始链接", _LONG_URL),
                CardRow("正文", "这是应当正常换行的完整中文段落，保留文字并且没有省略号。" * 8),
            )),
        ),
        footer=(_LONG_URL,),
    )


# Text ranges inspect rendered glyphs, including phrases crossing generated spans.
_GEOMETRY = r"""phrases => {
    const fields = [...document.querySelectorAll('.row-label, .row-value, .row-detail')];
    const phraseLines = Object.fromEntries(phrases.map(phrase => [phrase, []]));
    const overflow = [];
    const box = r => ({left:r.left, right:r.right, top:r.top, bottom:r.bottom});
    const linesFor = range => [...new Set([...range.getClientRects()]
        .filter(r => r.width > 0.1 && r.height > 0.1).map(r => Math.round(r.top)))];
    for (const element of fields) {
        const nodes = [];
        const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
        let node;
        let offset = 0;
        while ((node = walker.nextNode())) {
            nodes.push({node, start:offset, end:offset + node.length});
            offset += node.length;
        }
        for (const phrase of phrases) {
            let from = 0;
            for (;;) {
                const start = element.textContent.indexOf(phrase, from);
                if (start < 0) break;
                const end = start + phrase.length;
                const first = nodes.find(n => n.start <= start && n.end > start);
                const last = nodes.find(n => n.start < end && n.end >= end);
                const range = document.createRange();
                range.setStart(first.node, start - first.start);
                range.setEnd(last.node, end - last.start);
                phraseLines[phrase].push(linesFor(range));
                from = end;
            }
        }
        const section = element.closest('.section').getBoundingClientRect();
        for (const part of nodes) {
            const range = document.createRange();
            range.selectNodeContents(part.node);
            for (const rect of range.getClientRects()) {
                if (rect.width < 0.1 || rect.height < 0.1) continue;
                if (rect.left < section.left - 1 || rect.right > section.right + 1 ||
                    rect.top < section.top - 1 || rect.bottom > section.bottom + 1) {
                    overflow.push({text:part.node.textContent, rect:box(rect), section:box(section)});
                }
            }
        }
    }
    const rows = [...document.querySelectorAll('.row')].map(row => {
        const label = row.querySelector('.row-label');
        const value = row.querySelector('.row-value');
        const range = document.createRange();
        range.selectNodeContents(value);
        return {
            label:label.textContent, value:value.textContent,
            labelBox:box(label.getBoundingClientRect()), valueBox:box(value.getBoundingClientRect()),
            mainWidth:row.querySelector('.row-main').getBoundingClientRect().width,
            valueLines:linesFor(range), stacked:row.classList.contains('stacked'),
            overflow:getComputedStyle(value).textOverflow,
        };
    });
    const card = document.querySelector('#query-card');
    return {phraseLines, overflow, rows, cardWidth:card.clientWidth, scrollWidth:card.scrollWidth};
}"""


async def test_real_browser_keeps_equipment_phrases_and_fields_readable(monkeypatch):
    project_browser = Path(__file__).resolve().parents[1] / ".cache" / "ms-playwright"
    if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ and project_browser.exists():
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(project_browser))
    catalog = CatalogService()
    catalog.handle("武器", "AR-23")
    weapon_cards = [catalog.handle("选择", "1").card,
                    catalog.handle("武器", "AC-8").card]
    assert all(weapon_cards), "Bundled weapon catalogue must generate offline query cards"
    synthetic = _mixed_card()
    phrases = ("静止装填", "近炸弹", "升级后", "解放者", "640 发/分钟", "AC-8", "区域 #0")
    async with async_playwright() as driver:
        if not Path(driver.chromium.executable_path).is_file():
            pytest.skip("Chromium is not installed; run python -m playwright install chromium")
        browser = await driver.chromium.launch(headless=True)
        try:
            context = await browser.new_context(java_script_enabled=False, locale="zh-CN")
            await context.route("**/*", lambda route: route.abort())
            page = await context.new_page()
            for width in (720, 800, 1200):
                renderer = HtmlRenderer(width=width)
                await page.set_viewport_size({"width": width, "height": 900})
                for card in (synthetic, *weapon_cards):
                    await page.set_content(renderer.render_html(card), wait_until="load")
                    await page.evaluate("document.fonts.ready")
                    await renderer.fit_rows(page)
                    result = await page.evaluate(_GEOMETRY, phrases)
                    context_label = f"{width}px / {card.title}"
                    assert not result["overflow"], (context_label, result["overflow"])
                    assert result["scrollWidth"] <= result["cardWidth"] + 1, context_label
                    for phrase, occurrences in result["phraseLines"].items():
                        if card is synthetic:
                            assert occurrences, (context_label, phrase)
                        assert all(len(lines) == 1 for lines in occurrences), (
                            context_label, phrase, occurrences,
                        )
                    expected = [(row.label, row.value) for section in card.sections
                                for row in section.rows]
                    assert [(row["label"], row["value"]) for row in result["rows"]] == expected
                    assert all(row["overflow"] != "ellipsis" for row in result["rows"])
                    if card is not synthetic:
                        continue
                    rows = {row["label"]: row for row in result["rows"]}
                    short = rows["任务时长累计值"]
                    assert not short["stacked"], context_label
                    assert short["labelBox"]["right"] - short["labelBox"]["left"] > (
                        short["mainWidth"] * 0.65
                    ), context_label
                    assert len(short["valueLines"]) == 1
                    assert short["labelBox"]["right"] < short["valueBox"]["left"]
                    if width > 720:
                        long = rows["完全换弹时间"]
                        assert long["stacked"], context_label
                        assert long["valueBox"]["top"] >= long["labelBox"]["bottom"]
                    for label in ("原始单词", "原始链接", "正文"):
                        assert len(rows[label]["valueLines"]) > 1, (context_label, label)
        finally:
            await browser.close()
