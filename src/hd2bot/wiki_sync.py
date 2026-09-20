"""Polite, repeatable import of the five public HELLDIVERS 2 equipment indexes.

Only rendered /wiki/ and /zh/wiki/ articles are fetched: robots.txt excludes the API.
The importer never executes page scripts or follows arbitrary links. Weapon infobox
illustrations are verified as raster images, resized, and stored in a local cache.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen

from PIL import Image, ImageOps

BASE = "https://helldivers.wiki.gg"
INDEXES = {
    "weapons": "Weapons", "stratagems": "Stratagems", "armor": "Armor",
    "boosters": "Boosters", "cosmetics": "Cosmetics",
}
MINIMUM_COUNTS = {"weapons": 90, "stratagems": 70, "armor": 80, "boosters": 15, "cosmetics": 250}
SOURCE = {
    "name": "Helldivers Wiki contributors", "url": BASE,
    "license": "CC BY-NC-SA 4.0",
    "license_url": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
    "license_scope": "English-source content",
    "licenses": [
        {"source_url": BASE, "license": "CC BY-NC-SA 4.0",
         "license_url": "https://creativecommons.org/licenses/by-nc-sa/4.0/"},
        {"source_url": BASE + "/zh/", "license": "CC BY-SA 4.0",
         "license_url": "https://creativecommons.org/licenses/by-sa/4.0/deed.zh-hans"},
    ],
}
USER_AGENT = "Helldivers2QQBot/0.2 (offline equipment catalog; polite cached requests)"
BUNDLED_CATALOG = Path(__file__).parent / "assets" / "wiki_catalog.json"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr"}
ARROWS = {"Up": "↑", "Down": "↓", "Left": "←", "Right": "→"}
CURRENCIES = {"Medals", "Requisition Slips", "Super Credits", "Samples"}


def now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Node | str] = field(default_factory=list)
    parent: Node | None = field(default=None, repr=False)

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def has(self, value: str) -> bool:
        return value in self.attrs.get("class", "").split()

    def text(self) -> str:
        if self.tag in {"script", "style", "sup"}:
            return ""
        if self.tag == "img":
            alt = self.attrs.get("alt", "")
            match = re.fullmatch(r"Stratagem Arrow (\w+)\.svg", alt)
            return ARROWS.get(match[1], "") if match else (alt if alt in CURRENCIES else "")
        # Leading zeroes are invisible table alignment, not part of an armor stat.
        if self.attrs.get("style", "").replace(" ", "").startswith("color:gray"):
            return ""
        value = " ".join(" ".join(
            child.text() if isinstance(child, Node) else child for child in self.children
        ).split())
        return re.sub(r"^(Medals|Requisition Slips|Super Credits|Samples) (.+) \1$", r"\2 \1", value)


class DOM(HTMLParser):
    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), parent=self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                self.stack = self.stack[:index]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def article(html: str) -> Node:
    for node in DOM(html).root.walk():
        if node.has("mw-parser-output"):
            return node
    raise ValueError("Missing MediaWiki article body (possible challenge or changed page layout)")


def revision(html: str) -> str:
    match = re.search(r'"wgRevisionId":(\d+)', html)
    if not match:
        raise ValueError("Article has no revision ID")
    return match[1]


def safe_url(href: str) -> str | None:
    if href.startswith(("/wiki/", "/zh/wiki/")):
        href = BASE + href
    parts = urlsplit(href)
    prefix = "/zh/wiki/" if parts.path.startswith("/zh/wiki/") else "/wiki/"
    title = unquote(parts.path.removeprefix(prefix))
    if (parts.scheme != "https" or parts.netloc != "helldivers.wiki.gg"
            or not parts.path.startswith(("/wiki/", "/zh/wiki/")) or parts.query
            or ":" in title):
        return None
    return href


def first_link(node: Node) -> tuple[str, str] | None:
    for child in node.walk():
        url = safe_url(child.attrs.get("href", "")) if child.tag == "a" else None
        if url and child.text():
            return child.text(), url
    return None


def panels(node: Node) -> list[str]:
    values = []
    while node.parent:
        if node.has("tabber__panel"):
            values.append(re.sub(r"-\d+$", "", node.attrs.get("id", "")).replace("_", " "))
        node = node.parent
    return list(reversed(values))


def make_entry(category, subcategory, name, url, fields, rev):
    slug = unquote(urlsplit(url).path.removeprefix("/wiki/"))
    fragment = unquote(urlsplit(url).fragment)
    # Name is included for directory-only entries sharing a section URL.
    identity = slug + ("#" + fragment + ":" + name if fragment else "")
    token = name.split()[0] if name.split() else ""
    code = token if (re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/-]*", token)
                     and (any(c.isdigit() for c in token) or "/" in token)) else ""
    aliases = [name]
    if code:
        aliases.extend([code, name[len(code):].strip()])
    return {
        "id": f"{category}:{subcategory}:{identity}", "category": category,
        "name": name, "english_name": name, "code": code,
        "aliases": list(dict.fromkeys(alias.strip() for alias in aliases if alias.strip())),
        "subcategory": subcategory,
        "summary": "", "fields": fields, "source_url": url, "revision": rev,
        "revision_source_url": BASE + "/wiki/" + INDEXES[category],
    }


def rows(table: Node) -> list[list[Node]]:
    result = []
    pending: dict[int, tuple[Node, int]] = {}
    for row in table.walk():
        if row.tag != "tr":
            continue
        cells = [n for n in row.children if isinstance(n, Node) and n.tag in {"td", "th"}]
        if not cells:
            continue
        expanded: list[Node] = []
        column = 0
        def fill_pending():
            nonlocal column
            while column in pending:
                cell, left = pending[column]
                expanded.append(cell)
                if left <= 1:
                    del pending[column]
                else:
                    pending[column] = (cell, left - 1)
                column += 1
        for cell in cells:
            fill_pending()
            span = table_span(cell.attrs.get("colspan", "1"), 20)
            for _ in range(span):
                expanded.append(cell)
                row_span = table_span(cell.attrs.get("rowspan", "1"), 100)
                if row_span > 1:
                    pending[column] = (cell, row_span - 1)
                column += 1
        fill_pending()
        result.append(expanded)
    return result


def table_span(value: str, maximum: int) -> int:
    # MediaWiki occasionally emits malformed quoted attributes (e.g. rowspan=2").
    # Browsers read the leading integer; preserve those displayed merged cells.
    match = re.match(r"\s*(\d+)", value)
    return min(maximum, max(1, int(match[1]))) if match else 1


def attach_index_image(entry: dict, nodes: list[Node]) -> None:
    """Take the item's own grid/row art; currency and status icons are never illustrations."""
    for container in nodes:
        for image in container.walk():
            if image.tag != "img" or image.attrs.get("alt", "") in CURRENCIES:
                continue
            source = image.attrs.get("src") or image.attrs.get("data-src", "")
            source = BASE + source if source.startswith("/") and not source.startswith("//") else source
            if not (safe_image_url(source) or safe_svg_url(source)):
                continue
            entry["image_url"] = source
            credit = image.parent
            while credit is not None and credit is not container:
                if credit.tag == "a" and credit.attrs.get("href", "").startswith("/wiki/File:"):
                    entry["image_credit"] = BASE + credit.attrs["href"]
                    break
                credit = credit.parent
            entry.setdefault("image_credit", entry["source_url"])
            return


