var assert = require('assert')
var fs = require('fs')
var path = require('path')
var config = require('../miniprogram/config')

config.mode = 'live'
config.trialMode = false
config.calibrating = false
config.apiBase = '/test-api'

var pending = []
var calls = []
var payment = null
var toasts = []
var clock = []
var storage = {}
global.setTimeout = function (fn, ms) {
  clock.push({ fn: fn, ms: ms })
  return clock.length
}
global.clearTimeout = function () {}
global.getCurrentPages = function () { return [{}, {}] }
global.wx = {
  getStorageSync: function (key) { return storage[key] },
  setStorageSync: function (key, value) { storage[key] = value },
  getSystemInfoSync: function () { return { statusBarHeight: 20 } },
  request: function (opt) { calls.push(opt); pending.push(opt) },
  requestPayment: function (opt) { payment = opt },
  showToast: function (opt) { toasts.push(opt) },
  showModal: function () {},
  navigateBack: function () {},
  switchTab: function () {},
  navigateTo: function () {}
}

var state = require('../miniprogram/api/state')
state.setSession({
  token: 'test-pay-token',
  expires_at: new Date(Date.now() + 86400000).toISOString(),
  user: { id: 'pay-user', tier: 'free' }
})

var pageDef
global.Page = function (def) { pageDef = def }
require('../miniprogram/pages/pay/index')
var api = require('../miniprogram/api')

var ORDER_NO = 'ND261122000000abcd'

function drain() {
  return new Promise(function (resolve) { setImmediate(resolve) })
}

function payFields() {
  return {
    timeStamp: '1700000000',
    nonceStr: 'nonce-test',
    package: 'prepay_id=test-prepay',
    signType: 'RSA',
    paySign: 'sign-test'
  }
}

function orderBody(status, extra) {
  var body = {
    order_number: ORDER_NO,
    tier: 'membership_vip',
    title: 'VIP',
    membership: 'vip',
    duration_days: 30,
    amount_fen: 59900,
    status: status,
    paid_at: status === '待支付' ? null : '2026-11-22T12:00:00',
    granted: status === '已发放'
  }
  if (extra) Object.keys(extra).forEach(function (key) { body[key] = extra[key] })
  return body
}

function respond(statusCode, data) {
  if (!pending.length) throw new Error('no request to answer')
  var req = pending.shift()
  req.success({ statusCode: statusCode, data: data })
  return req
}

function createPage() {
  var page = { data: Object.assign({}, pageDef.data) }
  Object.keys(pageDef).forEach(function (key) {
    if (key === 'data') return
    page[key] = typeof pageDef[key] === 'function' ? pageDef[key].bind(page) : pageDef[key]
  })
  page.setData = function (value) { Object.assign(this.data, value) }
  return page
}

function resetTransport() {
  pending = []
  calls = []
  payment = null
  toasts = []
  clock = []
}

async function placeOrder(page) {
  page.onLoad({ product: 'membership_vip' })
  assert.strictEqual(pending.length, 0, 'opening the page must not order')
  assert.strictEqual(page.data.payLoading, false)
  page.pay()
  page.pay()
  page.pay()
  assert.strictEqual(pending.length, 1, 'repeated taps send one create')
  assert.strictEqual(page.data.payLoading, true)
  var req = pending[0]
  assert.strictEqual(req.method, 'POST')
  assert.strictEqual(req.url, '/test-api/api/v1/pay/orders')
  assert.deepStrictEqual(req.data, { tier: 'membership_vip' })
  assert.strictEqual(req.header.Authorization, 'Bearer test-pay-token')
  return req
}

async function created(page, statusCode, data) {
  await placeOrder(page)
  respond(statusCode, data)
  await drain()
}

