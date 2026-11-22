var assert = require('assert')
var fs = require('fs')
var path = require('path')
var config = require('../miniprogram/config')

config.mode = 'live'
config.calibrating = false
config.apiBase = '/test-api'

var storage = {}
var requests = []
global.wx = {
  getStorageSync: function (key) { return storage[key] },
  setStorageSync: function (key, value) { storage[key] = JSON.parse(JSON.stringify(value)) },
  removeStorageSync: function (key) { delete storage[key] },
  getSystemInfoSync: function () { return { statusBarHeight: 20 } },
  request: function (opt) { requests.push(opt) },
  navigateTo: function () {},
  navigateBack: function () {},
  switchTab: function () {},
  showToast: function () {}
}
global.getCurrentPages = function () { return [{}, {}] }

var state = require('../miniprogram/api/state')
var http = require('../miniprogram/api/http')
var defs = {}
var current = ''
global.Page = function (page) { defs[current] = page }
current = 'invite'
require('../miniprogram/pages/invite/index')
current = 'profile'
require('../miniprogram/pages/profile/index')

function drain() {
  return new Promise(function (resolve) { setImmediate(resolve) })
}

function login() {
  state.setSession({
    token: 'test-token',
    expires_at: new Date(Date.now() + 86400000).toISOString(),
    user: { id: 'user-1', tier: 'free', vip_expires_at: null, regular_expires_at: null }
  })
}

function page(name) {
  var def = defs[name]
  var view = Object.assign({}, def, {
    data: Object.assign({}, def.data),
    setData: function (patch) { Object.assign(this.data, patch) },
    getTabBar: function () { return { setData: function () {} } }
  })
  return view
}

async function answer(body, status) {
  await drain()
  assert.strictEqual(requests.length, 1)
  var opt = requests.shift()
  assert.strictEqual(opt.method, 'GET')
  assert.strictEqual(opt.url, '/test-api/api/v1/invite-codes/state')
  opt.success({ statusCode: status || 200, data: body })
  await drain()
  return opt
}