def parse_index(category: str, html: str) -> list[dict]:
    body, rev = article(html), revision(html)
    entries = []
    heading = ""
    table_counts: dict[str, int] = {}
    grid_count = 0
    for node in body.walk():
        if node.tag in {"h2", "h3"}:
            heading = node.text()
        if category == "weapons" and node.has("gallerybox"):
            box = next((n for n in node.walk() if n.has("gallerytext")), None)
            item = first_link(box) if box else None
            if not item or heading in {"Change History", "References"}:
                continue
            name, url = item
            groups = panels(node)
            group = " / ".join(groups) if groups and groups[0] in {
                "Primary", "Secondary", "Throwable"} else "Support Weapons"
            fields = [["目录说明", box.text()]]
            entries.append(make_entry(category, group, name, url, fields, rev))
        if category == "cosmetics" and node.has("hd2-itemgrid"):
            group = ["Helmets", "Capes", "Player Cards"][grid_count]
            grid_count += 1
            for box in [n for n in node.walk() if n.has("hd2-itembox")]:
                title = next((n for n in box.walk() if n.has("hd2-title")), None)
                if title is None:
                    continue
                item = first_link(title)
                name, url = item or (title.text(), BASE + "/wiki/Cosmetics#" + group.replace(" ", "_"))
                metadata = box.text().removeprefix(name).strip()
                entry = make_entry(category, group, name, url, [["获取方式", metadata]], rev)
                attach_index_image(entry, [box])
                if group == "Helmets":
                    entry["summary"] = "头盔为外观装饰；Wiki 标明所有头盔属性相同，不提供额外加成。"
                entries.append(entry)
        if node.tag != "table" or not node.has("wikitable") or category == "weapons":
            continue
        matrix = rows(node)
        if len(matrix) < 2:
            continue
        headers = [n.text() for n in matrix[0]]
        name_index = next((i for i, h in enumerate(headers) if h in {"Name", "Booster", "Title"}), None)
        if name_index is None:
            continue
        count = table_counts.get(heading, 0)
        table_counts[heading] = count + 1
        if category == "armor":
            if "Armor" not in headers or "Passive" not in headers:
                continue
            group = panels(node)[-1]
        elif category == "stratagems":
            groups = {
                "Offensive Permit": ["Orbital Strikes", "Eagle Strikes"],
                "Supply Permit": ["Support Weapons", "Backpacks", "Vehicles"],
                "Defensive Permit": ["Sentries", "Emplacements"],
                "Other": ["General", "Mission", "Unavailable"],
            }
            group = heading + " / " + groups.get(heading, [heading] * 10)[count]
        elif category == "cosmetics":
            group = ("Vehicle Patterns" if count == 0 else "Weapon Patterns") if heading == "Patterns" else heading
        else:
            group = "Boosters"
        for cells in matrix[1:]:
            if len(cells) == len(headers) - 1 and headers[-1] == "Pattern Icon":
                cells = cells + [Node("td")]
            if len(cells) != len(headers) or cells[name_index].tag == "th":
                continue
            item = first_link(cells[name_index])
            name, url = item or (cells[name_index].text(), BASE + "/wiki/" + INDEXES[category] + "#" + quote(group.replace(" ", "_")))
            if not name:
                continue
            fields = [[label, cell.text()] for label, cell in zip(headers, cells, strict=True)
                      if label not in {"Icon", "Pattern", "Pattern Icon", "Name", "Booster", "Title"}
                      and cell.text()]
            entry = make_entry(category, group, name, url, fields, rev)
            if category == "cosmetics":
                art_cells = [cell for label, cell in zip(headers, cells, strict=True)
                             if label in {"Icon", "Pattern", "Pattern Icon"}]
                attach_index_image(entry, art_cells)
            entry["summary"] = next((value for label, value in fields if label == "Description"), "")
            entries.append(entry)
    unique = {}
    for entry in entries:
        unique[entry["id"]] = entry
    if len(unique) < MINIMUM_COUNTS[category]:
        raise ValueError(f"Incomplete {category} index: only {len(unique)} entries")
    return list(unique.values())


def parse_armor_cosmetics(html: str) -> list[dict]:
    """Armor's helmet/cape grids can include items not yet on the Cosmetics index."""
    grids = [node for node in article(html).walk() if node.has("hd2-itemgrid")]
    if len(grids) != 2:
        raise ValueError("Armor helmet/cape directory layout changed")
    entries = []
    for group, grid in zip(("Helmets", "Capes"), grids, strict=True):
        for box in (node for node in grid.walk() if node.has("hd2-itembox")):
            title = next((node for node in box.walk() if node.has("hd2-title")), None)
            if title is None:
                raise ValueError("Armor cosmetic item has no title")
            item = first_link(title)
            name, url = item or (title.text(), BASE + "/wiki/Armor#" + group)
            metadata = box.text().removeprefix(name).strip()
            entry = make_entry("cosmetics", group, name, url, [["获取方式", metadata]], revision(html))
            attach_index_image(entry, [box])
            entry["revision_source_url"] = BASE + "/wiki/Armor"
            entry["summary"] = "来源为盔甲目录中的外观列表；未列出的价格不作推断。"
            entries.append(entry)
    return entries


