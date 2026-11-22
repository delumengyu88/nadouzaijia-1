// Real searches use the existing single-program endpoint. Job progress is local.
var http = require('./http')
var state = require('./state')
var text = require('../utils/text')
var jobs = {}
var sequence = 0

// A search that never settles (request never sent, callback lost, sync throw) must not
// leave the page polling forever: after timeoutMs the item fails and can be retried.
class QueryGuard {
  static now() { return Date.now() }
  static timeoutMs() { return 60000 }
  static timeoutText() { return '查询超时，请重试' }
  static expire(job) {
    var pending = job && job.pending
    if (!pending || QueryGuard.now() - pending.sentAt < QueryGuard.timeoutMs()) return
    pending.item.error = QueryGuard.timeoutText()
    pending.item.timedOut = true
    job.pending = null
  }
  static send(name) {
    try {
      return Promise.resolve(http.searchProgram(name))
    } catch (error) {
      return Promise.reject(new Error('查询失败，请重试'))
    }
  }
}

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
  var platforms = body.records.filter(function (r) { return !r.locked && r.platform && r.platform !== '平台未识别' }).map(function (r) { return r.platform })
  return { name: item.name, hit: hit, missed: !hit, failed: false,
    badge: hit ? '已命中' : '未命中', badgeCls: hit ? 'ok' : 'muted',
    evidencePlatform: platforms.filter(function (p, i) { return platforms.indexOf(p) === i }).join('、'),
    line: !hit ? '当前索引未收录该节目名。' : body.outcome === 'recent_only' ? '有最新记录，可委托取证' :
      '命中 ' + body.records.length + ' 条 · 涉及 ' + body.platform_count + ' 个平台' }
}
function view(job) {
  if (!job) return null
  QueryGuard.expire(job)
  var finished = job.items.filter(function (item) { return item.body || item.error }).length
  var rows = job.items.map(describe)
  var hits = rows.filter(function (r) { return r.hit }).length
  var misses = rows.filter(function (r) { return r.missed }).length
  var fails = rows.filter(function (r) { return r.failed }).length
  var timedOut = job.items.some(function (item) { return item.timedOut })
  var done = finished === rows.length
  return { id: job.id, queued: !job.started, running: !!job.started && !done, done: done,
    percent: Math.round(finished / rows.length * 100), items: rows,
    head: done ? '共 ' + rows.length + ' 个｜命中 ' + hits + '｜未命中 ' + misses + '｜失败 ' + fails : '已完成 ' + finished + ' / ' + rows.length,
    allFail: done && fails === rows.length, showFailBanner: done && fails > 0,
    failBanner: timedOut ? QueryGuard.timeoutText() : fails === rows.length ? '全部查询失败，请重试。' : '部分查询失败，请重试。',
    showRetryAll: done && fails > 0, itemRetry: true }
}
function advance(id) {
  var job = currentJobs()[id]
  QueryGuard.expire(job)
  if (!job || job.pending) return view(job)
  var item = job.items.filter(function (row) { return !row.body && !row.error })[0]
  if (!item) return view(job)
  job.started = true
  var pending = { item: item, sentAt: QueryGuard.now() }
  job.pending = pending
  // Capture the caller before awaiting; account changes never share results.
  // A late answer from a timed-out attempt is dropped, so a retry never runs twice.
  QueryGuard.send(item.name).then(function (body) {
    if (job.pending === pending) item.body = body
  }, function (error) {
    if (job.pending === pending) item.error = (error && error.message) || '查询失败，请重试'
  }).then(function () { if (job.pending === pending) job.pending = null })
  return view(job)
}
function retry(id, name) {
  var job = currentJobs()[id]
  if (!job) return null
  job.items.forEach(function (item) {
    if (item.error && (!name || item.name === name)) { item.error = ''; item.timedOut = false }
  })
  return advance(id)
}
module.exports = {
  isLive: true,
  QueryGuard: QueryGuard,
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
        return { platform: r.platform || '平台未识别', subtype: r.platform_subtype || '', category: r.category || '',
          type: '', uploaded: name, matched: body.program_name, date: (r.checked_at || '').slice(0, 10) }
      })
      if (!rows.length) return { ok: false, message: '没有可导出的记录' }
      return { ok: true, filename: '汇总.xlsx', bytes: require('../utils/xlsx').build({ title: '那豆在架 命中结果',
        scope: '节目 ' + name, day: new Date().toISOString().slice(0, 10), rows: rows }) }
    })
  }
}
