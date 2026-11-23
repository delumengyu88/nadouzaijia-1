// Real searches use the existing single-program endpoint. Job progress is local.
var http = require('./http')
var state = require('./state')
var text = require('../utils/text')
var config = require('../config')
var jobs = {}
var knownQueries = {}
var seenOnce = {}

function rememberQuery(id) {
  if (id) knownQueries[id] = state.queryOwner()
}
function queryError(message, statusCode) {
  var error = new Error(message)
  error.statusCode = statusCode
  return error
}
function queryRequest(path, data, method, binary) {
  var base = String(config.apiBase || '').replace(/\/$/, '')
  if (!base) return Promise.reject(queryError('未配置服务地址', 0))
  var readToken = binary ? state.token : state.queryToken
  var token = readToken()
  if (!token) {
    if (!binary && (!method || method === 'GET') && state.pendingGuestToken()) {
      return require('./auth').querySession().then(function () { return queryRequest(path, data, method, binary) })
    }
    return Promise.reject(queryError('请先登录', 401))
  }
  return new Promise(function (resolve, reject) {
    function sessionChanged() {
      if (readToken() === token) return false
      var error = queryError('登录状态已变化', 0)
      error.sessionChanged = true
      reject(error)
      return true
    }
    try {
      wx.request({
        url: base + path,
        method: method || 'GET',
        timeout: 15000,
        data: data || {},
        responseType: binary ? 'arraybuffer' : 'text',
        dataType: binary ? 'text' : 'json',
        header: { Authorization: 'Bearer ' + token },
        success: function (res) {
          if (sessionChanged()) return
          var code = res.statusCode
          if (code >= 200 && code < 300) {
            if (path === '/api/v1/queries' && method === 'POST') {
              var headers = res.header || {}
              var field = Object.keys(headers).find(function (name) { return name.toLowerCase() === 'x-nadou-session-expires-at' })
              if (field) state.extendGuest(token, headers[field])
            }
            resolve(binary ? res : res.data || {})
            return
          }
          if (code === 401) state.clearSession(token)
          var message = '服务暂不可用'
          if (code === 401) message = '请先登录'
          else if (code === 403) message = '当前账户无权执行此操作'
          else if (code === 404) message = '记录不存在'
          else if (code >= 500) message = '网络异常，请重试'
          reject(queryError(message, code))
        },
        fail: function () { if (!sessionChanged()) reject(queryError('网络异常，请重试', 0)) }
      })
    } catch (err) {
      reject(queryError('网络异常，请重试', 0))
    }
  })
}
function postResult(id, name, failed) {
  if (!id || knownQueries[id] !== state.queryOwner()) return
  var body = { name: name }
  if (failed) body.failed = true
  queryRequest('/api/v1/queries/' + encodeURIComponent(id) + '/results', body, 'POST').then(function () {}, function () {})
}
function syncReports(job) {
  if (!job || !job.items) return
  job.items.forEach(function (item) {
    if (item.reported) return
    if (!item.body && !item.error) return
    item.reported = true
    postResult(job.id, item.name, !item.body)
  })
}
function recordLimit(value) {
  var limit = parseInt(value, 10)
  if (!limit || limit < 1) limit = 20
  if (limit > 100) limit = 100
  return limit
}
function listRecordPage(limit, offset) {
  limit = recordLimit(limit)
  return queryRequest('/api/v1/queries', { limit: limit, offset: offset }, 'GET').then(function (body) {
    var items = body && body.items ? body.items : []
    var rows = items.map(function (row) {
      if (row && row.id) rememberQuery(row.id)
      return {
        id: row && row.id,
        name: row && row.name || '',
        badge: row && row.badge || '',
        badgeCls: row && row.badgeCls || '',
        line: row && row.line || ''
      }
    })
    var hasMore = body && typeof body.total === 'number' ? offset + rows.length < body.total : rows.length === limit
    return { rows: rows, hasMore: rows.length > 0 && hasMore }
  })
}
function sanitizeDetail(detail) {
  var src = detail || {}
  var items = (src.items || []).map(function (item) {
    var row = {
      name: item && item.name || '',
      badge: item && item.badge || '',
      badgeCls: item && item.badgeCls || '',
      line: item && item.line || '',
      hit: !!(item && item.hit),
      missed: !!(item && item.missed),
      failed: !!(item && item.failed)
    }
    if (item && item.unverified) {
      return { name: row.name, unverified: true, hit: false, missed: false, failed: false,
        badge: '待核验', badgeCls: 'muted', line: row.line || '数据待核验' }
    }
    if (item && item.evidenceName) row.evidenceName = item.evidenceName
    if (item && item.evidencePlatform) row.evidencePlatform = item.evidencePlatform
    return row
  })
  var failBanner = src.failBanner || ''
  return {
    id: src.id,
    queued: !!src.queued,
    running: !!src.running && !src.done,
    done: !!src.done,
    head: src.head || '',
    percent: typeof src.percent === 'number' ? src.percent : 0,
    items: items,
    failCount: src.failCount || 0,
    showRetryAll: !!src.showRetryAll,
    showFailBanner: !!src.showFailBanner && !!failBanner,
    allFail: !!src.allFail,
    failBanner: failBanner,
    itemRetry: !!src.itemRetry,
    networkError: false,
    showNetworkRetry: false
  }
}

