var assert = require('assert')
var fs = require('fs')
var path = require('path')
var config = require('../miniprogram/config')

config.mode = 'live'
config.trialMode = false
config.calibrating = false
config.apiBase = '/test-api'

var calls = []
var pending = []
var storage = {}
var tabs = []
var navigated = []
global.getCurrentPages = function () { return [{}, {}] }
global.wx = {
  getStorageSync: function (key) { return storage[key] },
  setStorageSync: function (key, value) { storage[key] = value },
  removeStorageSync: function (key) { delete storage[key] },
  getSystemInfoSync: function () { return { statusBarHeight: 20, windowWidth: 375 } },
  request: function (opt) {
    calls.push(opt)
    pending.push(opt)
  },
  navigateTo: function (opt) { navigated.push(opt.url) },
  switchTab: function (opt) { tabs.push(opt.url) },
  navigateBack: function () {},
  showToast: function () {}
}

var pageDef
global.Page = function (def) { pageDef = def }
require('../miniprogram/pages/orders/index')
var state = require('../miniprogram/api/state')
var http = require('../miniprogram/api/http')

function page() {
  return Object.assign({}, pageDef, {
    data: Object.assign({}, pageDef.data),
    setData: function (value) { Object.assign(this.data, value) }
  })
}

function drain() {
  return new Promise(function (resolve) { setImmediate(resolve) })
}

function respond(status, data) {
  var req = pending.shift()
  assert(req, 'missing request')
  if (status === 'fail') {
    req.fail()
    return
  }
  req.success({ statusCode: status, data: data })
}

function session() {
  state.setSession({
    token: 'orders-token',
    expires_at: new Date(Date.now() + 86400000).toISOString(),
    user: { id: 'orders-user', tier: 'free' }
  })
}

var LONG = '这是一个特别特别特别长的会员商品名称用来检查省略号是否单行显示'
var FIXTURE = [
  { orderNo: 'ND-A', title: '支付测试', line: '待支付 · ¥0.01 · 2026-11-22', status: 'pending', statusText: '待支付', product: 'membership_test', isTest: true },
  { orderNo: 'ND-B', title: LONG, line: '已支付 · ¥599 · 2026-11-21', status: 'paid', statusText: '', product: 'membership_vip', isTest: false },
  { orderNo: 'ND-C', title: 'VIP', line: '已发放 · ¥599 · 2026-11-21', status: 'granted', statusText: '已发放', product: 'membership_vip', isTest: false },
  { orderNo: 'ND-D', title: 'VIP', line: '发放异常 · ¥599 · 2026-11-21', status: 'grant_error', statusText: '发放异常', product: 'membership_vip', isTest: false },
  { orderNo: 'ND-E', title: '普通会员', line: '已退款 · ¥299 · 2026-11-20', status: 'refunded', statusText: null, product: 'membership_regular', isTest: false },
  { orderNo: 'ND-F', title: null, line: '已结清 · ¥0 · 2026-11-19', status: null, statusText: '已结清', product: 'membership_dev', isTest: true },
  { orderNo: 'ND-G', title: null, line: ' · ¥0 · 2026-11-18', status: null, statusText: '', product: null, isTest: false },
  { orderNo: 'ND-H', title: 'VIP', line: '已发放 · ¥599 · 2026-11-21', status: 'granted', statusText: '自定义文案', product: 'membership_vip', isTest: false },
  { orderNo: 'ND-I', title: '   ', line: '已支付 · ¥599 · 2026-11-21', status: 'paid', statusText: '已支付', product: 'membership_vip', isTest: false },
  { orderNo: 'ND-J', title: 'VIP', line: '已发放 · 尾部不是价格', status: 'granted', statusText: '已发放', product: 'membership_vip', isTest: false },
  { orderNo: 'ND-K', title: 'VIP', line: '请原样保留这段说明', status: 'paid', statusText: '已支付', product: 'membership_vip', isTest: false }
]

function row(list, no) {
  var found = null
  list.forEach(function (item) {
    if (item.orderNo === no) found = item
  })
  assert(found, 'missing row ' + no)
  return found
}

