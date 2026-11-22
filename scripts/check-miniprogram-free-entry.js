var assert = require('assert')
var config = require('../miniprogram/config')
config.mode = 'live'
config.apiBase = '/test-api'
var logins = [], requests = [], stack = [], storage = {}
global.wx = {
  login: function (opts) { logins.push(opts) },
  request: function (opts) { requests.push(opts) },
  getStorageSync: function (key) { return storage[key] },
  setStorageSync: function (key, value) { storage[key] = JSON.parse(JSON.stringify(value)) },
  getSystemInfoSync: function () { return { statusBarHeight: 20 } }
}
var app
global.App = function (def) { app = def }
global.getApp = function () { return app }
global.getCurrentPages = function () { return stack }
var state = require('../miniprogram/api/state')
var api = require('../miniprogram/api')
require('../miniprogram/app')
function settle() { return new Promise(function (resolve) { setImmediate(resolve) }) }
function account(id, tier) {
  return { token: 'token-' + id, token_type: 'bearer', expires_at: new Date(Date.now() + 3600000).toISOString(),
    user: { id: id, tier: tier } }
}
function page(name) {
  var def
  global.Page = function (value) { def = value }
  var file = require.resolve('../miniprogram/pages/' + name + '/index')
  delete require.cache[file]
  require(file)
  return Object.assign({}, def, { data: Object.assign({}, def.data), route: 'pages/' + name + '/index',
    setData: function (changes) { Object.assign(this.data, changes) } })
}
async function exchange() {
  assert.strictEqual(logins.length, 1)
  logins.shift().success({ code: 'valid-platform-code' })
  await settle()
  var req = requests.shift()
  assert(req && req.url.endsWith('/auth/wechat/login') && req.method === 'POST')
  assert.deepStrictEqual(req.header, {})
  return req
}
async function run() {
  var visible = page('profile')
  stack = [visible]
  app.onLaunch()
  app.onShow()
  visible.onShow()
  assert.strictEqual(visible.data.guest, true)
  assert.strictEqual(requests.length, 0)
  logins.shift().fail()
  await settle()
  assert(visible.data.loginError)
  assert.strictEqual(state.token(), '')

  api.setDraft('本次访客输入')
  app.onShow()
  var joined = api.ensureEntry()
  var req = await exchange()
  req.success({ statusCode: 200, data: account('paid', 'vip') })
  await joined
  await settle()
  var profile = requests.shift()
  assert(profile.url.endsWith('/auth/me'))
  assert.strictEqual(profile.header.Authorization, 'Bearer token-paid')
  profile.success({ statusCode: 200, data: { id: 'paid', tier: 'vip' } })
  await settle()
  assert.strictEqual(visible.data.heading, 'VIP')
  var invite = requests.shift()
  invite.success({ statusCode: 200, data: {guest: false, bound: false, bindText: '兑换邀请码'} })
  await settle()
  assert.strictEqual(api.getDraft(), '本次访客输入')
  app.onShow()
  assert.strictEqual(logins.length, 0)
  assert.strictEqual(api.homeState().tier, 'vip')

  var input = page('input')
  input.onLoad({})
  var textTimer
  var nav = require('../miniprogram/utils/nav')
  var later = nav.later
  nav.later = function (time, callback) { textTimer = callback }
  input.recognizeText()
  var fileRead
  wx.getFileSystemManager = function () { return { readFile: function (opts) { fileRead = opts } } }
  input.recognizeFile('test.xlsx', 2000, 'local')
  state.clearSession()
  state.setSession(account('free', 'free'))
  input.onShow()
  textTimer()
  fileRead.success({data: null})
  assert.deepStrictEqual(input.data.items, [])
  assert.strictEqual(api.getDraft(), '')
  nav.later = later

  var list = page('evidence-list')
  list.onShow()
  var old = requests.shift()
  state.setSession(account('second', 'free'))
  list.onShow()
  var latest = requests.shift()
  latest.success({statusCode:200,data:[]})
  old.fail()
  await settle()
  assert(list.data.empty && !list.data.loadError)
  state.clearSession()
  api.setDraft('访客输入')
  var switched = api.ensureEntry()
  var obsolete = await exchange()
  state.setSession(account('other', 'regular'))
  obsolete.success({statusCode:200,data:account('stale', 'free')})
  await assert.rejects(switched, function (e) { return e.sessionChanged })
  assert.strictEqual(state.userId(), 'other')
  assert.strictEqual(api.getDraft(), '')

  state.clearSession()
  stack = [input]
  input.onLoad({})
  input.setData({items:[{name:'仅测试一次提交'}]})
  var submitted = input.confirm()
  input.confirm()
  var pending = await exchange()
  input.onUnload()
  pending.success({statusCode:200,data:account('new', 'free')})
  await submitted
  assert.strictEqual(requests.length, 0, 'a discarded input page must not submit')
  state.clearSession()
  input = page('input')
  stack = [input]
  input.onLoad({})
  input.setData({items:[{name:'仅测试一次提交'}]})
  var creating = input.confirm()
  input.confirm()
  pending = await exchange()
  pending.success({statusCode:200,data:account('new', 'free')})
  await creating
  assert.strictEqual(requests.length, 1)
  var created = requests.shift()
  assert(created.url.endsWith('/queries') && created.method === 'POST')
  assert.strictEqual(created.header.Authorization, 'Bearer token-new')
  assert.deepStrictEqual(created.data, { name: '仅测试一次提交' })
  console.log('automatic free entry, paid identity, offline retry, isolated drafts and private callbacks ok')
}
run().catch(function (e) { console.error(e); process.exitCode = 1 })
