var state = require('./state')
var request = require('./request')
var xpay = require('./xpay')

// Validate the catalog row and signed parameters again at checkout for the current session.
function oneYuanProduct(spec) {
  function oneYuan(fen) { return fen === 100 || fen === '100' }

  function discover() {
    var token = state.token()
    if (!spec.enabled() || !token) return Promise.resolve(null)
    return request('/api/v1/pay/tiers?channel=virtual').then(function (body) {
      if (!spec.enabled() || token !== state.token() || !Array.isArray(body.tiers)) return null
      var rows = body.tiers.filter(function (row) { return row && row.id === spec.id })
      if (rows.length !== 1) return null
      var row = rows[0]
      if (row.membership !== spec.membership || !oneYuan(row.amount_fen) || row.amount_yuan !== '1.00' ||
          row.duration_days !== spec.days || typeof row.product_id !== 'string' || !row.product_id.trim()) return null
      if (spec.productId && row.product_id !== spec.productId) return null
      return row.product_id
    }, function () { return null })
  }

  function validOrder(body, productId) {
    if (!body || body.isTest !== spec.isTest || body.granted !== false || body.membership !== spec.membership || body.tier !== spec.id || !oneYuan(body.amount_fen) || body.duration_days !== spec.days ||
        body.status !== '待支付' || typeof body.order_number !== 'string' || !/^XV.+/.test(body.order_number)) return false
    var payment = xpay.params(body)
    if (!payment) return false
    try {
      var sign = JSON.parse(payment.signData)
      return sign.env === 0 && sign.buyQuantity === 1 && sign.goodsPrice === 100 && sign.currencyType === 'CNY' &&
        sign.productId === productId && sign.outTradeNo === body.order_number && sign.attach === spec.id
    } catch (err) { return false }
  }

  function checkout(normalize) {
    var token = state.token()
    return discover().then(function (productId) {
      if (!productId || !spec.enabled() || !token || state.token() !== token) return spec.unavailable()
      return xpay.checkout(spec.id, function (method, path, data) {
        if (!spec.enabled() || state.token() !== token) return Promise.resolve(spec.unavailable())
        return request(path, data, { method: method }).then(function (body) {
          if (!spec.enabled() || state.token() !== token || !validOrder(body, productId)) return spec.unavailable()
          return normalize(body)
        }, spec.unavailable)
      })
    })
  }

  return { discover: discover, checkout: checkout }
}

module.exports = oneYuanProduct
