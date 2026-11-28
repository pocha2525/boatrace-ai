// Run with: node tests/test_sheet_sync.js (no Apps Script access or credentials).
'use strict';
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function parseCsv(text) {
  const rows = []; let row = [], cell = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') {
      if (quoted && text[i + 1] === '"') { cell += '"'; i++; }
      else quoted = !quoted;
    } else if (!quoted && (c === ',' || c === '\n')) {
      row.push(cell.replace(/\r$/, '')); cell = '';
      if (c === '\n') { rows.push(row); row = []; }
    } else cell += c;
  }
  row.push(cell.replace(/\r$/, '')); rows.push(row); return rows;
}

function harness() {
  const sheets = new Map(), properties = new Map(), files = new Map();
  const events = [], propertyReads = [], triggers = [];
  let clock = Date.parse('2026-11-28T03:00:00Z'), busy = false, lockWait = 0, released = 0;
  class Clock extends Date {
    constructor(...args) { super(...(args.length ? args : [clock])); }
    static now() { return clock; }
  }
  class Range {
    constructor(sheet, row, col, rows = 1, cols = 1) { Object.assign(this, {sheet, row, col, rows, cols}); }
    mark(action) { events.push({sheet: this.sheet.name, action, row: this.row, col: this.col, rows: this.rows, cols: this.cols}); return this; }
    setValues(values) {
      assert.equal(values.length, this.rows);
      values.forEach((r, i) => {
        assert.equal(r.length, this.cols);
        r.forEach((value, j) => this.sheet.cells.set(`${this.row + i},${this.col + j}`, value));
      }); return this.mark('setValues');
    }
    getValues() {
      return Array.from({length: this.rows}, (_, i) => Array.from({length: this.cols}, (_, j) => this.sheet.cells.get(`${this.row + i},${this.col + j}`) ?? ''));
    }
    clearContent() {
      for (let r = this.row; r < this.row + this.rows; r++) for (let c = this.col; c < this.col + this.cols; c++) this.sheet.cells.delete(`${r},${c}`);
      return this.mark('clearContent');
    }
  }
  for (const method of ['setNumberFormat', 'setFontSize', 'setVerticalAlignment', 'setBackground', 'setFontColor', 'setFontWeight']) {
    Range.prototype[method] = function () { return this.mark(method); };
  }
  class Sheet {
    constructor(name) { this.name = name; this.cells = new Map(); this.charts = []; this.maxRows = 1000; this.maxCols = 26; }
    getRange(...args) { return new Range(this, ...args); }
    getDataRange() { return this.getRange(1, 1, Math.max(1, this.getLastRow()), Math.max(1, this.getLastColumn())); }
    getLastRow() { return Math.max(0, ...Array.from(this.cells.entries()).filter(([, v]) => v !== '').map(([k]) => +k.split(',')[0])); }
    getLastColumn() { return Math.max(0, ...Array.from(this.cells.entries()).filter(([, v]) => v !== '').map(([k]) => +k.split(',')[1])); }
    getMaxRows() { return this.maxRows; }
    getMaxColumns() { return this.maxCols; }
    insertRowsAfter(_, n) { this.maxRows += n; events.push({sheet: this.name, action: 'growRows'}); }
    insertColumnsAfter(_, n) { this.maxCols += n; events.push({sheet: this.name, action: 'growCols'}); }
    setFrozenRows() { events.push({sheet: this.name, action: 'freeze'}); }
    setColumnWidths() { events.push({sheet: this.name, action: 'widths'}); }
    setColumnWidth() { events.push({sheet: this.name, action: 'width'}); }
    getCharts() { return this.charts; }
    removeChart(chart) { this.charts = this.charts.filter(c => c !== chart); events.push({sheet: this.name, action: 'removeChart'}); }
    insertChart(chart) { this.charts.push(chart); events.push({sheet: this.name, action: 'insertChart'}); }
    newChart() {
      const builder = {};
      for (const name of ['setChartType', 'addRange', 'setMergeStrategy', 'setNumHeaders', 'setOption', 'setPosition']) builder[name] = () => builder;
      builder.build = () => ({}); return builder;
    }
    // No clear(), autoResizeColumns(), or setNote(): a regression fails immediately.
  }
  const spreadsheet = {getSheetByName: name => sheets.get(name) || null,
    insertSheet: name => { const sheet = new Sheet(name); sheets.set(name, sheet); return sheet; }};
  const scriptProperties = {
    getProperty: key => { propertyReads.push(key); return properties.get(key) ?? null; },
    setProperty: (key, value) => { properties.set(key, value); }
  };
  const context = vm.createContext({Date: Clock, console,
    Utilities: {parseCsv, DigestAlgorithm: {SHA_256: 'sha256'}, Charset: {UTF_8: 'utf8'},
      computeDigest: (_, value) => Array.from(crypto.createHash('sha256').update(value).digest()),
      formatDate: (value, zone, pattern) => {
        assert.equal(zone, 'Asia/Tokyo');
        const iso = new Date(value.getTime() + 9 * 3600000).toISOString();
        if (pattern === 'yyyyMMdd') return iso.slice(0, 10).replace(/-/g, '');
        return iso.slice(0, pattern.endsWith(':ss') ? 19 : 16).replace(/-/g, '/').replace('T', ' ');
      }},
    PropertiesService: {getScriptProperties: () => scriptProperties},
    SpreadsheetApp: {openByUrl: url => { assert.match(url, /1AtbhKouKMSq8ZbaoNmbSbIGsNHxtvU0GHr3VQ3uWlq4/); return spreadsheet; }},
    UrlFetchApp: {fetch: url => {
      const filename = url.slice(url.lastIndexOf('/') + 1).split('?')[0];
      const value = files.get(filename);
      if (value instanceof Error) throw value;
      return {getResponseCode: () => value === undefined ? 404 : 200, getContentText: () => value || ''};
    }},
    Charts: {ChartType: {LINE: 'LINE'}, ChartMergeStrategy: {MERGE_COLUMNS: 'MERGE_COLUMNS'}},
    LockService: {getScriptLock: () => ({tryLock: wait => { lockWait = wait; return !busy; }, releaseLock: () => { released++; }})},
    ContentService: {createTextOutput: value => value},
    ScriptApp: {getProjectTriggers: () => triggers, deleteTrigger: trigger => triggers.splice(triggers.indexOf(trigger), 1),
      newTrigger: handler => { const trigger = {handler, getHandlerFunction: () => handler}; const builder = {
        timeBased: () => builder, everyMinutes: minutes => { trigger.minutes = minutes; return builder; }, create: () => { triggers.push(trigger); }}; return builder; }}
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../gas/sheet.gs'), 'utf8'), context);
  return {context, sheets, properties, files, events, propertyReads, triggers, spreadsheet,
    tick: ms => { clock += ms; }, setBusy: value => { busy = value; }, lockWait: () => lockWait, released: () => released};
}

function seed(h) {
  h.files.set('notify_20261128.csv', 'notify_id,hd,jcd,venue,rno,deadline,decided_at,status,picks,odds,evs,reason\na,20261128,1,桐生,1,12:30,11:57:01,受付成功,1-2-3 1-3-2,10 20,1.2 1.1,');
  h.files.set('results_20261128.csv', 'jcd,rno,tri,tri_pay\n1,1,1-2-3,1000');
  h.files.set('20261128.csv', 'jcd,rno,pick\n1,1,1-2-3');
  h.files.set('morning_20261128.csv', 'hd,jcd,venue,rno,deadline,picks,probs\n20261128,1,桐生,1,12:30,1-2-3 1-3-2,0.2 0.15');
  h.files.set('all_bets.csv', '日付,会場,R,買い目,何点目,オッズ,期待値,結果,的中,払戻,精算\n20261128,桐生,1,1-2-3,1,10,1.2,1-2-3,1,1000,settled');
  h.files.set('judged_all.csv', '日付,締切,会場,R,判定,本命,本命当たり\n2026/11/28,12:30,桐生,1,買い,1-2-3,◎');
  h.files.set('daily.csv', '日付,点数,的中数,投資,払戻,回収率%,累計回収率%,累計収支\n2026/11/28,1,1,100,1000,1000,1000,900');
}
function values(h, name) { return h.sheets.get(name).getDataRange().getValues(); }
function run(name, fn) { fn(); console.log(`ok - ${name}`); }

run('unchanged data tabs have no writes, formatting, clears, or chart changes', () => {
  const h = harness(); seed(h); assert.equal(h.context.brSyncBoatraceStats(), true);
  assert.ok(h.lockWait() >= 1000 && h.lockWait() <= 3000);
  assert.equal(h.released(), 1);
  h.events.length = 0; h.tick(300000); h.context.brSyncBoatraceStats();
  assert.deepEqual(h.events.filter(e => e.sheet !== '更新状況'), []);
  assert.equal(h.events.filter(e => e.sheet === '更新状況' && e.action === 'setValues').length, 1);
  assert.ok(h.propertyReads.every(key => key.startsWith('BR_'))); // no reading GAS secrets
});
run('missing, empty, failed and header-only CSV keep every prior data tab', () => {
  const h = harness(); seed(h); h.context.brSyncBoatraceStats();
  const before = Array.from(h.sheets).filter(([n]) => n !== '更新状況').map(([n]) => [n, JSON.stringify(values(h, n))]);
  h.files.clear(); h.files.set('all_bets.csv', ''); h.files.set('daily.csv', new Error('network unavailable'));
  h.files.set('judged_all.csv', '日付,締切'); h.events.length = 0; h.tick(300000);
  assert.equal(h.context.brSyncBoatraceStats(), true);
  for (const [name, old] of before) assert.equal(JSON.stringify(values(h, name)), old);
  assert.deepEqual(h.events.filter(e => e.sheet !== '更新状況'), []);
  assert.ok(values(h, '更新状況').slice(1).every(r => r[3].includes('保持')));
});
run('a missing current-day file never replaces a newer display with yesterday', () => {
  const h = harness(); seed(h); h.context.brSyncBoatraceStats();
  const old = JSON.stringify(values(h, '今日の判定'));
  h.files.set('notify_20261127.csv', h.files.get('notify_20261128.csv').replaceAll('20261128', '20261127'));
  h.files.delete('notify_20261128.csv'); h.tick(300000); h.context.brSyncBoatraceStats();
  assert.equal(JSON.stringify(values(h, '今日の判定')), old);
  assert.match(values(h, '更新状況')[1][3], /最新日の取得不可/);
});
run('temporarily missing auxiliary CSV preserves confirmed results and purchased tickets', () => {
  const h = harness(); seed(h); h.context.brSyncBoatraceStats();
  const old = values(h, '今日の判定')[1];
  h.files.delete('results_20261128.csv'); h.files.delete('20261128.csv');
  h.tick(300000); h.context.brSyncBoatraceStats();
  assert.deepEqual(values(h, '今日の判定')[1], old);
  assert.match(values(h, '更新状況')[1][3], /一部未取得/);
});
run('display caps keep newest 300 logs and newest 500 daily rows, without changing source', () => {
  const h = harness();
  const bets = [['日付', '会場', 'R', '買い目']];
  const judged = [['日付', '会場', 'R']];
  const notify = [['notify_id', 'hd', 'rno', 'status']];
  const daily = [['日付', '累計収支']];
  for (let i = 1; i <= 700; i++) {
    bets.push(['20261128', '桐生', String(i), '1-2-3']);
    judged.push(['2026/11/28', '桐生', String(i)]);
    notify.push([String(i), '20261128', String(i), '条件未達']);
    daily.push([String(i), String(i)]);
  }
  const originals = JSON.stringify([bets, judged, notify, daily]);
  for (const [format, rows] of [['brFormatBets_', bets], ['brFormatJudged_', judged], ['brFormatNotify_', notify]]) {
    const result = h.context[format](rows);
    assert.equal(result.length, 301); assert.match(String(result[1][format === 'brFormatBets_' ? 1 : 2]), /700R$/);
  }
  const result = h.context.brFormatDaily_(daily);
  assert.equal(result.length, 501); assert.equal(result[1][0], '201'); assert.equal(result[500][0], '700');
  assert.equal(JSON.stringify([bets, judged, notify, daily]), originals);
});
run('changed range writes once; only surplus cells clear; stable chart extent is reused', () => {
  const h = harness();
  h.context.brWriteSheet_(h.spreadsheet, 'sample', [['a', 'b', 'c'], ['1', '2', '3'], ['4', '5', '6']], true);
  h.events.length = 0;
  h.context.brWriteSheet_(h.spreadsheet, 'sample', [['a', 'b'], ['new', 'value']], true);
  assert.equal(h.events.filter(e => e.action === 'setValues').length, 1);
  assert.deepEqual(h.events.filter(e => e.action === 'clearContent').map(e => [e.row, e.col, e.rows, e.cols]), [[3, 1, 1, 3], [1, 3, 2, 1]]);
  assert.deepEqual(values(h, 'sample'), [['a', 'b'], ['new', 'value']]);
  seed(h); h.context.brSyncBoatraceStats(); h.events.length = 0;
  h.files.set('daily.csv', h.files.get('daily.csv').replace(',900', ',950'));
  h.tick(300000); h.context.brSyncBoatraceStats();
  assert.equal(h.events.filter(e => e.sheet === '日別の成績' && e.action === 'setValues').length, 1);
  assert.ok(!h.events.some(e => e.action === 'insertChart' || e.action === 'removeChart' || e.action === 'widths'));
  assert.ok(!h.events.some(e => e.sheet === '日別の成績' && e.action === 'setNumberFormat'));
});
run('busy lock returns quickly without fetching or writing and doGet still checks key', () => {
  const h = harness(); h.setBusy(true);
  assert.equal(h.context.brSyncBoatraceStats(), false); assert.equal(h.lockWait(), 2000); assert.equal(h.released(), 0); assert.equal(h.events.length, 0);
  assert.equal(h.context.doGet({parameter: {key: 'wrong'}}), 'ng');
  h.properties.set('SYNC_KEY', 'test-only');
  assert.equal(h.context.doGet({parameter: {key: 'wrong'}}), 'ng');
  assert.equal(h.context.doGet({parameter: {key: 'test-only'}}), 'busy');
});
run('freshness distinguishes successful sync from actual source timestamp and date-only data', () => {
  const h = harness(); seed(h); h.context.brSyncBoatraceStats();
  let status = values(h, '更新状況');
  assert.equal(status[1][1], '2026/11/28 12:00:00');
  assert.equal(status[1][2], '2026/11/28 11:57:01');
  assert.match(status[2][2], /2026\/11\/28.*日付のみ.*時刻不明/);
  h.tick(300000); h.context.brSyncBoatraceStats(); status = values(h, '更新状況');
  assert.equal(status[1][1], '2026/11/28 12:05:00'); assert.equal(status[1][2], '2026/11/28 11:57:01');
});
run('setup replaces only the existing sync trigger with five-minute cadence', () => {
  const h = harness(); seed(h);
  h.triggers.push({getHandlerFunction: () => 'kick'}, {getHandlerFunction: () => 'brSyncBoatraceStats'});
  h.context.brSetupBoatraceStats();
  assert.equal(h.triggers.length, 2);
  assert.equal(h.triggers.filter(t => t.getHandlerFunction() === 'kick').length, 1);
  assert.equal(h.triggers.find(t => t.handler === 'brSyncBoatraceStats').minutes, 5);
});
console.log('All sheet sync tests passed.');
