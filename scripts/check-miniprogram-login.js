var assert = require('assert')
var fs = require('fs')
var config = require('../miniprogram/config')
config.mode = process.argv[2] || 'hybrid'
config.apiBase = '/test-api'
var storage = {}, logins = [], requests = [], navigation = []
global.wx = {
  getStorageSync: function (key) { return storage[key] },
  setStorageSync: function (key, value) { storage[key] = JSON.parse(JSON.stringify(value)) },
  getSystemInfoSync: function () { return { statusBarHeight: 20 } },
  login: function (opts) { logins.push(opts) },
  request: function (opts) { requests.push(opts) },
  showToast: function () {},
  showModal: function () { throw new Error('guest login must not be blocked by trial payment') },
  navigateTo: function (opts) { navigation.push(opts.url) },
  switchTab: function (opts) { navigation.push(opts.url) }
}
var api = require('../miniprogram/api')
var state = require('../miniprogram/api/state')
function drain() { return new Promise(function (resolve) { setImmediate(resolve) }) }
function page(name) {
  var def
  global.Page = function (value) { def = value }
  require('../miniprogram/pages/' + name + '/index')
  var p = Object.assign({}, def, { data: Object.assign({}, def.data), setData: function (data) { Object.assign(this.data, data) } })
  p.onShow()
  return p
}
function success(id) {
  logins.shift().success({ code: 'test-login-code' })
  return drain().then(function () {
    var index = -1
    requests.forEach(function (opt, i) {
      if (opt.url.indexOf('/api/v1/auth/wechat/login') !== -1) index = i
    })
    assert(index !== -1, 'login request')
    var request = requests.splice(index, 1)[0]
    assert.strictEqual(request.url, '/test-api/api/v1/auth/wechat/login')
    assert.strictEqual(request.method, 'POST')
    assert.deepStrictEqual(request.data, { code: 'test-login-code' })
    assert(!request.header.Authorization && !request.header['X-User-Id'])
    request.success({ statusCode: 200, data: { token: 'test-token-' + id, token_type: 'bearer',
      expires_at: new Date(Date.now() + 86400000).toISOString(), user: { id: id, tier: 'free' } } })
    return drain()
  }).then(function () {
    requests.forEach(function (opt) {
      if (opt.url.indexOf('/api/v1/invite-codes/state') !== -1) {
        opt.success({ statusCode: 200, data: { guest: false, bound: false, bindText: '兑换邀请码' } })
      }
    })
    requests = requests.filter(function (opt) { return opt.url.indexOf('/api/v1/invite-codes/state') === -1 })
    return drain()
  })
}
async function main() {
  for (var name of ['home', 'profile', 'member']) {
    api.reset(); api.markSplashSeen()
    var p = page(name)
    var handler = name === 'member' ? 'go' : 'login'
    p[handler]()
    assert.strictEqual(p.data.loginLoading, true, name)
    p[handler]()
    assert.strictEqual(logins.length, 1, 'duplicate clicks cannot start another login')
    logins.shift().fail({ errMsg: 'login:fail' }); await drain()
    assert.strictEqual(p.data.loginLoading, false)
    assert.strictEqual(p.data.loginError, '登录失败，请重试')
    assert.strictEqual(api.accountKind(), 'guest')
    p[handler]()
    assert.strictEqual(p.data.loginError, '')
    await success(name)
    assert.strictEqual(p.data.loginLoading, false)
    assert.strictEqual(p.data.loginError, '')
    assert.strictEqual(api.profile().guest, false)
    assert.strictEqual(state.userId(), name)
    assert.strictEqual(state.token(), 'test-token-' + name)
    assert.deepStrictEqual(navigation, [], 'success refreshes the original page')
    var template = fs.readFileSync(require.resolve('../miniprogram/pages/' + name + '/index.wxml'), 'utf8')
    assert(template.includes('loading="{{loginLoading}}"') && template.includes('disabled="{{loginLoading}}"'))
    assert(template.includes('login-error') && template.includes('{{loginError}}'))
  }
  // A freshly loaded state module must recover the session from persistent storage.
  delete require.cache[require.resolve('../miniprogram/api/state')]
  assert.strictEqual(require('../miniprogram/api/state').token(), 'test-token-member')
  var search = require('../miniprogram/api/http').searchProgram('测试节目')
  var req = requests.shift()
  assert.strictEqual(req.header.Authorization, 'Bearer test-token-member')
  assert(!req.header['X-User-Id'])
  req.success({ statusCode: 401, data: {} })
  await assert.rejects(search)
  assert.strictEqual(state.token(), '')
  assert.strictEqual(api.accountKind(), 'guest')
  var pending = api.login(); logins.shift().success({ code: 'bad-code' }); await drain()
  requests.shift().success({ statusCode: 503, data: {} })
  await assert.rejects(pending)
  assert.strictEqual(api.profile().guest, true)
  pending = api.login(); logins.shift().success({}); await assert.rejects(pending)
  assert.strictEqual(requests.length, 0, 'empty wx code never reaches backend')
  state.setSession({ token: 'expired', expires_at: new Date(Date.now() - 1000).toISOString(), user: { id: 'old' } })
  assert.strictEqual(state.token(), '')
  assert.strictEqual(api.accountKind(), 'guest')
  var realNow = Date.now
  Date.now = function () { return Date.parse('2026-11-23T00:30:00+08:00') }
  state.setSession({ token: 'membership-token', expires_at: '2026-11-29T00:00:00+08:00', user: {
    id: 'member-boundary', tier: 'vip', vip_expires_at: '2026-11-22T21:00:00+00:00',
    regular_expires_at: '2026-11-24T00:00:00+00:00'
  } })
  assert.strictEqual(api.profile().tier, 'vip', 'UTC expiry on previous calendar date is still active')
  assert.strictEqual(api.profile().title, 'VIP 至 2026-11-23')
  Date.now = function () { return Date.parse('2026-11-22T21:00:00+00:00') }
  assert.strictEqual(api.profile().tier, 'regular', 'VIP expires at its exact instant')
  assert.strictEqual(api.memberCenter().statusLine, '当前普通会员，有效至 2026-11-24')
  Date.now = function () { return Date.parse('2026-11-24T00:00:00+00:00') }
  assert.strictEqual(api.profile().tier, 'free')
  Date.now = realNow
  console.log('WeChat login ' + config.mode + ': three pages, loading/reentry/failure/retry, persistence, Bearer and expiry ok')
}
main().catch(function (error) { console.error(error); process.exitCode = 1 })
