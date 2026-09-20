"""A persistent, chat-paced adaptation of the ship's Stratagem Hero arcade."""

from __future__ import annotations

import json
import math
import random
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from hd2bot.storage.database import canonical_json

_DIRECTIONS = {
    "↑": "↑⬆⇧⇑⇡⤊↟⇈⮝🡅🡑🡙🡡🔼🔺△▲∧⏫👆☝^wW上",
    "↓": "↓⬇⇩⇓⇣⤋↡⇊⮟🡇🡓🡛🡣🔽🔻▽▼∨⏬👇vVsS下",
    "←": "←⬅⇦⇐⇠↞⇇⮜🡄🡐🡘🡠◀◁◄⏴⏪👈<aA左",
    "→": "→➡⇨⇒⇢↠⇉⮞🡆🡒🡚🡢▶▷►⏵⏩👉>dD右",
}
_CHAR_MAP = {char: direction for direction, chars in _DIRECTIONS.items() for char in chars}
_TOKENS = {
    "up": "↑", "down": "↓", "left": "←", "right": "→",
    "向上": "↑", "向下": "↓", "向左": "←", "向右": "→",
    "上箭头": "↑", "下箭头": "↓", "左箭头": "←", "右箭头": "→",
    "[上]": "↑", "[下]": "↓", "[左]": "←", "[右]": "→",
    "[上箭头]": "↑", "[下箭头]": "↓", "[左箭头]": "←", "[右箭头]": "→",
    "->": "→", "=>": "→", "<-": "←", "<=": "←",
}
_TOKEN_PATTERN = re.compile("|".join(re.escape(key) for key in sorted(_TOKENS, key=len, reverse=True)))
_SEPARATORS = frozenset(" \t\r\n,;、|")
_VARIATIONS = frozenset("\ufe0e\ufe0f")
_SKIN_TONES = frozenset("🏻🏼🏽🏾🏿")
_CJK = re.compile(r"[\u3400-\u9fff]")
_ASSETS = Path(__file__).resolve().parents[1] / "assets"


class ArrowInputError(ValueError):
    """The entire answer must be recognizable; invalid symbols are never removed."""


def normalize_arrows(text: str) -> str:
    """Parse Unicode/emoji arrows, fullwidth ASCII, WASD and Chinese directions.

    Variation selectors are accepted only immediately after a direction glyph.
    Diagonal/double-headed arrows are deliberately ambiguous and are rejected.
    """
    if not isinstance(text, str) or len(text) > 512:
        raise ArrowInputError("方向输入过长；每条最多 32 个方向。")
    # NFKC would rotate these vertical presentation forms into left/right brackets.
    text = unicodedata.normalize("NFKC", text.translate(str.maketrans("︿﹀", "↑↓"))).strip().casefold()
    result: list[str] = []
    offset = 0
    can_variation = False
    can_skin = False
    while offset < len(text):
        char = text[offset]
        if char in _SEPARATORS:
            can_variation = False
            can_skin = False
            offset += 1
            continue
        if char in _SKIN_TONES:
            if not can_skin:
                raise ArrowInputError("肤色修饰符必须紧跟指向手势。")
            can_skin = False
            offset += 1
            continue
        if char in _VARIATIONS:
            if not can_variation:
                raise ArrowInputError("表情修饰符必须紧跟方向箭头。")
            can_variation = False
            can_skin = False
            offset += 1
            continue
        match = _TOKEN_PATTERN.match(text, offset)
        if match:
            result.append(_TOKENS[match.group()])
            offset = match.end()
            can_variation = False
            can_skin = False
        elif char in _CHAR_MAP:
            result.append(_CHAR_MAP[char])
            offset += 1
            can_variation = char not in "wasd^v<>上下左右"
            can_skin = char in "👆👇👈👉☝"
        else:
            raise ArrowInputError(f"无法识别第 {offset + 1} 个字符 {char!r}；请只发送上下左右方向。")
        if len(result) > 32:
            raise ArrowInputError("方向输入过长；每条最多 32 个方向。")
    if not result:
        raise ArrowInputError("没有识别到方向。")
    return "".join(result)


