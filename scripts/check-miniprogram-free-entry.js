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
function guest(id) {
  var credentials = account(id, 'free')
  credentials.user.identity_kind = 'guest'
  return credentials
}
function guestRequest() {
  var req = requests.shift()
  assert(req && req.url.endsWith('/auth/guest/session'))
  assert.strictEqual(req.method, 'POST')
  assert.deepStrictEqual(req.header, {})
  assert.deepStrictEqual(req.data, {})
  assert.strictEqual(logins.length, 0)
  return req
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
  assert.strictEqual(logins.length, 0, 'cold start and opening the personal center must not call wx.login')
  var home = page('home')
  stack = [home]
  app.globalData.splashShownThisLaunch = true
  home.onShow()
  app.onShow()
  assert.strictEqual(home.data.banner, 'guest')
  assert.strictEqual(logins.length, 0)
  var publicHome = requests.shift()
  assert(publicHome.url.endsWith('/app/home'))
  assert.deepStrictEqual(publicHome.header, {})
  publicHome.success({statusCode:200,data:{platformTotal:null,examples:[],announcements:[]}})
  await settle()
  stack = [visible]
  visible.onShow()
  assert.strictEqual(requests.length, 0)
  assert.strictEqual(state.token(), '')

  api.setDraft('本次访客输入')
  app.onShow()
  assert.strictEqual(logins.length, 0, 'returning to the foreground stays anonymous')
  var joined = api.ensureEntry() // Explicit identity request from a protected action.
  var req = await exchange()
  req.success({ statusCode: 200, data: account('paid', 'vip') })
  await joined
  await settle()
  visible.onShow()
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
  var pending = guestRequest()
  input.onUnload()
  pending.success({statusCode:200,data:guest('first')})
  await submitted
  assert.strictEqual(requests.length, 0, 'a discarded input page must not submit')
  state.clearSession()
  input = page('input')
  stack = [input]
  input.onLoad({})
  input.setData({items:[{name:'仅测试一次提交'}]})
  var unavailable = input.confirm()
  guestRequest().success({statusCode:404,data:{}})
  await unavailable
  assert(input.data.loginError.includes('重试'))
  assert.deepStrictEqual(logins, [])
  var creating = input.confirm()
  input.confirm()
  var same = api.ensureQuerySession()
  pending = guestRequest()
  assert.strictEqual(requests.length, 0, 'concurrent confirm and session requests are joined')
  pending.success({statusCode:200,data:guest('new')})
  await Promise.all([same, creating])
  assert.strictEqual(state.token(), '', 'visitor credentials cannot authorize an account operation')
  assert.strictEqual(api.accountKind(), 'guest')
  assert.strictEqual(state.queryToken(), 'token-new')
  assert.strictEqual(requests.length, 1)
  var created = requests.shift()
  assert(created.url.endsWith('/queries') && created.method === 'POST')
  assert.strictEqual(created.header.Authorization, 'Bearer token-new')
  assert.deepStrictEqual(created.data, { name: '仅测试一次提交' })
  wx.redirectTo = function (route) { assert(route.url.endsWith('id=own-query')) }
  var expiry = new Date(Date.now() + 30 * 86400000).toISOString()
  created.success({statusCode:201,data:{id:'own-query'},header:{'X-Nadou-Session-Expires-At':expiry}})
  await settle()
  assert.strictEqual(state.load().auth.expires_at, expiry)
  var now = Date.now
  var dayEight = now() + 8 * 86400000
  Date.now = function () { return dayEight }
  assert.strictEqual(state.queryToken(), 'token-new', 'guest can read their original records on day eight')
  Date.now = now
  var listed = api.listRecordPage(20, 0)
  var ownList = requests.shift()
  assert.strictEqual(ownList.header.Authorization, 'Bearer token-new')
  ownList.success({statusCode:200,data:{items:[],total:0}})
  assert.deepStrictEqual((await listed).rows, [])
  var search = api.searchProgram('免费节目')
  var ownSearch = requests.shift()
  assert.strictEqual(ownSearch.header.Authorization, 'Bearer token-new')
  ownSearch.success({statusCode:200,data:{outcome:'miss',records:[]}})
  assert.strictEqual((await search).outcome, 'miss')
  assert.strictEqual(api.profile().guest, true)
  assert.strictEqual((await api.exportBook('免费节目')).ok, false)
  assert.strictEqual(requests.length, 0)
  api.setDraft('只属于此访客')
  state.load().auth.expires_at = new Date(Date.now() - 1000).toISOString()
  assert.strictEqual(state.queryToken(), '')
  assert.strictEqual(state.pendingGuestToken(), 'token-new')
  var checkPrevious = api.listRecords()
  var renewal = requests.shift()
  assert(renewal.url.endsWith('/auth/guest/session'))
  assert.deepStrictEqual(renewal.header, {Authorization:'Bearer token-new'})
  renewal.fail()
  await assert.rejects(checkPrevious)
  assert.strictEqual(state.pendingGuestToken(), 'token-new', 'a network error cannot abandon an existing visitor')
  checkPrevious = api.listRecords()
  renewal = requests.shift()
  renewal.success({statusCode:200,data:guest('new')})
  await settle()
  var resumed = requests.shift()
  assert(resumed.url.endsWith('/queries') && resumed.header.Authorization === 'Bearer token-new')
  resumed.success({statusCode:200,data:{items:[{id:'own-query',name:'仅测试一次提交'}],total:1}})
  assert.strictEqual((await checkPrevious)[0].id, 'own-query')
  assert.strictEqual(api.getDraft(), '只属于此访客')
  var expiredList = api.listRecords()
  requests.shift().success({statusCode:401,data:{}})
  await assert.rejects(expiredList)
  assert.strictEqual(state.queryToken(), '')
  assert.strictEqual(api.getDraft(), '')
  assert.strictEqual(state.load().guestExpired, true)
  assert.strictEqual(requests.length + logins.length, 0, 'expiry never replays a read, write or login')
  var waiting = api.ensureQuerySession()
  var guestRace = guestRequest()
  state.setSession(account('real', 'vip'))
  guestRace.success({statusCode:200,data:guest('late')})
  await assert.rejects(waiting, function (error) { return error.sessionChanged })
  assert.strictEqual(state.userId(), 'real')
  assert.strictEqual((await api.ensureQuerySession()).tier, 'vip')
  assert.strictEqual(logins.length + requests.length, 0)
  var form = page('evidence')
  form.onLoad({kind:'normal'})
  form.setData({phone:'13800000000',note:'原账户联系信息',name:'测试节目'})
  var submission = form.submit()
  var oldForm = requests.shift()
  state.setSession(account('different', 'free'))
  form.onShow()
  assert.strictEqual(form.data.phone + form.data.note + form.data.name, '')
  oldForm.success({statusCode:200,data:{id:'old-only',no:'old-number'}})
  await submission
  assert.strictEqual(form.data.phase, 'form')
  assert(!form.data.newId)

  state.clearSession()
  var tab = page('orders')
  stack = [tab]
  var retry = tab.login()
  stack = []
  logins.shift().fail()
  await retry
  stack = [tab]
  tab.onShow()
  assert(!tab.data.loginLoading && !tab.loginAttempt, 'returning tab can retry')
  state.setSession(account('expiring', 'vip'))
  state.load().auth.expires_at = new Date(Date.now() - 1000).toISOString()
  app.onShow()
  assert.strictEqual(state.load().auth, null)
  assert.strictEqual(logins.length, 0, 'expiry on returning to the app must not start a new login')
  console.log('guest entry without WeChat login, paid identity, expiry, isolated drafts and private callbacks ok')
}
run().catch(function (e) { console.error(e); process.exitCode = 1 })
