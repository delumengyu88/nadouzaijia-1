var assert = require('assert')
var fs = require('fs')
var config = require('../miniprogram/config')
config.mode = 'live'
config.apiBase = '/test-api'
config.calibrating = false
config.trialMode = true

var requests = []
var storage = {}
global.wx = {
  getStorageSync: function (key) { return storage[key] },
  setStorageSync: function (key, value) { storage[key] = JSON.parse(JSON.stringify(value)) },
  getSystemInfoSync: function () { return { statusBarHeight: 20 } },
  request: function (opts) {
    if (opts.url.endsWith('/invite-codes/state')) opts.success({statusCode: 200, data: {guest:false,bound:false,bindText:'兑换邀请码'}})
    else if (opts.url.endsWith('/pay/tiers')) opts.success({statusCode:200,data:{tiers:[
      {id:'membership_vip',amount_fen:59900,duration_days:30},
      {id:'membership_regular',amount_fen:31301,duration_days:42}
    ]}})
    else requests.push(opts)
  },
  navigateTo: function () {},
  switchTab: function () {},
  showToast: function () {}
}

var http = require('../miniprogram/api/http')
var identity = require('../miniprogram/api/identity')
var state = require('../miniprogram/api/state')
var member = require('../miniprogram/utils/member')
var MARK = identity.MARK
var VIP_AT = '2026-11-22T21:00:00+00:00'
var REGULAR_AT = '2026-11-24T00:00:00+00:00'
var VIP_DAY = '2026-11-23'
var REGULAR_DAY = '2026-11-24'

function drain() { return new Promise(function (resolve) { setImmediate(resolve) }) }
function page(name) {
  var def
  global.Page = function (value) { def = value }
  var file = require.resolve('../miniprogram/pages/' + name + '/index')
  delete require.cache[file]
  require(file)
  var view = Object.assign({}, def, {
    data: Object.assign({}, def.data),
    setData: function (data) { Object.assign(this.data, data) },
    getTabBar: function () { return { setData: function () {} } }
  })
  view.onShow = def.onShow.bind(view)
  view.paint = def.paint ? def.paint.bind(view) : view.paint
  view.readCenter = def.readCenter ? def.readCenter.bind(view) : view.readCenter
  view.retryLoad = def.retryLoad.bind(view)
  return view
}
function open(name) {
  var view = page(name)
  view.onShow()
  return view
}
function login(user) {
  state.setSession({
    token: 'profile-token',
    expires_at: new Date(Date.now() + 86400000).toISOString(),
    user: user
  })
}
function paidBody() {
  return { id: 'paid-user', tier: 'vip', vip_expires_at: VIP_AT, regular_expires_at: null }
}
function inviteBody() {
  return { id: 'invite-user', tier: 'vip', vip_expires_at: VIP_AT, regular_expires_at: REGULAR_AT }
}
function freeBody() {
  return { id: 'free-user', tier: 'free', vip_expires_at: null, regular_expires_at: null }
}
function noDate(text) {
  assert(!/\d{4}-\d{2}-\d{2}/.test(text || ''), text)
}
function noMock(value) {
  var text = JSON.stringify(value)
  assert(text.indexOf('A8K3') === -1, text)
  assert(text.indexOf('2026-12-18') === -1, text)
  assert(text.indexOf('2026-12-22') === -1, text)
  assert(!/邀请码\s*[A-Z0-9]{4}/.test(text), text)
}