def is_arrow_input(text: str) -> bool:
    """Whether this is a complete arrow-only answer (DSS remains a bot command)."""
    if not isinstance(text, str) or text.strip().casefold() == "dss":
        return False
    try:
        normalize_arrows(text)
    except ArrowInputError:
        return False
    return True


def _looks_like_answer(text: str) -> bool:
    if is_arrow_input(text):
        return True
    clean = unicodedata.normalize("NFKC", text).strip().casefold()
    if not clean:
        return False
    # A visibly arrow-led malformed answer gets useful feedback. Ordinary prose,
    # commands and words beginning in WASD are left to the normal command router.
    return (clean[0] in _CHAR_MAP and clean[0] not in "wasdv上下左右"
            or clean.startswith(("向上", "向下", "向左", "向右")))


@dataclass(frozen=True)
class Stratagem:
    id: str
    name: str
    arrows: str
    source_url: str = ""


def load_stratagems(path: Path | None = None) -> tuple[Stratagem, ...]:
    """Read real codes and Chinese names from the already attributed wiki catalogue.

    Unavailable/mission-only calls and untranslated entries are not arcade questions.
    No direction sequence is fabricated as a fallback.
    """
    payload = json.loads((path or _ASSETS / "wiki_catalog.json").read_text(encoding="utf-8"))
    choices: dict[str, Stratagem] = {}
    for entry in payload.get("entries", []):
        if entry.get("category") != "stratagems":
            continue
        if any(word in entry.get("subcategory", "") for word in ("Unavailable", "Mission")):
            continue
        name = next((value for value in [entry.get("name", ""), *entry.get("aliases", [])]
                     if isinstance(value, str) and _CJK.search(value)), "")
        if not name:
            continue
        code = next((value for label, value in entry.get("fields", [])
                     if label in ("Stratagem Code", "战备代码", "战备指令")), "")
        try:
            arrows = normalize_arrows(code)
        except ArrowInputError:
            continue
        if not 3 <= len(arrows) <= 12:
            continue
        identity = str(entry["id"])
        choices[identity] = Stratagem(identity, name, arrows, entry.get("source_url", ""))
    if not choices:
        raise ValueError("本地武器库中没有可验证的中文战备题目。")
    return tuple(choices.values())


SCHEMA = (
    "CREATE TABLE IF NOT EXISTS stratagem_hero_sessions ("
    "scope TEXT NOT NULL, target_id TEXT NOT NULL, user_id TEXT NOT NULL, "
    "state_json TEXT NOT NULL, deadline REAL NOT NULL, "
    "PRIMARY KEY(scope, target_id, user_id))",
    "CREATE INDEX IF NOT EXISTS stratagem_hero_deadline ON stratagem_hero_sessions(deadline)",
    "CREATE TABLE IF NOT EXISTS stratagem_hero_records ("
    "scope TEXT NOT NULL, target_id TEXT NOT NULL, user_id TEXT NOT NULL, "
    "display_name TEXT NOT NULL, best_score INTEGER NOT NULL DEFAULT 0, "
    "best_round INTEGER NOT NULL DEFAULT 0, games INTEGER NOT NULL DEFAULT 0, "
    "updated_at REAL NOT NULL, PRIMARY KEY(scope, target_id, user_id))",
    "CREATE INDEX IF NOT EXISTS stratagem_hero_ranking "
    "ON stratagem_hero_records(scope, target_id, best_score DESC)",
)

_START = {"战备英雄", "随机战备", "stratagemhero", "stratagem_hero", "hero"}
_ACTIONS = {
    "开始": "start", "start": "start", "结束": "stop", "退出": "stop", "stop": "stop",
    "状态": "status", "status": "status", "记录": "record", "战绩": "record",
    "record": "record", "排行": "rank", "排行榜": "rank", "rank": "rank",
    "帮助": "help", "规则": "help", "help": "help",
}
_ALIASES = {
    "结束战备": "stop", "退出战备": "stop", "战备状态": "status", "战备记录": "record",
    "战备排行": "rank", "战备排行榜": "rank", "战备帮助": "help",
}