def enrich_weapon(entry: dict, html: str) -> None:
    from hd2bot.wiki_details import extract_weapon_attachments, extract_weapon_details

    body = article(html)
    fields = []
    for node in body.walk():
        if not node.has("druid-row"):
            continue
        label = next((n.text() for n in node.walk() if n.has("druid-label")), "")
        value = next((n.text() for n in node.walk() if n.has("druid-data")), "")
        if label and value:
            fields.append([label, value])
    if not fields:
        raise ValueError(f"Weapon details are missing or their layout changed: {entry['name']}")
    entry["fields"] = fields
    enrich_weapon_acquisition(entry, body)
    quote_node = next((n for n in body.walk() if n.tag == "blockquote"), None)
    if quote_node:
        summary = next((n.text() for n in quote_node.walk() if n.tag == "p"), "")
        entry["summary"] = summary[:1000]
    if not entry["summary"]:
        for child in body.children:
            if isinstance(child, Node) and child.tag == "p" and len(child.text()) > 25:
                entry["summary"] = child.text()[:1000]
                break
    entry["revision"] = revision(html)
    entry["revision_source_url"] = entry["source_url"]
    main_image = next((n for n in body.walk() if n.has("druid-main-image") or (
        n.has("druid-main-images-file") and n.attrs.get("data-druid-tab-key") == "Weapon")), None)
    if main_image:
        picture = next((n for n in main_image.walk() if n.tag == "img"), None)
        credit = next((n.attrs.get("href", "") for n in main_image.walk() if n.tag == "a"), "")
        if picture:
            image_url = picture.attrs.get("src", "")
            if image_url.startswith("/"):
                image_url = BASE + image_url
            if safe_image_url(image_url):
                entry["image_url"] = image_url
                entry["image_credit"] = BASE + credit if credit.startswith("/") else entry["source_url"]
    if "Potentially Outdated Pages" in html:
        entry["fields"].append(["Wiki 提醒", "原页面标记可能尚未跟进当前补丁；数值为该修订版本记录。"])
    entry["fields"].extend([label, value] for label, value in extract_weapon_details(html))
    entry["fields"].extend([label, value] for label, value in extract_weapon_attachments(html))


def section_nodes(body: Node, heading: str) -> list[Node]:
    """Read one displayed article section, excluding navigation and later sections."""
    active = False
    result = []
    for node in body.children:
        if not isinstance(node, Node):
            continue
        if node.tag == "h2":
            if active:
                break
            active = node.text() == heading
        elif active and node.tag in {"p", "ul", "ol"}:
            result.append(node)
    return result


def set_field(entry: dict, label: str, value: str, *, replace=False) -> None:
    for pair in entry["fields"]:
        if pair[0] == label:
            if replace:
                pair[1] = value
            return
    entry["fields"].append([label, value])


def enrich_weapon_acquisition(entry: dict, body: Node) -> None:
    """Fill absent acquisition facts from this weapon's procurement section.

    A page may discuss a different weapon's price (notably CQC-72 / CQC-73).
    Only the first procurement paragraph can supply a missing purchase price.
    """
    current = dict(entry["fields"])
    procurement = section_nodes(body, "Procurement") or section_nodes(body, "Acquisition")
    first = procurement[0].text() if procurement else ""
    source = current.get("Source", "")
    if source == "Starter Equipment":
        set_field(entry, "获取方式", "初始装备，完成训练后获得")
        set_field(entry, "Unlock Cost", "免费")
    elif source == "Liberty Day" and "all present and future Helldivers" in first:
        set_field(entry, "获取方式", "自由日纪念奖励，发放给所有现有及未来玩家")
        set_field(entry, "Unlock Cost", "免费发放")
    elif source == "Celestial Fence" and "participated in any stage" in first:
        set_field(entry, "获取方式", "参与“天体围栏”战役任一阶段的玩家获得")
        set_field(entry, "Unlock Cost", "战役奖励")
    elif source == "Super Citizen Edition" and "included in the Super Citizen Edition" in first:
        set_field(entry, "获取方式", "购买超级公民版或超级公民版升级包获得")
        set_field(entry, "Unlock Cost", "超级公民版付费内容")
    elif ("can be found" in first or "they can be found" in first.lower()) and (
        "points of interest" in first or "Minor Places of Interest" in first
    ):
        set_field(entry, "获取方式", "任务中的兴趣点拾取")
        set_field(entry, "Source", "任务中拾取")
        set_field(entry, "Unlock Cost", "无需购买")
    # CQC-73 currently has its 20-medal cost only in article prose. Avoid reading
    # attachments, historical prices, or the CQC-73 variant on the CQC-72 page.
    if not any(label in {"Cost", "Unlock Cost"} for label, _ in entry["fields"]):
        match = re.search(r"\bis unlocked\b.*?\bfor\s+([\d,]+)\s+(Medals|Super Credits|Requisition Slips)\b", first)
        if match:
            set_field(entry, "Unlock Cost", f"{match[1]} {match[2]}")
            links = [node for node in procurement[0].walk() if node.tag == "a"]
            warbond = next((node for node in links if "Warbond" in node.attrs.get("href", "")), None)
            if warbond:
                set_field(entry, "Source", warbond.text())


def warbond_urls(html: str) -> list[str]:
    """Use the catalog's observed cover links, avoiding alternate display aliases."""
    urls = []
    for box in article(html).walk():
        if not box.has("Roundededges"):
            continue
        for node in box.walk():
            if node.tag != "a" or not any(child.tag == "img" for child in node.walk()):
                continue
            url = safe_url(node.attrs.get("href", ""))
            if url and "Warbond" in url and url not in urls:
                urls.append(url)
    if not 20 <= len(urls) <= 60:
        raise ValueError(f"Warbond directory layout or coverage changed: {len(urls)} cover links")
    return urls


def currency_value(value: str) -> str:
    """Normalize displayed currency order, without converting or adding currencies."""
    value = re.sub(r"\bMedal\b", "Medals", value)
    match = re.fullmatch(r"(Medals|Super Credits|Requisition Slips)\s+([\d,]+)", value)
    return f"{match[2]} {match[1]}" if match else value


