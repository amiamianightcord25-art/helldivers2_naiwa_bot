"""Isolated Chromium screenshots with bounded, readable JPEG output.

The renderer never fetches URLs, downloads browsers, or writes query data to disk.
Callers retain a plain-text reply and can fall back when ``RenderError`` is raised.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import math
import time
from collections import OrderedDict
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape
from PIL import Image
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from hd2bot.rendering.models import QueryCard
from hd2bot.rendering.typography import text_parts

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Playwright


class RenderError(RuntimeError):
    """A safe, recoverable rendering failure; no browser internals in the message."""


def _progress(value: float) -> str:
    """Clamp percentage values before putting them in a CSS declaration."""
    number = float(value)
    if not math.isfinite(number):
        number = 0.0
    return f"{min(100.0, max(0.0, number)):.2f}"


class HtmlRenderer:
    """Lazily share one browser while isolating every render in its own context."""

    MAX_HEIGHT = 8000
    MAX_PIXELS = 14_400_000
    CACHE_TTL = 30.0
    CACHE_ENTRIES = 8

    def __init__(
        self,
        *,
        browser_path: Path | None = None,
        browser_channel: str | None = None,
        width: int = 1200,
        jpeg_quality: int = 82,
        max_bytes: int = 900_000,
        timeout: float = 20,
        cache_dir: Path | None = None,
        max_concurrency: int = 2,
        qr_path: Path | None = None,
        include_qr: bool = True,
    ) -> None:
        if not 720 <= width <= 1800:
            raise ValueError("render width must be between 720 and 1800")
        if not 1 <= jpeg_quality <= 95:
            raise ValueError("JPEG quality must be between 1 and 95")
        if max_bytes < 1 or timeout <= 0 or not math.isfinite(timeout):
            raise ValueError("render size and timeout must be positive")
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= 4:
            raise ValueError("render concurrency must be between 1 and 4")
        self.browser_path = browser_path
        self.browser_channel = browser_channel
        self.width = width
        self.jpeg_quality = jpeg_quality
        self.max_bytes = max_bytes
        self.timeout = timeout
        # Kept as an integration option; actual query caching is memory-only.
        self.cache_dir = cache_dir
        self._environment = Environment(
            loader=PackageLoader("hd2bot.rendering", "templates"),
            autoescape=select_autoescape(["html"]),
            undefined=StrictUndefined,
        )
        self._environment.filters["progress"] = _progress
        self._environment.filters["text_parts"] = text_parts
        self._template = self._environment.get_template("query.html")
        # Legacy local checkouts may still keep their private QR asset here.
        # A distributable checkout has no operator QR and renders without it.
        qr = qr_path or files("hd2bot").joinpath("assets/qq_experience_qr.png")
        self._qr_data = ("data:image/png;base64," + base64.b64encode(qr.read_bytes()).decode("ascii")
                         if include_qr and qr.is_file() else "")
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._launch_lock = asyncio.Lock()
        self._capacity = asyncio.Semaphore(max_concurrency)
        self._closed = False
        self._cache: OrderedDict[str, tuple[float, bytes]] = OrderedDict()

    def render_html(self, card: QueryCard) -> str:
        """Render escaped, self-contained HTML without starting Chromium."""
        columns = min(max(1, card.columns), 3, max(1, (self.width - 61) // 335),
                      max(1, len(card.sections)))
        metric_columns = min(4, max(1, (self.width - 63) // 213), max(1, len(card.metrics)))
        return self._template.render(card=card, width=self.width, columns=columns,
                                     metric_columns=metric_columns, qq_qr_data=self._qr_data,
                                     qr_size=max(176, min(220, round(self.width * 0.16))))

    @staticmethod
    async def fit_rows(page) -> None:
        """Choose inline/stacked rows using the actual loaded font and column width."""
        await page.evaluate("""() => {
            const measure = document.createElement('canvas').getContext('2d');
            const naturalWidth = element => {
                measure.font = getComputedStyle(element).font;
                return Math.max(...element.textContent.split('\\n').map(t => measure.measureText(t).width));
            };
            const rows = [...document.querySelectorAll('.row:not(.prose)')];
            const decisions = rows.map(row => {
                const main = row.querySelector('.row-main');
                const label = row.querySelector('.row-label');
                const value = row.querySelector('.row-value');
                const gap = parseFloat(getComputedStyle(main).columnGap) || 0;
                return [row, naturalWidth(label) + naturalWidth(value) + gap > main.clientWidth - 2];
            });
            decisions.forEach(([row, stacked]) => row.classList.toggle('stacked', stacked));
        }""")

    def _cache_key(self, card: QueryCard) -> str:
        raw = json.dumps(
            [asdict(card), self.width, self.jpeg_quality, self.max_bytes],
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    async def _ensure_browser(self) -> Browser:
        async with self._launch_lock:
            if self._closed:
                raise RenderError("图片渲染服务已关闭")
            if self._browser is not None and self._browser.is_connected():
                return self._browser
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None
            self._browser = None
            driver = await async_playwright().start()
            try:
                browser = await driver.chromium.launch(
                    headless=True,
                    executable_path=str(self.browser_path) if self.browser_path else None,
                    channel=self.browser_channel,
                    timeout=self.timeout * 1000,
                )
            except BaseException:
                await driver.stop()
                raise
            self._playwright = driver
            self._browser = browser
            return browser

    async def render(self, card: QueryCard) -> bytes:
        """Return an entire JPEG card, or fail so callers can send full text."""
        if self._closed:
            raise RenderError("图片渲染服务已关闭")
        key = self._cache_key(card)
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached is not None and now - cached[0] < self.CACHE_TTL:
            self._cache.move_to_end(key)
            return cached[1]
        try:
            # Include queueing, browser startup, screenshot and encoding in the deadline.
            result = await asyncio.wait_for(self._render(card), timeout=self.timeout)
        except RenderError:
            raise
        except TimeoutError:
            raise RenderError("图片生成超时，请使用文字结果") from None
        except PlaywrightError:
            raise RenderError("图片渲染暂不可用，请使用文字结果") from None
        except (OSError, ValueError):
            raise RenderError("图片生成失败，请使用文字结果") from None
        self._cache[key] = (time.monotonic(), result)
        self._cache.move_to_end(key)
        while len(self._cache) > self.CACHE_ENTRIES:
            self._cache.popitem(last=False)
        return result

    async def _render(self, card: QueryCard) -> bytes:
        async with self._capacity:
            browser = await self._ensure_browser()
            context: BrowserContext | None = None
            try:
                context = await browser.new_context(
                    viewport={"width": self.width, "height": 900},
                    device_scale_factor=1,
                    java_script_enabled=False,
                    locale="zh-CN",
                    color_scheme="dark",
                    service_workers="block",
                )
                # The template is self-contained. All network requests are rejected,
                # including future accidental remote image/font/style references.
                await context.route("**/*", lambda route: route.abort())
                page = await context.new_page()
                page.set_default_timeout(self.timeout * 1000)
                await page.set_content(self.render_html(card), wait_until="load")
                await page.evaluate("document.fonts.ready")
                await self.fit_rows(page)
                target = page.locator("#query-card")
                bounds = await target.bounding_box()
                if bounds is None:
                    raise RenderError("图片内容为空，请使用文字结果")
                height = math.ceil(bounds["height"])
                width = math.ceil(bounds["width"])
                if height > self.MAX_HEIGHT or height * width > self.MAX_PIXELS:
                    raise RenderError("查询内容过长，已保留完整文字结果")
                # Lossless capture avoids double JPEG compression around small text.
                raster = await target.screenshot(type="png", animations="disabled")
                return await asyncio.to_thread(self._encode_jpeg, raster)
            finally:
                if context is not None:
                    try:
                        await context.close()
                    except PlaywrightError:
                        pass

    def _encode_jpeg(self, raster: bytes) -> bytes:
        """Fit a size budget without cropping rows or shrinking text to illegibility."""
        with Image.open(io.BytesIO(raster)) as source:
            image = source.convert("RGB")
        qualities = list(dict.fromkeys([
            self.jpeg_quality,
            min(self.jpeg_quality, max(38, self.jpeg_quality - 14)),
            min(self.jpeg_quality, max(38, self.jpeg_quality - 28)),
            min(38, self.jpeg_quality),
        ]))
        original_width = image.width
        while True:
            for quality in qualities:
                stream = io.BytesIO()
                try:
                    image.save(stream, format="JPEG", quality=quality, optimize=True,
                               progressive=True, subsampling=0)
                except OSError:
                    # Pillow's optimized 4:4:4 encoder can exhaust its internal
                    # buffer on high-entropy pixels. Standard 4:2:0 avoids that
                    # without global Pillow settings or temporary query files.
                    stream = io.BytesIO()
                    image.save(stream, format="JPEG", quality=quality, optimize=True,
                               progressive=True, subsampling=2)
                encoded = stream.getvalue()
                if len(encoded) <= self.max_bytes:
                    return encoded
            # Preserve the complete card and its aspect ratio. If even a readable
            # 720px image cannot fit, leave the response to the text fallback.
            minimum_width = min(720, original_width)
            if image.width <= minimum_width:
                raise RenderError("图片超过发送大小限制，已保留完整文字结果")
            next_width = max(minimum_width, int(image.width * 0.85))
            next_height = max(1, round(image.height * next_width / image.width))
            image = image.resize((next_width, next_height), Image.Resampling.LANCZOS)

    async def close(self) -> None:
        """Close browser/driver and clear the ephemeral cache; safe to call twice."""
        async with self._launch_lock:
            self._closed = True
            self._cache.clear()
            browser, self._browser = self._browser, None
            driver, self._playwright = self._playwright, None
            try:
                if browser is not None:
                    await browser.close()
            finally:
                if driver is not None:
                    await driver.stop()
