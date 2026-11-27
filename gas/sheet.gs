function myFunction() {
  
}
// =======================================================
// 競艇AI 成績の自動まとめ（スマホで見やすい版）
// GitHubの記録を10分ごとに読み込んで、スプレッドシートに書き写す
// ※最初の1回だけ brSetupBoatraceStats を実行（済んでいれば不要）
// =======================================================
const BR_LOG_BASE_URL = 'https://raw.githubusercontent.com/pocha2525/boatrace-ai/main/logs/';
const BR_TARGET_SHEET_URL = 'https://docs.google.com/spreadsheets/d/1AtbhKouKMSq8ZbaoNmbSbIGsNHxtvU0GHr3VQ3uWlq4/edit';

// GitHubの記録（CSV）を読む。無ければ null
function brFetchCsv_(filename) {
  const res = UrlFetchApp.fetch(BR_LOG_BASE_URL + filename + '?t=' + Date.now(), {muteHttpExceptions: true});
  if (res.getResponseCode() !== 200) return null;
  const text = res.getContentText('UTF-8').trim();
  if (!text) return null;
  const rows = Utilities.parseCsv(text);
  if (!rows.length) return null;
  const head = rows[0].join(',');
  return [rows[0]].concat(rows.slice(1).filter(r => r.join(',') !== head && r.join('').trim() !== ''));
}

// 今日の記録が無ければ、3日前までさかのぼって一番新しい日の記録を返す
function brLatest_(prefix, today) {
  for (let i = 0; i <= 3; i++) {
    const day = i === 0 ? today : Utilities.formatDate(new Date(Date.now() - i * 86400000), 'Asia/Tokyo', 'yyyyMMdd');
    const rows = brFetchCsv_(prefix + day + '.csv');
    if (rows && rows.length > 1) return {day: day, rows: rows};
  }
  return null;
}

// 20261124 → 11/24
function brMd_(d) {
  return d && d.length === 8 ? d.slice(4, 6) + '/' + d.slice(6) : d;
}

// 見出し名で列を取り出す道具
function brCol_(rows) {
  const h = rows[0];
  return (r, name) => { const i = h.indexOf(name); return i < 0 ? '' : (r[i] || ''); };
}

// 「1-3-2」が日付に化けないよう、文字のまま書き込む。列幅はスマホ向けに広めにする
function brWriteSheet_(ss, sheetName, rows, asText) {
  const sh = ss.getSheetByName(sheetName) || ss.insertSheet(sheetName);
  sh.clear();
  if (!rows || rows.length < 2) {
    sh.getRange(1, 1).setValue('まだ記録がありません').setFontWeight('bold');
    return sh;
  }
  const w = Math.max.apply(null, rows.map(r => r.length));
  const data = rows.map(r => r.concat(new Array(w - r.length).fill('')));
  const rg = sh.getRange(1, 1, data.length, w);
  if (asText) rg.setNumberFormat('@');
  rg.setValues(data).setFontSize(11).setVerticalAlignment('middle');
  sh.setFrozenRows(1);
  sh.getRange(1, 1, 1, w).setBackground('#1f4e78').setFontColor('#ffffff').setFontWeight('bold');
  sh.autoResizeColumns(1, w);
  for (let c = 1; c <= w; c++) sh.setColumnWidth(c, sh.getColumnWidth(c) + 24);
  return sh;
}

function brShortReason_(s) {
  let m = s.match(/確率([\d.]+%)/);
  if (m && s.indexOf('12%未満') >= 0) return '確率' + m[1] + '（12%未満）';
  m = s.match(/期待値([\d.]+)/);
  if (m) return '期待値' + m[1] + '（1.0未満）';
  return s;
}

