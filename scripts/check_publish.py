"""Check publishable files without printing credential contents."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from io import BytesIO, StringIO
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_NAMES = {"config.toml", "config.json", "config.yaml", "config.yml", ".env"}
PRIVATE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3"}
RUNTIME_PARTS = {
    "data", "logs", ".ssh", ".venv", "venv", "tmp", ".cache", "outputs", ".pytest-tmp",
    "napcat-data", "napcat-config", "qq-data",
}


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def private_path(name: str) -> bool:
    path = Path(name)
    lower = path.name.lower()
    return (any(part.lower() in RUNTIME_PARTS for part in path.parts)
            or (bool(path.parts) and path.parts[0].lower() == "napcat")
            or (lower.startswith(("onebot11_", "napcat_")) and lower.endswith(".json"))
            or lower in PRIVATE_NAMES or ".private." in lower
            or lower == "project_context_cn.md" or lower == "qq_experience_qr.png"
            or lower.startswith(("id_rsa", "id_ed25519", "cookies.", "credentials."))
            or path.suffix.lower() in PRIVATE_SUFFIXES
            or lower.endswith((".db-wal", ".db-shm", ".sqlite3-wal", ".sqlite3-shm"))
            or (lower.startswith(".env.") and not lower.endswith(".example")))


def known_local_values() -> set[bytes]:
    """Compare actual operator identifiers as well as credential values."""
    values = set()
    fields = re.compile(
        r"secret|token|password|cookie|session|api.?key|app.?id|push.?user|contact|"
        r"^url$|ws.?url$|ssh.?host|binding.?id|manager.?user.?ids|allowed.?groups", re.I,
    )

    def collect(key, value):
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                collect(child_key, child_value)
        elif isinstance(value, list):
            for child_value in value:
                collect(key, child_value)
        elif isinstance(value, str) and len(value) >= 8 and fields.search(key):
            if value == "example@example.com":
                return
            if key.lower() == "url" and urlsplit(value).hostname in {"127.0.0.1", "localhost"}:
                return
            values.add(value.encode())

    env = ROOT / ".env"
    if env.is_file():
        collect("", dotenv_values(env, encoding="utf-8-sig", interpolate=False))
    # Deployment receipts describe the installation; they aren't input configuration.
    # Their host aliases can coincide with ordinary project/game names in source.
    paths = {path for path in (ROOT / "data").glob("*.private.json")
             if not path.name.endswith("_receipt.private.json")}
    paths.update(ROOT / "data" / name for name in ("helper_download.json", "group_push.json"))
    for path in paths:
        if path.is_file():
            collect("", json.loads(path.read_text(encoding="utf-8-sig")))
    return values


def blank_example(name: str, content: bytes) -> bool:
    """Examples contain empty operator fields and only minimal schema defaults."""
    if Path(name).name == ".env.example":
        try:
            values = dotenv_values(stream=StringIO(content.decode("utf-8-sig")), interpolate=False)
            return all(value in (None, "") for value in values.values())
        except (UnicodeError, ValueError):
            return False
    if not name.endswith(".example.json"):
        return True
    try:
        data = json.loads(content.decode("utf-8-sig"))
    except (UnicodeError, ValueError):
        return False

    def empty(value, path=()):
        if isinstance(value, dict):
            return all(empty(item, (*path, key)) for key, item in value.items())
        if isinstance(value, list):
            return not value
        if value is None or value is False or value == "":
            return True
        return (path == ("schema",) and type(value) is int and value == 1
                or name == "qq_native_ui.example.json"
                and path == ("menu_mode",) and value == "merge")

    return isinstance(data, dict) and empty(data)


def workstation_path(name: str, content: bytes) -> bool:
    try:
        content.decode("utf-8")
    except UnicodeError:
        return False
    # The existing cross-platform filename test uses this synthetic invalid path.
    if name == "tests/test_wiki_service.py":
        content = content.replace(b"C:" + b"\\" * 2 + b"outside.png", b"")
    return bool(re.search(
        rb"(?<![A-Za-z0-9])[A-Za-z]:[\\/]|/(?:Users|home)/[^\s/]+/|/(?:root)/",
        content,
    ))


def development_trace(content: bytes) -> bool:
    try:
        value = content.decode("utf-8").lower()
    except UnicodeError:
        return False
    markers = ("session" + "host", "fidd" + "ler", "hd2_query" + "_reference",
               "read_" + "thread", "codex@" + "localhost")
    return any(marker in value for marker in markers) or bool(re.search(r"\b[a-z]\s*盘", value))


def content_problems(name: str, content: bytes, values: set[bytes]) -> list[str]:
    problems = []
    if workstation_path(name, content):
        problems.append("absolute workstation path")
    if development_trace(content) or development_trace(name.encode()):
        problems.append("local development trace")
    if re.search(rb"https?://[^\s\"'<>]*lanzou[a-z]*\.[^\s\"'<>]+", content, re.I):
        problems.append("private download link")
    if any(value in content for value in values):
        problems.append("matches a real local credential")
    return problems


def history_problems(values: set[bytes]) -> list[tuple[str, str]]:
    """Inspect old blobs even when their contents have been removed from HEAD."""
    objects = {}
    # Scan publishable refs and detached CI HEAD, not private editor checkpoints.
    for line in git("rev-list", "--objects", "HEAD", "--branches", "--tags", "--remotes").splitlines():
        oid, _, name = line.partition(b" ")
        objects.setdefault(oid, name.decode("utf-8", errors="replace"))
    if not objects:
        return []
    result = subprocess.run(
        ["git", "cat-file", "--batch"], input=b"\n".join(objects) + b"\n",
        cwd=ROOT, stdout=subprocess.PIPE, check=True,
    )
    stream = BytesIO(result.stdout)
    problems = []
    while header := stream.readline():
        oid, kind, size = header.split()
        content = stream.read(int(size))
        stream.read(1)
        if kind not in {b"blob", b"commit"}:
            continue
        name = objects.get(oid, "") or "commit"
        label = f"history:{oid.decode()[:12]}:{name}"
        if kind == b"blob" and private_path(name):
            problems.append((label, "private runtime/configuration path"))
        problems.extend((label, reason) for reason in content_problems(name, content, values))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="check every tracked file")
    parser.add_argument("--history", action="store_true", help="also scan reachable Git history")
    args = parser.parse_args()
    names = git("ls-files", "-z") if args.all else git(
        "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z",
    )
    files = [name for name in names.decode("utf-8").split("\0") if name]
    values = known_local_values()
    problems = []
    for name in files:
        if private_path(name):
            problems.append((name, "private runtime/configuration path"))
            continue
        content = git("show", ":" + name)
        if not blank_example(name, content):
            problems.append((name, "example contains nonblank operator values"))
        problems.extend((name, reason) for reason in content_problems(name, content, values))
    if args.history:
        problems.extend(history_problems(values))
    for name, reason in problems:
        print(f"BLOCKED: {name}: {reason}")
    if problems:
        return 1
    candidates = [os.environ.get("GITLEAKS_BIN"), shutil.which("gitleaks"),
                  str(ROOT / "tmp/gitleaks/gitleaks.exe")]
    executable = next((value for value in candidates if value and Path(value).is_file()), None)
    if executable is None:
        print("Gitleaks is required. Install it or set GITLEAKS_BIN; no commit/push performed.")
        return 2
    options = [executable, "git", ".", "--redact", "--no-banner", "--log-level", "warn"]
    if subprocess.run([*options, "--pre-commit", "--staged"], cwd=ROOT).returncode:
        return 1
    if args.history and subprocess.run([
        *options, "--log-opts=--full-history HEAD --branches --tags --remotes",
    ], cwd=ROOT).returncode:
        return 1
    print(f"Publish check passed: {len(files)} files; no credential values printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
