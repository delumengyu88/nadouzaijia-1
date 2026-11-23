var config = require('../config')

var ID = 'membership_test_live'
var PRICE = '¥1.00'

function dedicated() { return config.liveOneYuanTest === true }

function enabled() {
  return config.mode === 'live' && dedicated() && config.virtualPayment === true &&
    config.trialMode === false && config.calibrating === false
}

function unavailable() {
  return { ok: false, code: 'test_unavailable', message: '当前账号暂不可购买支付测试商品。' }
}

var product = require('./one-yuan-product')({ id: ID, days: 1, membership: 'regular', isTest: true,
  enabled: enabled, unavailable: unavailable })
module.exports = { id: ID, price: PRICE, dedicated: dedicated, enabled: enabled, unavailable: unavailable,
  discover: product.discover, checkout: product.checkout }