async function main() {
  var root = path.join(__dirname, '../miniprogram/pages/orders')
  var wxml = fs.readFileSync(path.join(root, 'index.wxml'), 'utf8')
  var wxss = fs.readFileSync(path.join(root, 'index.wxss'), 'utf8')
  var source = fs.readFileSync(path.join(root, 'index.js'), 'utf8')
  assert(wxml.indexOf('还没有订单') !== -1)
  assert(wxml.indexOf('去开通会员') !== -1)
  assert(wxml.indexOf('网络异常，请重试') !== -1)
  assert(/<button\b[^>]*bindtap="retry"[^>]*>重试<\/button>/.test(wxml), 'retry is a button')
  assert(wxml.indexOf('加载失败，点击重试') === -1)
  assert(wxml.indexOf('暂无订单') !== -1)
  assert(wxml.indexOf('>测试<') !== -1)
  assert(wxml.indexOf('联系客服') !== -1)
  assert(wxml.indexOf('open-type="contact"') !== -1)
  assert(wxml.indexOf('session-from="order:{{item.orderNo}}"') !== -1)
  assert(wxml.indexOf('登录后继续') !== -1)
  assert(wxml.indexOf('微信登录') !== -1)
  assert(wxml.indexOf('{{item.product}}') === -1)
  assert(wxss.indexOf('text-overflow: ellipsis') !== -1)
  assert(wxss.indexOf('white-space: nowrap') !== -1)
  assert(wxss.indexOf('#1D4E9E') !== -1)
  assert(wxss.indexOf('#e4570e') !== -1)
  assert(wxss.indexOf('#e24b4a') !== -1)
  assert(wxss.indexOf('#8b95a3') !== -1)
  assert(wxss.indexOf('#eef0f3') !== -1)
  assert(wxss.indexOf('#8b93a0') !== -1)
  assert(wxss.indexOf('20rpx') !== -1)
  assert(wxss.indexOf('.order-status.paid') === -1)
  assert(source.indexOf('setOrderStatus') === -1)
  assert(source.indexOf('settle') === -1)
  assert(source.indexOf('../../utils/login') !== -1)
  ;['setClipboardData', 'getClipboardData', 'chooseAvatar', 'getPhoneNumber', 'chooseImage', 'chooseMedia'].forEach(function (name) {
    assert(source.indexOf(name) === -1 && wxml.indexOf(name) === -1, name)
  })

  session()
  var listed = http.listOrders()
  assert.strictEqual(calls[0].url, '/test-api/api/v1/pay/orders')
  assert.strictEqual(calls[0].method, 'GET')
  assert.strictEqual(calls[0].header.Authorization, 'Bearer orders-token')
  var sent = JSON.parse(JSON.stringify(FIXTURE))
  respond(200, sent)
  var body = await listed
  assert.strictEqual(body, sent)
  assert.strictEqual(body[0].status, 'pending')
  assert.strictEqual(body[3].status, 'grant_error')
  assert.strictEqual(body[5].title, null)
  assert.strictEqual(body[5].isTest, true)

  var orders = page()
  var before = calls.length
  orders.onShow()
  assert.strictEqual(orders.data.loading, true)
  assert.strictEqual(orders.data.failed, false)
  assert.strictEqual(orders.data.needLogin, false)
  assert.strictEqual(orders.data.empty, false)
  assert.deepStrictEqual(orders.data.rows, [])
  assert(JSON.stringify(orders.data).indexOf('DD2026') === -1, 'live first paint must not show mock orders')
  assert.strictEqual(calls.length, before + 1)
  assert.strictEqual(calls[calls.length - 1].url, '/test-api/api/v1/pay/orders')
  respond(200, JSON.parse(JSON.stringify(FIXTURE)))
  await drain()
  assert.strictEqual(orders.data.loading, false)
  assert.strictEqual(orders.data.failed, false)
  assert.strictEqual(orders.data.empty, false)
  assert.deepStrictEqual(orders.data.rows.map(function (item) { return item.orderNo }), [
    'ND-A', 'ND-B', 'ND-C', 'ND-D', 'ND-E', 'ND-F', 'ND-G', 'ND-H', 'ND-I', 'ND-J', 'ND-K'
  ])

  var pendingRow = row(orders.data.rows, 'ND-A')
  assert.strictEqual(pendingRow.title, '支付测试')
  assert.strictEqual(pendingRow.line, '¥0.01 · 2026-11-22')
  assert.strictEqual(pendingRow.status, 'pending')
  assert.strictEqual(pendingRow.statusText, '待支付')
  assert.strictEqual(pendingRow.statusKind, 'pending')
  assert.strictEqual(pendingRow.isTest, true)
  assert.strictEqual(pendingRow.service, false)

  var paid = row(orders.data.rows, 'ND-B')
  assert.strictEqual(paid.title, LONG)
  assert.strictEqual(paid.line, '¥599 · 2026-11-21')
  assert.strictEqual(paid.status, 'paid')
  assert.strictEqual(paid.statusText, '已支付')
  assert.strictEqual(paid.statusKind, '')
  assert.strictEqual(paid.isTest, false)

  var granted = row(orders.data.rows, 'ND-C')
  assert.strictEqual(granted.line, '¥599 · 2026-11-21')
  assert.strictEqual(granted.status, 'granted')
  assert.strictEqual(granted.statusText, '已发放')
  assert.strictEqual(granted.statusKind, 'granted')

  var broken = row(orders.data.rows, 'ND-D')
  assert.strictEqual(broken.line, '¥599 · 2026-11-21')
  assert.strictEqual(broken.status, 'grant_error')
  assert.strictEqual(broken.statusText, '发放异常')
  assert.strictEqual(broken.statusKind, 'error')
  assert.strictEqual(broken.service, true)

  var refunded = row(orders.data.rows, 'ND-E')
  assert.strictEqual(refunded.line, '¥299 · 2026-11-20')
  assert.strictEqual(refunded.status, 'refunded')
  assert.strictEqual(refunded.statusText, '已退款')
  assert.strictEqual(refunded.statusKind, 'refunded')

  var missingTitle = row(orders.data.rows, 'ND-F')
  assert.strictEqual(missingTitle.line, '¥0 · 2026-11-19')
  assert.strictEqual(missingTitle.title, '会员订单')
  assert.strictEqual(missingTitle.status, null)
  assert.strictEqual(missingTitle.statusText, '已结清')
  assert.strictEqual(missingTitle.statusKind, '')
  assert.strictEqual(missingTitle.isTest, true)
  assert(JSON.stringify(missingTitle).indexOf('membership_dev') === -1)

  var unknown = row(orders.data.rows, 'ND-G')
  assert.strictEqual(unknown.line, ' · ¥0 · 2026-11-18')
  assert.strictEqual(unknown.title, '会员订单')
  assert.strictEqual(unknown.status, null)
  assert.strictEqual(unknown.statusText, '—')
  assert.strictEqual(unknown.statusKind, '')
  assert.strictEqual(unknown.isTest, false)

  var custom = row(orders.data.rows, 'ND-H')
  assert.strictEqual(custom.line, '已发放 · ¥599 · 2026-11-21')
  assert.strictEqual(custom.status, 'granted')
  assert.strictEqual(custom.statusText, '自定义文案')
  assert.strictEqual(custom.statusKind, 'granted')

  var blankTitle = row(orders.data.rows, 'ND-I')
  assert.strictEqual(blankTitle.line, '¥599 · 2026-11-21')
  assert.strictEqual(blankTitle.title, '会员订单')
  assert.strictEqual(row(orders.data.rows, 'ND-J').line, '尾部不是价格')
  assert.strictEqual(row(orders.data.rows, 'ND-K').line, '请原样保留这段说明')
  assert.strictEqual(orders.data.rows.filter(function (item) { return item.isTest }).length, 2)
  orders.open({ currentTarget: { dataset: { id: 'ND-A' } } })
  assert.strictEqual(navigated.length, 0, 'live list does not open the pay page')

  var empty = page()
  empty.onShow()
  respond(200, [])
  await drain()
  assert.strictEqual(empty.data.loading, false)
  assert.strictEqual(empty.data.empty, true)
  assert.strictEqual(empty.data.failed, false)
  assert.strictEqual(empty.data.needLogin, false)
  assert.deepStrictEqual(empty.data.rows, [])
  empty.member()
  assert.strictEqual(tabs.pop(), '/pages/member/index')

  var denied = page()
  denied.onShow()
  respond(401, { detail: 'authentication required' })
  await drain()
  assert.strictEqual(denied.data.needLogin, true)
  assert.strictEqual(denied.data.failed, false)
  assert.strictEqual(denied.data.empty, false)
  assert.strictEqual(denied.data.loading, false)
  assert.strictEqual(state.token(), '')

  var guest = page()
  var guestCalls = calls.length
  guest.onShow()
  assert.strictEqual(calls.length, guestCalls, 'logged-out list must not request')
  assert.strictEqual(guest.data.loading, false)
  assert.strictEqual(guest.data.needLogin, true)
  assert.strictEqual(guest.data.failed, false)
  assert.strictEqual(guest.data.empty, false)
  assert.deepStrictEqual(guest.data.rows, [])

  session()
  var failed = page()
  failed.onShow()
  respond('fail')
  await drain()
  assert.strictEqual(failed.data.failed, true)
  assert.strictEqual(failed.data.loading, false)
  assert.strictEqual(failed.data.needLogin, false)
  assert.strictEqual(failed.data.empty, false)
  failed.retry()
  assert.strictEqual(failed.data.loading, true)
  assert.strictEqual(failed.data.failed, false)
  assert.deepStrictEqual(failed.data.rows, [])
  respond(500, {})
  await drain()
  assert.strictEqual(failed.data.failed, true)
  failed.retry()
  respond(200, [])
  await drain()
  assert.strictEqual(failed.data.failed, false)
  assert.strictEqual(failed.data.empty, true)

  calls.forEach(function (opt) {
    assert.strictEqual(opt.method, 'GET')
    assert.strictEqual(opt.url, '/test-api/api/v1/pay/orders')
  })

  console.log('orders list api checks ok')
}

main().catch(function (error) {
  console.error(error)
  process.exitCode = 1
})