def parse_warbond(html: str, source_url: str) -> list[dict]:
    body = article(html)
    title = next((node.text() for node in body.walk() if node.has("druid-title")), "")
    cost = ""
    for node in body.walk():
        if node.has("druid-row"):
            label = next((child.text() for child in node.walk() if child.has("druid-label")), "")
            if label == "Cost":
                cost = next((child.text() for child in node.walk() if child.has("druid-data")), "")
    if not title or not cost:
        raise ValueError(f"Warbond price or title is missing: {source_url}")
    result = []
    page = 0
    threshold = ""
    for node in body.walk():
        if node.tag in {"h2", "h3"}:
            match = re.fullmatch(r"Page (\d+)", node.text())
            page = int(match[1]) if match else 0
            threshold = "首批开放" if page == 1 else ""
        if not page:
            continue
        if node.has("hd2-acq-stat-row"):
            match = re.fullmatch(r"Medals spent to unlock:\s*([\d,]+)(?: Medals)?", node.text())
            if match:
                threshold = f"{match[1]} Medals"
        if node.tag != "table" or not node.has("wikitable"):
            continue
        matrix = rows(node)
        if not matrix or [cell.text() for cell in matrix[0]] != ["Icon", "Item", "Type", "Cost"]:
            continue
        for cells in matrix[1:]:
            if len(cells) != 4:
                raise ValueError(f"Warbond item table changed: {source_url}")
            item = first_link(cells[1])
            if not item:
                continue
            item_cost = currency_value(cells[3].text())
            # Some upstream rows omit the icon; all rewards in these tables are
            # purchased with medals, as stated in the Warbonds overview.
            if re.fullmatch(r"[\d,]+", item_cost):
                item_cost += " Medals"
            result.append({"name": item[0], "url": item[1], "type": cells[2].text(),
                           "cost": item_cost, "warbond": title, "warbond_cost": currency_value(cost),
                           "page": page, "threshold": threshold,
                           "source_url": source_url, "revision": revision(html)})
    if not result:
        raise ValueError(f"Warbond reward tables are missing: {source_url}")
    return result


def warbond_item_matches(entry: dict, item: dict) -> bool:
    kind = item["type"]
    category, group = entry["category"], entry["subcategory"]
    if category == "cosmetics":
        allowed = {"Helmets": {"Helmet"}, "Capes": {"Cape"}, "Player Cards": {"Player Card"},
                   "Emotes": {"Emote"}, "Victory Poses": {"Victory Pose"}, "Titles": {"Title"},
                   "Vehicle Patterns": {"Pattern"}, "Weapon Patterns": {"Pattern"}}
        if kind not in allowed.get(group, set()):
            return False
    elif category == "armor":
        if kind not in {"Light Armor", "Medium Armor", "Heavy Armor"}:
            return False
    elif category == "boosters":
        if kind != "Booster":
            return False
    elif category == "stratagems":
        if kind not in {"Support Weapon", "Backpack Weapon", "Backpack", "Vehicle", "Sentry",
                        "Stratagem", "Stratagems", "Mortar", "Emplacement"}:
            return False
    elif category == "weapons" and kind in {
        "Helmet", "Cape", "Player Card", "Pattern", "Emote", "Victory Pose", "Title", "Booster",
        "Light Armor", "Medium Armor", "Heavy Armor", "Currency", "Backpack", "Vehicle", "Sentry",
        "Stratagem", "Stratagems", "Mortar", "Emplacement",
    }:
        return False
    same_name = entry["english_name"].casefold() == item["name"].casefold()
    same_url = entry["source_url"].split("#", 1)[0] == item["url"].split("#", 1)[0]
    # Directory-only items sharing a section URL must match their names, too.
    return same_name or (same_url and "#" not in entry["source_url"])


def enrich_warbond_acquisitions(entries: list[dict], fetcher: Fetcher) -> dict:
    """Add independently sourced purchase/page facts to every matching category."""
    index_url = BASE + "/wiki/Warbonds"
    index_html, _ = fetcher.fetch(index_url)
    urls = warbond_urls(index_html)
    rewards = []
    for url in urls:
        page_html, _ = fetcher.fetch(url)
        rewards.extend(parse_warbond(page_html, url))
    enriched = 0
    conflicts = []
    for entry in entries:
        matches = [item for item in rewards if warbond_item_matches(entry, item)]
        # Ambiguous shared pattern URLs do not establish which item was bought.
        if len(matches) != 1:
            continue
        item = matches[0]
        current = dict(entry["fields"])
        label = "Unlock Cost" if entry["category"] == "weapons" and "Cost" not in current else "Cost"
        prior = current.get(label, current.get("Cost", ""))
        usable_cost = bool(re.fullmatch(r"[\d,]+ Medals", item["cost"]))
        if prior and usable_cost and currency_value(prior) != item["cost"]:
            conflicts.append({"entry_id": entry["id"], "detail_cost": prior,
                              "warbond_cost": item["cost"], "warbond_source": item["source_url"]})
        elif usable_cost:
            set_field(entry, label, item["cost"])
        set_field(entry, "Source", item["warbond"] + f" P{item['page']}")
        set_field(entry, "战争债券费用", "免费" if item["warbond_cost"] == "Free" else item["warbond_cost"])
        set_field(entry, "战争债券页码", f"第 {item['page']} 页")
        if item["threshold"]:
            set_field(entry, "解锁页累计花费", item["threshold"])
        entry["source_urls"] = list(dict.fromkeys(
            entry.get("source_urls", [entry["source_url"]]) + [item["source_url"]]))
        entry["acquisition_sources"] = [{"url": item["source_url"], "revision": item["revision"]}]
        enriched += 1
    return {"source_url": index_url, "revision": revision(index_html), "warbond_pages": len(urls),
            "enriched_entries": enriched, "cost_conflicts": conflicts}


def safe_image_url(url: str) -> bool:
    parts = urlsplit(url)
    return (parts.scheme == "https" and parts.netloc == "helldivers.wiki.gg"
            and parts.path.startswith("/images/")
            and parts.path.lower().endswith((".png", ".webp", ".jpg", ".jpeg")))


