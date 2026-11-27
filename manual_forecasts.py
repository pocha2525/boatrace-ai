"""Score real pre-deadline ChatGPT submissions separately from model simulations."""
import datetime
import json
from pathlib import Path
import pandas as pd
from policy_replay import valid_pick
from settlement import settle


def report(logs, results_for, until):
    path=Path(logs)/'manual_forecasts.jsonl'
    if not path.exists():return None
    forecasts={}
    for line in path.read_text().splitlines():
        r=json.loads(line)
        if str(r['hd'])>str(until):continue
        made=datetime.datetime.fromisoformat(r['published_at']);deadline=datetime.datetime.fromisoformat(r['deadline'])
        if made.tzinfo is None or deadline.tzinfo is None or made>=deadline:
            raise ValueError('手動予想の締切前公表を照合できない')
        picks=r['stakes']
        if len(picks)!=4 or any(not valid_pick(k) or type(v)!=int or v<=0 or v%100 for k,v in picks.items()) or sum(picks.values())!=10000:
            raise ValueError('手動4点・配分の記録不正')
        key=(str(r['hd']),int(r['jcd']),int(r['rno']))
        if key in forecasts:raise ValueError('手動最終予想の重複')
        forecasts[key]=r
    outcomes={};rows=[]
    for (hd,jcd,rno),r in forecasts.items():
        if hd not in outcomes:
            table=results_for(hd)
            outcomes[hd]={(int(v['jcd']),int(v['rno'])):v for v in table.to_dict('records')} if table is not None else {}
        result=outcomes[hd].get((jcd,rno),{})
        for policy in ['manual_actual_allocation','manual_equal100']:
            settled=[(settle(k,result),stake if policy=='manual_actual_allocation' else 100) for k,stake in r['stakes'].items()]
            pending=any(x[0][0]=='pending' for x in settled)
            cost=sum(x[1] for x in settled) if not pending else 0
            returned=sum(x[0][2]*x[1]/100 for x in settled) if not pending else 0
            rows.append(dict(hd=hd,jcd=jcd,rno=rno,policy=policy,pending=int(pending),hits=int(any(x[0][1] for x in settled)) if not pending else 0,investment=cost,returned=returned,profit=returned-cost,source=r['source'],published_at=r['published_at']))
    if not rows:return None
    detail=pd.DataFrame(rows);detail.to_csv(Path(logs)/'manual_comparison.csv',index=False)
    summary=detail.groupby('policy')[['pending','hits','investment','returned','profit']].sum().reset_index()
    summary['resolved_races']=detail.groupby('policy').pending.apply(lambda v:int((v==0).sum())).to_numpy()
    summary['roi_pct']=summary.returned/summary.investment.where(summary.investment>0)*100
    summary.to_csv(Path(logs)/'manual_comparison_summary.csv',index=False)
    return summary
