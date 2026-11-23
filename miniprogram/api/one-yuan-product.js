var state = require('./state')
var request = require('./request')
var xpay = require('./xpay')

// Validate the catalog row and signed parameters again at checkout for the current session.
function oneYuanProduct(spec) {
  function oneYuan(fen) { return fen === 100 || fen === '100' }

  function matchedProduct(row) {
    if (!row || row.id !== spec.id || row.membership !== spec.membership || !oneYuan(row.amount_fen) ||
        row.amount_yuan !== '1.00' || row.duration_days !== spec.days ||
        typeof row.product_id !== 'string' || !row.product_id.trim()) return null
    return row.product_id
  }

  function contextProduct(row) {
    return row && row.purchasable === true && row.unlock_product === 'membership_vip' && matchedProduct(row)
  }

  function discover(programName) {
    var token = state.token()
    if (!spec.enabled() || !token) return Promise.resolve(null)
    var path = '/api/v1/pay/tiers?channel=virtual' + (programName ? '&program_name=' + encodeURIComponent(programName) : '')
    return request(path).then(function (body) {
      if (!spec.enabled() || token !== state.token() || !Array.isArray(body.tiers)) return null
      var rows = body.tiers.filter(function (row) { return row && row.id === spec.id })
      if (rows.length !== 1) return null
      return programName ? contextProduct(rows[0]) : matchedProduct(rows[0])
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

  function checkout(normalize, programName) {
    var token = state.token()
    return discover(programName).then(function (productId) {
      if (!productId || !spec.enabled() || !token || state.token() !== token) return spec.unavailable()
      return xpay.checkout(spec.id, function (method, path, data) {
        if (!spec.enabled() || state.token() !== token) return Promise.resolve(spec.unavailable())
        return request(path, data, { method: method }).then(function (body) {
          if (!spec.enabled() || state.token() !== token || !validOrder(body, productId)) return spec.unavailable()
          return normalize(body)
        }, spec.unavailable)
      }, programName)
    })
  }

  return { discover: discover, checkout: checkout, contextProduct: contextProduct }
}

module.exports = oneYuanProduct
