var shape = require('./shape')
var state = require('./state')
var request = require('./request')
var evidence = require('./evidence')

function notReady() {
  return Promise.resolve({ ok: false, message: '服务暂不可用，请改用本地数据' })
}

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
  profile: function () { return require('./mock').profile() },
  memberCenter: function () { return require('./mock').memberCenter() },
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
  checkout: function () { return notReady() },
  getOrder: function () { return null },
  listOrders: function () { return [] },
  startPay: function () { return notReady() },
  setOrderStatus: function () { return null },
  settle: function () { return notReady() },
  checkStatus: function () { return null },
  repay: function () { return notReady() },
  inviteState: function () {
    var account = state.account()
    return { guest: !state.userId(), bound: !!(account && account.inviteBound) }
  },
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
          return inviteSuccess(res.data, user || cached)
        }, function () {
          rememberInvite(code, expiresAt)
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
  confirmEvidence: function (id) { return evidence.postAction(request, id, 'confirm') },
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
