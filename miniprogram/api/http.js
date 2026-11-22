var shape = require('./shape')
var state = require('./state')
var request = require('./request')
var xpay = require('./xpay')
var testOffer = require('./live-test-offer')
var evidence = require('./evidence')

function notReady() {
  return Promise.resolve({ ok: false, message: '服务暂不可用，请改用本地数据' })
}

// 支付结果只认服务端订单状态。客户端回调不能把订单标成已支付。
class PayClient {
  static remember(order, pay, token) {
    if (!order || !order.orderNo || !token || token !== state.token()) return
    var previousPay = PayClient.saved(order.orderNo)
    PayClient.sessions[order.orderNo] = { token: token, pay: pay || previousPay || null }
  }

  static saved(orderNo) {
    var row = PayClient.sessions[orderNo]
    return row && row.token === state.token() ? row.pay : null
  }

  static yuan(fen) {
    if (typeof fen === 'number' && Number.isSafeInteger(fen)) fen = String(fen)
    if (typeof fen !== 'string' || !/^\d+$/.test(fen)) return ''
    var digits = '00' + fen
    var whole = digits.slice(0, -2).replace(/^0+(?=\d)/, '')
    var cents = digits.slice(-2)
    return '¥' + whole + (cents === '00' ? '' : '.' + cents)
  }

  static phaseOf(body) {
    var status = body && body.status
    if (body && (body.granted === true || status === '已发放')) return 'granted'
    if (status === '发放异常') return 'grant_error'
    if (status === '已支付') return 'granting'
    if (status === '已退款' || status === '已取消') return 'cancelled'
    if (status === '待支付') return 'pending'
    return 'pending'
  }

  static viewFromServer(body) {
    var phase = PayClient.phaseOf(body)
    var priceText = PayClient.yuan(body.amount_fen)
    if (body.tier === testOffer.id && priceText === '¥1') priceText = testOffer.price
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

  static payParams(body) {
    if (/^XV/.test(body.order_number)) return xpay.params(body)
    var keys = ['timeStamp', 'nonceStr', 'package', 'signType', 'paySign']
    var pay = {}
    var i
    for (i = 0; i < keys.length; i++) {
      if (typeof body[keys[i]] !== 'string' || !body[keys[i]]) return null
      pay[keys[i]] = body[keys[i]]
    }
    return pay
  }

  static detailText(body) {
    if (!body || typeof body !== 'object' || Array.isArray(body)) return ''
    if (typeof body.detail === 'string') return body.detail
    if (body.order_number) return ''
    if (typeof body.code === 'string') return body.code
    if (typeof body.message === 'string') return body.message
    return ''
  }

  static normalize(body) {
    if (body && Array.isArray(body.detail)) return { ok: false, code: 'invalid', message: '档位无效' }
    var detail = PayClient.detailText(body)
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
      var order = PayClient.viewFromServer(body)
      return { ok: true, code: order.code, order: order, pay: PayClient.payParams(body) }
    }
    if (detail) return { ok: false, code: 'failed', message: detail }
    return { ok: false, code: 'unknown', message: '服务暂不可用' }
  }

  static request(method, path, data) {
    var config = require('../config')
    var tokenState = require('./state')
    var base = String(config.apiBase || '').replace(/\/$/, '')
    if (!base) return Promise.resolve({ ok: false, code: 'offline', message: '未配置服务地址' })
    var token = tokenState.token()
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
            if (tokenState.token() !== token) {
              resolve({ ok: false, code: 'session_changed', message: '登录状态已变化' })
              return
            }
            if (res && (res.statusCode === 401 || res.statusCode === 404) && /\/sync$/.test(path)) {
              if (res.statusCode === 401) tokenState.clearSession(token)
              resolve({ ok: false, code: res.statusCode === 401 ? 'login' : 'missing', message: res.statusCode === 401 ? '请先登录' : '订单不存在' })
              return
            }
            var normalized = PayClient.normalize(res && res.data)
            if (!normalized.ok) normalized.httpStatus = res && res.statusCode
            if (normalized.code === 'login') tokenState.clearSession(token)
            resolve(normalized)
          },
          fail: function () { resolve({ ok: false, code: 'network', message: '网络异常，请重试' }) }
        })
      } catch (error) {
        resolve({ ok: false, code: 'network', message: '网络异常，请重试' })
      }
    })
  }

  static orderPath(orderNo) {
    var prefix = /^XV/.test(orderNo) ? '/api/v1/xpay/orders/' : '/api/v1/pay/orders/'
    return prefix + encodeURIComponent(orderNo || '')
  }

  static read(orderNo) {
    return PayClient.request('GET', PayClient.orderPath(orderNo))
  }

  static sync(orderNo) {
    return PayClient.request('POST', PayClient.orderPath(orderNo) + '/sync').then(function (result) {
      if (/^XV/.test(orderNo) && !result.ok && result.httpStatus >= 500 && result.code !== 'unconfigured') {
        return { ok: false, code: 'query_failed', message: '暂未确认支付结果' }
      }
      return result
    })
  }

  static failedView(orderNo, result) {
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
}
PayClient.sessions = {}

