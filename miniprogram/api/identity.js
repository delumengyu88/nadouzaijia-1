// GET /api/v1/auth/me is nadou.auth.user_profile:
// id, tier (vip|regular|free), vip_expires_at, regular_expires_at.
// That payload has no nickname, invite code, or plan price. Missing text is —, missing rows are omitted.
var state = require('./state')
var request = require('./request')
var member = require('../utils/member')

var MARK = '\u2014'

function beijingDay(value) {
  if (typeof value !== 'string' || !value.trim()) return ''
  var instant = Date.parse(value)
  if (!isFinite(instant)) return ''
  return new Date(instant + 8 * 3600 * 1000).toISOString().slice(0, 10)
}

// null: field absent, hide the row. A present but unusable value is —.
function expiryText(value) {
  if (value === undefined || value === null || value === '') return null
  if (typeof value !== 'string') return MARK
  return beijingDay(value) || MARK
}

function knownTier(value) {
  return value === 'vip' || value === 'regular' || value === 'free' ? value : ''
}

function guestProfile() {
  return {
    guest: true,
    tier: 'guest',
    title: '',
    badge: '',
    lines: [],
    bound: false,
    bindText: '兑换邀请码',
    bindSub: ''
  }
}

function guestCenter() {
  return { guest: true, tier: 'guest', statusLine: member.statusLine('guest') }
}

function profileFromMe(body) {
  var src = body && typeof body === 'object' ? body : {}
  var tier = knownTier(src.tier)
  var lines = []
  var title = MARK
  if (tier === 'vip' || tier === 'regular') {
    var raw = tier === 'vip' ? src.vip_expires_at : src.regular_expires_at
    var until = expiryText(raw)
    title = member.statusLine(tier, until === null ? MARK : until)
    if (tier === 'vip') {
      var extra = expiryText(src.regular_expires_at)
      if (extra !== null) lines.push(member.statusLine('regular', extra))
    }
  } else if (tier === 'free') {
    title = member.statusLine('free')
  }
  return {
    guest: false,
    tier: tier,
    nickname: MARK,
    title: title,
    badge: tier === 'vip' ? 'VIP' : '',
    lines: lines,
    bound: false,
    bindText: '兑换邀请码',
    bindSub: ''
  }
}

function memberFromMe(body) {
  var profile = profileFromMe(body)
  return {
    guest: false,
    tier: profile.tier,
    statusLine: profile.title,
    extraLine: profile.lines[0] || ''
  }
}

function remember(body) {
  if (!body || typeof body !== 'object') return
  var data = state.load()
  if (!data.auth || !data.auth.user) return
  data.auth.user = {
    id: typeof body.id === 'string' && body.id ? body.id : data.auth.user.id,
    tier: knownTier(body.tier),
    vip_expires_at: typeof body.vip_expires_at === 'string' ? body.vip_expires_at : null,
    regular_expires_at: typeof body.regular_expires_at === 'string' ? body.regular_expires_at : null
  }
  state.save()
}

function loadMe(guest, map) {
  if (!state.token()) return guest()
  return request('/api/v1/auth/me').then(function (body) {
    remember(body)
    return map(body)
  }, function () {
    if (!state.token()) return guest()
    return Promise.reject(new Error('加载失败'))
  })
}

function syncAccess() {
  if (!state.token()) return { guest: true, tier: 'guest' }
  var user = state.load().auth && state.load().auth.user
  return { guest: false, tier: knownTier(user && user.tier) }
}

module.exports = {
  MARK: MARK,
  beijingDay: beijingDay,
  guestProfile: guestProfile,
  guestCenter: guestCenter,
  profileFromMe: profileFromMe,
  memberFromMe: memberFromMe,
  loadProfile: function () { return loadMe(guestProfile, profileFromMe) },
  loadMemberCenter: function () { return loadMe(guestCenter, memberFromMe) },
  syncAccess: syncAccess
}
