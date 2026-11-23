var config = require('../config')
var id = 'membership_trial'

function isOrderProduct(code) {
  return code === id || code === 'membership_diamond'
}

function enabled() {
  return config.mode === 'live' && config.diamondPayment === true && !config.liveOneYuanTest && !config.calibrating
}
function unavailable() {
  return { ok: false, code: 'diamond_unavailable', message: '体验会员暂不可购买，请返回会员中心刷新。' }
}
var product = require('./one-yuan-product')({ id: id, productId: id, days: 5, membership: 'vip', isTest: false,
  enabled: enabled, unavailable: unavailable })
module.exports = { id: id, price: '¥1.00', isOrderProduct: isOrderProduct, enabled: enabled, unavailable: unavailable,
  discover: product.discover, checkout: product.checkout }
