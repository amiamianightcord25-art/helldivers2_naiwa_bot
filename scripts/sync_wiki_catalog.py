"""Refresh the offline equipment catalog without starting the QQ bot."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hd2bot.wiki_sync import sync_catalog  # noqa: E402


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=root / "src/hd2bot/assets/wiki_catalog.json")
    parser.add_argument("--cache-dir", type=Path, default=root / ".cache/wiki")
    parser.add_argument("--aliases", type=Path)
    parser.add_argument("--image-dir", type=Path, help="Local images directory; defaults to output.parent/wiki_images")
    parser.add_argument("--refresh", action="store_true", help="Revalidate cached pages using HTTP validators")
    parser.add_argument("--offline", action="store_true", help="Rebuild exclusively from cached articles")
    parser.add_argument("--delay", type=float, default=1.0, help="Minimum seconds between requests (>= 0.25)")
    args = parser.parse_args()
    try:
        result = sync_catalog(args.output, args.cache_dir, refresh=args.refresh,
                              offline=args.offline, delay=args.delay, aliases_path=args.aliases,
                              image_dir=args.image_dir)
    except Exception as exc:
        print(f"Wiki sync failed; previous catalog preserved: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