async function main() {
  assert.strictEqual(identity.beijingDay(VIP_AT), VIP_DAY)
  assert.strictEqual(identity.beijingDay(REGULAR_AT), REGULAR_DAY)
  assert.strictEqual(identity.beijingDay(''), '')
  assert.strictEqual(identity.beijingDay('not-a-date'), '')

  var paid = identity.profileFromMe(paidBody())
  assert.strictEqual(paid.tier, 'vip')
  assert.strictEqual(paid.heading, 'VIP')
  assert.strictEqual(paid.detail, member.statusLine('vip', VIP_DAY))
  assert.strictEqual(paid.nickname, undefined)
  assert.strictEqual(paid.badge, '')
  assert.strictEqual(paid.title, member.statusLine('vip', VIP_DAY))
  assert.deepStrictEqual(paid.lines, [])
  assert.strictEqual(paid.bindText, '兑换邀请码')
  noMock(paid)
  assert.strictEqual(identity.memberFromMe(paidBody()).statusLine, paid.title)
  assert.strictEqual(identity.memberFromMe(paidBody()).extraLine, '')

  var invite = identity.profileFromMe(inviteBody())
  assert.deepStrictEqual(invite.lines, [member.statusLine('regular', REGULAR_DAY)])
  assert.strictEqual(identity.memberFromMe(inviteBody()).extraLine, invite.lines[0])
  var inviteOnly = identity.profileFromMe({
    id: 'invite-only', tier: 'regular', vip_expires_at: null, regular_expires_at: REGULAR_AT
  })
  assert.strictEqual(inviteOnly.heading, '普通会员')
  assert.strictEqual(inviteOnly.detail, member.statusLine('regular', REGULAR_DAY))
  assert.strictEqual(inviteOnly.title, member.statusLine('regular', REGULAR_DAY))
  assert.deepStrictEqual(inviteOnly.lines, [])
  noMock(invite)
  noMock(inviteOnly)

  var free = identity.profileFromMe(freeBody())
  assert.strictEqual(free.tier, 'free')
  assert.strictEqual(free.heading, '免费用户')
  assert.strictEqual(free.detail, member.statusLine('free'))
  assert.strictEqual(free.nickname, undefined)
  assert.strictEqual(free.title, member.statusLine('free'))
  assert.deepStrictEqual(free.lines, [])
  assert.strictEqual(free.badge, '')
  noDate(free.title)
  noMock(free)

  var missing = identity.profileFromMe({ id: 'partial', tier: 'vip' })
  assert.strictEqual(missing.heading, 'VIP')
  assert.strictEqual(missing.detail, member.statusLine('vip', MARK))
  assert.strictEqual(missing.title, member.statusLine('vip', MARK))
  assert.deepStrictEqual(missing.lines, [])
  noDate(missing.detail)
  var blank = identity.profileFromMe({ id: 'bare' })
  assert.strictEqual(blank.tier, '')
  assert.strictEqual(blank.heading, '会员信息')
  assert.strictEqual(blank.detail, MARK)
  assert.strictEqual(blank.title, MARK)
  assert.strictEqual(identity.memberFromMe({ id: 'bare' }).statusLine, MARK)
  var bad = identity.profileFromMe({
    id: 'bad', tier: 'vip', vip_expires_at: 'not-a-date', regular_expires_at: 1
  })
  assert.strictEqual(bad.title, member.statusLine('vip', MARK))
  assert.deepStrictEqual(bad.lines, [member.statusLine('regular', MARK)])
  noDate(JSON.stringify(bad))

  var css = fs.readFileSync(require.resolve('../miniprogram/pages/profile/index.wxss'), 'utf8')
  var profileXml = fs.readFileSync(require.resolve('../miniprogram/pages/profile/index.wxml'), 'utf8')
  assert(css.indexOf('text-overflow: ellipsis') !== -1)
  assert(css.indexOf('white-space: nowrap') !== -1)
  assert(css.indexOf('min-width: 0') !== -1)
  assert(css.indexOf('padding-top: 16px') !== -1)
  assert(css.indexOf('padding-bottom: 16px') !== -1)
  assert(css.indexOf('margin: 12px 16px 12px') !== -1)
  assert(profileXml.indexOf('class="nick"') !== -1)
  assert(profileXml.indexOf('>我的</view>') !== -1)
  assert(profileXml.indexOf('网络异常，请重试') !== -1)
  assert(profileXml.indexOf('>重试</button>') !== -1)
  assert(!profileXml.includes('<block wx:if="{{!guest && !loading}}">') && profileXml.includes('/pages/records/index'))
  assert(profileXml.indexOf('加载中') !== -1)
  var memberCss = fs.readFileSync(require.resolve('../miniprogram/pages/member/index.wxss'), 'utf8')
  var memberXml = fs.readFileSync(require.resolve('../miniprogram/pages/member/index.wxml'), 'utf8')
  assert(memberCss.indexOf('text-overflow: ellipsis') !== -1)
  assert(memberCss.indexOf('overflow: hidden') === -1)
  assert(memberXml.indexOf('class="info-card"') !== -1)
  assert(memberXml.indexOf('网络异常，请重试') !== -1)
  assert(memberXml.indexOf('>重试</button>') !== -1)
  assert(memberXml.indexOf("plan.selectable ? '' : 'static'") !== -1)
  assert(memberXml.indexOf('\u2014') === -1)
  assert(memberXml.indexOf('免费') === -1)

  state.reset()
  var planCalls = requests.length
  var plans = http.membershipPlans()
  assert.strictEqual(requests.length, planCalls)
  assert.strictEqual(plans[0].id, 'membership_vip')
  assert.strictEqual(plans[0].price, null)
  assert.strictEqual(plans[1].id, 'membership_regular')
  assert.strictEqual(plans[1].price, null)
  assert.strictEqual(plans[2].name, '免费用户')
  assert.strictEqual(plans[2].selectable, false)

  var thrown = 0
  var originalRequest = global.wx.request
  global.wx.request = function () { thrown += 1; throw new Error('guest must not request') }
  assert.strictEqual(http.profile().guest, true)
  assert.strictEqual(http.memberCenter().statusLine, '未登录')
  assert.strictEqual(thrown, 0)
  var guestProfile = open('profile')
  var guestMember = open('member')
  assert.strictEqual(guestProfile.data.guest, true)
  assert.strictEqual(guestProfile.data.loading, false)
  assert.strictEqual(guestProfile.data.loadError, false)
  assert.strictEqual(guestMember.data.guest, true)
  assert.strictEqual(guestMember.data.statusLine, '未登录')
  assert.strictEqual(guestMember.data.loading, false)
  assert.strictEqual(guestMember.data.plans[2].priceText, '免费')
  assert.strictEqual(guestMember.data.plans[2].perks.length, 3)
  global.wx.request = originalRequest
  assert.strictEqual(requests.length, planCalls)

  login(paidBody())
  var loadingProfile = open('profile')
  var loadingMember = open('member')
  assert.strictEqual(loadingProfile.data.loading, true)
  assert.strictEqual(loadingProfile.data.loadError, false)
  assert.deepStrictEqual(loadingProfile.data.lines, [])
  assert.strictEqual(loadingProfile.data.nickname, '')
  assert.notStrictEqual(loadingProfile.data.title, member.statusLine('vip', VIP_DAY))
  assert.strictEqual(loadingMember.data.loading, true)
  assert.strictEqual(loadingMember.data.statusLine, '')
  assert.deepStrictEqual(loadingMember.data.plans, [])
  noMock(loadingProfile.data)
  noMock(loadingMember.data)
  assert.strictEqual(requests.length, 2)
  requests.forEach(function (req) {
    assert.strictEqual(req.url, '/test-api/api/v1/auth/me')
    assert.strictEqual(req.method, 'GET')
    assert.strictEqual(req.header.Authorization, 'Bearer profile-token')
    assert(!req.header['X-User-Id'])
  })
  requests.shift().success({ statusCode: 200, data: paidBody() })
  requests.shift().success({ statusCode: 200, data: paidBody() })
  await drain()
  assert.strictEqual(loadingProfile.data.loading, false)
  assert.strictEqual(loadingProfile.data.tier, 'vip')
  assert.strictEqual(loadingProfile.data.title, '我的')
  assert.strictEqual(loadingProfile.data.heading, 'VIP')
  assert.strictEqual(loadingProfile.data.detail, member.statusLine('vip', VIP_DAY))
  assert.strictEqual(loadingMember.data.selected, 'membership_vip')
  assert.strictEqual(loadingMember.data.payLabel, '续费 ¥599')
  assert.notStrictEqual(loadingMember.data.selected, 'membership_regular')
  assert.notStrictEqual(loadingProfile.data.heading, MARK)
  assert.deepStrictEqual(loadingProfile.data.lines, [])
  assert.strictEqual(loadingMember.data.statusLine, member.statusLine('vip', VIP_DAY))
  assert.strictEqual(loadingMember.data.extraLine, '')
  assert.strictEqual(loadingMember.data.plans[2].priceText, '免费')
  assert.strictEqual(loadingMember.data.plans[0].priceText.indexOf('¥'), 0)
  assert.strictEqual(loadingMember.data.plans[1].priceText, '¥313.01/42天')
  loadingMember.paint({ tier: 'regular', guest: false, pricedTiers: [{id:'membership_regular',amount_fen:31301,duration_days:42}] })
  assert.strictEqual(loadingMember.data.payLabel, '续费 ¥313.01')
  assert.strictEqual(loadingMember.data.plans[0].selectable, false)
  assert.strictEqual(loadingMember.data.plans[0].priceText, '')
  assert.strictEqual(loadingMember.data.plans[2].perks.length, 3)
  assert.strictEqual(loadingMember.data.plans[0].perks.length, 3)
  noMock(loadingProfile.data)
  noMock(loadingMember.data)

  login(inviteBody())
  var inviteProfile = open('profile')
  var inviteMember = open('member')
  requests.shift().success({ statusCode: 200, data: inviteBody() })
  requests.shift().success({ statusCode: 200, data: inviteBody() })
  await drain()
  assert.strictEqual(inviteProfile.data.detail, member.statusLine('vip', VIP_DAY))
  assert.strictEqual(inviteMember.data.selected, 'membership_vip')
  assert.strictEqual(inviteMember.data.payLabel, '续费 ¥599')
  assert.strictEqual(inviteMember.data.heading, 'VIP')
  assert.strictEqual(inviteMember.data.detail, member.statusLine('vip', VIP_DAY))
  assert.deepStrictEqual(inviteProfile.data.lines, [member.statusLine('regular', REGULAR_DAY)])
  assert.strictEqual(inviteProfile.data.bindText, '兑换邀请码')
  assert.strictEqual(inviteMember.data.extraLine, member.statusLine('regular', REGULAR_DAY))
  assert.strictEqual(inviteMember.data.statusLine, member.statusLine('vip', VIP_DAY))
  noMock(inviteProfile.data)

  login(freeBody())
  var freeProfile = open('profile')
  var freeMember = open('member')
  requests.shift().success({ statusCode: 200, data: freeBody() })
  requests.shift().success({ statusCode: 200, data: freeBody() })
  await drain()
  assert.strictEqual(freeProfile.data.title, '我的')
  assert.strictEqual(freeProfile.data.heading, '免费用户')
  assert.strictEqual(freeProfile.data.detail, member.statusLine('free'))
  assert.deepStrictEqual(freeProfile.data.lines, [])
  noDate(freeProfile.data.detail)
  assert.strictEqual(freeMember.data.statusLine, member.statusLine('free'))
  assert.strictEqual(freeMember.data.selected, 'membership_vip')
  assert.strictEqual(freeMember.data.payLabel, '确认开通')
  assert.strictEqual(freeMember.data.showPay, true)
  assert.strictEqual(freeMember.data.plans[2].priceText, '当前')
  assert.strictEqual(freeMember.data.plans[2].name, '免费用户')
  assert.strictEqual(freeMember.data.plans[2].perks.length, 3)
  assert.strictEqual(freeMember.data.extraLine, '')
  noDate(freeMember.data.statusLine)
  noMock(freeProfile.data)

  login({ id: 'partial', tier: 'vip' })
  var partialProfile = open('profile')
  var partialMember = open('member')
  requests.shift().success({ statusCode: 200, data: { id: 'partial', tier: 'vip' } })
  requests.shift().success({ statusCode: 200, data: { id: 'partial', tier: 'vip' } })
  await drain()
  assert.strictEqual(partialProfile.data.title, '我的')
  assert.strictEqual(partialProfile.data.heading, 'VIP')
  assert.strictEqual(partialProfile.data.detail, member.statusLine('vip', MARK))
  assert.deepStrictEqual(partialProfile.data.lines, [])
  assert.strictEqual(partialMember.data.statusLine, 'VIP 至 ' + MARK)
  assert.strictEqual(partialMember.data.extraLine, '')
  noDate(partialProfile.data.detail)
  noDate(partialMember.data.statusLine)
  noMock(partialProfile.data)

  login(freeBody())
  var failed = open('profile')
  assert.strictEqual(failed.data.loading, true)
  requests.shift().fail()
  await drain()
  assert.strictEqual(failed.data.loadError, true)
  assert.strictEqual(failed.data.loading, false)
  assert.strictEqual(failed.data.guest, false)
  noDate(failed.data.title)
  noMock(failed.data)
  failed.retryLoad()
  assert.strictEqual(failed.data.loading, true)
  assert.strictEqual(failed.data.loadError, false)
  requests.shift().success({ statusCode: 200, data: freeBody() })
  await drain()
  assert.strictEqual(failed.data.loadError, false)
  assert.strictEqual(failed.data.title, '我的')
  assert.strictEqual(failed.data.heading, '免费用户')
  assert.strictEqual(failed.data.detail, member.statusLine('free'))
  login(freeBody())
  var failedMember = open('member')
  requests.shift().fail()
  await drain()
  assert.strictEqual(failedMember.data.loadError, true)
  assert.strictEqual(failedMember.data.loading, false)
  assert.strictEqual(failedMember.data.plans.length, 3)
  assert.strictEqual(failedMember.data.plans[2].priceText, '免费')
  assert.strictEqual(failedMember.data.plans[0].selectable, false)
  assert.strictEqual(failedMember.data.plans[2].perks.length, 3)

  login(paidBody())
  var denied = open('member')
  requests.shift().success({ statusCode: 401, data: {} })
  await drain()
  assert.strictEqual(state.token(), '')
  assert.strictEqual(denied.data.guest, true)
  assert.strictEqual(denied.data.loadError, false)
  assert.strictEqual(denied.data.statusLine, '未登录')
  assert.strictEqual(denied.data.loading, false)

  login(paidBody())
  var changing = open('profile')
  state.setSession({ token: 'replacement', expires_at: new Date(Date.now() + 60000).toISOString(), user: freeBody() })
  requests.shift().success({ statusCode: 200, data: paidBody() })
  await drain()
  assert.strictEqual(state.userId(), freeBody().id)
  assert.strictEqual(requests[0].header.Authorization, 'Bearer replacement')
  requests.shift().success({statusCode: 200, data: freeBody()})
  await drain()
  assert.strictEqual(changing.data.tier, 'free')
  var expiring = open('member')
  state.load().auth.expires_at = new Date(0).toISOString()
  requests.shift().success({statusCode: 200, data: paidBody()})
  await drain()
  assert.strictEqual(expiring.data.guest, true)

  console.log('profile /auth/me: paid, invite, free, missing, failure, guest ok')
}

main().catch(function (error) { console.error(error); process.exitCode = 1 })
