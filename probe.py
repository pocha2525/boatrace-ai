"""データ形式の確認用（番組表Bファイルと成績Kファイルの見本を logs/samples に保存）"""
import os, datetime, tempfile
from common import get, MB, now_jst, download_k, k_path
import lhafile

os.makedirs('logs/samples', exist_ok=True)
d = now_jst().date() - datetime.timedelta(days=1)
r = get(f'{MB}B/{d:%Y%m}/b{d:%y%m%d}.lzh')
print('B', None if r is None else len(r.content))
if r is not None:
    with tempfile.NamedTemporaryFile(suffix='.lzh', delete=False) as t:
        t.write(r.content)
    f = lhafile.Lhafile(t.name)
    txt = f.read(f.namelist()[0]).decode('cp932', errors='replace')
    open('logs/samples/B_sample.txt', 'w', encoding='utf-8').write('\n'.join(txt.splitlines()[:400]))
p = download_k(d)
if p:
    txt = open(p, encoding='cp932', errors='replace').read()
    open('logs/samples/K_sample.txt', 'w', encoding='utf-8').write('\n'.join(txt.splitlines()[:200]))
