var state = require('./state')

function checkout(tier, request) {
  if (typeof wx.requestVirtualPayment !== 'function') {
    return Promise.resolve({ ok: false, code: 'unsupported', message: '当前微信版本不支持虚拟支付' })
  }
  var token = state.token()
  if (!token) return Promise.resolve({ ok: false, code: 'login', message: '请先登录' })
  return new Promise(function (resolve) {
    wx.login({
      success: function (result) { resolve(result.code || '') },
      fail: function () { resolve('') }
    })
  }).then(function (code) {
    if (state.token() !== token) return { ok: false, code: 'session_changed', message: '登录状态已变化' }
    if (!code) return { ok: false, code: 'login_failed', message: '微信登录失败，请重试' }
    return request('POST', '/api/v1/xpay/orders', { tier: tier, code: code }).then(function (result) {
      if (result.ok && (!result.pay || result.pay.mode !== 'short_series_goods')) {
        return { ok: false, code: 'invalid', message: '虚拟支付参数不完整' }
      }
      return result
    })
  })
}

function params(body) {
  var value = body.virtual_payment
  if (!value || value.mode !== 'short_series_goods') return null
  if (![value.signData, value.paySig, value.signature].every(function (s) { return typeof s === 'string' && s.length > 0 })) return null
  return { mode: value.mode, signData: value.signData, paySig: value.paySig, signature: value.signature }
}

module.exports = { checkout: checkout, params: params }
