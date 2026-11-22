var state = require('./state')

function clientError() {
  if (typeof wx.requestVirtualPayment !== 'function') return '当前微信版本不支持虚拟支付，请更新微信后再试。'
  var legacy = {}
  if (!wx.getDeviceInfo || !wx.getAppBaseInfo) legacy = wx.getSystemInfoSync ? wx.getSystemInfoSync() : {}
  var device = wx.getDeviceInfo ? wx.getDeviceInfo() : legacy
  if (String(device.platform || '').toLowerCase() !== 'ios') return ''
  var info = wx.getAppBaseInfo ? wx.getAppBaseInfo() : legacy
  var parts = String(info.version || '').split('.').map(function (part) { return parseInt(part, 10) || 0 })
  if (parts[0] > 8 || (parts[0] === 8 && (parts[1] > 0 || parts[2] >= 68))) return ''
  return 'iOS 需微信 8.0.68 或更高版本，请更新微信后再试。'
}

function configurationError(error) {
  var messages = {
    '-15008': '当前会员支付尚未开通，请稍后再试。',
    '-15010': '当前会员商品尚未上架，请稍后再试。',
    '-15011': '当前版本不能使用此支付环境，请联系运营人员。',
    '-15013': '当前会员商品价格待核对，请稍后再试。',
    '-15014': '当前会员商品还在同步，请稍后再试。',
    '-15018': '当前会员商品尚未通过审核，请稍后再试。'
  }
  return messages[error && error.errCode] || ''
}

function checkout(tier, request) {
  var unsupported = clientError()
  if (unsupported) return Promise.resolve({ ok: false, code: 'unsupported', message: unsupported })
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

module.exports = { checkout: checkout, params: params, configurationError: configurationError }