// A search that never settles (request never sent, callback lost, sync throw) must not
// leave the page polling forever: after timeoutMs the item fails and can be retried.
class QueryGuard {
  static now() { return Date.now() }
  static timeoutMs() { return 60000 }
  static timeoutText() { return '查询超时，请重试' }
  static networkText() { return '网络异常，请重试' }
  static expire(job) {
    var pending = job && job.pending
    if (!pending || QueryGuard.now() - pending.sentAt < QueryGuard.timeoutMs()) return
    // Stop the whole queue. Marking only the in-flight row would let the next
    // tick start another title, so the timeout banner would never appear.
    job.items.forEach(function (item) {
      if (item.body || item.error) return
      item.error = QueryGuard.timeoutText()
      item.timedOut = true
      item.network = false
    })
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
  var key = state.queryOwner() || 'guest'
  if (!jobs[key]) jobs[key] = {}
  return jobs[key]
}
function describe(item) {
  var body = item.body
  if (!body) return { name: item.name, failed: !!item.error, badge: item.error ? '失败' : '查询中',
    badgeCls: 'bad', line: item.error || '' }
  if (body.outcome === 'unverified') {
    return { name: item.name, unverified: true, hit: false, missed: false, failed: false,
      badge: '待核验', badgeCls: 'muted', line: '数据待核验' }
  }
  var hit = body.outcome === 'hit' || body.outcome === 'recent_only'
  // The server lists only platforms that may prefill 委托取证 (never generated-date rows).
  var platforms = (body.evidence_platforms || []).slice()
  var row = { name: item.name, hit: hit, missed: !hit, failed: false,
    badge: hit ? '已命中' : '未命中', badgeCls: hit ? 'ok' : 'muted',
    line: !hit ? '当前索引未收录该节目名。' : body.outcome === 'recent_only' ? '有最新记录，可委托取证' :
      '命中 ' + body.records.length + ' 条 · 涉及 ' + body.platform_count + ' 个平台' }
  if (body.evidence_program_name) row.evidenceName = body.evidence_program_name
  var platform = platforms.filter(function (p, i) { return platforms.indexOf(p) === i }).join('、')
  if (platform) row.evidencePlatform = platform
  return row
}
function view(job) {
  if (!job) return null
  QueryGuard.expire(job)
  syncReports(job)
  var finished = job.items.filter(function (item) { return item.body || item.error }).length
  var rows = job.items.map(describe)
  var hits = rows.filter(function (r) { return r.hit }).length
  var misses = rows.filter(function (r) { return r.missed }).length
  var unverified = rows.filter(function (r) { return r.unverified }).length
  var fails = rows.filter(function (r) { return r.failed }).length
  var timedOut = job.items.some(function (item) { return item.timedOut })
  var network = job.items.some(function (item) { return item.network })
  var progressed = job.items.filter(function (item) { return item.body }).length
  // Network failures still settle the row, so calibration and retries can see a
  // finished job, but they do not count as progress and do not open the results list.
  var done = finished === rows.length
  var count = network ? progressed : finished
  return { id: job.id, queued: !job.started && !network, running: !!job.started && !done && !network, done: done,
    networkError: network, showNetworkRetry: network,
    percent: rows.length ? Math.round(count / rows.length * 100) : 0, items: rows,
    head: done && !network ? '共 ' + rows.length + ' 个｜命中 ' + hits + '｜未命中 ' + misses + '｜失败 ' + fails + (unverified ? '｜待核验 ' + unverified : '') : '已完成 ' + count + ' / ' + rows.length,
    allFail: done && fails === rows.length, showFailBanner: done && fails > 0,
    failBanner: network ? QueryGuard.networkText() : timedOut ? QueryGuard.timeoutText() : fails === rows.length ? '全部查询失败，请重试。' : '部分查询失败，请重试。',
    showRetryAll: done && fails > 0, itemRetry: true }
}
function networkHeld(job) {
  return !!(job && job.items.some(function (item) { return item.network }))
}
function applyFailure(item, error) {
  var message = (error && error.message) || '查询失败，请重试'
  item.error = message
  item.network = message === QueryGuard.networkText()
  item.timedOut = false
}
function advance(id) {
  var job = currentJobs()[id]
  QueryGuard.expire(job)
  if (!job || job.pending || networkHeld(job)) return view(job)
  var item = job.items.filter(function (row) { return !row.body && !row.error })[0]
  if (!item) return view(job)
  job.started = true
  var pending = { item: item, sentAt: QueryGuard.now() }
  job.pending = pending
  // Capture the caller before awaiting; account changes never share results.
  // A late answer is applied only while this attempt is still the active one,
  // so a timed-out or retried request cannot run twice or overwrite the new one.
  QueryGuard.send(item.name).then(function (body) {
    if (job.pending !== pending) return
    if (body) {
      item.body = body
      item.error = ''
      item.network = false
      item.timedOut = false
    } else applyFailure(item, new Error('查询失败，请重试'))
  }, function (error) {
    if (job.pending === pending) applyFailure(item, error)
  }).then(function () {
    if (job.pending === pending) job.pending = null
    if (currentJobs()[id] === job) syncReports(job)
  })
  return view(job)
}
function retry(id, name) {
  var job = currentJobs()[id]
  if (!job || job.pending) return view(job)
  job.items.forEach(function (item) {
    if (item.error && (!name || item.name === name)) {
      item.error = ''
      item.timedOut = false
      item.network = false
      item.reported = false
    }
  })
  return advance(id)
}
module.exports = {
  isLive: true,
  QueryGuard: QueryGuard,
  searchProgram: http.searchProgram,
  programMeta: function (name) { return { name: name } },
  createQuery: function (names) {
    var parsed = text.parseTitles((names || []).join('\n')).items
    if (!parsed.length) return Promise.resolve({ ok: false, message: '请先输入节目名' })
    var titles = parsed.map(function (item) { return item.name })
    var payload = titles.length === 1 ? { name: titles[0] } : { names: titles }
    return queryRequest('/api/v1/queries', payload, 'POST').then(function (detail) {
      var id = detail && detail.id
      if (!id) return { ok: false, message: '服务暂不可用' }
      rememberQuery(id)
      currentJobs()[id] = { id: id, items: titles.map(function (name) { return { name: name } }) }
      return { ok: true, id: id }
    }, function (error) {
      if (error && error.sessionChanged) return { ok: false, sessionChanged: true }
      return { ok: false, message: (error && error.message) || '网络异常，请重试', statusCode: error && error.statusCode }
    })
  },
  getQuery: function (id) {
    if (!id) return null
    var job = currentJobs()[id]
    if (job) return view(job)
    return queryRequest('/api/v1/queries/' + encodeURIComponent(id), {}, 'GET').then(function (detail) {
      rememberQuery(id)
      return sanitizeDetail(detail)
    })
  },
  advanceQuery: advance,
  retryItem: retry,
  retryAll: function (id) { return retry(id) },
  markQuerySeen: function (id) {
    if (!id || knownQueries[id] !== state.queryOwner() || seenOnce[id] === state.queryOwner()) return
    seenOnce[id] = state.queryOwner()
    queryRequest('/api/v1/queries/' + encodeURIComponent(id) + '/seen', {}, 'POST').then(function () {}, function () {})
  },
  listRecordPage: listRecordPage,
  listRecords: function (limit) {
    return listRecordPage(limit, 0).then(function (page) { return page.rows })
  },
  exportBook: function (name, queryId) {
    if (!state.token() && state.queryToken()) return Promise.resolve({ ok: false, message: '当前身份无权导出报告' })
    function build() {
      // Refresh permissions at export time instead of trusting a mock profile or stale report.
      return http.searchProgram(name).then(function (body) {
        if (config.calibrating) return require('../utils/calibration').blockedResult()
        if (body.outcome === 'unverified') return { ok: false, message: '数据待核验' }
        var rows = body.records.filter(function (r) { return !r.locked && r.can_download_report }).map(function (r) {
          return { platform: r.platform || '其他平台', subtype: r.platform_subtype || '', category: r.category || '',
            type: '', uploaded: name, matched: body.program_name, date: require('../utils/date').beijingTime(r.checked_at).slice(0, 10) }
        })
        if (!rows.length) return { ok: false, message: '没有可导出的记录' }
        var book = require('../utils/xlsx')
        return { ok: true, count: rows.length, filename: book.filename(rows.length), bytes: book.build({ title: '那豆在架 命中结果',
          scope: '节目 ' + name, day: require('../utils/date').today(), rows: rows }) }
      })
    }
    if (!queryId) return build()
    return queryRequest('/api/v1/queries/' + encodeURIComponent(queryId) + '/program-export', { program_name: name }, 'GET', true).then(function (response) {
      return require('../utils/report-file').scopedFile(response)
    }, function (error) {
      if (error && error.statusCode === 404) return { ok: false, message: '记录不存在' }
      return Promise.reject(error)
    })
  }
}
