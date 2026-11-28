"""Refresh today's settled actual-notification totals from existing local CSVs."""
import csv
import json
from pathlib import Path

from settlement import settle


def run(logs, hd):
    logs = Path(logs)
    bets_path, results_path = logs/f'{hd}.csv', logs/f'results_{hd}.csv'
    if not bets_path.exists() or not results_path.exists():
        return dict(status='pending')
    def read(path):
        with path.open(newline='',encoding='utf8') as f:return [r for r in csv.DictReader(f) if r.get('hd')!='hd']
    outcomes = {(int(r['jcd']),int(r['rno'])):r for r in read(results_path)}
    unique={}
    for r in read(bets_path):
        if r.get('pick'):unique.setdefault((int(r['jcd']),int(r['rno']),r['pick']),r)
    resolved=[];pending=0
    for (jcd,rno,pick), row in unique.items():
        status,hit,ret,_=settle(pick,outcomes.get((jcd,rno),{}))
        if status=='pending':pending+=1
        else:resolved.append((status,hit,ret))
    path=logs/'daily.csv';daily=read(path) if path.exists() else []
    fields=list(daily[0]) if daily else ['日付','予想レース数','未確定点数','点数','的中数','返還点数','投資','払戻','的中率%','回収率%','累計投資','累計払戻','累計回収率%','収支','累計収支']
    date=f'{hd[:4]}/{hd[4:6]}/{hd[6:]}'
    n=len(resolved);invest=n*100;returned=sum(x[2] for x in resolved);hits=sum(x[1] for x in resolved)
    current={'日付':date,'予想レース数':len({(x[0],x[1]) for x in unique}),
             '未確定点数':pending,'点数':n,'的中数':hits,'返還点数':sum(x[0]=='refunded' for x in resolved),
             '投資':invest,'払戻':returned,'的中率%':round(hits/n*100,1) if n else '',
             '回収率%':round(returned/invest*100,1) if invest else '', '収支':returned-invest}
    fields=list(dict.fromkeys(fields+list(current)+['累計投資','累計払戻','累計回収率%','累計収支']))
    daily=[r for r in daily if r['日付']!=date]+[current]
    total_in,total_out=0,0
    for row in sorted(daily,key=lambda r:r['日付']):
        total_in+=int(float(row.get('投資') or 0));total_out+=int(float(row.get('払戻') or 0))
        row.update(累計投資=total_in,累計払戻=total_out,
                   累計回収率=round(total_out/total_in*100,1) if total_in else '')
        row['累計回収率%']=row.pop('累計回収率');row['累計収支']=total_out-total_in
    import io
    stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=fields,lineterminator='\n');writer.writeheader();writer.writerows(daily)
    value=stream.getvalue()
    if not path.exists() or path.read_text()!=value:path.write_text(value)
    return dict(status='ok',day=hd,resolved=n,pending=pending,investment=invest,returned=returned,
                roi_pct=returned/invest*100 if invest else None)
