// 查询结果页：请求不返回、同步抛错、超时重试、离开页面，都不能让页面一直停在「正在查询」。
var assert = require('assert')
var config = require('../miniprogram/config')
config.calibrating = false
config.apiBase = '/test-api'
var store = {}
var pending = []
var throwNext = false
global.getCurrentPages = function () { return [1, 2] }
global.wx = {
  getStorageSync: function (k) { return store[k] }, setStorageSync: function (k, v) { store[k] = v },
  getSystemInfoSync: function () { return { statusBarHeight: 20 } },
  request: function (opt) {
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
  opt.success({ statusCode: 200, data: { program_name: opt.data.name, outcome: outcome || 'miss', platform_count: 0,
    platform_groups: [], locked_records: [] } })
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
function open(names) {
  var created = api.createQuery(names)
  assert(created.ok)
  var page = Object.assign({}, def, { data: {}, setData: function (v) { Object.assign(this.data, v) } })
  page.onLoad({ id: created.id })
  page.onShow()
  return page
}

async function main() {
  api.reset()
  state.setSession({ token: 'query-token', expires_at: new Date(Date.now() + 86400000).toISOString(), user: { id: 'q', tier: 'free' } })

  // 1. 请求一直不回：59 秒仍在查询，进度只按真实完成数走；60 秒停下并提示超时、清掉轮询。
  var page = open(['不回片'])
  await drain()
  assert.strictEqual(pending.length, 1)
  assert.strictEqual(activeTimers(), 1)
  clock += 59000; page.tick(); await drain()
  assert(page.data.running && page.data.percent === 0 && page.data.head === '已完成 0 / 1')
  assert.strictEqual(pending.length, 1, 'polling must not resend while a request is in flight')
  clock += 1000; page.tick(); await drain()
  assert(page.data.done && !page.data.running)
  assert.strictEqual(page.data.failBanner, '查询超时，请重试')
  assert(page.data.items[0].failed && page.data.itemRetry)
  assert.strictEqual(page.data.items[0].line, '查询超时，请重试')
  assert.strictEqual(activeTimers(), 0, 'timeout must clear polling')

  // 2. 迟到的旧回包不覆盖；连点重试只发一次新请求。
  answer(pending[0], 'hit'); await drain()
  assert(api.getQuery(page.id).items[0].failed, 'late answer from a timed-out attempt is ignored')
  page.retry({ currentTarget: { dataset: { name: '不回片' } } })
  page.retry({ currentTarget: { dataset: { name: '不回片' } } })
  page.retryAll()
  await drain()
  assert.strictEqual(pending.length, 2, 'retry must not start duplicate requests')
  assert.strictEqual(activeTimers(), 1, 'retry keeps a single polling timer')
  assert(page.data.running)
  answer(pending[1]); await drain(); page.tick()
  assert(page.data.done && page.data.items[0].missed)
  assert.strictEqual(activeTimers(), 0)

  // 3. wx.request 同步抛错：停在原进度，提示可重试，不卡住也不改计成已完成。
  throwNext = true
  page = open(['抛错片']); await drain(); page.tick()
  assert(page.data.done && !page.data.running && page.data.networkError && page.data.showNetworkRetry)
  assert.strictEqual(page.data.failBanner, '网络异常，请重试')
  assert.strictEqual(page.data.items[0].line, '网络异常，请重试')
  assert.strictEqual(page.data.head, '已完成 0 / 1')
  assert.strictEqual(page.data.percent, 0)
  assert.strictEqual(activeTimers(), 0)
  var thrown = pending.length
  page.retry({ currentTarget: { dataset: {} } })
  page.retry({ currentTarget: { dataset: {} } })
  await drain()
  assert.strictEqual(pending.length, thrown + 1, 'network retry must not start duplicate requests')
  assert.strictEqual(activeTimers(), 1)
  page.onUnload()
  assert.strictEqual(activeTimers(), 0)

  // 4. 多个节目：进度只随真实返回变化，时间流逝不推进度。
  var before = pending.length
  page = open(['甲片', '乙片']); await drain()
  clock += 30000; page.tick(); await drain()
  assert.strictEqual(page.data.percent, 0)
  answer(pending[before]); await drain(); page.tick(); await drain()
  assert.strictEqual(page.data.head, '已完成 1 / 2')
  assert.strictEqual(page.data.percent, 50)

  // 5. 离开页面清掉计时器；回来只恢复一个，且不重发进行中的请求。
  var inflight = pending.length
  page.onHide()
  assert.strictEqual(activeTimers(), 0)
  page.onShow(); page.onShow()
  assert.strictEqual(pending.length, inflight, 'onShow must not resend an in-flight request')
  assert.strictEqual(activeTimers(), 1)
  page.onUnload()
  assert.strictEqual(activeTimers(), 0)

  // 6. 切到后台期间回包：回来直接展示结果，不再发请求。
  page = open(['回来片']); await drain()
  var hidden = pending.length
  page.onHide()
  answer(pending[hidden - 1], 'hit'); await drain()
  page.onShow()
  assert.strictEqual(pending.length, hidden)
  assert(page.data.done && page.data.items[0].hit && !page.data.running)
  assert.strictEqual(activeTimers(), 0)

  // 7. 回调在后台丢失：满 60 秒再回来，停下并提示超时，不另发请求。
  page = open(['后台片']); await drain()
  var lost = pending.length
  page.onHide()
  clock += 60000
  page.onShow(); await drain()
  assert.strictEqual(pending.length, lost, 'returning after the timeout must not resend')
  assert(page.data.done && !page.data.running)
  assert.strictEqual(page.data.failBanner, '查询超时，请重试')
  assert.strictEqual(activeTimers(), 0)

  // 8. 多条查询超时：停掉尚未发出的下一条，不能接着查。
  var batch = pending.length
  page = open(['一片', '二片', '三片']); await drain()
  answer(pending[batch], 'hit'); await drain(); page.tick(); await drain()
  assert.strictEqual(page.data.head, '已完成 1 / 3')
  var sent = pending.length
  clock += 60000; page.tick(); await drain()
  assert.strictEqual(pending.length, sent, 'timeout must not start the next title')
  assert(page.data.done && !page.data.running)
  assert.strictEqual(page.data.failBanner, '查询超时，请重试')
  assert(page.data.items[0].hit && page.data.items[1].failed && page.data.items[2].failed)
  assert.strictEqual(page.data.items[1].line, '查询超时，请重试')
  assert.strictEqual(page.data.items[2].line, '查询超时，请重试')
  assert.strictEqual(activeTimers(), 0)
  answer(pending[sent - 1], 'hit'); await drain(); page.tick()
  assert(page.data.items[1].failed, 'late answer after a multi-item timeout is ignored')

  // 9. 网络失败：进度停在原位，下面是「网络异常，请重试」和重试按钮。
  var netAt = pending.length
  page = open(['甲网', '乙网']); await drain()
  answer(pending[netAt], 'hit'); await drain(); page.tick(); await drain()
  assert.strictEqual(page.data.head, '已完成 1 / 2')
  assert.strictEqual(page.data.percent, 50)
  var netSent = pending.length
  pending[netSent - 1].fail(); await drain(); page.tick()
  assert(page.data.networkError && page.data.showNetworkRetry && !page.data.running && page.data.done)
  assert.strictEqual(page.data.failBanner, '网络异常，请重试')
  assert.strictEqual(page.data.head, '已完成 1 / 2')
  assert.strictEqual(page.data.percent, 50)
  assert.strictEqual(pending.length, netSent, 'network failure must not start the next title')
  assert.strictEqual(activeTimers(), 0)
  page.onHide(); page.onShow()
  assert.strictEqual(pending.length, netSent, 'showing the network error again must not resend')
  assert.strictEqual(activeTimers(), 0)
  assert.strictEqual(page.data.head, '已完成 1 / 2')
  page.retry({ currentTarget: { dataset: {} } })
  page.retry({ currentTarget: { dataset: {} } })
  page.retryAll()
  await drain()
  assert.strictEqual(pending.length, netSent + 1, 'retry after a network error sends one request')
  assert(page.data.running && !page.data.networkError)
  assert.strictEqual(page.data.head, '已完成 1 / 2')
  assert.strictEqual(page.data.percent, 50)
  assert.strictEqual(activeTimers(), 1)
  page.onUnload()
  assert.strictEqual(activeTimers(), 0)

  // 10. 服务端错误不是网络异常，进入可重试的失败结果，不占用「停在原进度」。
  page = open(['服务片']); await drain()
  pending[pending.length - 1].success({ statusCode: 503, data: {} })
  await drain(); page.tick()
  assert(page.data.done && !page.data.networkError && page.data.items[0].failed && page.data.itemRetry)
  assert.strictEqual(page.data.items[0].line, '服务暂不可用')
  assert.strictEqual(activeTimers(), 0)
  console.log('query page timeout, retry, progress and timer cleanup checks ok')
}
main().catch(function (error) { console.error(error); process.exitCode = 1 })
