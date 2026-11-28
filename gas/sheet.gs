function myFunction() {
  
}
// =======================================================
// 競艇AI 成績の自動まとめ（スマホで見やすい版）
// GitHubの記録を5分ごとに確認し、変わった表示だけを書き換える
// ※最初の1回だけ brSetupBoatraceStats を実行（済んでいれば不要）
// =======================================================
const BR_LOG_BASE_URL = 'https://raw.githubusercontent.com/pocha2525/boatrace-ai/main/logs/';
const BR_TARGET_SHEET_URL = 'https://docs.google.com/spreadsheets/d/1AtbhKouKMSq8ZbaoNmbSbIGsNHxtvU0GHr3VQ3uWlq4/edit';

// GitHubの記録（CSV）を読む。無ければ null
function brFetchCsv_(filename) {
  try {
    const res = UrlFetchApp.fetch(BR_LOG_BASE_URL + filename + '?t=' + Date.now(), {muteHttpExceptions: true});
    if (res.getResponseCode() !== 200) return null;
    const text = res.getContentText('UTF-8').trim();
    if (!text) return null;
    const rows = Utilities.parseCsv(text);
    if (!rows.length) return null;
    const head = rows[0].join(',');
    const data = [rows[0]].concat(rows.slice(1).filter(r => r.join(',') !== head && r.join('').trim() !== ''));
    data.brSource = brSourceInfo_(data, filename);
    return data;
  } catch (err) {
    // 一時的な通信失敗でも、最後に表示できた成績を消さない。
    return null;
  }
}

// 実際に記録された時刻だけを使う。締切や同期時刻をデータ時刻にしない。
function brSourceInfo_(rows, filename) {
  const g = brCol_(rows);
  let latestDate = '';
  let latestTime = '';
  rows.slice(1).forEach(r => {
    const raw = g(r, 'hd') || g(r, '日付');
    const m = String(raw).match(/^(\d{4})[/-]?(\d{2})[/-]?(\d{2})$/);
    const date = m ? m[1] + '/' + m[2] + '/' + m[3] : '';
    if (date > latestDate) latestDate = date;
    ['send_end', 'decided_at', 'run_at', 'run_start', 'updated_at', 'timestamp', 'created_at'].forEach(col => {
      const value = String(g(r, col));
      let time = '';
      if (date && /^\d{2}:\d{2}(:\d{2})?$/.test(value)) {
        time = date + ' ' + value + (value.length === 5 ? ':00' : '');
      } else if (/^\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}/.test(value)) {
        const parsed = new Date(value);
        if (!isNaN(parsed.getTime())) time = Utilities.formatDate(parsed, 'Asia/Tokyo', 'yyyy/MM/dd HH:mm:ss');
      }
      if (time > latestTime) latestTime = time;
    });
  });
  // 日付しかないCSVは「日付のみ」と明記し、更新済みに見せかけない。
  const time = latestTime && latestTime.slice(0, 10) >= latestDate ? latestTime :
    latestDate ? latestDate + '（日付のみ・時刻不明）' : '時刻不明';
  return {filename: filename, time: time};
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

// 表示内容のハッシュ。同期キー等の秘密プロパティは参照しない。
function brHash_(value) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, JSON.stringify(value), Utilities.Charset.UTF_8)
    .map(b => ('0' + ((b + 256) % 256).toString(16)).slice(-2)).join('');
}

