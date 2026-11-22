// 查询记录：四个调用时机、写失败不打断检索、空列表、401、404、null 字段、四种 badge。
var assert = require('assert')
var fs = require('fs')
var path = require('path')
var config = require('../miniprogram/config')
config.mode = 'hybrid'
config.calibrating = false
config.trialMode = false
config.apiBase = '/test-api'

var calls = []
var toasts = []
var redirects = []
var tabs = []
var backs = []
var route = function (opt) { opt.success({ statusCode: 200, data: {} }) }
var store = {}
global.getCurrentPages = function () { return [1, 2] }
global.wx = {
  getStorageSync: function (k) { return store[k] },
  setStorageSync: function (k, v) { store[k] = v },
  getSystemInfoSync: function () { return { statusBarHeight: 20, windowWidth: 375 } },
  showToast: function (opt) { toasts.push(opt) },
  redirectTo: function (opt) { redirects.push(opt.url) },
  switchTab: function (opt) { tabs.push(opt.url) },
  navigateTo: function (opt) { redirects.push(opt.url) },
  navigateBack: function () { backs.push(1) },
  login: function (opt) { if (opt && opt.success) opt.success({ code: 'records-code' }) },
  request: function (opt) {
    calls.push(opt)
    route(opt)
  }
}
function drain() { return new Promise(function (resolve) { setImmediate(resolve) }) }
global.setInterval = function () { return 1 }
global.clearInterval = function () {}
function hitBody(name) {
  return {
    program_name: name, outcome: 'hit', platform_count: 1,
    evidence_platforms: ['真实测试平台'], evidence_program_name: name,
    platform_groups: [{ records: [{ record_id: 'real-1', locked: false, platform: '真实测试平台', access: 'full',
      checked_at: '2026-01-02T03:04:05+08:00', can_download_report: true }] }],
    locked_records: []
  }
}
function urls(part) {
  return calls.filter(function (opt) { return (opt.url || '').indexOf(part) !== -1 })
}

var defs = {}
function loadPage(rel) {
  global.Page = function (def) { defs[rel] = def }
  var file = require.resolve('../miniprogram/' + rel)
  delete require.cache[file]
  require(file)
}
loadPage('pages/records/index')
loadPage('pages/query/index')
loadPage('pages/input/index')
loadPage('pages/report/index')
function makePage(rel) {
  var def = defs[rel]
  var page = { data: Object.assign({}, def.data || {}), query: {} }
  Object.keys(def).forEach(function (key) {
    if (typeof def[key] === 'function') page[key] = def[key]
  })
  page.setData = function (value) { Object.assign(this.data, value) }
  return page
}
var api = require('../miniprogram/api')
var state = require('../miniprogram/api/state')
var root = path.join(__dirname, '../miniprogram')
var recordsWxml = fs.readFileSync(path.join(root, 'pages/records/index.wxml'), 'utf8')
var recordsWxss = fs.readFileSync(path.join(root, 'pages/records/index.wxss'), 'utf8')
var queryWxml = fs.readFileSync(path.join(root, 'pages/query/index.wxml'), 'utf8')

function session(id) {
  state.setSession({
    token: (id || 'rec') + '-token',
    expires_at: new Date(Date.now() + 86400000).toISOString(),
    user: { id: id || 'rec', tier: 'free' }
  })
}
function resetTraffic() {
  calls = []
  toasts = []
  redirects = []
  tabs = []
  backs = []
}