def _command(text: str) -> str | None:
    clean = unicodedata.normalize("NFKC", text).strip().casefold().removeprefix("/")
    if clean in _ALIASES:
        return _ALIASES[clean]
    for name in _START:
        if clean == name:
            return "start"
        if clean.startswith(name + " "):
            return _ACTIONS.get(clean[len(name):].strip(), "help")
    return None


class StratagemHeroService:
    """All state transitions use Database.transaction, including across instances.

    Chat rounds start with 120 seconds; each correct code refunds 8 seconds up to
    that cap. A round contains 4 + round questions, capped at 12 for chat. Scores
    follow the familiar 5-per-arrow plus round/time/perfect bonus structure.
    """

    ROUND_SECONDS = 120
    CORRECT_SECONDS = 8
    MAX_SESSIONS = 512
    MAX_RECORDS = 50_000
    MAX_ROUNDS = 100

    def __init__(self, db, *, catalog_path: Path | None = None, clock=time.time, rng=None,
                 stratagems: tuple[Stratagem, ...] | None = None):
        self.db = db
        self.clock = clock
        self.rng = rng or random.SystemRandom()
        self.stratagems = stratagems if stratagems is not None else load_stratagems(catalog_path)
        if not self.stratagems:
            raise ValueError("战备题库不能为空。")
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self.db.transaction() as connection:
            for statement in SCHEMA:
                await connection.execute(statement)
        self._initialized = True

    @staticmethod
    def help_text() -> str:
        return (
            "【战备英雄 · 聊天版】\n"
            "发送「战备英雄」或「随机战备」开始，看战备名称和方向完成输入。\n"
            "每关 120 秒，每答对一题补回 8 秒（上限 120 秒）；首关 5 题，逐关增加，最多 12 题。\n"
            "可整串发送，也可分条输入。输错会清空当前题进度，并失去本关完美奖励。\n"
            "每个正确方向计 5 分；过关另加关卡、剩余时间和完美奖励。\n"
            "支持 ↑↓←→ / ⬆️⬇️⬅️➡️ / ^v<> / 全角符号 / WASD / 上下左右。\n"
            "「战备英雄 状态」「战备英雄 结束」「战备记录」「战备排行」。\n"
            "群内请 @机器人输入；每人独立游戏，各聊天场景分别保存记录。"
        )

    def _question(self, round_number: int, previous: str = "") -> dict:
        pool = [item for item in self.stratagems if len(item.arrows) <= min(12, round_number + 4)]
        pool = pool or list(self.stratagems)
        different = [item for item in pool if item.id != previous]
        item = self.rng.choice(different or pool)
        return {"id": item.id, "name": item.name, "arrows": item.arrows}

    def _new_state(self, now: float) -> dict:
        return {"round": 1, "completed": 0, "score": 0, "prefix": "", "perfect": True,
                "deadline": now + self.ROUND_SECONDS, "question": self._question(1)}

    @staticmethod
    def _total(state: dict) -> int:
        return min(12, 4 + state["round"])

    def _render(self, state: dict, now: float) -> str:
        question = state["question"]
        progress = f"\n已输入：{' '.join(state['prefix'])}" if state["prefix"] else ""
        return (f"【战备英雄 · 第 {state['round']} 关】\n"
                f"第 {state['completed'] + 1}/{self._total(state)} 题 · 得分 {state['score']} · "
                f"剩余 {max(0, math.ceil(state['deadline'] - now))} 秒\n"
                f"{question['name']}\n{' '.join(question['arrows'])}{progress}\n"
                "直接发送方向；可一次输完或分步发送。结束：战备英雄 结束")

    @staticmethod
    def _name(display_name: str, user_id: str) -> str:
        # Do not put raw platform identifiers or line-breaking display names in rankings.
        del user_id
        return "".join(char for char in display_name if char.isprintable()).strip()[:24] or "匿名潜兵"

    async def _save(self, connection, key: tuple, state: dict) -> None:
        await connection.execute(
            "INSERT INTO stratagem_hero_sessions VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(scope, target_id, user_id) DO UPDATE SET "
            "state_json = excluded.state_json, deadline = excluded.deadline",
            (*key, canonical_json(state), state["deadline"]),
        )

    async def _finish(self, connection, key: tuple, state: dict, now: float) -> int:
        async with connection.execute(
            "SELECT best_score FROM stratagem_hero_records "
            "WHERE scope = ? AND target_id = ? AND user_id = ?", key,
        ) as cursor:
            row = await cursor.fetchone()
        best = max(state["score"], row["best_score"] if row else 0)
        await connection.execute(
            "UPDATE stratagem_hero_records SET best_score = MAX(best_score, ?), "
            "best_round = MAX(best_round, ?), games = games + 1, updated_at = ? "
            "WHERE scope = ? AND target_id = ? AND user_id = ?",
            (state["score"], state["round"] - 1, now, *key),
        )
        await connection.execute(
            "DELETE FROM stratagem_hero_sessions WHERE scope = ? AND target_id = ? AND user_id = ?",
            key,
        )
        return best

    async def _expire_others(self, connection, now: float) -> None:
        async with connection.execute(
            "SELECT * FROM stratagem_hero_sessions WHERE deadline <= ?", (now,),
        ) as cursor:
            rows = await cursor.fetchall()
        for row in rows:
            key = (row["scope"], row["target_id"], row["user_id"])
            await self._finish(connection, key, json.loads(row["state_json"]), now)

    async def handle(self, text: str, *, scope: str, target_id: str, user_id: str,
                     display_name: str = "") -> str | None:
        """Return None for another feature; unknown prose never mutates a game.

        Route recognized ordinary bot commands before this method because WASD
        sequences can also spell words. Call with the event's plain, mention-free text.
        """
        if not all(isinstance(value, str) and value.strip()
                   for value in (scope, target_id, user_id)):
            return None
        if not isinstance(text, str):
            return None
        action = _command(text)
        generic_stop = text.strip() in {"结束", "退出"}
        if action is None and not generic_stop and not _looks_like_answer(text):
            return None
        if action == "help":
            return self.help_text()
        await self.initialize()
        key = (scope, target_id, user_id)
        async with self.db.transaction() as connection:
            now = self.clock()
            async with connection.execute(
                "SELECT state_json FROM stratagem_hero_sessions "
                "WHERE scope = ? AND target_id = ? AND user_id = ?", key,
            ) as cursor:
                row = await cursor.fetchone()
            state = json.loads(row["state_json"]) if row else None
            if state is None and action is None:
                return None
            if generic_stop:
                action = "stop"
            expired = state is not None and state["deadline"] <= now
            timeout_text = ""
            if expired:
                best = await self._finish(connection, key, state, now)
                timeout_text = f"本局时间到！得分 {state['score']}，最高分 {best}。"
                state = None
                if action not in {"start", "rank", "record"}:
                    return timeout_text + "\n发送「战备英雄」重新开始。"
            if action in {"record", "rank"}:
                await self._expire_others(connection, now)
                if action == "record":
                    async with connection.execute(
                        "SELECT * FROM stratagem_hero_records "
                        "WHERE scope = ? AND target_id = ? AND user_id = ?", key,
                    ) as cursor:
                        record = await cursor.fetchone()
                    result = (f"【战备记录】\n最高分：{record['best_score']}\n"
                              f"最高通关：{record['best_round']}\n已结束局数：{record['games']}"
                              if record else "还没有战备英雄记录，发送「战备英雄」开始。")
                    if state:
                        result += f"\n进行中：第 {state['round']} 关，{state['score']} 分。"
                else:
                    async with connection.execute(
                        "SELECT user_id, display_name, best_score, best_round "
                        "FROM stratagem_hero_records WHERE scope = ? AND target_id = ? "
                        "AND games > 0 ORDER BY best_score DESC, best_round DESC, updated_at ASC "
                        "LIMIT 10", (scope, target_id),
                    ) as cursor:
                        records = await cursor.fetchall()
                    result = "【本聊天战备排行榜】\n" + ("\n".join(
                        f"{index}. {'你' if record['user_id'] == user_id else record['display_name']}"
                        f" · {record['best_score']} 分 · 通关 {record['best_round']}"
                        for index, record in enumerate(records, 1)
                    ) or "暂无记录，发送「战备英雄」开始。")
                return (timeout_text + "\n" if timeout_text else "") + result
            if action == "start":
                if state:
                    return "你在本聊天已有一局进行中。\n" + self._render(state, now)
                await self._expire_others(connection, now)
                async with connection.execute("SELECT COUNT(*) FROM stratagem_hero_sessions") as c:
                    count = (await c.fetchone())[0]
                if count >= self.MAX_SESSIONS:
                    return "当前游戏人数较多，请稍后再试。"
                async with connection.execute(
                    "SELECT 1 FROM stratagem_hero_records "
                    "WHERE scope = ? AND target_id = ? AND user_id = ?", key,
                ) as cursor:
                    known = await cursor.fetchone()
                if not known:
                    async with connection.execute("SELECT COUNT(*) FROM stratagem_hero_records") as c:
                        if (await c.fetchone())[0] >= self.MAX_RECORDS:
                            return "游戏记录已满，请联系管理员扩容。"
                await connection.execute(
                    "INSERT INTO stratagem_hero_records "
                    "(scope, target_id, user_id, display_name, updated_at) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(scope, target_id, user_id) DO UPDATE SET "
                    "display_name = excluded.display_name, updated_at = excluded.updated_at",
                    (*key, self._name(display_name, user_id), now),
                )
                state = self._new_state(now)
                await self._save(connection, key, state)
                return ((timeout_text + "\n") if timeout_text else "") + (
                    "聊天版：每关 120 秒，答对补 8 秒；输错重输当前题。\n"
                    "支持箭头表情、全角符号、WASD、上下左右。\n" + self._render(state, now)
                )
            if not state:
                return "本聊天没有进行中的游戏，发送「战备英雄」开始。"
            if action == "status":
                return self._render(state, now)
            if action == "stop":
                best = await self._finish(connection, key, state, now)
                return f"本局已结束，得分 {state['score']}，最高分 {best}。\n发送「战备英雄」再来一局。"
            try:
                arrows = normalize_arrows(text)
            except ArrowInputError as exc:
                return f"{exc}\n当前进度保留；可用 ↑↓←→、WASD 或上下左右。"
            combined = state["prefix"] + arrows
            expected = state["question"]["arrows"]
            # Whole-message validation prevents an extra arrow from answering an unseen next question.
            if not expected.startswith(combined):
                state["prefix"] = ""
                state["perfect"] = False
                await self._save(connection, key, state)
                return "方向不匹配，当前题进度已清空；请从第一个方向重输。\n" + self._render(state, now)
            if combined != expected:
                state["prefix"] = combined
                await self._save(connection, key, state)
                return self._render(state, now)
            earned = 5 * len(expected)
            state["score"] += earned
            state["completed"] += 1
            state["prefix"] = ""
            state["deadline"] = min(now + self.ROUND_SECONDS,
                                    state["deadline"] + self.CORRECT_SECONDS)
            lead = f"正确！+{earned} 分。\n"
            if state["completed"] >= self._total(state):
                round_bonus = 75 + 25 * (state["round"] - 1)
                time_bonus = math.floor(100 * (state["deadline"] - now) / self.ROUND_SECONDS)
                perfect_bonus = 100 if state["perfect"] else 0
                state["score"] += round_bonus + time_bonus + perfect_bonus
                lead += (f"第 {state['round']} 关完成！关卡 +{round_bonus} / "
                         f"时间 +{time_bonus} / 完美 +{perfect_bonus}。\n")
                state["round"] += 1
                if state["round"] > self.MAX_ROUNDS:
                    best = await self._finish(connection, key, state, now)
                    return lead + f"全部 {self.MAX_ROUNDS} 关完成！总分 {state['score']}，最高分 {best}。"
                state["completed"] = 0
                state["perfect"] = True
                state["deadline"] = now + self.ROUND_SECONDS
            state["question"] = self._question(state["round"], state["question"]["id"])
            await self._save(connection, key, state)
            return lead + self._render(state, now)
