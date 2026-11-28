function myFunction() {
  
}
const REPO = 'pocha2525/boatrace-ai';

// GitHub のワークフローを起動する。成功したら true を返す
function run_(wf) {
  const token = PropertiesService.getScriptProperties().getProperty('GH_TOKEN');
  const r = UrlFetchApp.fetch('https://api.github.com/repos/' + REPO +
    '/actions/workflows/' + wf + '.yml/dispatches', {
    method: 'post', contentType: 'application/json',
    headers: {Authorization: 'Bearer ' + token},
    payload: JSON.stringify({ref: 'main', inputs: {auto: '1'}}),
    muteHttpExceptions: true});
  Logger.log(wf + ' ' + r.getResponseCode());
  return r.getResponseCode() === 204;
}

// 1日1回だけ起動する（起動に成功した日を覚えておく。時刻の窓を外しても次の回で起動する）
function once_(wf, key, today) {
  const p = PropertiesService.getScriptProperties();
  if (p.getProperty(key) === today) return;
  if (run_(wf)) p.setProperty(key, today);
}

function kick() {
  const d = new Date();
  const h = Number(Utilities.formatDate(d, 'Asia/Tokyo', 'H'));
  const m = Number(Utilities.formatDate(d, 'Asia/Tokyo', 'm'));
  const today = Utilities.formatDate(d, 'Asia/Tokyo', 'yyyyMMdd');
  if (h >= 8) run_('predict');
  // 成績レポート：8時台以降、11時までに1回
  if (h >= 8 && h < 11) once_('report', 'REPORT_DAY', today);
  // 夜間の改善（成績表の更新、月曜はモデルの比較）：朝5時台〜6時台に1回
  if (h >= 5 && h < 7) once_('nightly', 'NIGHTLY_DAY', today);
  // 朝の候補：7:30以降、11時までに1回（8時台の判定より先に届くように）
  if ((h === 7 && m >= 30 || h > 7) && h < 11) once_('morning', 'MORNING_DAY', today);
  // 自動学習は止めている（方式A）。動かさないこと
  // const w = Utilities.formatDate(d, 'Asia/Tokyo', 'u');
  // if (w === '1' && h === 4 && m < 10) run_('train');
}

function setup() {
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'kick')
    .forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('kick').timeBased().everyMinutes(5).create();
  run_('predict');
}