function homePlatformTotal(value) {
  if (typeof value !== 'number' || !isFinite(value) || value <= 0) return null
  return value
}

function homeExamples(list) {
  if (!Array.isArray(list)) return []
  var out = []
  list.forEach(function (item) {
    if (!item || item.name == null || !String(item.name).trim()) return
    out.push({
      name: String(item.name),
      line: item.line == null ? '' : String(item.line),
      sub: item.sub == null ? '' : String(item.sub)
    })
  })
  return out
}

function homeAnnouncements(list) {
  if (!Array.isArray(list)) return []
  var out = []
  list.forEach(function (item) {
    if (!item) return
    var title = item.title == null ? '' : String(item.title)
    var body = item.body == null ? '' : String(item.body)
    if (!title && !body) return
    var button = item.button_text == null ? '' : String(item.button_text).trim()
    out.push({
      id: item.id == null ? '' : String(item.id),
      title: title,
      body: body,
      buttonText: button || '知道了'
    })
  })
  return out
}

function emptyToNull(value) {
  if (value == null || value === '') return null
  return value
}

function adaptRecord(rec) {
  if (!rec) return null
  return {
    record_id: rec.record_id == null ? '' : String(rec.record_id),
    platform: rec.platform == null ? '' : String(rec.platform),
    platform_subtype: rec.platform_subtype == null ? '' : String(rec.platform_subtype),
    network_type: rec.network_type == null ? '' : String(rec.network_type),
    category: rec.category == null ? '' : String(rec.category),
    frame_count: typeof rec.frame_count === 'number' ? rec.frame_count : undefined,
    timestamps: Array.isArray(rec.timestamps) ? rec.timestamps.map(String) : [],
    checked_at: emptyToNull(rec.checked_at) == null ? null : String(rec.checked_at),
    age_bucket: emptyToNull(rec.age_bucket) == null ? null : String(rec.age_bucket),
    thumbnail_url: emptyToNull(rec.thumbnail_url) == null ? null : String(rec.thumbnail_url),
    hd_url: emptyToNull(rec.hd_url) == null ? null : String(rec.hd_url),
    access: emptyToNull(rec.access),
    can_download_report: rec.can_download_report == null ? null : !!rec.can_download_report,
    can_download_screenshot: rec.can_download_screenshot == null ? null : !!rec.can_download_screenshot,
    locked: rec.locked == null ? null : !!rec.locked
  }
}

function adaptExample(body) {
  var src = body || {}
  var records = []
  ;(src.records || []).forEach(function (rec) {
    var row = adaptRecord(rec)
    if (row) records.push(row)
  })
  return {
    name: src.name == null ? '' : String(src.name),
    program_name: src.program_name == null ? '' : String(src.program_name),
    example: true,
    publicExample: true,
    outcome: src.outcome || (records.length ? 'hit' : 'miss'),
    platform_count: typeof src.platform_count === 'number' ? src.platform_count : 0,
    unverified_source_count: typeof src.unverified_source_count === 'number' ? src.unverified_source_count : 0,
    line: src.line == null ? '' : String(src.line),
    sub: src.sub == null ? '' : String(src.sub),
    records: records,
    evidence_program_name: '',
    evidence_platforms: [],
    locked_summary: shape.emptySummary(),
    locked_window: { count: 0, start: '', end: '' }
  }
}

// POST /api/v1/invite-codes/redeem puts the domain code in `detail`
// (invalid, expired, used, bound). Map that string, never the HTTP status.
var INVITE_ERRORS = {
  invalid: '邀请码无效，请核对后再试。',
  expired: '邀请码已过期，不能再兑换。',
  used: '邀请码已被使用。',
  bound: '已绑定过邀请码，不能再填'
}

function inviteErrorCode(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return ''
  return typeof body.detail === 'string' ? body.detail : ''
}

function beijingDay(iso) {
  var instant = Date.parse(iso)
  if (!(instant > Date.now())) return ''
  return new Date(instant + 8 * 3600 * 1000).toISOString().slice(0, 10)
}