def safe_svg_url(url: str) -> bool:
    parts = urlsplit(url)
    return (parts.scheme == "https" and parts.netloc == "helldivers.wiki.gg"
            and parts.path.startswith("/images/") and parts.path.lower().endswith(".svg"))


class Fetcher:
    def __init__(self, cache_dir: Path, *, refresh=False, delay=1.0, offline=False):
        self.cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.refresh = refresh
        self.delay = max(0.25, delay)
        self.offline = offline
        self.last_request = 0.0
        self.network_requests = 0
        self.checked_at: list[float] = []

    def fetch(self, url: str) -> tuple[str, str]:
        if not safe_url(url):
            raise ValueError(f"Refusing non-article URL: {url}")
        key = hashlib.sha256(url.encode()).hexdigest()
        body_path, meta_path = self.cache_dir / (key + ".html"), self.cache_dir / (key + ".json")
        meta = json.loads(meta_path.read_text("utf-8")) if meta_path.exists() else {}
        if body_path.exists() and (self.offline or (not self.refresh and time.time() - meta.get("checked_at", 0) < 86400)):
            html = body_path.read_text("utf-8")
            self.checked_at.append(meta.get("checked_at", body_path.stat().st_mtime))
            return html, hashlib.sha256(html.encode()).hexdigest()
        if self.offline:
            raise ValueError(f"No cached article: {url}")
        headers = {"User-Agent": USER_AGENT, "Accept": "text/html"}
        if body_path.exists() and meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if body_path.exists() and meta.get("last_modified"):
            headers["If-Modified-Since"] = meta["last_modified"]
        for attempt in range(3):
            time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            self.network_requests += 1
            try:
                with urlopen(Request(url, headers=headers), timeout=35) as response:
                    if not safe_url(response.url):
                        raise ValueError("Unexpected redirect outside an allowed wiki article")
                    raw = response.read(8_000_001)
                    if len(raw) > 8_000_000:
                        raise ValueError("Article exceeds the 8 MB limit")
                    html = raw.decode("utf-8")
                    revision(html)
                    article(html)
                    meta = {"url": url, "etag": response.headers.get("ETag"),
                            "last_modified": response.headers.get("Last-Modified"),
                            "checked_at": time.time(), "fetched_at": now()}
                atomic_text(body_path, html)
                atomic_json(meta_path, meta)
                self.checked_at.append(meta["checked_at"])
                return html, hashlib.sha256(html.encode()).hexdigest()
            except HTTPError as exc:
                if exc.code == 304 and body_path.exists():
                    meta["checked_at"] = time.time()
                    atomic_json(meta_path, meta)
                    html = body_path.read_text("utf-8")
                    self.checked_at.append(meta["checked_at"])
                    return html, hashlib.sha256(html.encode()).hexdigest()
                if exc.code not in {429, 502, 503, 504} or attempt == 2:
                    raise
                retry = exc.headers.get("Retry-After", "")
                time.sleep(min(30, max(1, int(retry) if retry.isdecimal() else 2 ** (attempt + 1))))
        raise RuntimeError("Unreachable retry state")

    def optional_article(self, url: str) -> str | None:
        """A known 404 on the unfinished Chinese wiki is data, not a broken sync."""
        key = hashlib.sha256(url.encode()).hexdigest()
        missing_path = self.cache_dir / (key + ".missing.json")
        if missing_path.exists():
            missing = json.loads(missing_path.read_text("utf-8"))
            if self.offline or (not self.refresh and time.time() - missing.get("checked_at", 0) < 86400):
                return None
        try:
            result, _ = self.fetch(url)
        except HTTPError as exc:
            if exc.code != 404:
                raise
            atomic_json(missing_path, {"url": url, "status": 404, "checked_at": time.time()})
            return None
        if missing_path.exists():
            missing_path.unlink()
        return result

    def image(self, url: str, image_dir: Path) -> str:
        """Validate and locally resize raster art; return its content-addressed filename."""
        if not (safe_image_url(url) or safe_svg_url(url)):
            raise ValueError("Refusing image URL outside the observed wiki image host/path")
        key = hashlib.sha256(url.encode()).hexdigest()
        meta_path = self.cache_dir / ("image-" + key + ".json")
        meta = json.loads(meta_path.read_text("utf-8")) if meta_path.exists() else {}
        previous = image_dir / meta.get("filename", "absent")
        if previous.is_file() and (self.offline or (not self.refresh and time.time() - meta.get("checked_at", 0) < 86400)):
            return previous.name
        if self.offline:
            raise ValueError(f"Missing cached image for {url}")
        headers = {"User-Agent": USER_AGENT, "Accept": "image/png,image/webp,image/jpeg"}
        if previous.is_file() and meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if previous.is_file() and meta.get("last_modified"):
            headers["If-Modified-Since"] = meta["last_modified"]
        for attempt in range(3):
            time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            self.network_requests += 1
            try:
                with urlopen(Request(url, headers=headers), timeout=35) as response:
                    if not (safe_image_url(response.url) or safe_svg_url(response.url)):
                        raise ValueError("Unexpected image redirect")
                    raw = response.read(5_000_001)
                    if len(raw) > 5_000_000:
                        raise ValueError("Image exceeds the 5 MB limit")
                    if safe_svg_url(response.url):
                        from hd2bot.wiki.images import validated_svg
                        data = validated_svg(raw)
                        filename = hashlib.sha256(data).hexdigest() + ".svg"
                    else:
                        with Image.open(io.BytesIO(raw)) as original:
                            if original.format not in {"PNG", "JPEG", "WEBP"}:
                                raise ValueError("Unsupported image format")
                            if original.width * original.height > 25_000_000:
                                raise ValueError("Image dimensions exceed limit")
                            original.verify()
                        with Image.open(io.BytesIO(raw)) as original:
                            rendered = ImageOps.exif_transpose(original).convert("RGBA")
                            rendered.thumbnail((800, 800))
                            buffer = io.BytesIO()
                            rendered.save(buffer, format="WEBP", quality=90, method=6)
                            data = buffer.getvalue()
                        filename = hashlib.sha256(data).hexdigest() + ".webp"
                    image_dir.mkdir(parents=True, exist_ok=True)
                    destination = image_dir / filename
                    if not destination.exists():
                        temporary = image_dir / (filename + ".tmp")
                        temporary.write_bytes(data)
                        os.replace(temporary, destination)
                    atomic_json(meta_path, {"url": url, "filename": filename,
                                "etag": response.headers.get("ETag"),
                                "last_modified": response.headers.get("Last-Modified"),
                                "checked_at": time.time()})
                    return filename
            except HTTPError as exc:
                if exc.code == 304 and previous.is_file():
                    meta["checked_at"] = time.time()
                    atomic_json(meta_path, meta)
                    return previous.name
                if exc.code not in {429, 502, 503, 504} or attempt == 2:
                    raise
                retry = exc.headers.get("Retry-After", "")
                time.sleep(min(30, max(1, int(retry) if retry.isdecimal() else 2 ** (attempt + 1))))
        raise RuntimeError("Unreachable image retry state")


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def atomic_json(path: Path, value) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def publish_catalog(path: Path, value: dict) -> None:
    """Use the bot's exact schema validation before replacing its last good snapshot."""
    from hd2bot.wiki.service import _read_snapshot

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        _read_snapshot(temporary)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


