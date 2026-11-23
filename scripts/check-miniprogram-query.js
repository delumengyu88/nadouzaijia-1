// 查询结果页：请求不返回、同步抛错、超时重试、离开页面，都不能让页面一直停在「正在查询」。
var assert = require('assert')
var config = require('../miniprogram/config')
config.calibrating = false
config.apiBase = '/test-api'
var store = {}
var pending = []
var throwNext = false
var querySeq = 0
global.getCurrentPages = function () { return [1, 2] }
global.wx = {
  getStorageSync: function (k) { return store[k] }, setStorageSync: function (k, v) { store[k] = v },
  getSystemInfoSync: function () { return { statusBarHeight: 20 } },
  request: function (opt) {
    var url = opt.url || ''
    if (url.indexOf('/api/v1/queries') !== -1) {
      var creating = (opt.method || 'GET') === 'POST' && /\/api\/v1\/queries$/.test(url)
      var id = 'srv-q'
      if (creating) id = 'srv-q-' + (++querySeq)
      else {
        var found = url.match(/\/api\/v1\/queries\/([^/?]+)/)
        if (found) id = decodeURIComponent(found[1])
      }
      opt.success({ statusCode: creating ? 201 : 200, data: {
        id: id, queued: true, running: false, done: false, head: '', percent: 0, items: [],
        networkError: null, showNetworkRetry: null, failBanner: null, seen_at: null
      } })
      return
    }
    if (throwNext) { throwNext = false; throw new Error('request crashed') }
    pending.push(opt) // never answered unless a test does it
  }
}
var timers = {}
var timerSeq = 0
global.setInterval = function () { timerSeq += 1; timers[timerSeq] = true; return timerSeq }
global.clearInterval = function (id) { delete timers[id] }
function activeTimers() { return Object.keys(timers).length }
function answer(opt, outcome) {
  opt.success({ statusCode: 200, data: opt.data.names.map(function (name) {
    return { program_name: name, outcome: outcome || 'miss', platform_count: 0, platform_groups: [], locked_records: [] }
  }) })
}
function drain() { return new Promise(function (resolve) { setImmediate(resolve) }) }

var api = require('../miniprogram/api')
var state = require('../miniprogram/api/state')
var guard = api.QueryGuard
var clock = 1000000
guard.now = function () { return clock }
var def
global.Page = function (p) { def = p }
require('../miniprogram/pages/query/index')
async function open(names) {
  var created = await Promise.resolve(api.createQuery(names))
  assert(created.ok)
  var page = Object.assign({}, def, { data: {}, setData: function (v) { Object.assign(this.data, v) } })
  page.onLoad({ id: created.id })
  page.onShow()
  return page
}

async function main() {
  api.reset()
  state.setSession({ token: 'query-token', expires_at: new Date(Date.now() + 86400000).toISOString(), user: { id: 'q', tier: 'free' } })

  // A lost callback stops at the 60 s watchdog without an automatic resend. Late callbacks must not win.
  var page = await open(['不回片'])
  clock += 59000
  page.tick(); await drain()
  assert.strictEqual(pending.length, 1)
  assert(page.data.running && page.data.percent === 0)
  clock += 1000
  page.tick(); await drain()
  assert.strictEqual(pending.length, 1, 'the watchdog must not resend by itself')
  assert(page.data.done && page.data.items[0].failed)
  assert.strictEqual(page.data.failBanner, '查询超时，请重试')
  assert.strictEqual(activeTimers(), 0)
  answer(pending[0], 'hit'); await drain()
  assert(api.getQuery(page.id).items[0].failed)
  page.retryAll(); page.retryAll(); await drain()
  assert.strictEqual(pending.length, 2)
  page.onHide()
  assert.strictEqual(activeTimers(), 0)
  answer(pending[1]); await drain()
  page.onShow()
  assert(page.data.done && page.data.items[0].missed)
  assert.strictEqual(activeTimers(), 0)

  // 500 names launch exactly three requests. The middle batch fails independently.
  var names = Array.from({length:500}, function (_, i) { return '合成节目' + i })
  var start = pending.length
  page = await open(names); await drain()
  var sent = pending.slice(start)
  assert.deepStrictEqual(sent.map(req => req.data.names.length), [200, 200, 100])
  assert.deepStrictEqual([].concat(...sent.map(req => req.data.names)), names)
  assert(sent.every(req => req.method === 'POST' && req.url.endsWith('/programs/search/batch')))
  page.onHide(); page.onShow(); page.onShow()
  assert.strictEqual(activeTimers(), 1)
  assert.strictEqual(pending.length, start + 3)
  answer(sent[2]); await drain(); page.tick()
  assert.strictEqual(page.data.percent, 20)
  sent[1].fail(); await drain()
  pending[start + 3].fail(); await drain()
  pending[start + 4].fail(); await drain(); page.tick()
  assert(page.data.running && !page.data.done)
  answer(sent[0], 'hit'); await drain(); page.tick()
  assert.strictEqual(pending.length, start + 5)
  assert(page.data.done && page.data.showFailBanner && page.data.showRetryAll)
  assert(!page.data.networkError, 'successful rows remain visible in the results list')
  assert.deepStrictEqual(page.data.items.map(row => row.name), names)
  assert(page.data.items.slice(0,200).every(row => row.hit))
  assert(page.data.items.slice(200,400).every(row => row.failed))
  assert(page.data.items.slice(400).every(row => row.missed))
  assert.strictEqual(page.data.percent, 60)
  assert.strictEqual(activeTimers(), 0)
  page.retry({currentTarget:{dataset:{name:names[255]}}}); page.retryAll(); await drain()
  assert.strictEqual(pending.length, start + 6)
  assert(pending.slice(start + 3).every(req => JSON.stringify(req.data.names) === JSON.stringify(sent[1].data.names)))
  answer(pending[start + 5]); await drain(); page.tick()
  assert(page.data.done && !page.data.showFailBanner && page.data.percent === 100)
  assert.strictEqual(activeTimers(), 0)

  // A synchronous transport exception is retried. A permission error is terminal.
  throwNext = true
  page = await open(['抛错片']); await drain()
  var request = pending[pending.length - 1]
  assert.deepStrictEqual(request.data.names, ['抛错片'])
  request.success({statusCode:403, data:{}}); await drain(); page.tick()
  assert(page.data.done && page.data.items[0].failed && page.data.itemRetry)
  page.onUnload()
  assert.strictEqual(activeTimers(), 0)
  console.log('query page timeout, retry, progress and timer cleanup checks ok')
}
main().catch(function (error) { console.error(error); process.exitCode = 1 })