function rememberInvite(code, expiresAt) {
  var data = state.load()
  var id = state.currentId()
  if (!data.accounts || !id || id === 'guest' || !data.accounts[id]) return
  var account = data.accounts[id]
  account.inviteBound = true
  if (code) account.inviteCode = String(code)
  var day = beijingDay(expiresAt)
  if (day) account.inviteExpiry = day
  state.save()
}

function storeProfile(user) {
  if (!user || typeof user !== 'object' || !user.id) return null
  var data = state.load()
  if (!data.auth) return user
  data.auth.user = {
    id: user.id,
    tier: user.tier,
    vip_expires_at: user.vip_expires_at || null,
    regular_expires_at: user.regular_expires_at || null
  }
  state.save()
  return data.auth.user
}

function refreshMembership(sent) {
  return request('/api/v1/auth/me').then(function (user) {
    if (sent !== state.token()) return null
    return storeProfile(user) || user
  })
}

function inviteSuccess(body, user) {
  var expiry = beijingDay(body && body.expires_at)
  var hasVip = !!(user && user.tier === 'vip')
  return {
    ok: true,
    code: 'success',
    expiry: expiry,
    hasVip: hasVip,
    headline: '兑换成功',
    sub: expiry ? ('有效至 ' + expiry) : ''
  }
}

function inviteFailure(code) {
  if (!code) return null
  var message = INVITE_ERRORS[code]
  if (!message) return { ok: false, code: 'unknown', message: '网络异常，请重试' }
  if (code === 'bound') rememberInvite()
  return { ok: false, code: code, message: message }
}

// GET /api/v1/invite-codes/state. Always hits the network; nothing is stored.
// 403 account is disabled uses the same client error as other non-401 responses.
function loadInviteState() {
  var sent = state.token()
  return request('/api/v1/invite-codes/state', {}, { exposeStatus: true, optionalAuth: true }).then(function (res) {
    if (sent !== state.token()) return { ok: false, sessionChanged: true }
    var detail = inviteErrorCode(res && res.data)
    if (res && res.statusCode === 403 && detail === 'account is disabled') {
      return { ok: false, banned: true, message: '服务暂不可用' }
    }
    var body = res && res.data
    if (!res || res.statusCode < 200 || res.statusCode >= 300 || !body || typeof body !== 'object' || Array.isArray(body) ||
        typeof body.guest !== 'boolean' || typeof body.bound !== 'boolean' || typeof body.bindText !== 'string') {
      return { ok: false, message: '网络异常，请重试' }
    }
    if (body.guest && sent) state.clearSession(sent)
    return { ok: true, guest: body.guest, bound: body.bound, bindText: body.bindText }
  }, function () {
    return { ok: false, message: '网络异常，请重试' }
  })
}

