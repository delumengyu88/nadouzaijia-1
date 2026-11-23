var assert = require('assert')
var config = require('../miniprogram/config')
config.mode = process.argv[2] || 'hybrid'
config.calibrating = true
config.trialMode = false
config.apiBase = '/test-api'
var pending = []
var calls = []
var storage = {}
var redirects = []
var tabs = []
var modals = []
var timers = {}
var timerId = 0
global.setInterval = function (fn) { timers[++timerId] = fn; return timerId }
global.clearInterval = function (id) { delete timers[id] }
global.getCurrentPages = function () { return [{}] }
global.wx = {
  getStorageSync: function (key) { return storage[key] },
  setStorageSync: function (key, value) { storage[key] = value },
  getSystemInfoSync: function () { return { statusBarHeight: 20 } },
  request: function (opts) {
    var url = opts.url || ''
    if (url.indexOf('/api/v1/queries') !== -1) {
      var creating = (opts.method || 'GET') === 'POST' && /\/api\/v1\/queries$/.test(url)
      opts.success({ statusCode: creating ? 201 : 200, data: {
        id: 'cal-query', queued: true, running: false, done: false, head: '', percent: 0, items: [],
        networkError: null, showNetworkRetry: null, failBanner: null, seen_at: null
      } })
      return
    }
    calls.push(opts)
    pending.push(opts)
  },
  redirectTo: function (opts) { redirects.push(opts.url) },
  switchTab: function (opts) { tabs.push(opts.url) },
  navigateTo: function () { throw new Error('calibrating must not open report or checkout') },
  showModal: function (opts) { modals.push(opts.content) },
  showToast: function () {},
  getFileSystemManager: function () { throw new Error('calibrating must not write reports') }
}
function page(name) {
  var def
  global.Page = function (value) { def = value }
  var file = require.resolve('../miniprogram/pages/' + name + '/index')
  delete require.cache[file]
  require(file)
  return Object.assign({}, def, { data: Object.assign({}, def.data), updates: [], setData: function (value) {
    this.updates.push(value); Object.assign(this.data, value)
  } })
}
function drain() { return new Promise(function (resolve) { setImmediate(resolve) }) }
function forgetIdentity() {
  var keptCalls = calls.filter(function (item) { return item.url.indexOf('/auth/me') === -1 })
  var keptPending = pending.filter(function (item) { return item.url.indexOf('/auth/me') === -1 })
  calls.length = 0
  pending.length = 0
  keptCalls.forEach(function (item) { calls.push(item) })
  keptPending.forEach(function (item) { pending.push(item) })
}
function isOrderListRequest(item) {
  return (item.method || 'GET') === 'GET' && /\/api\/v1\/pay\/orders$/.test(item.url || '')
}
function forgetOrderRequests() {
  for (var i = pending.length - 1; i >= 0; i--) {
    if (isOrderListRequest(pending[i])) pending.splice(i, 1)
  }
}
function callsExceptOrders() {
  return calls.filter(function (item) {
    return !isOrderListRequest(item)
  }).length
}
function respond(outcome) {
  var req = pending.shift()
  if (outcome === 'fail') { req.fail(); return }
  req.success({ statusCode: 200, data: { program_name: req.data.name, outcome: outcome, platform_count: 1,
    platform_groups: [{ records: [{ record_id: 'real-record', locked: false, platform: '真实测试平台',
      access: 'full', can_download_report: true }] }], locked_records: [] } })
}
async function main() {
  var api = require('../miniprogram/api')
  api.reset()
  if (config.mode === 'mock') api.switchAccount('member')
  else require('../miniprogram/api/state').setSession({ token: 'test-member-token',
    expires_at: new Date(Date.now() + 86400000).toISOString(), user: { id: 'member', tier: 'vip' } })
  config.calibrating = false
  var existingPromise = api.checkout('membership_vip')
  while (pending.length && String(pending[0].url || '').indexOf('/api/v1/pay/') !== -1) {
    pending.shift().success({ statusCode: 200, data: { detail: '支付未配置' } })
  }
  var existing = await existingPromise
  var orderNo = existing.ok ? existing.order.orderNo : 'unavailable'
  config.calibrating = true
  var input = page('input')
  input.data.items = ['真实测试片', '无结果测试片', '失败测试片'].map(function (name) { return { name: name } })
  input.confirm()
  if (api.isLive) await drain()
  var id = redirects.pop().split('id=')[1]
  assert(id, 'input still creates a query')
  var result = page('query')
  result.onLoad({ id: id, view: 'allfail' }); result.onShow()
  assert.strictEqual(result.data.calibrating, true)
  assert.strictEqual(result.data.title, '数据校准中')
  for (var i = 0; i < 4; i++) {
    if (api.isLive && pending.length) { respond(['hit', 'miss', 'fail'][i]); await drain() }
    result.tick()
    assert.strictEqual(result.data.head, '')
    assert.deepStrictEqual(result.data.items, [])
  }
  assert(api.getQuery(id).done, 'queries still finish during calibration')
  if (api.isLive) {
    var searches = calls.filter(function (item) { return String(item.url || '').indexOf('/api/v1/programs/search') !== -1 })
    assert.strictEqual(searches.length, 3, 'every title still reaches the real search endpoint')
  }
  result.updates.forEach(function (update) {
    assert(!update.head && !(update.items || []).length, 'no results or counts enter P07 data')
  })
  result.openHit({ currentTarget: { dataset: { hit: true, name: '真实测试片' } } })
  var nonListCount = callsExceptOrders()
  var requestCount = calls.length
  var book = await api.exportBook('真实测试片')
  assert.strictEqual(book.ok, false)
  assert(!book.bytes)
  assert.strictEqual(calls.length, requestCount, 'export is blocked before requesting report data')
  var orders = JSON.stringify(api.listOrders())
  var profile = JSON.stringify(api.profile())
  ;['checkout', 'startPay', 'repay', 'settle', 'setOrderStatus'].forEach(function (method) {
    assert.strictEqual(api[method](method === 'checkout' ? 'membership_regular' : orderNo, 'success').ok, false, method)
  })
  var pay = page('pay'); pay.onLoad({ product: 'membership_vip' })
  assert.strictEqual(pay.data.phase, 'trial')
  pay.pay(); pay.repay(); pay.settle()
  assert.strictEqual(modals.length, 3)
  assert(modals.every(function (message) { return message.indexOf('数据校准中') !== -1 }))
  assert.strictEqual(JSON.stringify(api.listOrders()), orders)
  forgetOrderRequests()
  assert.strictEqual(JSON.stringify(api.profile()), profile)
  forgetIdentity()
  ;['report', 'viewer'].forEach(function (name) {
    var blocked = page(name)
    blocked.onLoad({ id: 'old-id', name: '真实测试片', example: '1' })
    assert(redirects.pop().indexOf('/pages/query/index') === 0, 'old and example report routes return to P07')
  })
  assert.strictEqual(callsExceptOrders(), nonListCount)
  result.home()
  assert.strictEqual(tabs.pop(), '/pages/home/index')
  assert.strictEqual(Object.keys(timers).length, 0)
  config.calibrating = false
  result.refresh()
  assert.strictEqual(result.data.calibrating, false)
  assert.strictEqual(result.data.items.length, 3)
  assert(result.data.head)
  var exported = api.exportBook('星河旧梦')
  if (api.isLive) { respond('hit'); await drain() }
  assert.strictEqual((await exported).ok, true, 'disabling calibration restores exports')
  if (api.isLive) {
    var inFlightReport = page('report')
    inFlightReport.query = { name: '真实测试片' }
    inFlightReport.load()
    var inFlightExport = api.exportBook('真实测试片')
    config.calibrating = true
    respond('hit'); respond('hit'); await drain()
    assert.notStrictEqual(inFlightReport.data.phase, 'ready', 'late report response cannot reveal results')
    assert.strictEqual((await inFlightExport).ok, false, 'late export response cannot generate a workbook')
    assert(redirects.pop().indexOf('/pages/query/index') === 0)
  }
  console.log('P07-calibrating ' + config.mode + ': submissions, hidden results, payment/report guards and disabled switch ok')
}
main().catch(function (error) { console.error(error); process.exitCode = 1 })