// 「1-3-2」が日付に化けないよう文字で書く。変更のある範囲だけを更新する。
function brWriteSheet_(ss, sheetName, rows, asText) {
  let sh = ss.getSheetByName(sheetName);
  if (!rows || rows.length < 2) return sh; // 読めないときは前回の表示を保持
  const props = PropertiesService.getScriptProperties();
  const hashKey = 'BR_DISPLAY_HASH_' + sheetName;
  const hash = brHash_([2, rows, !!asText]);
  if (sh && sh.getLastRow() > 0 && props.getProperty(hashKey) === hash) return sh;
  if (!sh) sh = ss.insertSheet(sheetName);
  const oldRows = sh.getLastRow();
  const oldCols = sh.getLastColumn();
  const w = Math.max.apply(null, rows.map(r => r.length));
  const data = rows.map(r => r.concat(new Array(w - r.length).fill('')));
  if (sh.getMaxRows() < data.length) sh.insertRowsAfter(sh.getMaxRows(), data.length - sh.getMaxRows());
  if (sh.getMaxColumns() < w) sh.insertColumnsAfter(sh.getMaxColumns(), w - sh.getMaxColumns());
  const rg = sh.getRange(1, 1, data.length, w);
  if (asText) rg.setNumberFormat('@');
  rg.setValues(data);
  // 全消去せず、前回より短くなった末尾だけを消す。
  if (oldRows > data.length) sh.getRange(data.length + 1, 1, oldRows - data.length, oldCols).clearContent();
  if (oldCols > w) sh.getRange(1, w + 1, Math.min(oldRows, data.length), oldCols - w).clearContent();
  const layoutKey = 'BR_LAYOUT_HASH_' + sheetName;
  const layout = brHash_([2, rows[0], !!asText]);
  if (props.getProperty(layoutKey) !== layout) {
    rg.setFontSize(11).setVerticalAlignment('middle');
    sh.setFrozenRows(1);
    sh.getRange(1, 1, 1, w).setBackground('#1f4e78').setFontColor('#ffffff').setFontWeight('bold');
    sh.setColumnWidths(1, w, 110);
    if (sheetName === '更新状況') sh.setColumnWidths(1, w, 230);
    else if (w >= 15) sh.setColumnWidth(w, 260);
    props.setProperty(layoutKey, layout);
  }
  props.setProperty(hashKey, hash);
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
  ids.reverse().slice(0, 300).forEach(id => {
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
  rows.slice(-Math.min(rows.length - 1, 300)).reverse().forEach(r => {
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
  rows.slice(-Math.min(rows.length - 1, 300)).reverse().forEach(r => {
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
  rows.slice(1, 301).forEach(r => {
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
  return [cols].concat(rows.slice(-Math.min(rows.length - 1, 500)).map(r => cols.map(c => g(r, c))));
}

// 累計収支（円）のグラフ：マイナスならマイナスに見える
function brUpdateChart_(sh, rows) {
  if (!sh || !rows || rows.length < 2) return;
  const props = PropertiesService.getScriptProperties();
  const n = rows.length;
  const extent = String(n);
  const charts = sh.getCharts();
  if (charts.length && props.getProperty('BR_DAILY_CHART_ROWS') === extent) return;
  charts.forEach(c => sh.removeChart(c));
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
  props.setProperty('BR_DAILY_CHART_ROWS', extent);
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

// 5分ごと（と、GitHubから呼ばれたとき）に実行される本体（名前は変えない：時計がこの名前で呼ぶ）
function brSyncBoatraceStats() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(2000)) return false;   // 同時に2つ動かない
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
  const checked = Utilities.formatDate(new Date(), 'Asia/Tokyo', 'yyyy/MM/dd HH:mm:ss');
  const props = PropertiesService.getScriptProperties();
  const status = [['表示', '同期確認時刻（日本時間）', '元データ時刻（日本時間）', '状態', '参照元', '表示件数']];
  function syncTab(name, source, formatted, note) {
    const prior = ss.getSheetByName(name);
    const metaKey = 'BR_SOURCE_META_' + name;
    let meta = null;
    const saved = props.getProperty(metaKey);
    if (saved) { try { meta = JSON.parse(saved); } catch (err) {} }
    if (source && formatted && formatted.length > 1) {
      const oldDay = meta && meta.filename.match(/_(\d{8})\.csv$/);
      const newDay = source.brSource && source.brSource.filename.match(/_(\d{8})\.csv$/);
      if (prior && oldDay && newDay && oldDay[1] > newDay[1]) {
        status.push([name, checked, meta.time, '最新日の取得不可・前回表示を保持', meta.filename, Math.max(0, prior.getLastRow() - 1)]);
        return prior;
      }
      const sh = brWriteSheet_(ss, name, formatted, name !== '日別の成績');
      meta = source.brSource || {filename: '', time: '時刻不明'};
      const serialized = JSON.stringify(meta);
      if (saved !== serialized) props.setProperty(metaKey, serialized);
      status.push([name, checked, meta.time, note || '取得成功', meta.filename, formatted.length - 1]);
      return sh;
    }
    status.push([name, checked, meta ? meta.time : '不明',
      prior ? '取得不可・前回表示を保持' : '取得不可・未表示', meta ? meta.filename : '',
      prior ? Math.max(0, prior.getLastRow() - 1) : 0]);
    return prior;
  }
  // 今日が未作成なら最近3日前まで。見つからなくても前回表示は残す。
  const nd = brLatest_('notify_', today);
  let notify = null;
  let notifyNote = '';
  if (nd) {
    const results = brFetchCsv_('results_' + nd.day + '.csv');
    const bets = brFetchCsv_(nd.day + '.csv');
    // 結果等が一時的に読めなくても、既存の確定情報を消さない。
    const old = ss.getSheetByName('今日の判定');
    const oldMeta = props.getProperty('BR_SOURCE_META_今日の判定');
    let sameDay = false;
    if (oldMeta) { try { sameDay = JSON.parse(oldMeta).filename === nd.rows.brSource.filename; } catch (err) {} }
    if (old && sameDay && (!results || !bets)) {
      const oldValues = old.getDataRange().getValues();
      notify = brFormatNotify_(nd.rows, results, bets);
      const previous = {};
      oldValues.slice(1).forEach(r => { previous[r[0] + '|' + r[2]] = r; });
      notify.slice(1).forEach(r => {
        const prior = previous[r[0] + '|' + r[2]];
        if (!prior) return;
        if (!results && prior[11] && prior[11] !== 'レース後に反映') {
          r[11] = prior[11]; r[12] = prior[12]; r[13] = prior[13];
        }
        if (!bets && String(prior[3]).indexOf('買い') === 0) {
          r[3] = prior[3];
          if (r[11] === prior[11] && String(prior[12]).indexOf('（的中）') >= 0 && String(r[12]).indexOf('（的中）') < 0) r[12] += '（的中）';
        }
      });
    } else notify = brFormatNotify_(nd.rows, results, bets);
    if (!results || !bets) notifyNote = '判定取得成功・結果/買い目の一部未取得';
  }
  syncTab('今日の判定', nd && nd.rows, notify, notifyNote);
  const md = brLatest_('morning_', today);
  syncTab('今日の朝の候補', md && md.rows, md && brFormatMorning_(md.rows));
  const bets = brFetchCsv_('all_bets.csv');
  syncTab('全部の買い目', bets, brFormatBets_(bets));
  const judged = brFetchCsv_('judged_all.csv');
  syncTab('判定の記録（結果つき）', judged, brFormatJudged_(judged));
  const dailySource = brFetchCsv_('daily.csv');
  const daily = brFormatDaily_(dailySource);
  const dsh = syncTab('日別の成績', dailySource, daily);
  brUpdateChart_(dsh, daily);
  brWriteSheet_(ss, '更新状況', status, true);
}

// 最初だけ1回手動実行：5分ごとの時計を登録（kick等の時計には触らない）
function brSetupBoatraceStats() {
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'brSyncBoatraceStats')
    .forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('brSyncBoatraceStats').timeBased().everyMinutes(5).create();
  brSyncBoatraceStats();
}
