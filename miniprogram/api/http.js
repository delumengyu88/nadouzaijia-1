var shape = require('./shape')
var state = require('./state')
var request = require('./request')

function notReady() {
  return Promise.resolve({ ok: false, message: '服务暂不可用，请改用本地数据' })
}

// 支付结果只认服务端订单状态。客户端回调不能把订单标成已支付。
var paySessions = {}

function rememberPay(order, pay) {
  if (!order || !order.orderNo) return
  var prev = paySessions[order.orderNo] || {}
  paySessions[order.orderNo] = { pay: pay || prev.pay || null }
}

function savedPay(orderNo) {
  var row = paySessions[orderNo]
  return row && row.pay ? row.pay : null
}

function yuan(fen) {
  if (typeof fen !== 'number' || fen !== Math.floor(fen) || fen < 0) return ''
  var whole = Math.floor(fen / 100)
  var cents = fen % 100
  if (!cents) return '¥' + whole
  var text = cents < 10 ? '0' + cents : String(cents)
  return '¥' + whole + '.' + text
}

function phaseOf(body) {
  var status = body && body.status
  if (body && (body.granted === true || status === '已发放')) return 'granted'
  if (status === '发放异常') return 'grant_error'
  if (status === '已支付') return 'granting'
  if (status === '已退款' || status === '已取消') return 'cancelled'
  if (status === '待支付') return 'pending'
  return 'pending'
}

function viewFromServer(body) {
  var phase = phaseOf(body)
  var priceText = yuan(body.amount_fen)
  var title = body.title || '订单'
  return {
    orderNo: body.order_number,
    product: body.tier || '',
    code: phase,
    status: phase,
    serverStatus: body.status,
    card: title,
    title: title,
    priceText: priceText,
    blurb: body.duration_days ? ('有效期 ' + body.duration_days + ' 天') : '',
    renewLine: '',
    coverLine: '',
    payText: priceText ? ('立即支付 ' + priceText) : '立即支付',
    allowCheck: false,
    allowRepay: false,
    showPay: phase === 'pending',
    successLine: phase === 'granted' ? (title + '已发放') : '',
    navTitle: body.status === '已退款' ? '已退款' : '',
    centerTitle: body.status === '已退款' ? '订单已退款' : ''
  }
}

function payParams(body) {
  var keys = ['timeStamp', 'nonceStr', 'package', 'signType', 'paySign']
  var pay = {}
  var i
  for (i = 0; i < keys.length; i++) {
    if (typeof body[keys[i]] !== 'string' || !body[keys[i]]) return null
    pay[keys[i]] = body[keys[i]]
  }
  return pay
}

function detailText(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return ''
  if (typeof body.detail === 'string') return body.detail
  if (body.order_number) return ''
  if (typeof body.code === 'string') return body.code
  if (typeof body.message === 'string') return body.message
  return ''
}

function normalizePay(body) {
  if (body && Array.isArray(body.detail)) return { ok: false, code: 'invalid', message: '档位无效' }
  var detail = detailText(body)
  if (detail.indexOf('未配置') !== -1) return { ok: false, code: 'unconfigured', message: '支付暂未开通' }
  if (detail === '未登录' || detail === 'authentication required') return { ok: false, code: 'login', message: '请先登录' }
  if (detail === '档位无效') return { ok: false, code: 'invalid_tier', message: '档位无效' }
  if (detail === '缺少微信 openid') return { ok: false, code: 'openid', message: '缺少微信 openid' }
  if (detail === '微信支付下单失败') return { ok: false, code: 'upstream', message: '微信支付下单失败' }
  if (detail === '订单不存在') return { ok: false, code: 'missing', message: '订单不存在' }
  if (detail === '微信支付查询失败') return { ok: false, code: 'query_failed', message: '微信支付查询失败' }
  if (detail === '金额不符') return { ok: false, code: 'amount', message: '金额不符' }
  if (detail === '支付校验失败') return { ok: false, code: 'verify', message: '支付校验失败' }
  if (detail === '订单创建失败') return { ok: false, code: 'create_failed', message: '订单创建失败' }
  if (detail === 'account is disabled') return { ok: false, code: 'disabled', message: '账号不可用' }
  if (body && typeof body.order_number === 'string' && body.order_number) {
    var order = viewFromServer(body)
    return { ok: true, code: order.code, order: order, pay: payParams(body) }
  }
  if (detail) return { ok: false, code: 'failed', message: detail }
  return { ok: false, code: 'unknown', message: '服务暂不可用' }
}

function payRequest(method, path, data) {
  var config = require('../config')
  var state = require('./state')
  var base = String(config.apiBase || '').replace(/\/$/, '')
  if (!base) return Promise.resolve({ ok: false, code: 'offline', message: '未配置服务地址' })
  var token = state.token()
  if (!token) return Promise.resolve({ ok: false, code: 'login', message: '请先登录' })
  return new Promise(function (resolve) {
    try {
      wx.request({
        url: base + path,
        method: method,
        timeout: 15000,
        data: data === undefined ? {} : data,
        header: { Authorization: 'Bearer ' + token },
        success: function (res) {
          var normalized = normalizePay(res && res.data)
          if (normalized.code === 'login') state.clearSession(token)
          resolve(normalized)
        },
        fail: function () { resolve({ ok: false, code: 'network', message: '网络异常，请重试' }) }
      })
    } catch (error) {
      resolve({ ok: false, code: 'network', message: '网络异常，请重试' })
    }
  })
}

function orderPath(orderNo) {
  return '/api/v1/pay/orders/' + encodeURIComponent(orderNo || '')
}

