"""Backfill cosmetic art from its exact rendered index cells without replacing other fields."""
import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from hd2bot.wiki_sync import (  # noqa: E402
    BASE,
    Fetcher,
    atomic_json,
    parse_armor_cosmetics,
    parse_index,
    publish_catalog,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--delay', type=float, default=0.4)
    args = parser.parse_args()
    case = ROOT/'research/cosmetic_images_20260919'
    case.mkdir(exist_ok=True)
    fetcher = Fetcher(ROOT/'.cache/wiki', delay=args.delay)
    cosmetics, _ = fetcher.fetch(BASE+'/wiki/Cosmetics')
    armor, _ = fetcher.fetch(BASE+'/wiki/Armor')
    entries = parse_index('cosmetics', cosmetics)
    known = {e['id'] for e in entries}
    entries.extend(e for e in parse_armor_cosmetics(armor) if e['id'] not in known)
    images = ROOT/'data/wiki_images'
    progress = {'expected': len(entries), 'completed': 0, 'errors': [], 'missingSourceImages': []}
    for i, entry in enumerate(entries, 1):
        source = entry.get('image_url')
        if not source:
            progress['missingSourceImages'].append(entry['id'])
        else:
            try:
                entry['image_path'] = fetcher.image(source, images)
                progress['completed'] += 1
            except Exception as error:
                progress['errors'].append({'id':entry['id'], 'type':type(error).__name__, 'reason':str(error)[:180]})
        if i % 20 == 0 or i == len(entries):
            atomic_json(case/'progress.json', {**progress, 'processed':i})
            print(json.dumps({'processed':i, 'completed':progress['completed'], 'errors':len(progress['errors'])}), flush=True)
    atomic_json(case/'parsed_image_records.json', entries)
    # Partial asset cache is reusable; never replace the catalogue with missing art silently.
    if progress['errors']:
        print(json.dumps(progress, ensure_ascii=False))
        return 1
    indexed = {e['id']:e for e in entries}
    for catalog in (ROOT/'data/wiki_catalog.json', ROOT/'src/hd2bot/assets/wiki_catalog.json'):
        if not catalog.exists():
            continue
        data = json.loads(catalog.read_text(encoding='utf-8'))
        dest = catalog.parent/'wiki_images'
        dest.mkdir(exist_ok=True)
        for record in data['entries']:
            if record['category'] != 'cosmetics' or record['id'] not in indexed:
                continue
            image = indexed[record['id']]
            for field in ('image_url','image_path','image_credit'):
                if image.get(field):
                    record[field] = image[field]
            name = record.get('image_path')
            if name and dest.resolve() != images.resolve():
                shutil.copy2(images/name, dest/name)
        publish_catalog(catalog, data)
    progress['bySubcategory'] = dict(Counter(e['subcategory'] for e in entries if e.get('image_path')))
    progress['networkRequests'] = fetcher.network_requests
    atomic_json(case/'sync_result.json', progress)
    print(json.dumps({'success':True, 'completed':progress['completed'], 'networkRequests':fetcher.network_requests}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