// 今日の判定：新しい順、1レース1行。本命と2番手を両方表示。終わったレースは結果つき
// bets：その日の成績記録（実際に買った買い目）
function brFormatNotify_(rows, results, bets) {
  if (!rows || rows.length < 2) return null;
  const g = brCol_(rows);
  const res = {};
  if (results && results.length > 1) {
    const gr = brCol_(results);
    results.slice(1).forEach(r => { res[gr(r, 'jcd') + '-' + gr(r, 'rno')] = [gr(r, 'tri'), gr(r, 'tri_pay')]; });
  }
  const bought = {};
  if (bets && bets.length > 1) {
    const gb = brCol_(bets);
    bets.slice(1).forEach(r => {
      if (!gb(r, 'pick')) return;
      const k = gb(r, 'jcd') + '-' + gb(r, 'rno');
      (bought[k] = bought[k] || []).push(gb(r, 'pick'));
    });
  }
  const last = {};
  const ids = [];
  rows.slice(1).forEach(r => {
    const id = g(r, 'notify_id');
    if (!id) return;
    if (!last[id]) ids.push(id);
    last[id] = r;
  });
  const out = [['日付', '締切', 'レース', '判定', '朝', '本命', 'オッズ', '期待値',
                '2番手', 'オッズ', '期待値', '結果', '当たり', '配当', '見送り理由']];
  ids.reverse().forEach(id => {
    const r = last[id];
    const st = g(r, 'status');
    const key = g(r, 'jcd') + '-' + g(r, 'rno');
    const buy = st === '受付成功';
    const got = bought[key] || [];
    const p = g(r, 'picks').split(' ');
    const od = g(r, 'odds').split(' ');
    const ev = g(r, 'evs').split(' ');
    const x = res[key];
    const tri = x ? x[0] : 'レース後に反映';
    let mark = '';
    if (x) {
      if (!/^\d-\d-\d$/.test(tri)) mark = '-';
      else if (tri === p[0]) mark = '本命◎';
      else if (tri === p[1]) mark = '2番手◎';
      else mark = '×';
      if (buy && got.indexOf(tri) >= 0) mark += '（的中）';
    }
    out.push([
      brMd_(g(r, 'hd')),
      g(r, 'deadline'),
      g(r, 'venue') + g(r, 'rno') + 'R',
      buy ? '買い' + (got.length || '') + (got.length ? '点' : '') : st === '条件未達' ? '見送り' : st,
      g(r, 'morning'),
      p[0] || '', od[0] ? od[0] + '倍' : '', ev[0] || '',
      p[1] || '', od[1] ? od[1] + '倍' : '', ev[1] || '',
      tri,
      mark,
      x && x[1] ? x[1] + '円' : '',
      buy ? '' : brShortReason_(g(r, 'reason'))
    ]);
  });
  return out;
}

// 判定の記録（結果つき）：前日までの全レース。新しい順。「今日の判定」と同じ並び
// 結果は翌朝の成績レポートで公式の成績から付く
function brFormatJudged_(rows) {
  if (!rows || rows.length < 2) return null;
  const g = brCol_(rows);
  const x = (v, unit) => v ? v + unit : '';
  const out = [['日付', '締切', 'レース', '判定', '朝', '本命', 'オッズ', '期待値',
                '2番手', 'オッズ', '期待値', '結果', '当たり', '配当', '見送り理由']];
  rows.slice(1).reverse().forEach(r => {
    const d = g(r, '日付');
    const reason = g(r, '見送り理由');
    out.push([
      d.length === 10 ? d.slice(5) : d,
      g(r, '締切'),
      g(r, '会場') + g(r, 'R') + 'R',
      g(r, '判定'),
      g(r, '朝の候補'),
      g(r, '本命'), x(g(r, '本命オッズ'), '倍'), g(r, '本命期待値'),
      g(r, '2番手'), x(g(r, '2番手オッズ'), '倍'), g(r, '2番手期待値'),
      g(r, '結果'),
      g(r, '当たり') || g(r, '本命当たり'),
      g(r, '払戻') && g(r, '払戻') !== '0' ? g(r, '払戻') + '円' : '',
      reason ? brShortReason_(reason) : ''
    ]);
  });
  return out;
}

// 全部の買い目：新しい順
function brFormatBets_(rows) {
  if (!rows || rows.length < 2) return null;
  const g = brCol_(rows);
  const out = [['日付', 'レース', '買い目', '点目', 'オッズ', '期待値', '結果', '当たり', '払戻']];
  rows.slice(1).reverse().forEach(r => {
    const hit = g(r, '的中');
    out.push([
      brMd_(g(r, '日付')),
      g(r, '会場') + g(r, 'R') + 'R',
      g(r, '買い目'),
      g(r, '何点目'),
      g(r, 'オッズ') ? g(r, 'オッズ') + '倍' : '',
      g(r, '期待値'),
      g(r, '結果'),
      g(r, '精算') === 'refunded' ? '返還' : hit === '1' ? '◎' : hit === '0' ? '×' : '',
      g(r, '払戻') ? g(r, '払戻') + '円' : ''
    ]);
  });
  return out;
}

