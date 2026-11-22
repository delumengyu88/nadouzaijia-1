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

function drain() {
  return new Promise(function (resolve) { setImmediate(resolve) })
}

async function takeState() {
  await drain()
  assert.strictEqual(requests.length, 1)
  var opt = requests.shift()
  assert.strictEqual(opt.method, 'GET')
  assert.strictEqual(opt.url, '/test-api/api/v1/invite-codes/state')
  return opt
}

function login(tier) {
  state.setSession({
    token: 'test-token',
    expires_at: new Date(Date.now() + 86400000).toISOString(),
    user: { id: 'user-1', tier: tier || 'free', vip_expires_at: null, regular_expires_at: null }
  })
}

function laterIso() {
  return new Date(Date.now() + 10 * 86400000).toISOString()
}

function beijingDay(iso) {
  return new Date(Date.parse(iso) + 8 * 3600 * 1000).toISOString().slice(0, 10)
}

function clearBound() {
  var data = state.load()
  var account = data.accounts['wx:user-1']
  account.inviteBound = false
  account.inviteCode = ''
  account.inviteExpiry = ''
  state.save()
}

async function post(code) {
  var pending = http.redeem(code)
  await drain()
  assert.strictEqual(requests.length, 1)
  var opt = requests.shift()
  assert.strictEqual(opt.method, 'POST')
  assert.strictEqual(opt.url, '/test-api/api/v1/invite-codes/redeem')
  assert.deepStrictEqual(opt.data, { code: code })
  assert.strictEqual(opt.header.Authorization, 'Bearer test-token')
  assert.strictEqual(opt.header['X-User-Id'], undefined)
  return { pending: pending, opt: opt }
}

