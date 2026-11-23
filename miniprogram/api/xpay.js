var state = require('./state')

function deviceInfo() {
  if (typeof wx.getDeviceInfo === 'function') return wx.getDeviceInfo()
  return wx.getSystemInfoSync ? wx.getSystemInfoSync() : {}
}

function isAppleDevice(device) {
  return String(device.platform || '').toLowerCase() === 'ios'
}

function clientError(pay) {
  if (typeof wx.requestVirtualPayment !== 'function') return '当前微信版本不支持虚拟支付，请更新微信后再试。'
  var device = deviceInfo()
  if (!isAppleDevice(device)) return ''
  var system = /(?:iOS|iPadOS)\s*(\d+)/i.exec(String(device.system || ''))
  if (!system || Number(system[1]) < 15) return 'Apple 支付需 iOS 15 或更高版本，请检查设备系统版本。'
  var info = wx.getAppBaseInfo ? wx.getAppBaseInfo() : (wx.getSystemInfoSync ? wx.getSystemInfoSync() : {})
  var parts = String(info.version || '').split('.').map(function (part) { return parseInt(part, 10) || 0 })
  if (!(parts[0] > 8 || (parts[0] === 8 && (parts[1] > 0 || parts[2] >= 68)))) {
    return 'iOS 需微信 8.0.68 或更高版本，请更新微信后再试。'
  }
  if (!pay) return ''
  var data
  try { data = JSON.parse(pay.signData) } catch (error) { return '支付订单信息不完整，请重试。' }
  if (!data || (data.env !== 0 && data.env !== 1) || !Number.isSafeInteger(data.goodsPrice) ||
      data.goodsPrice <= 0 || data.buyQuantity !== 1) return '支付订单信息不完整，请重试。'
  if (data.env === 1) return 'Apple 支付不支持沙箱，请联系运营人员。'
  if (data.goodsPrice < 100) return 'Apple 支付单笔最低 1 元，当前商品无法在此设备购买。'
  return ''
}

function paymentNote() {
  return isAppleDevice(deviceInfo()) ? 'Apple 支付仅支持中国大陆 App Store 账户。' : '支付方式：微信虚拟支付'
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

function checkout(tier, request, programName) {
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
    var body = { tier: tier, code: code }
    if (programName) body.program_name = programName
    return request('POST', '/api/v1/xpay/orders', body).then(function (result) {
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

module.exports = { checkout: checkout, params: params, configurationError: configurationError, clientError: clientError, paymentNote: paymentNote }
