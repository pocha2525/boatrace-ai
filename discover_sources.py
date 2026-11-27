"""Bounded official-site discovery. Only successful parsed sources are enabled.

Unavailable sources remain explicit observations, never fake zero features.
Run by nightly, not in the time-critical prediction path.
"""
import json
import os
import re
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse
from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup
from common import get_direct, MODEL, now_jst
from probe import SITES
from venue_data import decode


def one(item):
    import cm
    venue, site = item
    initial = [site + '/']
    builtin = cm.URLS.get(venue, '')
    if builtin.startswith('https://'):
        initial.insert(0, builtin.format(d=now_jst().strftime('%Y%m%d'), r=1))
    queue, seen, notes = initial, set(), []
    found = []
    while queue and len(seen) < 5:
        url = queue.pop(0)
        if url in seen:
            continue
        if urlparse(url).hostname != urlparse(site).hostname:
            continue
        seen.add(url)
        response = get_direct(url, tries=1, timeout=12)
        if response is None:
            notes.append({'url': url, 'state': '取得できず'})
            continue
        text = decode(response)
        rows = cm.parse_fukuoka(text) if venue == 22 else cm.parse_race(text) if venue in (19, 23) else cm.parse_table(text)
        notes.append({'url': url, 'state': '読取成功' if rows else '対応表なし', 'boats': len(rows)})
        if len(rows) == 6:
            # Only date/race query arguments that actually exist are replaced.
            parts = urlparse(url)
            q = parse_qs(parts.query)
            for key in ('day', 'hd', 'date'):
                if key in q and re.fullmatch(r'\d{8}', q[key][0]):
                    q[key] = ['{d}']
            for key in ('race', 'rno'):
                if key in q and re.fullmatch(r'\d{1,2}', q[key][0]):
                    q[key] = ['{r}']
            template = urlunparse(parts._replace(query=urlencode(q, doseq=True)))
            template = template.replace('%7Bd%7D', '{d}').replace('%7Br%7D', '{r}')
            found.append(template)
        soup = BeautifulSoup(text, 'html.parser')
        links = []
        for tag in soup.find_all(['a', 'iframe']):
            href = tag.get('href') or tag.get('src') or ''
            label = tag.get_text(' ', strip=True)
            if not href or href.startswith(('#', 'javascript:')):
                continue
            if any(w in href.lower()+label for w in ('comment', '選手コメント', 'kyogihtml/index', 'yosou/')):
                links.append(urljoin(url, href))
        links.sort(key=lambda u: 'comment' not in u.lower())
        queue = list(dict.fromkeys(queue+links))[:12]
    return str(venue), dict(comment_urls=list(dict.fromkeys(found)), checked=notes)


def main():
    with ThreadPoolExecutor(4) as pool:
        entries = dict(pool.map(one, SITES.items()))
    path = os.path.join(MODEL, 'source_registry.json')
    # A temporary outage must not erase a previously verified parser source.
    old = json.load(open(path)) if os.path.exists(path) else {}
    for venue, entry in entries.items():
        if not entry['comment_urls']:
            entry['comment_urls'] = old.get(venue, {}).get('comment_urls', [])
        entries[venue] = entry = dict(old.get(venue, {}), **entry)
        entry['checked_at'] = now_jst().isoformat()
    os.makedirs(MODEL, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    print('公式コメント取得先を確認', sum(bool(v['comment_urls']) for v in entries.values()), '/24場')
    return entries


if __name__ == '__main__':
    main()