// 朝の候補
function brFormatMorning_(rows) {
  if (!rows || rows.length < 2) return null;
  const g = brCol_(rows);
  const pct = s => s ? Math.round(Number(s) * 100) + '%' : '';
  const out = [['日付', '締切', 'レース', '本命', '確率', '2番手', '確率']];
  rows.slice(1).forEach(r => {
    const p = g(r, 'picks').split(' ');
    const q = g(r, 'probs').split(' ');
    out.push([brMd_(g(r, 'hd')), g(r, 'deadline'), g(r, 'venue') + g(r, 'rno') + 'R',
              p[0] || '', pct(q[0]), p[1] || '', pct(q[1])]);
  });
  return out;
}

// 日別の成績（よく見る列だけ）
function brFormatDaily_(rows) {
  if (!rows || rows.length < 2) return null;
  const g = brCol_(rows);
  const cols = ['日付', '点数', '的中数', '投資', '払戻', '回収率%', '累計回収率%', '累計収支'];
  return [cols].concat(rows.slice(1).map(r => cols.map(c => g(r, c))));
}

// 累計収支（円）のグラフ：マイナスならマイナスに見える
function brUpdateChart_(sh, rows) {
  sh.getCharts().forEach(c => sh.removeChart(c));
  if (!rows || rows.length < 2) return;
  const n = rows.length;
  const chart = sh.newChart()
    .setChartType(Charts.ChartType.LINE)
    .addRange(sh.getRange(1, 1, n, 1))
    .addRange(sh.getRange(1, 8, n, 1))
    .setMergeStrategy(Charts.ChartMergeStrategy.MERGE_COLUMNS)
    .setNumHeaders(1)
    .setOption('title', '累計収支（円）の推移（0より上ならプラス）')
    .setOption('vAxis', {format: '#,##0円'})
    .setOption('legend', {position: 'none'})
    .setOption('pointSize', 5)
    .setOption('width', 360)
    .setOption('height', 260)
    .setPosition(n + 2, 1, 0, 0)
    .build();
  sh.insertChart(chart);
}

// GitHub（予想の実行）から、記録した直後に呼ばれる入口
// スクリプトのプロパティ SYNC_KEY と同じ key が付いているときだけ動く
function doGet(e) {
  const key = PropertiesService.getScriptProperties().getProperty('SYNC_KEY');
  if (!key || !e || !e.parameter || e.parameter.key !== key) {
    return ContentService.createTextOutput('ng');
  }
  return ContentService.createTextOutput(brSyncBoatraceStats() ? 'ok' : 'busy');
}

// 10分ごと（と、GitHubから呼ばれたとき）に実行される本体（名前は変えない：時計がこの名前で呼ぶ）
function brSyncBoatraceStats() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(60000)) return false;   // 同時に2つ動かない
  try {
    brSyncMain_();
    return true;
  } finally {
    lock.releaseLock();
  }
}

function brSyncMain_() {
  const ss = SpreadsheetApp.openByUrl(BR_TARGET_SHEET_URL);
  const today = Utilities.formatDate(new Date(), 'Asia/Tokyo', 'yyyyMMdd');
  // 今日の分がまだ無い（朝8時の判定開始前など）ときは、記録のある一番新しい日を表示する
  const nd = brLatest_('notify_', today);
  brWriteSheet_(ss, '今日の判定', nd ? brFormatNotify_(nd.rows,
                                                  brFetchCsv_('results_' + nd.day + '.csv'),
                                                  brFetchCsv_(nd.day + '.csv')) : null, true);
  const md = brLatest_('morning_', today);
  brWriteSheet_(ss, '今日の朝の候補', md ? brFormatMorning_(md.rows) : null, true);
  brWriteSheet_(ss, '全部の買い目', brFormatBets_(brFetchCsv_('all_bets.csv')), true);
  brWriteSheet_(ss, '判定の記録（結果つき）', brFormatJudged_(brFetchCsv_('judged_all.csv')), true);
  const daily = brFormatDaily_(brFetchCsv_('daily.csv'));
  const dsh = brWriteSheet_(ss, '日別の成績', daily, false);
  brUpdateChart_(dsh, daily);
  dsh.getRange(1, 1).setNote('最終自動更新: ' + Utilities.formatDate(new Date(), 'Asia/Tokyo', 'yyyy/MM/dd HH:mm'));
}

// 最初だけ1回手動実行：10分ごとの時計を登録（kick等の時計には触らない）
function brSetupBoatraceStats() {
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'brSyncBoatraceStats')
    .forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('brSyncBoatraceStats').timeBased().everyMinutes(10).create();
  brSyncBoatraceStats();
}