ZH_INDEXES = {"weapons": "武器", "stratagems": "战略配备", "armor": "盔甲",
              "boosters": "强化资源", "cosmetics": "装饰"}
ZH_MINIMUM_WEAPON_NAMES = 90
ZH_FIELD_KEYS = {
    "许可类型": "Permit Type", "特性": "Traits", "战略配备代码": "Stratagem Code",
    "基础冷却": "Base Cooldown", "武器类别": "Weapon Category", "武器类型": "Weapon Type",
    "开火模式": "Firing Modes", "基础伤害": "Standard Damage", "穿甲值": "Armor Penetration",
    "射速": "Fire Rate", "容量": "Capacity", "补给箱补充": "Mags from Supply",
    "弹药箱补充": "Mags from Ammo Box", "装填时间": "Reload Time",
    "战术装填时间": "Tactical Reload Time", "解锁等级": "Unlock Level",
    "解锁费用": "Unlock Cost", "来源": "Source", "盔甲": "Armor", "速度": "Speed",
    "耐力恢复": "Stamina", "盔甲被动": "Passive",
}


def has_chinese(value: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", value))


def is_chinese_prose(value: str) -> bool:
    chinese_count = len(re.findall(r"[\u3400-\u9fff]", value))
    latin_count = len(re.findall(r"[A-Za-z]", value))
    return chinese_count >= 4 and chinese_count >= latin_count * .35


def known_code(name: str, codes: set[str]) -> str:
    if name.startswith("M6C/特种作战") and "M6C/SOCOM" in codes:
        return "M6C/SOCOM"
    upper = name.upper()
    for code in sorted(codes, key=len, reverse=True):
        if not upper.startswith(code.upper()):
            continue
        remainder = name[len(code):]
        if not remainder or not re.match(r"[A-Za-z0-9/-]", remainder):
            return code
    return ""


def chinese_records(category: str, html: str, source_url: str) -> tuple[list[dict], int]:
    """Read displayed names even when a not-yet-created detail page is a red link."""
    body = article(html)
    records = []
    seen = set()
    rows_seen = 0
    groups = {id(grid): group for grid, group in zip(
        (node for node in body.walk() if node.has("hd2-itemgrid")),
        ("Helmets", "Capes", "Player Cards"), strict=False)}
    for node in body.walk():
        title = None
        group = ""
        if node.has("gallerytext") and category == "weapons":
            title = next((n for n in node.walk() if n.tag == "a" and n.text()), None)
        elif node.has("hd2-itembox") and category == "cosmetics":
            title = next((n for n in node.walk() if n.has("hd2-title")), None)
            ancestor = node.parent
            while ancestor:
                if id(ancestor) in groups:
                    group = groups[id(ancestor)]
                    break
                ancestor = ancestor.parent
        elif node.tag == "table" and node.has("wikitable") and category != "weapons":
            matrix = rows(node)
            if not matrix:
                continue
            headers = [cell.text() for cell in matrix[0]]
            column = next((i for i, text in enumerate(headers) if text in {"Name", "名称", "Title", "Booster"}), None)
            if column is None:
                continue
            for cells in matrix[1:]:
                if column >= len(cells) or cells[column].tag == "th":
                    continue
                rows_seen += 1
                name = cells[column].text()
                if not has_chinese(name):
                    continue
                key = (name, group)
                if key not in seen:
                    records.append({"name": name, "subcategory": group,
                                    "source_url": source_url, "revision": revision(html)})
                    seen.add(key)
        if title is not None:
            rows_seen += 1
            name = title.text()
            key = (name, group)
            if has_chinese(name) and key not in seen:
                records.append({"name": name, "subcategory": group,
                                "source_url": source_url, "revision": revision(html)})
                seen.add(key)
    return records, rows_seen


def merge_chinese_catalog(entries: list[dict], fetcher: Fetcher) -> dict:
    """Prefer observed Chinese wiki content without discarding fuller English coverage."""
    codes = {entry["code"] for entry in entries if entry["code"]}
    documents = {}
    indexes = {}
    name_count = 0
    details = {}
    for category, title in ZH_INDEXES.items():
        url = BASE + "/zh/wiki/" + quote(title)
        html = fetcher.optional_article(url)
        if html is None:
            indexes[category] = {"source_url": url, "status": 404, "listed_count": 0,
                                 "chinese_names": 0}
            continue
        records, count = chinese_records(category, html, url)
        if category == "weapons" and len(records) < ZH_MINIMUM_WEAPON_NAMES:
            raise ValueError("Chinese weapon directory unexpectedly lost its translated names")
        documents[url] = html
        indexes[category] = {"source_url": url, "status": 200, "revision": revision(html),
                             "listed_count": count, "chinese_names": len(records)}
        for record in records:
            code = known_code(record["name"], codes)
            if not code:
                continue
            eligible = {category}
            if category == "weapons":
                eligible.add("stratagems")
            for entry in entries:
                if entry["category"] not in eligible or entry["code"] != code:
                    continue
                if record["subcategory"] and entry["subcategory"] != record["subcategory"]:
                    continue
                name_count += not has_chinese(entry["name"])
                entry["name"] = record["name"]
                entry["name_source_url"] = url
                entry["name_revision"] = record["revision"]
                entry["name_license"] = "CC BY-SA 4.0"
                entry["aliases"] = list(dict.fromkeys(entry["aliases"] + [record["name"]]))
                entry["source_urls"] = list(dict.fromkeys(
                    entry.get("source_urls", [entry["source_url"]]) + [url]))
        # A directory may contain a red link while its navigation links the correctly
        # spaced translated title. Follow only observed, existing Chinese equipment links.
        for anchor in article(html).walk():
            if anchor.tag != "a" or not has_chinese(anchor.text()):
                continue
            href = safe_url(anchor.attrs.get("href", ""))
            code = known_code(anchor.text(), codes)
            if href and "/zh/wiki/" in href and code:
                details[code] = href
    localized_details = 0
    # A small bounded second pass picks up proper Chinese links in the two live weapon
    # pages, such as AC-8 机炮, without crawling English copies or unrelated articles.
    processed = set()
    for _ in range(2):
        for code, url in list(details.items()):
            if url in processed:
                continue
            if len(processed) >= 100:
                raise ValueError("Unexpectedly large Chinese equipment detail traversal")
            processed.add(url)
            html = fetcher.optional_article(url)
            if html is None:
                continue
            body = article(html)
            pairs = []
            for node in body.walk():
                if not node.has("druid-row"):
                    continue
                label = next((n.text() for n in node.walk() if n.has("druid-label")), "")
                value = next((n.text() for n in node.walk() if n.has("druid-data")), "")
                if label and value and has_chinese(value) and "Error:" not in value:
                    pairs.append([ZH_FIELD_KEYS.get(label, label), value])
            summary = ""
            quote_node = next((node for node in body.walk() if node.tag == "blockquote"), None)
            if quote_node:
                summary = next((node.text() for node in quote_node.walk()
                                if node.tag == "p" and is_chinese_prose(node.text())), "")
            paragraphs = [node.text() for node in body.children
                          if isinstance(node, Node) and node.tag == "p" and is_chinese_prose(node.text())]
            if not summary and paragraphs:
                summary = paragraphs[0]
            for paragraph in paragraphs:
                if "解锁" in paragraph and ("奖章" in paragraph or "申购" in paragraph):
                    pairs.append(["获取方式", paragraph])
                    break
            for entry in entries:
                if entry["code"] != code:
                    continue
                if entry["category"] not in {"weapons", "stratagems", "armor"}:
                    continue
                # Armor descriptions must not be assigned to the matching helmet.
                replacement = dict(pairs)
                entry["fields"] = [[label, replacement.pop(label, value)] for label, value in entry["fields"]]
                entry["fields"].extend([label, value] for label, value in replacement.items())
                if summary:
                    entry["summary"] = summary[:1000]
                if pairs or summary:
                    localized_details += 1
                    entry["chinese_detail_source"] = {"url": url, "revision": revision(html),
                                                       "license": "CC BY-SA 4.0",
                                                       "fields": [label for label, _ in pairs],
                                                       "summary": bool(summary)}
                    entry["source_urls"] = list(dict.fromkeys(
                        entry.get("source_urls", [entry["source_url"]]) + [url]))
            for anchor in body.walk():
                href = safe_url(anchor.attrs.get("href", "")) if anchor.tag == "a" else None
                linked_code = known_code(anchor.text(), codes) if href and has_chinese(anchor.text()) else ""
                if href and "/zh/wiki/" in href and linked_code and href not in processed:
                    details.setdefault(linked_code, href)
    return {"preferred_language": "zh", "source_url": BASE + "/zh/", "indexes": indexes,
            "license": "CC BY-SA 4.0",
            "license_url": "https://creativecommons.org/licenses/by-sa/4.0/deed.zh-hans",
            "chinese_name_entries": name_count, "chinese_detail_entries": localized_details,
            "detail_pages_checked": len(processed),
            "notes": ["中文名称优先来自真实中文Wiki目录；英文编号及名称保留用于检索。",
                      "中文站仍在建设；缺失、仍为英文或模板报错的内容由英文站完整快照补充。",
                      "每条中文名称与已翻译详情单独记录来源及修订号；英文属性出处继续保留。"]}


def sync_catalog(output_path: Path, cache_dir: Path, *, refresh=False, offline=False,
                 delay=1.0, aliases_path: Path | None = None,
                 image_dir: Path | None = None, include_chinese: bool = True) -> dict:
    """Generate a complete replacement; any fetch/parse failure leaves output untouched."""
    fetcher = Fetcher(cache_dir, refresh=refresh, delay=delay, offline=offline)
    entries, coverage = [], {}
    cosmetic_supplements = []
    parsed_dir = cache_dir / "parsed"
    parsed_dir.mkdir(exist_ok=True)
    # Queries already fall back when a local snapshot is damaged. The updater must
    # also be able to replace it, instead of failing on the same file every day.
    from hd2bot.wiki.service import _read_snapshot
    try:
        _read_snapshot(output_path)
        old = json.loads(output_path.read_text("utf-8-sig"))
    except (OSError, ValueError, TypeError, OverflowError):
        old = {}
    comparison = old
    if not old and output_path.resolve() != BUNDLED_CATALOG.resolve() and BUNDLED_CATALOG.exists():
        try:
            bundled = json.loads(BUNDLED_CATALOG.read_text("utf-8"))
            if (bundled.get("schema_version") == 1 and isinstance(bundled.get("entries"), list)
                    and all(isinstance(bundled.get("coverage", {}).get(key, {}).get("count"), int)
                            for key in ("weapons", "stratagems", "armor", "boosters", "cosmetics"))):
                comparison = bundled
        except (OSError, ValueError, TypeError):
            pass
    old_entries = {entry["id"]: entry for entry in old.get("entries", [])}
    image_dir = image_dir or output_path.parent / "wiki_images"
    image_errors = []
    for category, title in INDEXES.items():
        url = BASE + "/wiki/" + title
        html, digest = fetcher.fetch(url)
        parsed_path = parsed_dir / (category + "-v5-art-" + digest + ".json")
        if parsed_path.exists():
            imported = json.loads(parsed_path.read_text("utf-8"))
        else:
            imported = parse_index(category, html)
            atomic_json(parsed_path, imported)
        if category == "armor":
            cosmetic_supplements = parse_armor_cosmetics(html)
        if category == "cosmetics":
            known = {entry["id"] for entry in imported}
            imported.extend(entry for entry in cosmetic_supplements if entry["id"] not in known)
        if category == "weapons":
            for entry in imported:
                html_detail, detail_digest = fetcher.fetch(entry["source_url"])
                detail_cache = parsed_dir / ("detail-v7-" + detail_digest + ".json")
                if detail_cache.exists():
                    details = json.loads(detail_cache.read_text("utf-8"))
                    entry.update(details)
                else:
                    enrich_weapon(entry, html_detail)
                    atomic_json(detail_cache, {k: entry[k] for k in (
                        "summary", "fields", "revision", "revision_source_url",
                        "image_url", "image_credit") if k in entry})
                if entry.get("image_url"):
                    try:
                        entry["image_path"] = fetcher.image(entry["image_url"], image_dir)
                    except (OSError, ValueError, Image.DecompressionBombError) as exc:
                        image_errors.append({"name": entry["name"], "error": str(exc)})
                        previous_image = old_entries.get(entry["id"], {}).get("image_path", "")
                        if previous_image and (image_dir / previous_image).is_file():
                            entry["image_path"] = previous_image
                if len([e for e in imported if e.get("image_path")]) % 20 == 0:
                    print(f"weapon detail: {entry['name']}", flush=True)
        if category == "cosmetics":
            for entry in imported:
                if not entry.get("image_url"):
                    continue
                try:
                    entry["image_path"] = fetcher.image(entry["image_url"], image_dir)
                except (OSError, ValueError, Image.DecompressionBombError) as exc:
                    image_errors.append({"name": entry["name"], "error": str(exc)})
                    previous_image = old_entries.get(entry["id"], {}).get("image_path", "")
                    if previous_image and (image_dir / previous_image).is_file():
                        entry["image_path"] = previous_image
        previous = comparison.get("coverage", {}).get(category, {}).get("count", 0)
        if len(imported) < max(MINIMUM_COUNTS[category], previous * .8):
            raise ValueError(f"{category} count unexpectedly decreased: {previous} -> {len(imported)}")
        coverage[category] = {"count": len(imported), "complete": True,
                              "scope": f"All equipment listed on /wiki/{title}",
                              "source_url": url, "revision": revision(html)}
        if category == "cosmetics" and cosmetic_supplements:
            coverage[category]["scope"] = "All cosmetics listed on /wiki/Cosmetics and /wiki/Armor"
            coverage[category]["source_urls"] = [url, BASE + "/wiki/Armor"]
        entries.extend(imported)
        print(f"{category}: {len(imported)}", flush=True)
    if aliases_path is None:
        aliases_path = Path(__file__).parent / "assets" / "wiki_aliases.json"
    aliases = json.loads(aliases_path.read_text("utf-8")) if aliases_path.exists() else {}
    alias_map = aliases.get("aliases", {})
    for entry in entries:
        additions = alias_map.get(entry["english_name"], []) + alias_map.get(entry["code"], [])
        entry["aliases"] = list(dict.fromkeys(
            alias.strip() for alias in entry["aliases"] + additions if alias.strip()))
    acquisition = enrich_warbond_acquisitions(entries, fetcher)
    previous_acquisition = comparison.get("acquisition", {})
    if acquisition.get("enriched_entries", 0) < previous_acquisition.get("enriched_entries", 0) * .9:
        raise ValueError("Warbond acquisition coverage unexpectedly decreased; keeping last good snapshot")
    localization = merge_chinese_catalog(entries, fetcher) if include_chinese else {}
    if include_chinese:
        previous_localization = comparison.get("localization", {})
        previous_names = previous_localization.get("chinese_name_entries", 0)
        if localization["chinese_name_entries"] < previous_names * .9:
            raise ValueError("Chinese name coverage unexpectedly decreased; keeping last good snapshot")
        for category, previous_index in previous_localization.get("indexes", {}).items():
            if (previous_index.get("status") == 200
                    and localization["indexes"].get(category, {}).get("status") != 200):
                raise ValueError(f"Previously available Chinese directory disappeared: {category}")
        new_by_id = {entry["id"]: entry for entry in entries}
        for previous_entry in comparison.get("entries", []):
            current = new_by_id.get(previous_entry["id"], {})
            prior_detail = previous_entry.get("chinese_detail_source", {})
            has_prior_translation = prior_detail.get("fields") or (
                prior_detail.get("summary") and is_chinese_prose(previous_entry.get("summary", "")))
            if has_prior_translation and not current.get("chinese_detail_source"):
                raise ValueError(f"Previously translated details disappeared: {previous_entry['name']}")
    checked_at = datetime.fromtimestamp(min(fetcher.checked_at), UTC).isoformat() if fetcher.checked_at else now()
    if offline and old.get("synced_at"):
        checked_at = old["synced_at"]
    result = {"schema_version": 1, "synced_at": checked_at, "built_at": now(), "source": SOURCE,
              "coverage": coverage, "entries": entries,
              "localization": localization,
              "acquisition": acquisition,
              "images": {"count": sum(bool(e.get("image_path")) for e in entries),
                         "scope": "Weapon infobox illustrations; locally resized WebP",
                         "copyright": "Game artwork remains the property of its respective rights holders; the wiki text license does not grant ownership of game art.",
                         "failures": image_errors},
              "notices": ["仅包含 HELLDIVERS 2 五类装备目录；不是整个 Wiki 的镜像。",
                          "中文Wiki已有名称/说明优先采用；不足部分来自英文站，另附人工社区检索别名。",
                          "快照数值可能落后于游戏补丁；来源与原页面修订号保留在条目中。",
                          "英文Wiki内容遵循CC BY-NC-SA 4.0；中文Wiki内容遵循CC BY-SA 4.0；各部分保留各自来源许可。"]}
    publish_catalog(output_path, result)
    return {"count": len(entries), "coverage": coverage, "network_requests": fetcher.network_requests,
            "output": str(output_path), "synced_at": result["synced_at"]}
