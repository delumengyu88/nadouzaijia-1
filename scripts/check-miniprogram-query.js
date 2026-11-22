// 查询结果页：请求不返回、同步抛错、超时重试、离开页面，都不能让页面一直停在「正在查询」。
// 进度环：0 为旋转，部分进度按已完成/总数，全部完成满环，超时和网络异常沿用已有重试。
var assert = require('assert')
var fs = require('fs')
var path = require('path')
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
  assert.strictEqual(page.data.ringWait, '通常 10–30 秒')
  assert.strictEqual(page.data.rows.length, 0, 'querying page does not render result rows')
  clock += 59000; page.tick(); await drain()
  assert(page.data.running && page.data.percent === 0 && page.data.head === '已完成 0 / 1')
  assert.strictEqual(page.data.ringMode, 'spin', 'zero progress stays on the rotating arc')
  assert.strictEqual(page.data.ringSpin, true)
  assert.strictEqual(page.data.ringRatio, 0)
  assert.strictEqual(page.data.ringText, '0/1')
  assert.strictEqual(page.data.ringLabel || page.data.ringDone + '/' + page.data.ringTotal, '0/1')
  assert.strictEqual(page.data.ringWait, '比平时慢一些，请稍候')
  assert.strictEqual(page.data.ringRight, -180)
  assert.strictEqual(page.data.ringLeft, 180)
  assert.strictEqual(pending.length, 1, 'polling must not resend while a request is in flight')
  clock += 1000; page.tick(); await drain()
  assert(page.data.done && !page.data.running)
  assert.strictEqual(page.data.failBanner, '查询超时，请重试')
  assert(page.data.showFailBanner, 'timeout copy is the banner the page renders')
  assert(page.data.items[0].failed && page.data.itemRetry, 'timeout row keeps the 重试 button')
  assert(page.data.rows[0].failed && page.data.rows[0].line === '查询超时，请重试', 'timeout list is the rendered rows')
  assert.strictEqual(page.data.items[0].line, '查询超时，请重试')
  assert.strictEqual(page.data.ringSpin, false, 'timeout stops the indeterminate arc')
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
  assert.strictEqual(page.data.ringSpin, false, 'a stopped zero is not the querying arc')
  assert.strictEqual(page.data.ringText, '0/1')
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
  assert.strictEqual(page.data.ringMode, 'spin')
  assert.strictEqual(page.data.ringRatio, 0, 'elapsed time must not move the ring')
  assert.strictEqual(page.data.ringSpin, true, 'a single unfinished count keeps spinning')
  assert.strictEqual(page.data.ringWait, '比平时慢一些，请稍候')
  assert.strictEqual(page.data.rows.length, 0)
  answer(pending[before]); await drain(); page.tick(); await drain()
  assert.strictEqual(page.data.head, '已完成 1 / 2')
  assert.strictEqual(page.data.percent, 50)
  assert.strictEqual(page.data.ringMode, 'progress')
  assert.strictEqual(page.data.ringSpin, true, 'partial progress keeps a moving arc until the count changes again or the query ends')
  assert.strictEqual(page.data.ringText, '1/2')
  assert.strictEqual(page.data.ringDone, 1)
  assert.strictEqual(page.data.ringTotal, 2)
  assert.strictEqual(page.data.ringRatio, 0.5)
  assert.strictEqual(page.data.ringRight, 0)
  assert.strictEqual(page.data.ringLeft, 180)
  assert.strictEqual(page.data.ringWait, '通常 10–30 秒', 'a new request restarts the same clock')

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
  assert.strictEqual(page.data.ringMode, 'progress')
  assert.strictEqual(page.data.ringRatio, 0.5, 'network failure keeps the real ratio')
  assert.strictEqual(page.data.ringText, '1/2')
  assert(page.data.showNetworkRetry, '网络异常，请重试 keeps the 重试 button')
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

  // 11. 进度环：三分之一按真实比例，两条都返回后是满环，不再旋转。
  var thirdAt = pending.length
  page = open(['环一', '环二', '环三']); await drain()
  assert.strictEqual(page.data.ringMode, 'spin')
  answer(pending[thirdAt]); await drain(); page.tick(); await drain()
  assert.strictEqual(page.data.ringText, '1/3')
  assert.strictEqual(page.data.ringMode, 'progress')
  assert.strictEqual(page.data.ringSpin, true)
  assert.strictEqual(page.data.ringRatio, 1 / 3)
  assert.strictEqual(page.data.ringRight, -60)
  assert.strictEqual(page.data.ringLeft, 180)
  page.onUnload()

  var fullAt = pending.length
  page = open(['满甲', '满乙']); await drain()
  answer(pending[fullAt], 'hit'); await drain(); page.tick(); await drain()
  answer(pending[fullAt + 1], 'hit'); await drain(); page.tick(); await drain()
  assert(page.data.done && !page.data.running)
  assert(page.data.items[0].hit && page.data.items[1].hit)
  assert.strictEqual(page.data.ringMode, 'full')
  assert.strictEqual(page.data.ringFull, true)
  assert.strictEqual(page.data.ringSpin, false)
  assert.strictEqual(page.data.ringRatio, 1)
  assert.strictEqual(page.data.ringText, '2/2')
  assert.strictEqual(page.data.ringRight, 0)
  assert.strictEqual(page.data.ringLeft, 0)
  page.onUnload()

  // 12. 30 秒换文案、60 秒仍走原来的超时；重试后从同一次请求的起点重新计。只查一条时全程旋转。
  var anchor = clock
  page = open(['慢片']); await drain()
  assert.strictEqual(page.data.ringSpin, true)
  assert.strictEqual(page.data.ringWait, '通常 10–30 秒')
  clock = anchor + 29999; page.tick(); await drain()
  assert(page.data.running && page.data.ringRatio === 0 && page.data.ringSpin)
  assert.strictEqual(page.data.ringWait, '通常 10–30 秒')
  clock = anchor + 30000; page.tick(); await drain()
  assert(page.data.running && page.data.ringRatio === 0 && page.data.ringSpin)
  assert.strictEqual(page.data.ringWait, '比平时慢一些，请稍候')
  assert.strictEqual(page.data.percent, 0, 'the slow hint does not invent progress')
  clock = anchor + 60000; page.tick(); await drain()
  assert(page.data.done && !page.data.running && !page.data.ringSpin)
  assert.strictEqual(page.data.failBanner, '查询超时，请重试')
  assert(page.data.showFailBanner && page.data.itemRetry && page.data.rows[0].failed)
  page.retry({ currentTarget: { dataset: { name: '慢片' } } })
  await drain()
  assert(page.data.running && page.data.ringSpin && page.data.ringRatio === 0)
  assert.strictEqual(page.data.ringWait, '通常 10–30 秒', 'retry restarts the wait')
  clock += 30000; page.tick(); await drain()
  assert(page.data.running && page.data.ringRatio === 0 && page.data.ringSpin)
  assert.strictEqual(page.data.ringWait, '比平时慢一些，请稍候')
  page.onUnload()

  // 13. 圆环尺寸颜色、文案位置、不横向溢出，以及页面里没有假倒计时。
  var queryDir = path.join(__dirname, '../miniprogram/pages/query')
  var pageSrc = fs.readFileSync(path.join(queryDir, 'index.js'), 'utf8')
  var ringSrc = fs.readFileSync(path.join(queryDir, 'ring.js'), 'utf8')
  var wxml = fs.readFileSync(path.join(queryDir, 'index.wxml'), 'utf8')
  var wxss = fs.readFileSync(path.join(queryDir, 'index.wxss'), 'utf8')
  var blob = pageSrc + '\n' + ringSrc + '\n' + wxml + '\n' + wxss
  ;['倒计时', '预计剩余', '预计', '模拟进度', '假进度', 'countdown', 'fakeProgress'].forEach(function (word) {
    assert(blob.indexOf(word) === -1, 'no fake countdown: ' + word)
  })
  assert(!/setInterval|setTimeout|Date\.now|new Date/.test(ringSrc), 'ring ratio is not driven by a clock')
  assert(!/percent\s*\+=|ringRatio\s*\+=|ringRight\s*\+=/.test(pageSrc + ringSrc), 'no local progress increment')
  assert(/setInterval\(function \(\) \{ self\.tick\(\) \}, 700\)/.test(pageSrc), 'the only timer is the result poll')
  assert(wxml.indexOf('bar-track') === -1, 'the linear bar is gone')
  assert(wxml.indexOf('{{ringText}}') !== -1)
  assert(wxml.indexOf('q-ring-arc') !== -1)
  assert(wxml.indexOf('已完成 {{ringDone}} / 共 {{ringTotal}}') !== -1)
  assert(ringSrc.indexOf('通常 10–30 秒') !== -1 && ringSrc.indexOf('比平时慢一些，请稍候') !== -1)
  var querying = wxml.split('wx:elif="{{running || networkError}}"')[1].split('<view class="body" wx:else>')[0]
  assert(querying.indexOf('wx:for') === -1, 'querying state has no result list')
  assert(querying.indexOf('q-ring-progress') !== -1 && querying.indexOf('q-ring-arc') !== -1,
    'partial progress keeps the fill and overlays the rotating arc')
  var hole = querying.split('q-ring-hole')[1].split('q-ring-label')[0]
  assert(hole.indexOf('{{ringText}}') !== -1 && hole.indexOf('已完成') === -1,
    'the long label stays outside the ring')
  assert(querying.indexOf('{{failBanner}}') !== -1)
  assert(querying.indexOf('showNetworkRetry') !== -1 && querying.indexOf('>重试</view>') !== -1,
    '网络异常，请重试 is rendered with 重试')
  var results = wxml.split('<view class="body" wx:else>')[1]
  assert(results.indexOf('{{failBanner}}') !== -1)
  assert(results.indexOf('wx:for="{{rows}}"') !== -1)
  assert(results.indexOf('item.failed && itemRetry') !== -1 && results.indexOf('>重试</view>') !== -1,
    '查询超时，请重试 is rendered with 重试')
  function rule(selector) {
    var body = new RegExp(selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*\\{([^}]*)\\}').exec(wxss)
    assert(body, 'missing rule ' + selector)
    return body[1]
  }
  var ringBox = rule('.q-ring')
  assert(ringBox.indexOf('width: 160rpx') !== -1 && ringBox.indexOf('height: 160rpx') !== -1)
  assert(ringBox.indexOf('overflow: hidden') !== -1, 'the ring clips transformed paint')
  assert(rule('page').indexOf('overflow-x: hidden') !== -1)
  assert(rule('.query-root').indexOf('overflow-x: hidden') !== -1)
  assert(rule('.q-ring-track').indexOf('border: 8rpx solid #E5E9F0') !== -1)
  var half = rule('.q-ring-half')
  assert(half.indexOf('background: #1D4E9E') !== -1)
  assert(half.indexOf('transition: transform 300ms linear') !== -1)
  var arc = rule('.q-ring-arc')
  assert(arc.indexOf('border: 8rpx solid transparent') !== -1)
  assert(arc.indexOf('border-top-color: #1D4E9E') !== -1, 'indeterminate arc is one 90 degree border')
  assert(arc.indexOf('border-right-color') === -1 && arc.indexOf('border-bottom-color') === -1)
  var ratio = rule('.q-ring-ratio')
  assert(ratio.indexOf('font-size: 32rpx') !== -1 && ratio.indexOf('height: 44rpx') !== -1)
  assert(ratio.indexOf('white-space: nowrap') !== -1 && ratio.indexOf('overflow: hidden') !== -1)
  var label = rule('.q-ring-label')
  assert(label.indexOf('font-size: 32rpx') !== -1 && label.indexOf('height: 44rpx') !== -1)
  assert(label.indexOf('white-space: nowrap') !== -1 && label.indexOf('overflow: hidden') !== -1)
  var wait = rule('.q-ring-wait')
  assert(wait.indexOf('height: 32rpx') !== -1 && wait.indexOf('color: #8b95a3') !== -1)
  assert(wait.indexOf('white-space: nowrap') !== -1)
  assert(wxss.indexOf('visibility: hidden') === -1, 'spinning does not hide the filled arc')
  assert(!/\.is-full[^{]*\{[^}]*(width|height)\s*:/.test(wxss), 'full state does not resize the ring')
  assert(!/\.is-spin[^{]*\{[^}]*(width|height)\s*:/.test(wxss), 'spin state does not resize the ring')
  var ringMod = require('../miniprogram/pages/query/ring')
  var direct = ringMod.ringState
  assert.strictEqual(direct({ running: true, head: '已完成 0 / 4' }).ringMode, 'spin')
  assert.strictEqual(direct({ running: true, head: '已完成 0 / 1' }).ringSpin, true)
  var twoThirds = direct({ running: true, head: '已完成 2 / 3' })
  assert.strictEqual(twoThirds.ringRatio, 2 / 3)
  assert.strictEqual(twoThirds.ringSpin, true)
  assert.strictEqual(twoThirds.ringRight, 0)
  assert.strictEqual(twoThirds.ringLeft, 120)
  assert.strictEqual(twoThirds.ringText, '2/3')
  assert.strictEqual(twoThirds.ringDone, 2)
  assert.strictEqual(twoThirds.ringTotal, 3)
  assert.strictEqual(direct({ running: false, done: true, items: [{ hit: true }, { hit: true }] }).ringSpin, false)
  assert.strictEqual(ringMod.waitCopy(true, 100, 100 + 29999, 30000), '通常 10–30 秒')
  assert.strictEqual(ringMod.waitCopy(true, 100, 100 + 30000, 30000), '比平时慢一些，请稍候')
  assert.strictEqual(ringMod.waitCopy(false, 100, 100 + 40000, 30000), '通常 10–30 秒')
  assert.strictEqual(ringMod.waitCopy(true, 0, 100 + 40000, 30000), '通常 10–30 秒')
  assert.strictEqual(api.QueryGuard.slowMs(), 30000)
  assert.strictEqual(api.QueryGuard.timeoutMs(), 60000)
  console.log('query page timeout, retry, progress ring and timer cleanup checks ok')
}
main().catch(function (error) { console.error(error); process.exitCode = 1 })
