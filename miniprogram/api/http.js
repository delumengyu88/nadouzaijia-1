var shape = require('./shape')
var state = require('./state')
var request = require('./request')

function notReady() {
  return Promise.resolve({ ok: false, message: '服务暂不可用，请改用本地数据' })
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

function refreshMembership() {
  return request('/api/v1/auth/me').then(function (user) {
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
  checkout: function () { return notReady() },
  getOrder: function () { return null },
  listOrders: function () { return [] },
  startPay: function () { return notReady() },
  setOrderStatus: function () { return null },
  settle: function () { return notReady() },
  checkStatus: function () { return null },
  repay: function () { return notReady() },
  inviteState: function () { return loadInviteState() },
  redeem: function (raw) {
    var code = raw == null ? '' : String(raw)
    return request('/api/v1/invite-codes/redeem', { code: code }, { method: 'POST', exposeStatus: true })
      .then(function (res) {
        var failure = inviteFailure(inviteErrorCode(res && res.data))
        if (failure) return failure
        if (!res || res.statusCode < 200 || res.statusCode >= 300) {
          return { ok: false, code: 'unknown', message: '网络异常，请重试' }
        }
        var cached = state.load().auth && state.load().auth.user
        var expiresAt = res.data && res.data.expires_at
        return refreshMembership().then(function (user) {
          rememberInvite(code, expiresAt)
          loadInviteState()
          return inviteSuccess(res.data, user || cached)
        }, function () {
          rememberInvite(code, expiresAt)
          loadInviteState()
          return inviteSuccess(res.data, cached)
        })
      }, function () {
        return { ok: false, code: 'unknown', message: '网络异常，请重试' }
      })
  },
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