async function polls(count, body, trailingTimer) {
  var i
  for (i = 0; i < count; i++) {
    assert.strictEqual(pending.length, 1, 'poll ' + (i + 1))
    var req = pending[0]
    assert.strictEqual(req.method, 'POST')
    assert.strictEqual(req.url, '/test-api/api/v1/pay/orders/' + ORDER_NO + '/sync')
    assert.strictEqual(req.data.paid, undefined)
    assert.strictEqual(req.data.outcome, undefined)
    assert.strictEqual(req.data.status, undefined)
    assert.strictEqual(req.header.Authorization, 'Bearer test-pay-token')
    respond(200, typeof body === 'function' ? body(i) : body)
    await drain()
    if (i < count - 1) {
      assert.strictEqual(clock.length, 1, 'one wait after poll ' + (i + 1))
      assert.strictEqual(clock[0].ms, 2000)
      clock.shift().fn()
    }
  }
  assert.strictEqual(clock.length, trailingTimer ? 1 : 0, trailingTimer ? 'poll keeps waiting' : 'polling stops after the limit')
}

async function main() {
  var payDir = path.join(__dirname, '../miniprogram/pages/pay')
  var paySource = ['index.js', 'index.wxml', 'index.wxss', 'index.json'].map(function (name) {
    return fs.readFileSync(path.join(payDir, name), 'utf8')
  }).join('\n') + fs.readFileSync(path.join(__dirname, '../miniprogram/api/http.js'), 'utf8')
  ;['setClipboardData', 'getClipboardData', 'chooseAvatar', 'getPhoneNumber', 'chooseImage', 'chooseMedia', 'getLocation'].forEach(function (name) {
    assert.strictEqual(paySource.indexOf(name), -1, 'pay must not add privacy api ' + name)
  })
  var wxml = fs.readFileSync(path.join(payDir, 'index.wxml'), 'utf8')
  assert(/<button\b[^>]*loading="\{\{payLoading\}\}"[^>]*disabled="\{\{payLoading\}\}"[^>]*bindtap="pay"/.test(wxml),
    'pay button shows loading and ignores taps while disabled')
  assert(wxml.indexOf('支付暂未开通') !== -1)
  assert(wxml.indexOf('type="waiting" size="56" color="#1D4E9E"') !== -1)
  assert(wxml.indexOf('type="success" size="56" color="#16A34A"') !== -1)
  assert(wxml.indexOf('type="warn" size="56" color="#DC2626"') !== -1)
  assert(wxml.indexOf('class="tag') === -1)
  assert(wxml.indexOf('正在确认支付结果…') !== -1)
  assert(/wx:if="\{\{confirmTimeout && !confirmSpin\}\}" bindtap="refreshStatus"/.test(wxml))
  assert(wxml.indexOf('如已扣款，可在「我的订单」查看') !== -1)
  assert(wxml.indexOf('或联系客服') !== -1)
  assert(wxml.indexOf('mockSettle') !== -1)

  resetTransport()
  var page = createPage()
  await created(page, 200, Object.assign(orderBody('待支付'), payFields()))
  assert.strictEqual(page.data.payLoading, false, 'loading ends after create')
  assert.strictEqual(page.data.orderNo, ORDER_NO)
  assert.strictEqual(page.data.phase, 'paying')
  assert.strictEqual(page.data.priceText, '¥599')
  assert.strictEqual(page.data.mockSettle, false, 'live pay does not show prototype result buttons')
  assert.ok(payment)
  assert.deepStrictEqual(
    [payment.timeStamp, payment.nonceStr, payment.package, payment.signType, payment.paySign],
    ['1700000000', 'nonce-test', 'prepay_id=test-prepay', 'RSA', 'sign-test']
  )
  assert.strictEqual(payment.appId, undefined)
  assert.strictEqual(Object.prototype.hasOwnProperty.call(payment, 'appId'), false)
  assert.strictEqual(page.data.phase === 'granted', false)
  console.log('下单成功: order number and requestPayment params')
  console.log('防连点: one POST while the button is loading')

  resetTransport()
  page = createPage()
  await created(page, 200, { detail: '支付未配置' })
  assert.strictEqual(page.data.phase, 'unavailable')
  assert.strictEqual(page.data.title, '支付暂未开通')
  assert.strictEqual(page.data.payLoading, false)
  assert.strictEqual(payment, null)
  assert.strictEqual(toasts.length, 0)
  assert.strictEqual(state.token(), 'test-pay-token')
  console.log('商户号未配置: body detail stops on 支付暂未开通 (HTTP 200 ignored)')

  resetTransport()
  page = createPage()
  await created(page, 503, { code: '商户号未配置' })
  assert.strictEqual(page.data.phase, 'unavailable')
  assert.strictEqual(page.data.title, '支付暂未开通')
  assert.strictEqual(page.data.payLoading, false)
  assert.strictEqual(payment, null)
  console.log('商户号未配置: body code stops on 支付暂未开通 (HTTP 503 ignored)')

  resetTransport()
  page = createPage()
  await created(page, 200, Object.assign(orderBody('待支付'), payFields()))
  payment.fail({ errMsg: 'requestPayment:fail cancel' })
  assert.strictEqual(page.data.phase, 'confirming')
  assert.notStrictEqual(page.data.phase, 'granted')
  assert.strictEqual(page.data.title, '支付确认中')
  await polls(1, orderBody('待支付'), true)
  assert.strictEqual(page.data.phase, 'confirming')
  assert.notStrictEqual(page.data.phase, 'granted', 'cancel plus unpaid query is not success')
  console.log('用户取消: requestPayment fail does not mark the order paid')

  resetTransport()
  page = createPage()
  await created(page, 201, Object.assign(orderBody('待支付'), payFields()))
  payment.success({})
  assert.strictEqual(page.data.phase, 'confirming')
  assert.notStrictEqual(page.data.phase, 'granted', 'requestPayment success is not payment success')
  respond(500, orderBody('已支付'))
  await drain()
  assert.strictEqual(page.data.phase, 'granting')
  assert.strictEqual(page.data.title, '权益发放中')
  assert.notStrictEqual(page.data.phase, 'granted')
  assert.strictEqual(clock.length, 1)
  assert.strictEqual(clock[0].ms, 2000)
  clock.shift().fn()
  respond(404, orderBody('已发放'))
  await drain()
  assert.strictEqual(page.data.phase, 'granted')
  assert.ok(page.data.successLine.indexOf('已发放') !== -1)
  assert.strictEqual(clock.length, 0, 'granted stops the poll')
  console.log('查单得到已支付: 已支付 shows 权益发放中, 已发放 shows 开通成功, HTTP status ignored')

  resetTransport()
  page = createPage()
  await created(page, 200, Object.assign(orderBody('待支付'), payFields()))
  payment.success({})
  assert.notStrictEqual(page.data.phase, 'granted')
  assert.strictEqual(page.data.confirmSpin, true, 'spinner while the first queries are in flight')
  assert.strictEqual(page.data.confirmTimeout, false)
  assert.strictEqual(page.data.resultTitle, '正在确认支付结果…')
  assert.strictEqual(page.data.resultSub, '')
  await polls(10, orderBody('待支付'))
  assert.strictEqual(page.data.phase, 'confirming')
  assert.strictEqual(page.data.title, '支付结果确认中')
  assert.strictEqual(page.data.confirmSpin, false)
  assert.strictEqual(page.data.confirmTimeout, true)
  assert.strictEqual(page.data.resultSub, '暂未收到最终结果，请稍后刷新，请勿重复支付。')
  assert.notStrictEqual(page.data.phase, 'granted', 'success callback plus unpaid queries must not show success')
  page.refreshStatus()
  page.refreshStatus()
  assert.strictEqual(pending.length, 1, 'refresh cannot be tapped twice')
  assert.strictEqual(page.data.confirmSpin, true)
  assert.strictEqual(page.data.confirmTimeout, false)
  respond(200, orderBody('待支付'))
  await drain()
  assert.strictEqual(page.data.confirmSpin, true, 'one unpaid answer keeps the spinner')
  assert.strictEqual(clock.length, 1)
  clock.shift().fn()
  await polls(9, orderBody('待支付'))
  assert.strictEqual(page.data.phase, 'confirming')
  assert.strictEqual(page.data.confirmTimeout, true)
  assert.notStrictEqual(page.data.phase, 'granted')
  page.check()
  page.check()
  assert.strictEqual(pending.length, 1)
  respond(200, orderBody('待支付'))
  await drain()
  assert.strictEqual(page.data.phase, 'failed')
  assert.strictEqual(page.data.allowRepay, true)
  assert.strictEqual(page.data.allowCheck, false)
  assert.strictEqual(page.data.orderNo, ORDER_NO)
  assert.notStrictEqual(page.data.phase, 'granted')
  console.log('轮询超时: 10 syncs, 2s apart, then the refresh subtitle')
  console.log('查单期间转圈: confirmSpin, no refresh subtitle')
  console.log('查满 10 次后显示刷新')
  console.log('刷新不能重复点: second tap sends no request')
  console.log('查单得到未支付: refresh round still unpaid does not show success')
  console.log('requestPayment 成功但查单未支付: 不显示开通成功')

  resetTransport()
  page = createPage()
  await created(page, 200, Object.assign(orderBody('待支付'), payFields()))
  payment.success({})
  respond(200, { detail: '金额不符' })
  await drain()
  assert.strictEqual(page.data.phase, 'failed')
  assert.strictEqual(page.data.allowCheck, true)
  assert.strictEqual(page.data.allowRepay, false)
  assert.notStrictEqual(page.data.phase, 'granted')
  console.log('支付失败: 金额不符 shows 失败, not unpaid and not success')

  resetTransport()
  page = createPage()
  page.onLoad({ id: ORDER_NO })
  assert.strictEqual(pending[0].method, 'GET')
  assert.strictEqual(pending[0].url, '/test-api/api/v1/pay/orders/' + ORDER_NO)
  respond(200, orderBody('发放异常'))
  await drain()
  assert.strictEqual(page.data.phase, 'grant_error')
  assert.strictEqual(page.data.title, '发放异常')
  console.log('异常: 发放异常')

  resetTransport()
  page = createPage()
  page.onLoad({ id: ORDER_NO })
  respond(200, orderBody('已取消'))
  await drain()
  assert.strictEqual(page.data.phase, 'cancelled')
  console.log('已取消: status 已取消')

  resetTransport()
  page = createPage()
  page.onLoad({ id: ORDER_NO })
  respond(200, orderBody('已退款'))
  await drain()
  assert.strictEqual(page.data.phase, 'cancelled')
  assert.strictEqual(page.data.title, '已退款')
  assert.strictEqual(page.data.centerTitle, '订单已退款')
  console.log('已退款: shown apart from an unpaid order')

  var settled = api.settle(ORDER_NO, 'success')
  var settleReq = pending[0]
  assert.strictEqual(settleReq.method, 'POST')
  assert.strictEqual(settleReq.url, '/test-api/api/v1/pay/orders/' + ORDER_NO + '/sync')
  assert.strictEqual(settleReq.data.outcome, undefined)
  assert.strictEqual(settleReq.data.paid, undefined)
  respond(200, orderBody('待支付'))
  var settleResult = await settled
  assert.strictEqual(settleResult.order.code, 'pending')
  assert.notStrictEqual(settleResult.order.code, 'granted')
  console.log('settle(success) follows the server and stays unpaid')

  var repaid = api.repay(ORDER_NO)
  assert.strictEqual(pending[0].url, '/test-api/api/v1/pay/orders/' + ORDER_NO + '/sync')
  respond(200, orderBody('待支付'))
  var repayResult = await repaid
  assert.strictEqual(repayResult.ok, true)
  assert.strictEqual(repayResult.order.orderNo, ORDER_NO)
  assert.strictEqual(repayResult.order.sameNo, true)
  assert.strictEqual(repayResult.pay.package, 'prepay_id=test-prepay')
  assert.notStrictEqual(repayResult.order.code, 'granted')
  console.log('repay reuses the order number and cached pay params')

  console.log('pay check ok')
}

main().catch(function (error) {
  console.error(error)
  process.exitCode = 1
})
