import csv
import live_stats


def write(path,rows):
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def test_current_day_settlement_updates_daily_without_changing_past(tmp_path):
    write(tmp_path/'daily.csv',[{'日付':'2026/11/27','点数':2,'投資':200,'払戻':100,'累計収支':-100}])
    write(tmp_path/'20261128.csv',[dict(hd='20261128',jcd=18,rno=4,pick='1-2-5'),dict(hd='20261128',jcd=18,rno=4,pick='1-5-2'),dict(hd='20261128',jcd=18,rno=5,pick='1-2-3')])
    write(tmp_path/'results_20261128.csv',[dict(hd='20261128',jcd=18,rno=4,tri='1-2-5',tri_pay=480,race_status='settled',refund_boats='')])
    r=live_stats.run(tmp_path,'20261128')
    assert r['investment']==200 and r['returned']==480 and r['pending']==1
    saved=list(csv.DictReader((tmp_path/'daily.csv').open()))
    assert saved[0]['投資']=='200' and saved[0]['払戻']=='100'
    assert saved[1]['累計収支']=='180' and saved[1]['回収率%']=='240.0'
    stamp=(tmp_path/'daily.csv').stat().st_mtime_ns
    live_stats.run(tmp_path,'20261128')
    assert (tmp_path/'daily.csv').stat().st_mtime_ns==stamp
