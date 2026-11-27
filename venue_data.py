"""Official venue endpoints discovered from their own navigation/JavaScript.

Static ASP pages are current-day only. Never backfill an old date from them.
The Miyajima endpoint returns HTML fragments separated by ####, not JSON.
"""
import re
import threading

from common import ThreadState, now_jst

ORIGINAL = {
    4: 'https://www.heiwajima.gr.jp/asp/kyogi/04/pc/yoso05{r2}.htm',
    7: 'https://www.gamagori-kyotei.com/asp/gamagori/sp/kyogi/kyogihtml/recomend/recomend{d}07{r2}.htm',
    12: 'https://www.boatrace-suminoe.jp/asp/kyogi/12/pc/st02{r2}.htm',
    15: 'https://www.marugameboat.jp/asp/kyogi/15/pc/yoso05{r2}.htm',
    16: 'https://www.kojimaboat.jp/asp/kyogi/16/sp/yoso05{r2}.htm',
    17: 'https://www.boatrace-miyajima.com/race_common/require/kaisai_reload.php',
    24: 'https://www.omurakyotei.jp/yosou/sp/syussou/?day={d}&race={r2}',
}
STATIC = {4, 12, 15, 16}
_STATE = ThreadState()


def decode(response):
    try:
        return response.content.decode('utf-8')
    except UnicodeDecodeError:
        return response.content.decode('cp932', errors='replace')


def html(get, venue, race, day):
    """Read once per race/thread; both original times and comments reuse it."""
    key = (int(venue), int(race), str(day))
    if _STATE.get('key') == key:
        return _STATE['html']
    _STATE.clear()
    if venue in STATIC and str(day) != now_jst().strftime('%Y%m%d'):
        return ''
    template = ORIGINAL.get(venue)
    if not template:
        return ''
    url = template.format(d=day, r2=f'{race:02d}')
    kwargs = {'tries': 1}
    if venue == 17:
        kwargs.update(method='POST', data={'race': race, 'date': str(day)})
    response = get(url, **kwargs)
    text = decode(response) if response is not None else ''
    # Do not accept static pages merely because a CSS cache-buster has today's
    # date. A race video date or explicit content date is required for data.
    if venue in STATIC and not re.search(
            r'(?:raceDate=|["\'](?:date|day)["\']\s*[:=]\s*["\'])' + str(day), text):
        text = ''
    _STATE.update(key=key, html=text, url=url)
    return text


def clear():
    _STATE.clear()
