"""Safe HTML, bounded JPEGs and a real, isolated Chromium rendering path."""

import io
import os
import random
import re
from dataclasses import replace
from html import unescape
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from playwright.async_api import async_playwright

from hd2bot.rendering import (
    CardMetric,
    CardRow,
    CardSection,
    HtmlRenderer,
    QueryCard,
    RenderError,
)


def career_card(rows_per_section=9):
    return QueryCard(
        "个人生涯战绩",
        "已配置测试账号 · 完整数据",
        metrics=(CardMetric("敌人总击杀", "234,567", "虫族 / 机器人 / 光能族"),
                 CardMetric("完成任务", "3,219"), CardMetric("撤离", "2,981")),
        sections=tuple(CardSection(
            title,
            tuple(CardRow(f"指标 {i * rows_per_section + j + 1}", str(j * 1234),
                          "当前数值" if j == 0 else "")
                  for j in range(rows_per_section)),
        ) for i, title in enumerate(("作战记录", "任务与战略", "行动与资源"))),
        notices=("模拟数据 · 非真实战绩",),
        footer=("原始查询时间：2026-09-16 19:00 +08:00", "仅限已配置测试账号"),
    )


def test_template_escapes_every_untrusted_text_field():
    attack = '<img src="https://invalid.example/leak" onerror="alert(1)">'
    card = QueryCard(
        attack, attack, attack,
        (CardMetric(attack, attack, attack),),
        (CardSection(attack, (CardRow(attack, attack, attack),)),),
        (attack,), (attack,),
    )
    html = HtmlRenderer().render_html(card)
    assert attack not in html
    assert html.count("&lt;img") == 13  # title is used in the page title and main heading.
    assert "onerror=\"alert" not in html
    assert "default-src 'none'" in html
    assert "script-src 'none'" in html
    assert "<script" not in html


def test_template_retains_all_27_fields_and_metadata():
    card = career_card()
    html = HtmlRenderer().render_html(card)
    for section in card.sections:
        for row in section.rows:
            assert f">{row.label}<" in html
    assert html.count('class="row"') == 27
    assert "模拟数据 · 非真实战绩" in html
    assert "原始查询时间：2026-09-16 19:00 +08:00" in html


@pytest.mark.parametrize(("progress", "expected"), [(125, "100.00"), (-4, "0.00"),
                                                    (12.345, "12.35"),
                                                    (float("nan"), "0.00")])
def test_progress_is_numeric_and_bounded(progress, expected):
    card = QueryCard("星球", sections=(CardSection("解放", (
        CardRow("进度", "数值", progress=progress),
    )),))
    html = HtmlRenderer().render_html(card)
    assert f'style="width: {expected}%"' in html


def test_long_prose_uses_full_width_and_preserves_linebreaks():
    message = "本条公告包含完整详情，请依照超级地球指令完成当前任务。" * 5 + "\n下一阶段安排。"
    html = HtmlRenderer().render_html(QueryCard(
        "银河公告", sections=(CardSection("公告", (CardRow("任务更新", message),)),),
    ))
    assert 'class="row prose"' in html
    assert message in unescape(re.sub(r"<[^>]+>", "", html))
    assert "text-overflow" not in html
    assert "line-clamp" not in html


def test_jpeg_encoder_enforces_size_without_cropping():
    # Seeded high-entropy content exercises size reduction, unlike a flat image.
    width, height = 1200, 700
    raw = random.Random(21).randbytes(width * height * 3)
    source = Image.frombytes("RGB", (width, height), raw)
    buffer = io.BytesIO()
    source.save(buffer, "PNG")
    large = HtmlRenderer(max_bytes=2_000_000, jpeg_quality=90)._encode_jpeg(buffer.getvalue())
    small = HtmlRenderer(max_bytes=170_000, jpeg_quality=90)._encode_jpeg(buffer.getvalue())
    assert small[:3] == b"\xff\xd8\xff"
    assert len(small) <= 170_000 < len(large)
    with Image.open(io.BytesIO(small)) as compressed:
        assert compressed.format == "JPEG"
        assert compressed.width >= 720
        assert abs(compressed.width / compressed.height - width / height) < 0.003
        assert compressed.info["progressive"] == 1


def test_impossible_size_budget_reports_fallback_instead_of_cropping():
    source = Image.new("RGB", (1200, 800), "#283438")
    buffer = io.BytesIO()
    source.save(buffer, "PNG")
    with pytest.raises(RenderError, match="大小限制"):
        HtmlRenderer(max_bytes=30)._encode_jpeg(buffer.getvalue())


async def test_missing_browser_has_safe_error_and_cleans_up(tmp_path):
    renderer = HtmlRenderer(browser_path=tmp_path / "missing-browser.exe")
    try:
        with pytest.raises(RenderError) as exc:
            await renderer.render(career_card())
        assert str(tmp_path) not in str(exc.value)
        assert "Playwright" not in str(exc.value)
        assert renderer._browser is None
        assert renderer._playwright is None
    finally:
        await renderer.close()


async def test_memory_cache_and_closed_renderer():
    renderer = HtmlRenderer()
    renderer._render = AsyncMock(return_value=b"jpeg-payload")
    card = career_card()
    assert await renderer.render(card) == b"jpeg-payload"
    assert await renderer.render(card) == b"jpeg-payload"
    assert renderer._render.await_count == 1
    await renderer.render(replace(card, subtitle="更新后的账号信息"))
    assert renderer._render.await_count == 2
    await renderer.close()
    await renderer.close()
    assert not renderer._cache
    with pytest.raises(RenderError, match="已关闭"):
        await renderer.render(card)


async def test_real_chromium_complete_card_and_height_guard(monkeypatch):
    project_browser = Path(__file__).resolve().parents[1] / ".cache" / "ms-playwright"
    if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ and project_browser.exists():
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(project_browser))
    async with async_playwright() as driver:
        if not Path(driver.chromium.executable_path).is_file():
            pytest.skip("Chromium is not installed; run python -m playwright install chromium")
    renderer = HtmlRenderer(timeout=40)
    try:
        small = await renderer.render(career_card())
        browser = renderer._browser
        assert small[:3] == b"\xff\xd8\xff"
        with Image.open(io.BytesIO(small)) as first:
            first_height = first.height
            assert first.width == 1200
            assert first_height > 700
        larger = await renderer.render(career_card(20))
        with Image.open(io.BytesIO(larger)) as second:
            assert second.width == 1200
            assert second.height > first_height
            assert len(larger) <= renderer.max_bytes
        assert renderer._browser is browser
        assert browser.contexts == []
        with pytest.raises(RenderError, match="内容过长"):
            await renderer.render(career_card(170))
        assert browser.contexts == []
    finally:
        await renderer.close()
    assert not browser.is_connected()