async function main() {
  login('free')
  var cases = [
    ['invalid', 400, 'invalid', '邀请码无效，请核对后再试。'],
    ['invalid', 409, 'invalid', '邀请码无效，请核对后再试。'],
    ['expired', 400, 'expired', '邀请码已过期，不能再兑换。'],
    ['expired', 409, 'expired', '邀请码已过期，不能再兑换。'],
    ['used', 409, 'used', '邀请码已被使用。'],
    ['used', 200, 'used', '邀请码已被使用。'],
    ['bound', 409, 'bound', '已绑定过邀请码，不能再填'],
    ['bound', 500, 'bound', '已绑定过邀请码，不能再填'],
    ['user not found', 404, 'unknown', '网络异常，请重试'],
    ['nope', 503, 'unknown', '网络异常，请重试']
  ]
  for (var i = 0; i < cases.length; i++) {
    clearBound()
    var code = cases[i][0]
    var sent = await post('CODE' + i)
    sent.opt.success({ statusCode: cases[i][1], data: { detail: code } })
    var result = await sent.pending
    assert.strictEqual(result.ok, false, code + ' ' + cases[i][1])
    assert.strictEqual(result.code, cases[i][2], code + ' status ' + cases[i][1])
    assert.strictEqual(result.message, cases[i][3], code)
    assert.strictEqual(requests.length, 0, 'error code must not refresh membership')
  }

  clearBound()
  var empty = await post('')
  empty.opt.success({ statusCode: 422, data: { detail: [{ loc: ['body', 'code'] }] } })
  var emptyResult = await empty.pending
  assert.strictEqual(emptyResult.code, 'unknown')
  assert.strictEqual(emptyResult.message, '网络异常，请重试')

  clearBound()
  var dropped = await post('NET')
  dropped.opt.fail()
  var droppedResult = await dropped.pending
  assert.strictEqual(droppedResult.ok, false)
  assert.strictEqual(droppedResult.code, 'unknown')
  assert.strictEqual(droppedResult.message, '网络异常，请重试')
  assert.strictEqual(requests.length, 0)

  var iso = laterIso()
  clearBound()
  var vip = await post('GOODCODE')
  vip.opt.success({
    statusCode: 200,
    data: { entitlement_id: 'ent-1', tier: 'regular', source: 'invite', expires_at: iso }
  })
  await drain()
  assert.strictEqual(requests.length, 1)
  var me = requests.shift()
  assert.strictEqual(me.method, 'GET')
  assert.strictEqual(me.url, '/test-api/api/v1/auth/me')
  assert.strictEqual(me.header.Authorization, 'Bearer test-token')
  me.success({
    statusCode: 200,
    data: { id: 'user-1', tier: 'vip', vip_expires_at: iso, regular_expires_at: iso }
  })
  var vipResult = await vip.pending
  assert.strictEqual(vipResult.ok, true)
  assert.strictEqual(vipResult.code, 'success')
  assert.strictEqual(vipResult.hasVip, true)
  assert.strictEqual(vipResult.headline, '兑换成功')
  assert.strictEqual(vipResult.sub, '有效至 ' + beijingDay(iso))
  assert.strictEqual(vipResult.hasVip, true)
  assert.strictEqual(state.load().auth.user.tier, 'vip')
  assert.strictEqual(http.profile().tier, 'vip')
  await takeState()

  clearBound()
  login('free')
  var regular = await post('GOODCODE')
  regular.opt.success({
    statusCode: 201,
    data: { entitlement_id: 'ent-2', tier: 'regular', source: 'invite', expires_at: iso }
  })
  await drain()
  requests.shift().success({
    statusCode: 200,
    data: { id: 'user-1', tier: 'regular', vip_expires_at: null, regular_expires_at: iso }
  })
  var regularResult = await regular.pending
  assert.strictEqual(regularResult.ok, true)
  assert.strictEqual(regularResult.hasVip, false)
  assert.strictEqual(regularResult.headline, '兑换成功')
  assert.strictEqual(regularResult.sub, '有效至 ' + beijingDay(iso))
  assert.strictEqual(regularResult.hasVip, false)
  assert.strictEqual(http.profile().tier, 'regular')
  await takeState()

  clearBound()
  login('vip')
  state.load().auth.user.vip_expires_at = iso
  state.load().auth.user.tier = 'vip'
  state.save()
  var kept = await post('GOODCODE')
  kept.opt.success({
    statusCode: 200,
    data: { entitlement_id: 'ent-3', tier: 'regular', source: 'invite', expires_at: iso }
  })
  await drain()
  requests.shift().fail()
  var keptResult = await kept.pending
  assert.strictEqual(keptResult.ok, true)
  assert.strictEqual(keptResult.hasVip, true)
  await takeState()

  var wxml = fs.readFileSync(path.join(__dirname, '../miniprogram/pages/invite/index.wxml'), 'utf8')
  var wxss = fs.readFileSync(path.join(__dirname, '../miniprogram/pages/invite/index.wxss'), 'utf8')
  assert(wxml.indexOf('loading="{{phase == \'redeeming\'}}"') !== -1, 'submit button loading')
  assert(wxml.indexOf('code.length < 8') !== -1, 'short code disables redeem')
  assert(wxml.indexOf('重新输入') === -1, 'error state has no retype button')
  assert(wxml.indexOf('>8 位字母或数字</view>') !== -1, 'format hint stays on the form')
  assert(wxml.indexOf('<icon type="success" size="56" color="#16A34A"') !== -1, 'green success icon')
  assert(wxml.indexOf('class="success-title">兑换成功</view>') !== -1)
  assert(wxml.indexOf('class="success-primary" bindtap="profile"') !== -1)
  assert(wxml.indexOf('class="success-link" bindtap="home"') !== -1)
  assert(/\.success-wrap\s*\{[^}]*padding-top:\s*128rpx/.test(wxss), 'icon sits 64px under the nav')
  assert(/\.success-title\s*\{[^}]*margin-top:\s*32rpx[^}]*font-size:\s*40rpx[^}]*font-weight:\s*700[^}]*color:\s*#1F2329/.test(wxss), 'success title')
  assert(/\.success-sub\s*\{[^}]*margin-top:\s*16rpx[^}]*padding:\s*0 64rpx[^}]*font-size:\s*28rpx[^}]*color:\s*#646A73[^}]*max-height:\s*80rpx/.test(wxss), 'success subtitle')
  assert(/\.success-primary\s*\{[^}]*width:\s*480rpx[^}]*height:\s*88rpx[^}]*margin:\s*48rpx 0 0/.test(wxss), 'primary button')
  assert(/\.success-link\s*\{[^}]*margin-top:\s*24rpx/.test(wxss), 'text link')
  assert(wxml.indexOf('catchtap="clearCode"') !== -1)
  assert(wxml.indexOf('fieldError') !== -1)
  assert(/\.code-clear\s*\{[^}]*width:\s*88rpx[^}]*height:\s*88rpx/.test(wxss), 'clear control is at least 44px')

  clearBound()
  login('free')
  var def
  global.Page = function (page) { def = page }
  require('../miniprogram/pages/invite/index')
  async function open(body) {
    var page = Object.assign({}, def, {
      data: Object.assign({}, def.data),
      setData: function (patch) { Object.assign(this.data, patch) }
    })
    page.onLoad({})
    assert.strictEqual(page.data.phase, 'loading')
    await drain()
    var opt = requests.shift()
    assert.strictEqual(opt.url, '/test-api/api/v1/invite-codes/state')
    opt.success({ statusCode: 200, data: body || { guest: false, bound: false, bindText: '兑换邀请码' } })
    await drain()
    return page
  }

  var page = await open()
  page.onCode({ detail: { value: 'GOODCODE' } })
  page.submit()
  page.submit()
  assert.strictEqual(page.data.phase, 'redeeming')
  assert.strictEqual(page.data.code, 'GOODCODE')
  await drain()
  assert.strictEqual(requests.length, 1, 'repeated taps send one redeem request')
  page.submit()
  await drain()
  assert.strictEqual(requests.length, 1, 'taps during the request send nothing else')
  requests.shift().success({
    statusCode: 400,
    data: { detail: 'invalid' }
  })
  await drain()
  assert.strictEqual(page.data.phase, 'error')
  assert.strictEqual(page.data.fieldError, true)
  assert.strictEqual(page.data.message, '邀请码无效，请核对后再试。')
  assert.strictEqual(page.data.code, 'GOODCODE')
  page.onCode({ detail: { value: 'GOODCOD' } })
  assert.strictEqual(page.data.fieldError, false)
  assert.strictEqual(page.data.code, 'GOODCOD')
  page.clearCode()
  assert.strictEqual(page.data.code, '')
  page.onCode({ detail: { value: 'SHORT' } })
  var blocked = requests.length
  page.submit()
  await drain()
  assert.strictEqual(requests.length, blocked, 'fewer than 8 characters does not redeem')
  assert.notStrictEqual(page.data.phase, 'redeeming')

  page = await open()
  page.onCode({ detail: { value: 'USEDCODE' } })
  page.submit()
  await drain()
  requests.shift().success({ statusCode: 409, data: { detail: 'used' } })
  await drain()
  assert.strictEqual(page.data.phase, 'error')
  assert.strictEqual(page.data.message, '邀请码已被使用。')
  assert.strictEqual(page.data.code, 'USEDCODE')

  page = await open()
  page.onCode({ detail: { value: 'EXPIRED1' } })
  page.submit()
  await drain()
  requests.shift().success({ statusCode: 409, data: { detail: 'expired' } })
  await drain()
  assert.strictEqual(page.data.message, '邀请码已过期，不能再兑换。')
  assert.strictEqual(page.data.code, 'EXPIRED1')
  assert.strictEqual(page.data.fieldError, true)

  page = await open()
  page.onCode({ detail: { value: 'KEEPCODE' } })
  page.submit()
  await drain()
  requests.shift().fail()
  await drain()
  assert.strictEqual(page.data.phase, 'error')
  assert.strictEqual(page.data.message, '网络异常，请重试')
  assert.strictEqual(page.data.code, 'KEEPCODE')
  page.submit()
  await drain()
  requests.shift().success({ statusCode: 404, data: { detail: 'user not found' } })
  await drain()
  assert.strictEqual(page.data.message, '网络异常，请重试')
  assert.strictEqual(page.data.code, 'KEEPCODE')

  page = await open()
  page.onCode({ detail: { value: 'GOODCODE' } })
  page.submit()
  await drain()
  requests.shift().success({
    statusCode: 200,
    data: { entitlement_id: 'ent-4', tier: 'regular', source: 'invite', expires_at: iso }
  })
  await drain()
  requests.shift().success({
    statusCode: 200,
    data: { id: 'user-1', tier: 'free', vip_expires_at: null, regular_expires_at: null }
  })
  await drain()
  assert.strictEqual(page.data.phase, 'success')
  assert.strictEqual(page.data.hasVip, false)
  assert.strictEqual(page.data.headline, '兑换成功')
  assert.strictEqual(page.data.sub, '有效至 ' + beijingDay(iso))
  await takeState()

  clearBound()
  state.load().auth.user.tier = 'vip'
  state.load().auth.user.vip_expires_at = iso
  state.save()
  page = await open()
  page.onCode({ detail: { value: 'GOODCODE' } })
  page.submit()
  await drain()
  requests.shift().success({
    statusCode: 200,
    data: { entitlement_id: 'ent-5', tier: 'regular', source: 'invite', expires_at: iso }
  })
  await drain()
  requests.shift().success({
    statusCode: 200,
    data: { id: 'user-1', tier: 'vip', vip_expires_at: iso, regular_expires_at: iso }
  })
  await drain()
  assert.strictEqual(page.data.phase, 'success')
  assert.strictEqual(page.data.hasVip, true)
  assert.strictEqual(page.data.sub, '有效至 ' + beijingDay(iso))
  await takeState()

  page = await open({ guest: false, bound: true, bindText: '邀请码 ABCD****' })
  assert.strictEqual(page.data.phase, 'bound')
  assert.strictEqual(page.data.message, '已绑定过邀请码，不能再填')
  var before = requests.length
  page.submit()
  await drain()
  assert.strictEqual(requests.length, before)

  console.log('redeem check ok')
}

main().catch(function (error) {
  console.error(error && error.stack ? error.stack : error)
  process.exit(1)
})
