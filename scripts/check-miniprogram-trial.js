var assert = require('assert')
var config = require('../miniprogram/config')
var trial = require('../miniprogram/utils/trial')
assert.strictEqual(config.trialMode, true)
var modals = 0
var store = {}
global.wx = { showModal: function (opts) { assert.strictEqual(opts.content, trial.message); modals++ },
  getStorageSync: function (k) { return store[k] }, setStorageSync: function (k, v) { store[k] = v },
  getSystemInfoSync: function () { return { statusBarHeight: 20 } },
  navigateTo: function () { throw new Error('payment must not navigate') } }
var api = require('../miniprogram/api')
api.reset(); api.switchAccount('free')
var orders = api.listOrders().length
var pages = { member: ['go'], report: ['buyMember', 'buySingle'], viewer: ['upgrade'], pay: ['pay', 'repay', 'settle'] }
Object.keys(pages).forEach(function (name) {
  var def
  global.Page = function (p) { def = p }
  require('../miniprogram/pages/' + name + '/index')
  var page = Object.assign({}, def, { data: {}, setData: function (v) { Object.assign(this.data, v) } })
  pages[name].forEach(function (handler) { page[handler]() })
  if (name === 'pay') { page.onLoad({ product: 'membership_vip' }); assert.strictEqual(page.data.phase, 'trial'); assert(page.data.trialData) }
})
assert.strictEqual(modals, 7)
assert.strictEqual(api.listOrders().length, orders)
;['home','input','member','profile','records','orders','invite','evidence','evidence-list','evidence-detail','legal','legal-doc','pay'].forEach(function (page) {
  assert(trial.mockPage(page), page)
})
;['query','report','viewer','splash'].forEach(function (page) { assert(!trial.mockPage(page), page) })
assert(trial.mockPage('report', { example: '1' }))
config.mode = 'mock'; assert(trial.mockPage('report'))
config.trialMode = false
assert(!trial.mockPage('home') && !trial.mockPage('report', { example: '1' }))
assert.strictEqual(trial.blockPayment(), false)
console.log('trial payment guards, mock banners and disabled switch checks ok')
