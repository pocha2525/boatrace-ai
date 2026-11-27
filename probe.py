"""データ形式の確認用：前日の直前情報の風向（画像の番号）と成績ファイルの風向を突き合わせる"""
import os, re, datetime, unicodedata
from common import page, now_jst, download_k, read_k

os.makedirs('logs/samples', exist_ok=True)
d = now_jst().date() - datetime.timedelta(days=1)
hd = f'{d:%Y%m%d}'
rows, races = read_k(download_k(d))
out = ['venue,race,k_dir,k_wind,img_wind,img_course,stab']
seen = 0
for r in races:
    if r['race'] not in (1, 4, 8, 12):
        continue
    b = page('beforeinfo', r['venue'], r['race'], hd)
    w = re.search(r'is-wind(\d+)"', b)
    c = re.search(r'is-direction(\d+)"', b)
    stab = '安定板' in b
    if stab and seen < 1:
        i = b.find('安定板'); open('logs/samples/stab_snip.txt', 'w').write(b[i-1500:i+500]); seen += 1
    out.append(f"{r['venue']},{r['race']},{r['wind_dir']},{r['wind']},"
               f"{w.group(1) if w else ''},{c.group(1) if c else ''},{int(stab)}")
open('logs/samples/wind_map.csv', 'w', encoding='utf-8').write('\n'.join(out) + '\n')
print('\n'.join(out))