async function main() {
  api.reset()
  session()
  assert(recordsWxml.indexOf('加载失败') === -1, 'records page must not say 加载失败')
  assert(recordsWxml.indexOf('还没有查询记录') !== -1)
  assert(recordsWxml.indexOf('去查询') !== -1)
  assert(recordsWxml.indexOf('网络异常，请重试') !== -1)
  assert(recordsWxml.indexOf('>重试<') !== -1)
  assert(recordsWxml.indexOf('tag {{item.badgeCls}}') !== -1)
  assert(recordsWxml.indexOf('record-name') !== -1)
  assert(recordsWxss.indexOf('text-overflow: ellipsis') !== -1)
  assert(queryWxml.indexOf('记录不存在') !== -1)
  assert(queryWxml.indexOf('返回') !== -1)
  assert(recordsWxml.indexOf('登录后继续') !== -1)
  assert(recordsWxml.indexOf('微信登录') !== -1)

  // 1. createQuery：一条用 {name}，多条用 {names}，之后用服务端 id。
  var createdId = ''
  route = function (opt) {
    if ((opt.method || 'GET') === 'POST' && /\/api\/v1\/queries$/.test(opt.url || '')) {
      createdId = opt.data && opt.data.name ? 'one-id' : 'many-id'
      opt.success({ statusCode: 201, data: { id: createdId, queued: true, items: [], networkError: null, showNetworkRetry: null, failBanner: null, seen_at: null } })
      return
    }
    opt.success({ statusCode: 200, data: {} })
  }
  resetTraffic()
  var one = await api.createQuery(['星河旧梦'])
  assert.strictEqual(one.ok, true)
  assert.strictEqual(one.id, 'one-id')
  assert.strictEqual(calls[0].method, 'POST')
  assert.strictEqual(calls[0].url, '/test-api/api/v1/queries')
  assert.deepStrictEqual(calls[0].data, { name: '星河旧梦' })
  assert.strictEqual(calls[0].header.Authorization, 'Bearer rec-token')
  resetTraffic()
  var many = await api.createQuery(['星河旧梦', '雾港来信'])
  assert.strictEqual(many.id, 'many-id')
  assert.deepStrictEqual(calls[0].data, { names: ['星河旧梦', '雾港来信'] })

  // 2. 检索成功上报 {name}；失败上报 {name, failed:true}。写记录失败不打断检索、不弹错、不重复提交。
  var failWrites = true
  route = function (opt) {
    var url = opt.url || ''
    if ((opt.method || 'GET') === 'POST' && /\/api\/v1\/queries$/.test(url)) {
      opt.success({ statusCode: 201, data: { id: 'job-hit', queued: true, items: [], networkError: null, showNetworkRetry: null } })
      return
    }
    if (url.indexOf('/programs/search') !== -1) {
      if (opt.data.name === '失败片') { opt.fail(); return }
      opt.success({ statusCode: 200, data: hitBody(opt.data.name) })
      return
    }
    if (url.indexOf('/results') !== -1 || url.indexOf('/seen') !== -1) {
      if (failWrites) { opt.fail(); return }
      opt.success({ statusCode: 200, data: {} })
      return
    }
    opt.success({ statusCode: 200, data: {} })
  }
  resetTraffic()
  var hit = await api.createQuery(['星河旧梦'])
  api.advanceQuery(hit.id)
  await drain()
  var hitView = api.getQuery(hit.id)
  assert(hitView.items[0].hit, 'search result still shows after the record write fails')
  assert.strictEqual(hitView.networkError, false)
  assert.strictEqual(toasts.length, 0)
  var resultPosts = urls('/results')
  assert.strictEqual(resultPosts.length, 1)
  assert.deepStrictEqual(resultPosts[0].data, { name: '星河旧梦' })
  api.getQuery(hit.id)
  await drain()
  assert.strictEqual(urls('/results').length, 1, 'a failed result write is not sent again')

  resetTraffic()
  route = function (opt) {
    var url = opt.url || ''
    if ((opt.method || 'GET') === 'POST' && /\/api\/v1\/queries$/.test(url)) {
      opt.success({ statusCode: 201, data: { id: 'job-fail', queued: true, items: [] } })
      return
    }
    if (url.indexOf('/programs/search') !== -1) { opt.fail(); return }
    if (url.indexOf('/results') !== -1) { opt.fail(); return }
    opt.success({ statusCode: 200, data: {} })
  }
  var failed = await api.createQuery(['失败片'])
  api.advanceQuery(failed.id)
  await drain()
  var failedView = api.getQuery(failed.id)
  assert(failedView.networkError && failedView.showNetworkRetry && failedView.items[0].failed)
  assert.strictEqual(failedView.failBanner, '网络异常，请重试')
  assert.deepStrictEqual(urls('/results')[0].data, { name: '失败片', failed: true })
  assert.strictEqual(toasts.length, 0)
  api.getQuery(failed.id)
  await drain()
  assert.strictEqual(urls('/results').length, 1, 'a failed failure-report is not sent again')
  failedView = api.getQuery(failed.id)
  assert.strictEqual(failedView.networkError, true, 'server null must not clear the client network flag')
  assert.strictEqual(failedView.showNetworkRetry, true)

  // 直接检索（报告页、导出刷新）不写查询记录。
  route = function (opt) {
    if ((opt.url || '').indexOf('/programs/search') !== -1) {
      opt.success({ statusCode: 200, data: hitBody(opt.data.name) })
      return
    }
    if ((opt.url || '').indexOf('/results') !== -1) throw new Error('plain search must not record a result')
    opt.success({ statusCode: 200, data: {} })
  }
  await api.searchProgram('星河旧梦')

  // 3. 结果页第一次看到完成时标记 seen，失败也不重复。
  resetTraffic()
  var seenFails = true
  route = function (opt) {
    var url = opt.url || ''
    if ((opt.method || 'GET') === 'POST' && /\/api\/v1\/queries$/.test(url)) {
      opt.success({ statusCode: 201, data: { id: 'job-seen', queued: true, items: [] } })
      return
    }
    if (url.indexOf('/programs/search') !== -1) {
      opt.success({ statusCode: 200, data: hitBody(opt.data.name) })
      return
    }
    if (url.indexOf('/seen') !== -1) {
      if (seenFails) { opt.fail(); return }
      opt.success({ statusCode: 200, data: {} })
      return
    }
    opt.success({ statusCode: 200, data: {} })
  }
  var seenJob = await api.createQuery(['星河旧梦'])
  var queryPage = makePage('pages/query/index')
  queryPage.onLoad({ id: seenJob.id })
  queryPage.onShow()
  await drain()
  queryPage.tick()
  await drain()
  assert(queryPage.data.done && queryPage.data.items[0].hit)
  assert.strictEqual(urls('/seen').length, 1)
  assert(urls('/seen')[0].url.indexOf('/api/v1/queries/job-seen/seen') !== -1)
  assert.strictEqual(urls('/seen')[0].method, 'POST')
  assert.strictEqual(toasts.length, 0)
  queryPage.refresh()
  queryPage.tick()
  await drain()
  assert.strictEqual(urls('/seen').length, 1, 'seen is posted once')

  // 4. 空列表是成功，不是加载失败。limit 默认 20，最大 100。
  resetTraffic()
  session()
  route = function (opt) {
    assert.strictEqual(opt.method || 'GET', 'GET')
    assert.strictEqual(opt.url, '/test-api/api/v1/queries')
    opt.success({ statusCode: 200, data: { items: [], total: 0, limit: opt.data.limit, offset: 0 } })
  }
  var listed = api.listRecords()
  var clamped = api.listRecords(500)
  await drain()
  assert.strictEqual(calls[0].data.limit, 20)
  assert.strictEqual(calls[0].data.offset, 0)
  assert.strictEqual(calls[1].data.limit, 100)
  assert.deepStrictEqual(await listed, [])
  var records = makePage('pages/records/index')
  records.onShow()
  assert.strictEqual(records.data.loading, true)
  assert.strictEqual(records.data.rows.length, 0)
  assert.strictEqual(records.data.empty, false)
  assert.strictEqual(records.data.failed, false)
  await drain()
  assert.strictEqual(records.data.loading, false)
  assert.strictEqual(records.data.empty, true)
  assert.strictEqual(records.data.failed, false)
  assert.strictEqual(records.data.needLogin, false)
  records.goQuery()
  assert.strictEqual(tabs[tabs.length - 1], '/pages/home/index')

  assert(recordsWxml.includes('catchtap="requestEvidence"'))
  var openedForms = []
  wx.navigateTo = function (opt) { openedForms.push(opt.url) }
  var batchRows = Array.from({ length: 26 }, function (_, i) {
    return { id: 'batch', name: '节目&' + i, badge: ['已命中', '未命中', '失败'][i % 3], line: '' }
  })
  batchRows[24].name = ''
  function listPage(opt) {
    var start = opt.data.offset
    opt.success({ statusCode: 200, data: { items: batchRows.slice(start, start + opt.data.limit), total: batchRows.length } })
  }
  route = listPage
  records.onShow()
  await drain()
  assert.strictEqual(records.data.rows.length, 20)
  assert(records.data.hasMore)
  var firstCalls = calls.length
  for (var i of [0, 1, 2]) {
    records.requestEvidence({ currentTarget: { dataset: { index: i } } })
    var formUrl = openedForms[openedForms.length - 1]
    assert.strictEqual(formUrl, '/pages/evidence/index?fromRecord=1&kind=normal&name=' + encodeURIComponent(batchRows[i].name))
    records.onShow()
  }
  assert.strictEqual(calls.length, firstCalls, 'opening forms never submits or refreshes the list on return')
  route = function (opt) { opt.fail() }
  records.loadMore()
  await drain()
  assert(records.data.moreError && records.data.hasMore)
  assert.strictEqual(records.data.rows.length, 20)
  route = listPage
  records.loadMore()
  records.loadMore()
  await drain()
  assert.strictEqual(calls[calls.length - 1].data.offset, 19)
  assert.strictEqual(calls[calls.length - 1].data.limit, 21)
  assert.deepStrictEqual(records.data.rows.map(row => row.name), batchRows.map(row => row.name))
  assert(!records.data.hasMore)
  records.requestEvidence({ currentTarget: { dataset: { index: 25 } } })
  assert(openedForms.pop().endsWith(encodeURIComponent(batchRows[25].name)))
  records.onShow()
  assert.strictEqual(records.data.rows.length, 26, 'return preserves additional pages')
  records.requestEvidence({ currentTarget: { dataset: { index: 24 } } })
  assert(openedForms.pop().endsWith('name='))
  records.onShow()
  records.onShow()
  await drain()
  batchRows.unshift({ id: 'new', name: '新查询', badge: '未命中' })
  records.loadMore()
  await drain()
  assert.strictEqual(records.data.rows[0].name, '新查询', 'shifted page boundary refreshes rather than skipping names')
  var pendingPage
  route = function (opt) { pendingPage = opt }
  records.loadMore()
  var oldPage = pendingPage
  session('another')
  var formsBeforeSwitch = openedForms.length
  records.requestEvidence({ currentTarget: { dataset: { index: 0 } } })
  assert.strictEqual(openedForms.length, formsBeforeSwitch)
  var newList = pendingPage
  listPage(oldPage)
  route = function (opt) { opt.success({ statusCode: 200, data: { items: [], total: 0 } }) }
  newList.success({ statusCode: 200, data: { items: [], total: 0 } })
  await drain()
  assert.strictEqual(records.data.rows.length, 0)
  session()
  // 5. 未登录与 401 走登录引导，不显示空列表或网络错误。
  resetTraffic()
  state.clearSession()
  records.onShow()
  await drain()
  assert.strictEqual(records.data.needLogin, true)
  assert.strictEqual(records.data.failed, false)
  assert.strictEqual(records.data.empty, false)
  assert.strictEqual(calls.length, 0)
  session()
  route = function (opt) { opt.success({ statusCode: 401, data: {} }) }
  records.onShow()
  await drain()
  assert.strictEqual(records.data.needLogin, true)
  assert.strictEqual(records.data.failed, false)
  assert.strictEqual(records.data.empty, false)
  assert.strictEqual(records.data.rows.length, 0)
  assert.strictEqual(state.token(), '')

  // 6. 只有网络错误和 5xx 显示「网络异常，请重试」和重试。
  session()
  route = function (opt) { opt.fail() }
  records.onShow()
  await drain()
  assert.strictEqual(records.data.failed, true)
  assert.strictEqual(records.data.empty, false)
  assert.strictEqual(records.data.needLogin, false)
  route = function (opt) { opt.success({ statusCode: 503, data: {} }) }
  records.retryLoad()
  await drain()
  assert.strictEqual(records.data.failed, true)
  assert.strictEqual(records.data.empty, false)
  route = function (opt) { opt.success({ statusCode: 200, data: { items: [], total: 0, limit: 20, offset: 0 } }) }
  records.retryLoad()
  await drain()
  assert.strictEqual(records.data.failed, false)
  assert.strictEqual(records.data.empty, true)

  // 7. 详情 404 显示「记录不存在」并可以返回。客户端网络标记不用服务端 null。
  resetTraffic()
  route = function (opt) {
    if ((opt.url || '').indexOf('/api/v1/queries/missing') !== -1) {
      opt.success({ statusCode: 404, data: {} })
      return
    }
    opt.success({ statusCode: 200, data: {} })
  }
  var missing = makePage('pages/query/index')
  missing.onLoad({ id: 'missing' })
  await drain()
  assert.strictEqual(missing.data.missing, true)
  assert.strictEqual(missing.data.loadError, false)
  assert.strictEqual(missing.data.networkError, false)
  assert.strictEqual(missing.data.showNetworkRetry, false)
  assert.strictEqual(missing.data.items.length, 0)
  missing.back()
  assert.strictEqual(backs.length, 1)

  route = function (opt) { opt.fail() }
  var offline = makePage('pages/query/index')
  offline.onLoad({ id: 'offline' })
  await drain()
  assert.strictEqual(offline.data.loadError, true)
  assert.strictEqual(offline.data.missing, false)
  assert.strictEqual(offline.data.networkError, false)
  route = function (opt) { opt.success({ statusCode: 500, data: {} }) }
  offline.retryLoad()
  await drain()
  assert.strictEqual(offline.data.loadError, true)
  assert.strictEqual(offline.data.missing, false)

  // 8. null 字段不占行，检测日期不补今天，模拟平台不进委托取证预填。
  route = function (opt) {
    opt.success({ statusCode: 200, data: {
      id: 'q-null', created_at: '2026-11-22T00:00:00+08:00', seen_at: null,
      queued: false, running: false, done: true,
      head: '共 1 个｜命中 1｜未命中 0｜失败 0', percent: 100,
      networkError: null, showNetworkRetry: null, failBanner: null, showFailBanner: false,
      failCount: 0, showRetryAll: false, allFail: false, itemRetry: false,
      items: [{
        name: '星河旧梦', badge: '已命中', badgeCls: 'ok', line: '命中 1 条 · 涉及 1 个平台',
        hit: true, missed: false, failed: false,
        evidenceName: null, evidencePlatform: null,
        checked_at: '2026-07-01', date: '2026-07-01'
      }]
    } })
  }
  var blank = makePage('pages/query/index')
  blank.onLoad({ id: 'q-null' })
  await drain()
  assert.strictEqual(blank.data.networkError, false)
  assert.strictEqual(blank.data.showNetworkRetry, false)
  assert.strictEqual(blank.data.seen_at, undefined)
  assert.strictEqual(blank.data.created_at, undefined)
  assert.strictEqual(blank.data.showFailBanner, false)
  assert.ok(!blank.data.failBanner)
  assert.strictEqual(blank.data.items[0].line, '命中 1 条 · 涉及 1 个平台')
  assert.strictEqual(blank.data.items[0].evidenceName, undefined)
  assert.strictEqual(blank.data.items[0].evidencePlatform, undefined)
  assert.strictEqual(blank.data.items[0].checked_at, undefined)
  assert.strictEqual(blank.data.items[0].date, undefined)
  var opened = []
  wx.navigateTo = function (opt) { opened.push(opt.url) }
  blank.evidence({ currentTarget: { dataset: { name: '星河旧梦', evidenceName: '', platform: '' } } })
  assert(opened[0].indexOf('星河旧梦') === -1)
  assert(opened[0].indexOf('2026-') === -1)
  blank.evidence({ currentTarget: { dataset: { evidenceName: '真实节目', platform: '真实平台' } } })
  assert(opened[1].indexOf(encodeURIComponent('真实节目')) !== -1)
  assert(opened[1].indexOf(encodeURIComponent('真实平台')) !== -1)
  assert(opened[1].indexOf('2026-') === -1)
  assert(queryWxml.indexOf('查看报告') !== -1)
  assert(queryWxml.indexOf('class="btn report-btn"') !== -1)
  assert.strictEqual((queryWxml.match(/class="btn ghost miss-action"/g) || []).length, 2)
  assert(queryWxml.indexOf('btn plain') === -1, '未命中的两个按钮使用同一描边样式')
  blank.openHit({ currentTarget: { dataset: { hit: true, name: '星河旧梦' } } })
  assert.strictEqual(opened[opened.length - 1], '/pages/report/index?name=' + encodeURIComponent('星河旧梦') + '&id=q-null')
  config.calibrating = true
  var beforeBlocked = opened.length
  blank.openHit({ currentTarget: { dataset: { hit: true, name: '星河旧梦' } } })
  assert.strictEqual(opened.length, beforeBlocked, '校准中不打开报告')
  config.calibrating = false

  // 9. 四种 badge，超长名字交给单行省略，line 原样保留。
  var longName = '这是一个非常非常非常长的节目名称用来测试单行省略显示效果'
  route = function (opt) {
    opt.success({ statusCode: 200, data: { items: [
      { id: 'a', name: '查询中的节目', badge: '查询中', badgeCls: 'bad', line: '2026-11-22 · 查询中' },
      { id: 'a', name: '已命中的节目', badge: '已命中', badgeCls: 'ok', line: '2026-11-22 · 已命中 2 条 · 涉及 1 个平台' },
      { id: 'b', name: '未命中的节目', badge: '未命中', badgeCls: 'muted', line: '2026-11-22 · 未命中' },
      { id: 'c', name: longName, badge: '失败', badgeCls: 'bad', line: null }
    ], total: 4, limit: 20, offset: 0 } })
  }
  var badges = makePage('pages/records/index')
  badges.onShow()
  await drain()
  assert.deepStrictEqual(badges.data.rows.map(function (row) { return row.badge }), ['查询中', '已命中', '未命中', '失败'])
  assert.deepStrictEqual(badges.data.rows.map(function (row) { return row.badgeCls }), ['bad', 'ok', 'muted', 'bad'])
  assert.strictEqual(badges.data.rows[1].line, '2026-11-22 · 已命中 2 条 · 涉及 1 个平台')
  assert.strictEqual(badges.data.rows[3].line, '')
  assert.strictEqual(badges.data.rows[3].name, longName)
  assert.strictEqual(badges.data.rows[0].date, undefined)
  assert.strictEqual(badges.data.failed, false)

  // 10. 导出遇到旧记录 404 时提示「记录不存在」，并且不再去检索。
  resetTraffic()
  session()
  route = function (opt) {
    if ((opt.url || '').indexOf('/export') !== -1) {
      opt.success({ statusCode: 404, data: '' })
      return
    }
    if ((opt.url || '').indexOf('/programs/search') !== -1) {
      throw new Error('export 404 must not search')
    }
    opt.success({ statusCode: 200, data: {} })
  }
  var book = await api.exportBook('旧记录', 'old-id')
  assert.strictEqual(book.ok, false)
  assert.strictEqual(book.message, '记录不存在')
  assert.strictEqual(urls('/programs/search').length, 0)
  var report = makePage('pages/report/index')
  report.query = { name: '旧记录', id: 'old-id' }
  report.data.name = '旧记录'
  report.download()
  await drain()
  assert.strictEqual(toasts[toasts.length - 1].title, '记录不存在')
  assert.strictEqual(toasts[toasts.length - 1].icon, 'none')

  // 11. 确认片单时创建失败，按网络异常提示，不进入结果页。
  resetTraffic()
  route = function (opt) {
    if ((opt.method || 'GET') === 'POST' && /\/api\/v1\/queries$/.test(opt.url || '')) { opt.fail(); return }
    opt.success({ statusCode: 200, data: {} })
  }
  var input = makePage('pages/input/index')
  input.data.items = [{ name: '星河旧梦' }]
  input.confirm()
  await drain()
  assert.strictEqual(toasts[0].title, '网络异常，请重试')
  assert.strictEqual(redirects.length, 0)
  route = function (opt) { opt.success({ statusCode: 502, data: {} }) }
  input.confirm()
  await drain()
  assert.strictEqual(toasts[toasts.length - 1].title, '网络异常，请重试')
  assert.strictEqual(redirects.length, 0)

  var pending = []
  route = function (opt) { pending.push(opt) }
  session()
  var privateList = makePage('pages/records/index')
  privateList.onShow()
  state.clearSession()
  pending.shift().success({ statusCode: 200, data: { items: [{ name: 'old' }] } })
  await drain()
  assert.strictEqual(privateList.data.needLogin, true)
  assert.deepStrictEqual(privateList.data.rows, [])

  session()
  var detailPage = makePage('pages/query/index')
  detailPage.onLoad({ id: 'private-detail' })
  state.load().auth.expires_at = new Date(0).toISOString()
  pending.shift().success({ statusCode: 200, data: { items: [{ name: 'old' }] } })
  await drain()
  assert.strictEqual(detailPage.data.needLogin, true)
  assert.deepStrictEqual(detailPage.data.items, [])

  session()
  privateList.onShow()
  session('replacement')
  pending.shift().success({ statusCode: 401, data: {} })
  await drain()
  assert.strictEqual(pending.length, 1)
  assert.strictEqual(pending[0].header.Authorization, 'Bearer replacement-token')
  pending.shift().success({ statusCode: 403, data: {} })
  await drain()
  assert.strictEqual(privateList.data.failed, true)
  assert.strictEqual(privateList.data.failureText, '当前账户无权查看查询记录')
  detailPage.retryLoad()
  pending.shift().success({ statusCode: 403, data: {} })
  await drain()
  assert.strictEqual(detailPage.data.loadError, true)
  assert.strictEqual(detailPage.data.failureText, privateList.data.failureText)

  session()
  resetTraffic()
  input.confirm()
  session('replacement')
  pending.shift().success({ statusCode: 200, data: { id: 'private-create' } })
  await drain()
  assert.strictEqual(redirects.length, 0)
  assert.strictEqual(toasts.length, 0)
  var unknownJob = api.getQuery('private-create')
  assert.strictEqual(pending.length, 1, 'old create response did not install a local job for the new user')
  pending.shift().success({ statusCode: 404, data: {} })
  await assert.rejects(unknownJob, function (error) { return error.statusCode === 404 })

  console.log('query record timing, empty list, auth, 404 and badge checks ok')
}
main().catch(function (error) { console.error(error); process.exitCode = 1 })