async function main() {
  var inviteWxml = fs.readFileSync(path.join(__dirname, '../miniprogram/pages/invite/index.wxml'), 'utf8')
  var profileWxml = fs.readFileSync(path.join(__dirname, '../miniprogram/pages/profile/index.wxml'), 'utf8')
  assert(inviteWxml.indexOf('phase == \'guest\'') !== -1)
  assert(inviteWxml.indexOf('微信登录') !== -1)
  assert(profileWxml.indexOf('{{inviteError || bindText}}') !== -1)
  assert(profileWxml.indexOf('wx:if="{{!inviteLoading && bound && !inviteError}}"') !== -1)
  assert(profileWxml.indexOf('>邀请码</view>') !== -1)
  assert(profileWxml.indexOf('已绑定 {{inviteMask}}') !== -1)
  assert(profileWxml.indexOf('class="invite-bound"') !== -1)
  var boundRow = profileWxml.split('invite-row').slice(1, 2).join('')
  assert(boundRow.indexOf('bindtap') === -1, 'bound row does not navigate')
  assert(fs.readFileSync(path.join(__dirname, '../miniprogram/pages/profile/index.wxss'), 'utf8').indexOf('min-height: 102rpx') !== -1)
  assert(fs.readFileSync(path.join(__dirname, '../miniprogram/pages/profile/index.wxss'), 'utf8').indexOf('color: #646A73') !== -1)

  state.reset()
  var guestPage = page('invite')
  guestPage.onLoad({})
  assert.strictEqual(guestPage.data.phase, 'loading')
  assert.notStrictEqual(guestPage.data.message, '已绑定过邀请码，不能再填')
  var guestReq = await answer({ guest: true, bound: false, bindText: '兑换邀请码' })
  assert.strictEqual(guestReq.header.Authorization, undefined)
  assert.strictEqual(guestPage.data.phase, 'guest')
  var guestRead = http.inviteState()
  var guestOpt = await answer({ guest: true, bound: false, bindText: '兑换邀请码' })
  assert.deepStrictEqual(await guestRead, { ok: true, guest: true, bound: false, bindText: '兑换邀请码' })
  assert.strictEqual(guestOpt.header.Authorization, undefined)

  login()
  state.load().accounts['wx:user-1'].inviteBound = true
  state.load().accounts['wx:user-1'].inviteCode = 'SHOULDNOTSHOW'
  state.save()
  var stale = page('invite')
  stale.onLoad({})
  assert.strictEqual(stale.data.phase, 'loading')
  assert.strictEqual(stale.data.message || '', '')
  await answer({ guest: false, bound: false, bindText: '兑换邀请码' })
  assert.strictEqual(stale.data.phase, 'form')
  assert.notStrictEqual(stale.data.message, '已绑定过邀请码，不能再填')

  var profile = page('profile')
  profile.onShow()
  assert.strictEqual(profile.data.inviteLoading, true)
  assert.strictEqual(profile.data.bindText, '')
  assert.strictEqual(profile.data.guest, false)
  await answer({ guest: false, bound: false, bindText: '兑换邀请码' })
  assert.strictEqual(profile.data.inviteLoading, false)
  assert.strictEqual(profile.data.bindText, '兑换邀请码')
  assert.strictEqual(profile.data.bound, false)
  assert.strictEqual(profile.data.inviteError, '')

  var boundPage = page('invite')
  boundPage.onLoad({})
  await answer({ guest: false, bound: true, bindText: '邀请码 ABCD****' })
  assert.strictEqual(boundPage.data.phase, 'bound')
  assert.strictEqual(boundPage.data.message, '已绑定过邀请码，不能再填')
  var before = requests.length
  boundPage.submit()
  await drain()
  assert.strictEqual(requests.length, before)
  profile.onShow()
  assert.strictEqual(profile.data.bindText, '')
  await answer({ guest: false, bound: true, bindText: '邀请码 ABCD****' })
  assert.strictEqual(profile.data.bindText, '邀请码 ABCD****')
  assert.strictEqual(profile.data.inviteMask, 'ABCD****')
  assert.strictEqual(profile.data.bound, true)
  assert.strictEqual(http.profile().tier, 'free')

  var expired = http.inviteState()
  var again = http.inviteState()
  await drain()
  assert.strictEqual(requests.length, 2, 'invite state is not cached')
  requests.shift().success({
    statusCode: 200,
    data: { guest: false, bound: true, bindText: '邀请码 WXYZ****' }
  })
  requests.shift().success({
    statusCode: 200,
    data: { guest: false, bound: true, bindText: '邀请码 WXYZ****' }
  })
  var first = await expired
  var second = await again
  assert.strictEqual(first.bound, true)
  assert.strictEqual(second.bindText, '邀请码 WXYZ****')
  assert.strictEqual(http.profile().tier, 'free')

  var banned = http.inviteState()
  var bannedReq = await answer({ detail: 'account is disabled' }, 403)
  assert.strictEqual(bannedReq.header.Authorization, 'Bearer test-token')
  assert.deepStrictEqual(await banned, { ok: false, banned: true, message: '服务暂不可用' })
  assert.strictEqual(state.token(), 'test-token')
  var bannedPage = page('invite')
  bannedPage.onLoad({})
  await answer({ detail: 'account is disabled' }, 403)
  assert.strictEqual(bannedPage.data.phase, 'unavailable')
  assert.strictEqual(bannedPage.data.message, '服务暂不可用')
  bannedPage.submit()
  await drain()
  assert.strictEqual(requests.length, 0)

  var dropped = http.inviteState()
  await drain()
  requests.shift().success({ statusCode: 200, data: { guest: true, bound: false, bindText: null } })
  assert.strictEqual((await dropped).message, '网络异常，请重试')

  login()
  var invalid = http.inviteState()
  await answer({ guest: true, bound: false, bindText: '兑换邀请码' })
  assert.strictEqual((await invalid).guest, true)
  assert.strictEqual(state.token(), '')

  console.log('invite state check ok')
}

main().catch(function (error) {
  console.error(error && error.stack ? error.stack : error)
  process.exit(1)
})
