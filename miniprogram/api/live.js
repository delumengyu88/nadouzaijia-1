// Real searches use the existing single-program endpoint. Job progress is local.
var http = require('./http')
var state = require('./state')
var text = require('../utils/text')
var jobs = {}
var sequence = 0

function currentJobs() {
  var key = state.userId() || 'guest'
  if (!jobs[key]) jobs[key] = {}
  return jobs[key]
}
function describe(item) {
  var body = item.body
  if (!body) return { name: item.name, failed: !!item.error, badge: item.error ? '失败' : '查询中',
    badgeCls: 'bad', line: item.error || '' }
  var hit = body.outcome !== 'miss'
  // The server lists only platforms that may prefill 委托取证 (never generated-date rows).
  var platforms = (body.evidence_platforms || []).slice()
  return { name: item.name, hit: hit, missed: !hit, failed: false,
    badge: hit ? '已命中' : '未命中', badgeCls: hit ? 'ok' : 'muted',
    evidenceName: body.evidence_program_name || '',
    evidencePlatform: platforms.filter(function (p, i) { return platforms.indexOf(p) === i }).join('、'),
    line: !hit ? '当前索引未收录该节目名。' : body.outcome === 'recent_only' ? '有最新记录，可委托取证' :
      '命中 ' + body.records.length + ' 条 · 涉及 ' + body.platform_count + ' 个平台' }
}
function view(job) {
  if (!job) return null
  var finished = job.items.filter(function (item) { return item.body || item.error }).length
  var rows = job.items.map(describe)
  var hits = rows.filter(function (r) { return r.hit }).length
  var misses = rows.filter(function (r) { return r.missed }).length
  var fails = rows.filter(function (r) { return r.failed }).length
  var done = finished === rows.length
  return { id: job.id, queued: !job.started, running: !!job.started && !done, done: done,
    percent: Math.round(finished / rows.length * 100), items: rows,
    head: done ? '共 ' + rows.length + ' 个｜命中 ' + hits + '｜未命中 ' + misses + '｜失败 ' + fails : '已完成 ' + finished + ' / ' + rows.length,
    allFail: done && fails === rows.length, showFailBanner: done && fails > 0,
    failBanner: fails === rows.length ? '全部查询失败，请重试。' : '部分查询失败，请重试。',
    showRetryAll: done && fails > 0, itemRetry: true }
}
function advance(id) {
  var job = currentJobs()[id]
  if (!job || job.pending) return view(job)
  var item = job.items.filter(function (row) { return !row.body && !row.error })[0]
  if (!item) return view(job)
  job.started = true
  job.pending = true
  // Capture the caller before awaiting; account changes never share results.
  http.searchProgram(item.name).then(function (body) { item.body = body }).catch(function (error) {
    item.error = error.message || '查询失败，请重试'
  }).then(function () { job.pending = false })
  return view(job)
}
function retry(id, name) {
  var job = currentJobs()[id]
  if (!job) return null
  job.items.forEach(function (item) { if (item.error && (!name || item.name === name)) item.error = '' })
  return advance(id)
}
module.exports = {
  isLive: true,
  searchProgram: http.searchProgram,
  programMeta: function (name) { return { name: name } },
  createQuery: function (names) {
    var parsed = text.parseTitles(names.join('\n')).items
    if (!parsed.length) return { ok: false, message: '请先输入节目名' }
    var id = 'live-' + Date.now() + '-' + (++sequence)
    currentJobs()[id] = { id: id, items: parsed.map(function (item) { return { name: item.name } }) }
    return { ok: true, id: id }
  },
  getQuery: function (id) { return view(currentJobs()[id]) },
  advanceQuery: advance,
  retryItem: retry,
  retryAll: function (id) { return retry(id) },
  markQuerySeen: function () {},
  exportBook: function (name) {
    // Refresh permissions at export time instead of trusting a mock profile or stale report.
    return http.searchProgram(name).then(function (body) {
      if (require('../config').calibrating) return require('../utils/calibration').blockedResult()
      var rows = body.records.filter(function (r) { return !r.locked && r.can_download_report }).map(function (r) {
        return { platform: r.platform || '其他平台', subtype: r.platform_subtype || '', category: r.category || '',
          type: '', uploaded: name, matched: body.program_name, date: (r.checked_at || '').slice(0, 10) }
      })
      if (!rows.length) return { ok: false, message: '没有可导出的记录' }
      var book = require('../utils/xlsx')
      return { ok: true, count: rows.length, filename: book.filename(rows.length), bytes: book.build({ title: '那豆在架 命中结果',
        scope: '节目 ' + name, day: new Date().toISOString().slice(0, 10), rows: rows }) }
    })
  }
}
