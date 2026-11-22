var assert = require('assert')
var config = require('../miniprogram/config')
config.calibrating = false
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
  if (name === 'member') {
    page.onShow()
    page.pick({ currentTarget: { dataset: { id: 'membership_vip' } } })
    assert.strictEqual(page.data.showPay, true)
    assert.strictEqual(page.data.payLabel, '确认开通')
    assert.strictEqual(page.data.selected, 'membership_vip')
    // Hybrid mode does not switch the demo account, so onShow stays a guest.
    // The payment guard is what a signed-in confirm must hit.
    page.setData({ guest: false })
  }
  pages[name].forEach(function (handler) { page[handler]() })
  if (name === 'pay') { page.onLoad({ product: 'membership_vip' }); assert.strictEqual(page.data.phase, 'trial') }
})
assert.strictEqual(modals, 7)
assert.strictEqual(api.listOrders().length, orders)
config.trialMode = false
assert.strictEqual(trial.blockPayment(), false)
console.log('trial payment guards and disabled switch checks ok')