function readOrder(orderNo) {
  return payRequest('GET', orderPath(orderNo))
}

function syncOrder(orderNo) {
  return payRequest('POST', orderPath(orderNo) + '/sync')
}

function failedView(orderNo, result) {
  return {
    orderNo: orderNo,
    code: result.code || 'failed',
    status: 'failed',
    allowCheck: true,
    allowRepay: false,
    showPay: false,
    message: result.message || ''
  }
}

module.exports = {
  bootstrap: function () { state.load() },
  reset: function () { state.reset() },
  splashSeen: function () { return true },
  markSplashSeen: function () {},
  markAnnounceSeen: function () {},
  homeState: function () {
    return {
      loggedIn: !!state.userId(),
      tier: 'free',
      banner: state.userId() ? 'free' : 'guest',
      bannerText: state.userId() ? '尚未开通会员' : '未登录',
      runningId: '',
      showAnnounce: false,
      showDone: false,
      doneText: '',
      doneQueryId: '',
      examples: [],
      platformTotal: 0,
      draft: state.load().draft || ''
    }
  },
  login: require('./auth').login,
  switchAccount: function () { return this.profile() },
  accountKind: function () { return state.currentId() },
  profile: function () { return require('./mock').profile() },
  memberCenter: function () { return require('./mock').memberCenter() },
  setDraft: function (value) { state.load().draft = String(value || ''); state.save() },
  getDraft: function () { return state.load().draft || '' },
  parseTitles: function (raw) { return require('../utils/text').parseTitles(raw) },
  parseFile: function () { return { ok: false, message: '请改为粘贴节目名' } },
  createQuery: function () { return notReady() },
  getQuery: function () { return null },
  advanceQuery: function () { return null },
  markQuerySeen: function () {},
  retryItem: function () { return null },
  retryAll: function () { return null },
  listRecords: function () { return [] },
  searchProgram: function (name) {
    return request('/api/v1/programs/search', { name: name }).then(function (body) {
      return shape.sanitizeSearch(body)
    })
  },
  exampleReport: function () { return null },
  programMeta: function (name) { return { name: name, type: '', year: '' } },
  tierOfAccount: function () { return state.userId() ? 'free' : 'guest' },
  checkout: function (product) {
    var tier = typeof product === 'string' ? product : ''
    return payRequest('POST', '/api/v1/pay/orders', { tier: tier }).then(function (result) {
      if (result.ok) rememberPay(result.order, result.pay)
      return result
    })
  },
  getOrder: function (orderNo) {
    if (!orderNo) return Promise.resolve(null)
    return readOrder(orderNo).then(function (result) { return result.ok ? result.order : null })
  },
  listOrders: function () { return [] },
  startPay: function (orderNo) {
    var pay = savedPay(orderNo)
    return readOrder(orderNo).then(function (result) {
      if (!result.ok) return { ok: false, code: result.code, message: result.message }
      var order = result.order
      if (order.code === 'granted' || order.code === 'granting' || order.code === 'grant_error') {
        return { ok: false, code: order.code, message: '请勿重复支付', order: order }
      }
      if (!pay) return { ok: false, code: 'no_params', message: '请重新下单', order: order }
      var paying = Object.assign({}, order, { status: 'paying', code: 'paying', showPay: false })
      return { ok: true, code: 'paying', order: paying, pay: pay }
    })
  },
  setOrderStatus: function () { return null },
  settle: function (orderNo, outcome) {
    // outcome 只在原型里模拟结果。线上忽略它，避免把订单标成已支付。
    return syncOrder(orderNo).then(function (result) {
      if (result.ok) return { ok: true, code: result.code, order: result.order }
      return { ok: false, code: result.code, message: result.message, order: result.code === 'unconfigured' ? null : failedView(orderNo, result) }
    })
  },
  checkStatus: function (orderNo) {
    if (!orderNo) return Promise.resolve(null)
    return syncOrder(orderNo).then(function (result) {
      if (result.ok) return result.order
      if (result.code === 'unconfigured') return { orderNo: orderNo, code: 'unconfigured', status: 'unavailable' }
      return failedView(orderNo, result)
    })
  },
  repay: function (orderNo) {
    var pay = savedPay(orderNo)
    return syncOrder(orderNo).then(function (result) {
      if (!result.ok) return { ok: false, code: result.code, message: result.message, order: result.code === 'unconfigured' ? null : failedView(orderNo, result) }
      var order = result.order
      if (order.code !== 'pending') return { ok: false, code: order.code, message: '订单已支付，不能再次支付', order: order }
      if (!pay) return { ok: false, code: 'no_params', message: '请重新下单', order: order }
      var paying = Object.assign({}, order, { status: 'paying', code: 'paying', showPay: false, allowRepay: false })
      paying.sameNo = paying.orderNo === orderNo
      return { ok: true, code: 'paying', order: paying, pay: pay }
    })
  },
  inviteState: function () { return { guest: !state.userId(), bound: false } },
  redeem: function () { return notReady() },
  listEvidence: function () { return [] },
  evidenceDetail: function () { return null },
  createEvidence: function () { return notReady() },
  withdrawEvidence: function () { return notReady() },
  confirmEvidence: function () { return notReady() },
  agreements: function () {
    return [
      { id: 'user', title: '用户协议' },
      { id: 'privacy', title: '隐私指引' },
      { id: 'member', title: '会员服务协议' }
    ]
  },
  agreement: function (id) {
    return {
      title: '协议',
      paragraphs: ['运营主体：那豆在架', '完整文本由服务端下发。']
    }
  },
  exportBook: function () { return { ok: false, message: '当前服务未提供导出' } },
  maskPhone: function (phone) { return phone }
}
