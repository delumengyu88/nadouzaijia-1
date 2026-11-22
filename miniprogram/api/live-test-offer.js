var config = require('../config')
var state = require('./state')
var request = require('./request')
var xpay = require('./xpay')

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

function oneYuan(fen) { return fen === 100 || fen === '100' }

function discover() {
  var token = state.token()
  if (!enabled() || !token) return Promise.resolve(null)
  return request('/api/v1/pay/tiers?channel=virtual').then(function (body) {
    if (!enabled() || token !== state.token() || !Array.isArray(body.tiers)) return null
    var rows = body.tiers.filter(function (row) { return row && row.id === ID })
    if (rows.length !== 1) return null
    var row = rows[0]
    if (row.membership !== 'regular' || !oneYuan(row.amount_fen) || row.amount_yuan !== '1.00' ||
        row.duration_days !== 1 || typeof row.product_id !== 'string' || !row.product_id.trim()) return null
    return row.product_id
  }, function () { return null })
}

function validOrder(body, productId) {
  if (!body || body.isTest !== true || body.granted !== false || body.membership !== 'regular' || body.tier !== ID || !oneYuan(body.amount_fen) || body.duration_days !== 1 ||
      body.status !== '待支付' || typeof body.order_number !== 'string' || !/^XV.+/.test(body.order_number)) return false
  var payment = xpay.params(body)
  if (!payment) return false
  try {
    var sign = JSON.parse(payment.signData)
    return sign.env === 0 && sign.buyQuantity === 1 && sign.goodsPrice === 100 && sign.currencyType === 'CNY' &&
      sign.productId === productId && sign.outTradeNo === body.order_number && sign.attach === ID
  } catch (err) { return false }
}

function checkout(normalize) {
  var token = state.token()
  return discover().then(function (productId) {
    if (!productId || !enabled() || !token || state.token() !== token) return unavailable()
    return xpay.checkout(ID, function (method, path, data) {
      if (!enabled() || state.token() !== token) return Promise.resolve(unavailable())
      return request(path, data, { method: method }).then(function (body) {
        if (!enabled() || state.token() !== token || !validOrder(body, productId)) return unavailable()
        return normalize(body)
      }, unavailable)
    })
  })
}

module.exports = { id: ID, price: PRICE, dedicated: dedicated, enabled: enabled, unavailable: unavailable, discover: discover, checkout: checkout }