module.exports = {
  bootstrap: function () { state.load() },
  reset: function () { state.reset() },
  splashSeen: function () { return require('./splash').seen() },
  markSplashSeen: function () { require('./splash').markSeen() },
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
  profile: function () { return require('./identity').loadProfile() },
  memberCenter: function () { return require('./identity').loadMemberCenter() },
  membershipPlans: function () { return require('./mock').membershipPlans() },
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
  homeContent: function () {
    return request('/api/v1/app/home', {}, { public: true }).then(function (body) {
      var src = body || {}
      var total = homePlatformTotal(src.platformTotal)
      return {
        platformTotal: total,
        showPlatformTotal: total !== null,
        examples: homeExamples(src.examples),
        announcements: homeAnnouncements(src.announcements)
      }
    })
  },
  exampleReport: function (name) {
    var title = String(name || '').trim()
    if (!title) return Promise.resolve(null)
    return request('/api/v1/app/examples/' + encodeURIComponent(title), {}, { public: true }).then(function (body) {
      return adaptExample(body)
    })
  },
  programMeta: function (name) { return { name: name, type: '', year: '' } },
  tierOfAccount: function () { return state.userId() ? 'free' : 'guest' },
  checkout: function (product) {
    var tier = typeof product === 'string' ? product : ''
    if (tier === testOffer.id) return testOffer.checkout(PayClient.normalize)
    if (testOffer.dedicated()) return Promise.resolve(testOffer.unavailable())
    var token = state.token()
    var pending = require('../config').virtualPayment
      ? xpay.checkout(tier, PayClient.request)
      : PayClient.request('POST', '/api/v1/pay/orders', { tier: tier })
    return pending.then(function (result) {
      if (result.ok) PayClient.remember(result.order, result.pay, token)
      return result
    })
  },
  getOrder: function (orderNo) {
    if (!orderNo) return Promise.resolve(null)
    return PayClient.read(orderNo).then(function (result) { return result.ok ? result.order : null })
  },
  listOrders: function () {
    return request('/api/v1/pay/orders').then(function (body) {
      if (!Array.isArray(body)) throw new Error('服务暂不可用')
      return body
    })
  },
  startPay: function (orderNo) {
    if (testOffer.dedicated()) return Promise.resolve(testOffer.unavailable())
    var pay = PayClient.saved(orderNo)
    return PayClient.read(orderNo).then(function (result) {
      if (!result.ok) return { ok: false, code: result.code, message: result.message }
      var order = result.order
      if (order.product === testOffer.id) return testOffer.unavailable()
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
    return PayClient.sync(orderNo).then(function (result) {
      if (result.ok) return { ok: true, code: result.code, order: result.order }
      return { ok: false, code: result.code, message: result.message, order: result.code === 'unconfigured' ? null : PayClient.failedView(orderNo, result) }
    })
  },
  checkStatus: function (orderNo) {
    if (!orderNo) return Promise.resolve(null)
    return PayClient.sync(orderNo).then(function (result) {
      if (result.ok) return result.order
      if (result.code === 'unconfigured') return { orderNo: orderNo, code: 'unconfigured', status: 'unavailable' }
      if (result.code === 'login' || result.code === 'missing') {
        return { orderNo: orderNo, code: result.code, status: 'orderUnavailable', centerTitle: result.message }
      }
      return PayClient.failedView(orderNo, result)
    })
  },
  repay: function (orderNo) {
    if (testOffer.dedicated()) return Promise.resolve(testOffer.unavailable())
    var pay = PayClient.saved(orderNo)
    return PayClient.sync(orderNo).then(function (result) {
      if (!result.ok) return { ok: false, code: result.code, message: result.message, order: result.code === 'unconfigured' ? null : PayClient.failedView(orderNo, result) }
      var order = result.order
      if (order.product === testOffer.id) return testOffer.unavailable()
      if (order.code !== 'pending') return { ok: false, code: order.code, message: '订单已支付，不能再次支付', order: order }
      if (!pay) return { ok: false, code: 'no_params', message: '请重新下单', order: order }
      var paying = Object.assign({}, order, { status: 'paying', code: 'paying', showPay: false, allowRepay: false })
      paying.sameNo = paying.orderNo === orderNo
      return { ok: true, code: 'paying', order: paying, pay: pay }
    })
  },
  inviteState: function () { return loadInviteState() },
  redeem: function (raw) {
    var sent = state.token()
    var code = raw == null ? '' : String(raw)
    return request('/api/v1/invite-codes/redeem', { code: code }, { method: 'POST', exposeStatus: true })
      .then(function (res) {
        if (sent !== state.token()) return { ok: false, sessionChanged: true }
        var failure = inviteFailure(inviteErrorCode(res && res.data))
        if (failure) return failure
        if (!res || res.statusCode < 200 || res.statusCode >= 300) {
          return { ok: false, code: 'unknown', message: '网络异常，请重试' }
        }
        var cached = state.load().auth && state.load().auth.user
        var expiresAt = res.data && res.data.expires_at
        return refreshMembership(sent).then(function (user) {
          if (sent !== state.token()) return { ok: false, sessionChanged: true }
          rememberInvite(code, expiresAt)
          loadInviteState()
          return inviteSuccess(res.data, user || cached)
        }, function () {
          if (sent !== state.token()) return { ok: false, sessionChanged: true }
          rememberInvite(code, expiresAt)
          loadInviteState()
          return inviteSuccess(res.data, cached)
        })
      }, function () {
        return { ok: false, code: 'unknown', message: '网络异常，请重试' }
      })
  },
  listEvidence: function () {
    return request('/api/v1/evidence-requests', null, { method: 'GET', readBody: true }).then(function (body) {
      var rows = evidence.listRows(body)
      if (!rows) return Promise.reject(new Error('网络异常，请重试'))
      return rows.map(evidence.presentList)
    })
  },
  evidenceDetail: function (id) {
    return request('/api/v1/evidence-requests/' + encodeURIComponent(id || ''), null, { method: 'GET', readBody: true }).then(function (body) {
      if (!body || typeof body !== 'object' || Array.isArray(body) || body.code || !body.id) return null
      return evidence.presentDetail(body)
    }, function () { return null })
  },
  createEvidence: function (form) {
    return request('/api/v1/evidence-requests', evidence.createPayload(form || {}), { method: 'POST', readBody: true }).then(function (body) {
      return evidence.createOutcome(body)
    }, function () {
      return evidence.createOutcome(null)
    })
  },
  withdrawEvidence: function (id) { return evidence.postAction(request, id, 'withdraw') },
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
