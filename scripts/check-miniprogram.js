var fs = require('fs')
var path = require('path')
var cp = require('child_process')

var repo = path.join(__dirname, '..')
var root = path.join(repo, 'miniprogram')
var failed = false

function fail(message) {
  console.error(message)
  failed = true
}

function walk(dir, out) {
  fs.readdirSync(dir).forEach(function (name) {
    var full = path.join(dir, name)
    if (fs.statSync(full).isDirectory()) walk(full, out)
    else out.push(full)
  })
  return out
}

var banned = ['待确认', '疑似命中', '确认命中', '别名', '匹配方式', '命中类型', '顺延', 'VIP 结束后生效']
walk(root, []).forEach(function (file) {
  if (path.extname(file) === '.png') return
  var text = fs.readFileSync(file, 'utf8')
  var rel = path.relative(root, file)
  banned.forEach(function (word) {
    if (text.indexOf(word) !== -1) fail('banned string ' + word + ' in ' + rel)
  })
  if (path.extname(file) === '.json') {
    try { JSON.parse(text) } catch (err) { fail('invalid json ' + rel + ': ' + err.message) }
  }
  if (path.extname(file) === '.js') {
    var check = cp.spawnSync(process.execPath, ['--check', file], { encoding: 'utf8' })
    if (check.status !== 0) fail((check.stderr || check.stdout || 'syntax error') + ' in ' + rel)
  }
})

function readJson(rel) {
  return JSON.parse(fs.readFileSync(path.join(root, rel), 'utf8'))
}

var app = readJson('app.json')
var project = readJson('project.config.json')
if (project.appid !== 'touristappid') fail('appid is ' + project.appid)
var rootVal = project.miniprogramRoot
var rootOk = rootVal === undefined || rootVal === '' || rootVal === '.' || rootVal === './'
if (!rootOk) fail('miniprogramRoot is not the directory that contains app.json: ' + rootVal)
var resolvedRoot = path.resolve(root, rootVal || '.')
if (!fs.existsSync(path.join(resolvedRoot, 'app.json'))) fail('miniprogramRoot does not contain app.json')

var exts = ['.js', '.json', '.wxml', '.wxss']
function quad(rel) {
  exts.forEach(function (ext) {
    var file = path.join(root, rel + ext)
    if (!fs.existsSync(file)) fail('missing ' + rel + ext)
  })
}
app.pages.forEach(quad)
if ((app.tabBar.list || []).map(function (item) { return item.text }).join('/') !== '首页/会员中心/个人中心') {
  fail('tab bar texts changed')
}
app.tabBar.list.forEach(function (item) {
  quad(item.pagePath)
  ;['iconPath', 'selectedIconPath'].forEach(function (key) {
    if (!item[key] || !fs.existsSync(path.join(root, item[key]))) fail('missing tab icon ' + item[key])
  })
})
if (app.tabBar.custom) quad('custom-tab-bar/index')
if (app.sitemapLocation && !fs.existsSync(path.join(root, app.sitemapLocation))) fail('missing sitemap')

function resolveComponent(fromFile, spec) {
  if (spec.indexOf('plugin://') === 0) return null
  if (spec.charAt(0) === '/') return path.join(root, spec)
  return path.resolve(path.dirname(fromFile), spec)
}
walk(root, []).forEach(function (file) {
  if (path.extname(file) !== '.json') return
  var data = JSON.parse(fs.readFileSync(file, 'utf8'))
  var comps = data.usingComponents || {}
  Object.keys(comps).forEach(function (name) {
    var target = resolveComponent(file, comps[name])
    if (!target) return
    if (!fs.existsSync(target + '.js') && !fs.existsSync(target + '.json')) {
      fail('usingComponents ' + name + ' in ' + path.relative(root, file) + ' -> ' + comps[name])
    }
  })
})

var WXML_TAGS = {
  view: 1, text: 1, button: 1, image: 1, input: 1, textarea: 1, block: 1,
  'scroll-view': 1, navigator: 1, form: 1, label: 1, icon: 1, progress: 1,
  checkbox: 1, radio: 1, slider: 1, switch: 1, picker: 1, canvas: 1, video: 1,
  'cover-view': 1, 'cover-image': 1, 'rich-text': 1, slot: 1, template: 1,
  wxs: 1, import: 1, include: 1, camera: 1, map: 1, 'web-view': 1, audio: 1,
  'swiper': 1, 'swiper-item': 1, 'movable-view': 1, 'movable-area': 1,
  'official-account': 1, 'open-data': 1, ad: 1, page: 1
}
var VOID_TAGS = { input: 1, image: 1, import: 1, include: 1, progress: 1, checkbox: 1, radio: 1, slider: 1, switch: 1, camera: 1, icon: 1, wxs: 1 }

function checkWxml(file, text) {
  var rel = path.relative(root, file)
  var i = 0
  var stack = [{ name: '#root', ifChain: false }]
  function tagEnd(from) {
    var quote = ''
    for (var p = from; p < text.length; p++) {
      var ch = text.charAt(p)
      if (quote) {
        if (ch === quote) quote = ''
        continue
      }
      if (ch === '"' || ch === "'") { quote = ch; continue }
      if (ch === '>') return p
    }
    return -1
  }
  function attrsOf(raw) {
    var attrs = {}
    var re = /([:@A-Za-z_][\w:.-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g
    var m
    while ((m = re.exec(raw))) attrs[m[1]] = m[2] || m[3] || m[4] || ''
    return attrs
  }
  while (i < text.length) {
    if (text.slice(i, i + 4) === '<!--') {
      var c = text.indexOf('-->', i)
      i = c < 0 ? text.length : c + 3
      continue
    }
    if (text.charAt(i) !== '<') { i++; continue }
    if (text.slice(i, i + 2) === '</') {
      var end = text.indexOf('>', i)
      var name = text.slice(i + 2, end).trim().split(/\s/)[0]
      var top = stack.pop()
      if (!top || top.name !== name) fail(rel + ': closing ' + name + ' does not match ' + (top && top.name))
      i = end + 1
      continue
    }
    var endPos = tagEnd(i + 1)
    if (endPos < 0) { fail(rel + ': unclosed tag'); break }
    var raw = text.slice(i + 1, endPos).replace(/\/\s*$/, '')
    var self = /\/\s*>$/.test(text.slice(i, endPos + 1))
    var name = raw.trim().split(/\s/)[0]
    if (!WXML_TAGS[name]) fail(rel + ': unknown tag <' + name + '>')
    var attrs = attrsOf(raw.slice(name.length))
    ;['wx:if', 'wx:elif', 'wx:for'].forEach(function (key) {
      if (attrs[key] !== undefined && attrs[key].indexOf('{{') === -1) fail(rel + ': ' + key + ' needs {{ }}')
    })
    if (attrs['wx:for'] !== undefined && attrs['wx:key'] === undefined) {
      fail(rel + ': wx:for without wx:key')
    }
    if (name === 'image' && attrs.src && attrs.src.indexOf('{{') === -1) {
      var img = attrs.src.charAt(0) === '/'
        ? path.join(root, attrs.src)
        : path.resolve(path.dirname(file), attrs.src)
      if (!fs.existsSync(img)) fail(rel + ': missing image ' + attrs.src)
    }
    ;['bindtap', 'catchtap', 'bindinput', 'bindsubmit'].forEach(function (key) {
      if (attrs[key] !== undefined && !/^[A-Za-z_][\w$]*$/.test(attrs[key])) fail(rel + ': bad ' + key + '=' + attrs[key])
    })
    var parent = stack[stack.length - 1]
    var chain = !!(attrs['wx:if'] || attrs['wx:elif'] || Object.prototype.hasOwnProperty.call(attrs, 'wx:else'))
    if ((attrs['wx:elif'] || Object.prototype.hasOwnProperty.call(attrs, 'wx:else')) && !parent.ifChain) {
      fail(rel + ': ' + name + ' has wx:elif/wx:else without wx:if')
    }
    parent.ifChain = !!(attrs['wx:if'] || attrs['wx:elif'])
    if (!self && !VOID_TAGS[name]) stack.push({ name: name, ifChain: false })
    if (!chain && !attrs['wx:if']) parent.ifChain = parent.ifChain && chain ? parent.ifChain : (attrs['wx:if'] || attrs['wx:elif'] ? true : false)
    i = endPos + 1
  }
  if (stack.length !== 1) {
    fail(rel + ': unbalanced tags, still open ' + stack.map(function (n) { return n.name }).join(' > '))
  }
}

function checkWxss(file, text) {
  var rel = path.relative(root, file)
  var css = text.replace(/\/\*[\s\S]*?\*\//g, '')
  if (/(^|[,{}\n])\s*\*(?=\s|[,.{:#\[]|$)/.test(css)) fail(rel + ': universal selector is not valid in wxss')
  if (/:nth-|::placeholder|::selection/.test(css)) {
    fail(rel + ': unsupported wxss selector')
  }
  var re = /@import\s+(?:url\(\s*)?['"]([^'"]+)['"]\s*\)?\s*;/g
  var m
  while ((m = re.exec(css))) {
    var spec = m[1]
    if (spec.indexOf('http') === 0 || spec.charAt(0) === '/' || spec.slice(-5) !== '.wxss') {
      fail(rel + ': unsupported @import ' + spec)
      continue
    }
    if (!fs.existsSync(path.resolve(path.dirname(file), spec))) fail(rel + ': missing @import ' + spec)
  }
}

walk(root, []).forEach(function (file) {
  if (path.extname(file) === '.wxml') checkWxml(file, fs.readFileSync(file, 'utf8'))
  if (path.extname(file) === '.wxss') checkWxss(file, fs.readFileSync(file, 'utf8'))
})

var queue = []
global.setTimeout = function (fn) { queue.push(fn); return queue.length }
global.setInterval = function () { return 0 }
global.clearInterval = function () {}
global.clearTimeout = function () {}
function flush() {
  var batch = queue.splice(0, queue.length)
  batch.forEach(function (fn) { fn() })
}

var storage = {}
global.wx = {
  getStorageSync: function (key) { return storage[key] },
  setStorageSync: function (key, value) { storage[key] = value },
  removeStorageSync: function (key) { delete storage[key] },
  getSystemInfoSync: function () { return { statusBarHeight: 44, windowWidth: 390, windowHeight: 844, safeArea: { bottom: 844 } } },
  getMenuButtonBoundingClientRect: function () { return { top: 48, bottom: 80, height: 32, width: 87, left: 296, right: 383 } },
  showToast: function () {},
  showModal: function (opt) { if (opt && opt.success) opt.success({ confirm: true }) },
  navigateTo: function () {},
  redirectTo: function () {},
  switchTab: function () {},
  navigateBack: function () {},
  request: function () {},
  chooseMessageFile: function () {},
  getFileSystemManager: function () { return { writeFile: function (opt) { if (opt && opt.success) opt.success() } } },
  env: { USER_DATA_PATH: '/tmp' },
  openDocument: function () {},
  saveImageToPhotosAlbum: function (opt) { if (opt && opt.success) opt.success() },
  stopPullDownRefresh: function () {}
}
global.getApp = function () { return { globalData: {} } }
global.App = function (def) { global.__app = def }
global.Component = function (def) { global.__component = def }
global.getCurrentPages = function () { return [{}, {}] }

var defs = {}
var currentRel = ''
global.Page = function (def) { defs[currentRel] = def }
app.pages.forEach(function (rel) {
  currentRel = rel
  require(path.join(root, rel + '.js'))
})
require(path.join(root, 'app.js'))
require(path.join(root, 'custom-tab-bar/index.js'))

function instantiate(rel) {
  var def = defs[rel]
  if (!def) throw new Error('page not registered ' + rel)
  var page = {
    data: Object.assign({}, def.data || {}),
    getTabBar: function () { return { setData: function () {} } }
  }
  Object.keys(def).forEach(function (key) {
    if (typeof def[key] === 'function') page[key] = def[key].bind(page)
  })
  page.setData = function (obj) {
    Object.keys(obj || {}).forEach(function (key) { page.data[key] = obj[key] })
  }
  return page
}

var api = require(path.join(root, 'api/index'))
var covered = {}
function cover(id) { covered[id] = true }
function expect(cond, message) {
  if (!cond) throw new Error(message)
}

function boot(account, opts) {
  api.reset()
  api.switchAccount(account)
  if (!opts || opts.splash !== false) api.markSplashSeen()
  if (!opts || opts.announce !== false) api.markAnnounceSeen()
}

function open(rel, query, life) {
  var page = instantiate(rel)
  if (page.onLoad) page.onLoad(query || {})
  if (life !== 'load' && page.onShow) page.onShow()
  return page
}

try {
  boot('guest', { splash: false, announce: false })
  var home = open('pages/home/index', {}, 'show')
  expect(home.data.showSplash, 'splash did not cover home')
  cover('P01')
  home.finishSplash()
  expect(!home.data.showSplash && home.data.showAnnounce, 'announcement did not follow splash')
  cover('P02')
  home.closeAnnounce()

  boot('guest')
  home = open('pages/home/index', {}, 'show')
  expect(home.data.banner === 'guest' && !home.data.showSplash, 'guest home')
  cover('P04-guest')
  home.setData({ draft: '星河旧梦' })
  home.start()
  expect(home.data.needLogin, 'guest query should ask for login')
  home.login()
  expect(api.accountKind() === 'free', 'login should enter the free account')
  home = open('pages/home/index', {}, 'show')
  expect(home.data.banner === 'free', 'free home banner')
  cover('P04-nomember')
  cover('P18-empty')
  var memberPage = open('pages/member/index', {}, 'show')
  expect(memberPage.data.statusLine === '当前免费用户', 'free member center')
  expect(memberPage.data.payLabel.indexOf('开通 VIP') === 0, 'default tier label')
  memberPage.pick({ currentTarget: { dataset: { id: 'membership_regular' } } })
  expect(memberPage.data.selected === 'membership_regular', 'tier pick')
  memberPage.pick({ currentTarget: { dataset: { id: 'membership_vip' } } })

  var freeProfile = open('pages/profile/index', {}, 'show')
  expect(freeProfile.data.tier === 'free' && !freeProfile.data.guest, 'free profile')
  cover('P13-empty')
  var records = open('pages/records/index', {}, 'show')
  expect(records.data.empty, 'free records should start empty')
  cover('P14-empty')
  var orders = open('pages/orders/index', {}, 'show')
  expect(orders.data.empty, 'free orders should start empty')
  cover('P15-empty')

  var input = open('pages/input/index', {}, 'load')
  expect(input.data.phase === 'edit' && !input.data.raw, 'empty input')
  cover('P05-empty')
  input.onInput({ detail: { value: '星河旧梦\n长街烟火\n雾港来信\n南岭追光' } })
  expect(input.data.phase === 'edit' && input.data.raw, 'filled input')
  cover('P05-input')
  input.recognizeText()
  expect(input.data.phase === 'recognizing', 'recognizing')
  cover('P05-recognizing')
  flush()
  expect(input.data.phase === 'preview' && input.data.items.length === 4, 'preview')
  cover('P05-preview')
  input.confirm()

  var created = { id: api.listRecords()[0].id }
  var query = open('pages/query/index', { id: created.id }, 'load')
  expect(query.data.queued, 'queued')
  cover('P07-queued')
  query.tick()
  expect(query.data.running, 'querying')
  cover('P07-querying')
  var guard = 0
  while (!query.data.done && guard < 10) { query.tick(); guard++ }
  expect(query.data.done && query.data.showFailBanner && query.data.itemRetry, 'results with retry')
  cover('P07-results')
  cover('P07-retry')
  query.retry({ currentTarget: { dataset: { name: '南岭追光' } } })

  var report = open('pages/report/index', { name: encodeURIComponent('星河旧梦'), view: 'loading' }, 'load')
  expect(report.data.phase === 'loading', 'report loading')
  cover('P08-loading')
  report = open('pages/report/index', { name: encodeURIComponent('星河旧梦'), view: 'loadfail' }, 'load')
  expect(report.data.phase === 'fail', 'report load fail')
  cover('P08-loadfail')
  report = open('pages/report/index', { name: encodeURIComponent('雾港来信'), view: 'empty' }, 'load')
  flush()
  expect(report.data.phase === 'empty', 'report empty')
  cover('P08-empty')
  report = open('pages/report/index', { name: encodeURIComponent('星河旧梦') }, 'load')
  flush()
  expect(report.data.showLocked && report.data.fakeRows.length === 3, 'locked report')
  expect(!report.data.groups.length, 'locked report must not list real rows')
  cover('P08-locked')

  var limits = []
  for (var n = 0; n < 501; n++) limits.push('节目' + n)
  input = open('pages/input/index', {}, 'load')
  input.onInput({ detail: { value: limits.join('\n') } })
  input.recognizeText()
  flush()
  expect(input.data.failText.indexOf('500') !== -1, 'over limit')
  cover('P05-limit')
  ;[
    ['a.xls', 20, 'xlsx', 'P05-fail-xlsx'],
    ['a.xlsx', 0, 'empty', 'P05-fail-emptyfile'],
    ['a.xlsx', 6 * 1024 * 1024, 'size', 'P05-fail-size'],
    ['nocol.xlsx', 100, 'col', 'P05-fail-col'],
    ['manyrows.xlsx', 100, 'rows', 'P05-fail-rows'],
    ['plain.xlsx', 100, 'read', 'P05-fail-read']
  ].forEach(function (row) {
    input = open('pages/input/index', {}, 'load')
    input.recognizeFile(row[0], row[1])
    flush()
    expect(input.data.phase === 'fail', row[3])
    cover(row[3])
  })

  var pay = open('pages/pay/index', { product: 'membership_vip' }, 'load')
  expect(pay.data.phase === 'pending' && pay.data.showPay, 'checkout')
  cover('P10')
  var orderNo = pay.data.orderNo
  pay.pay()
  expect(pay.data.phase === 'paying' && pay.data.orderNo === orderNo, 'paying')
  cover('P10-paying')
  pay.settle({ currentTarget: { dataset: { outcome: 'fail' } } })
  expect(pay.data.phase === 'failed' && pay.data.allowCheck && !pay.data.allowRepay, 'failure before status check')
  expect(pay.data.orderNo === orderNo, 'failure keeps the order number')
  cover('P10-failure')
  pay.check()
  expect(pay.data.allowRepay && pay.data.orderNo === orderNo, 'unpaid confirmation')
  cover('P10-failure-unpaid')
  pay.repay()
  expect(pay.data.phase === 'paying' && pay.data.orderNo === orderNo, 'retry reuses the order number')
  pay.settle({ currentTarget: { dataset: { outcome: 'success' } } })
  expect(pay.data.phase === 'confirming', 'confirming')
  cover('P10-confirming')
  flush()
  expect(pay.data.phase === 'granting', 'granting')
  cover('P10-granting')
  flush()
  expect(pay.data.phase === 'granted' && pay.data.orderNo === orderNo, 'success on the same order')
  cover('P10-success')

  var coveredPay = open('pages/pay/index', { product: 'single_program_30d', name: encodeURIComponent('星河旧梦') }, 'load')
  expect(coveredPay.data.phase === 'covered', 'vip covers the single-program tier')
  cover('P10-covered')
  var cancelPay = open('pages/pay/index', { product: 'membership_regular' }, 'load')
  cancelPay.pay()
  cancelPay.settle({ currentTarget: { dataset: { outcome: 'cancel' } } })
  expect(cancelPay.data.phase === 'cancelled', 'cancelled')
  cover('P10-cancelled')
  var abnormal = open('pages/pay/index', { product: 'membership_regular' }, 'load')
  abnormal.pay()
  abnormal.settle({ currentTarget: { dataset: { outcome: 'grant_error' } } })
  expect(abnormal.data.phase === 'grant_error', 'grant error')
  cover('P10-abnormal')

  report = open('pages/report/index', { name: encodeURIComponent('星河旧梦') }, 'load')
  flush()
  expect(report.data.showOpen && !report.data.showLocked, 'unlocked report after vip')
  cover('P08-unlocked')
  var shot = open('pages/viewer/index', { id: 'xh1', name: encodeURIComponent('星河旧梦') }, 'load')
  expect(shot.data.canSave && !shot.data.low, 'screenshot with download')
  cover('P09')
  shot = open('pages/viewer/index', { id: 'xh1', name: encodeURIComponent('星河旧梦'), view: 'nodl' }, 'load')
  expect(shot.data.programOnly && !shot.data.canSave, 'no download')
  cover('P09-nodl')
  shot = open('pages/viewer/index', { id: 'xh1', name: encodeURIComponent('星河旧梦'), view: 'free' }, 'load')
  expect(shot.data.low && !shot.data.canSave, 'free screenshot')
  cover('P09-free')
  report = open('pages/report/index', { name: encodeURIComponent('星河旧梦'), view: 'expired' }, 'load')
  flush()
  expect(report.data.expired && report.data.showLocked, 'expired report')
  cover('P08-expired')

  var invite = open('pages/invite/index', {}, 'load')
  invite.onCode({ detail: { value: 'A8K3N2P7' } })
  invite.submit()
  expect(invite.data.phase === 'redeeming', 'redeeming')
  cover('P16-redeeming')
  flush()
  expect(invite.data.phase === 'success' && invite.data.hasVip, 'success beside vip')
  cover('P16-success-vip')

  boot('member')
  home = open('pages/home/index', {}, 'show')
  expect(home.data.showDone && !home.data.doneSingle, 'merged completion dialog')
  cover('P03')
  api.markQuerySeen(home.data.doneQueryId)
  var single = api.createQuery(['星河旧梦'])
  guard = 0
  var singleView = api.getQuery(single.id)
  while (singleView && !singleView.done && guard < 10) {
    singleView = api.advanceQuery(single.id)
    guard++
  }
  home = open('pages/home/index', {}, 'show')
  expect(home.data.showDone && home.data.doneSingle, 'single completion dialog')
  cover('P03-single')
  api.markQuerySeen(home.data.doneQueryId)
  api.markAnnounceSeen()
  home = open('pages/home/index', {}, 'show')
  expect(!home.data.showDone && home.data.examples.length === 3 && home.data.banner !== 'guest', 'member home')
  cover('P04')
  api.toggleExamples()
  home = open('pages/home/index', {}, 'show')
  expect(home.data.examplesError, 'examples failed')
  cover('P04-examples-fail')

  query = open('pages/query/index', { id: 'qfail' }, 'load')
  expect(query.data.allFail, 'all failed')
  cover('P07-allfail')
  query = open('pages/query/index', { id: 'qmiss' }, 'load')
  expect(query.data.head.indexOf('均未命中') !== -1, 'all miss')
  cover('P07-allmiss')

  memberPage = open('pages/member/index', {}, 'show')
  expect(memberPage.data.statusLine.indexOf('当前 VIP') === 0, 'vip member center')
  expect(memberPage.data.payLabel.indexOf('续费 VIP') === 0, 'vip renew label')
  cover('P18')
  var profile = open('pages/profile/index', {}, 'show')
  expect(profile.data.tier === 'vip' && !profile.data.guest, 'member profile')
  cover('P13')
  records = open('pages/records/index', {}, 'show')
  expect(!records.data.empty, 'member records')
  cover('P14')
  orders = open('pages/orders/index', {}, 'show')
  expect(!orders.data.empty, 'member orders')
  cover('P15')
  var legal = open('pages/legal/index', {}, 'load')
  expect(legal.data.rows.length === 3, 'agreements')
  cover('P17')
  open('pages/legal-doc/index', { id: 'member' }, 'load')

  var evidenceIds = {
    '1': 'P12-submitted',
    '2': 'P12-accepted',
    '3': 'P12-pay',
    '4': 'P12-working',
    '5': 'P12-delivered',
    '6': 'P12-done',
    '7': 'P12-failed-refunded',
    '7a': 'P12-failed',
    '8': 'P12-closed',
    '9': 'P12-cancelled'
  }
  Object.keys(evidenceIds).forEach(function (id) {
    var detail = open('pages/evidence-detail/index', { id: id }, 'load')
    expect(detail.data.name, 'evidence ' + id)
    if (evidenceIds[id] === 'P12-failed-refunded') expect(detail.data.hasRefund && detail.data.refundAmount === 680, 'refund line')
    if (evidenceIds[id] === 'P12-failed') expect(!detail.data.hasRefund && detail.data.badge === '取证失败', 'failed without refund')
    cover(evidenceIds[id])
  })
  var form = open('pages/evidence/index', { name: encodeURIComponent('星河旧梦'), kind: 'normal' }, 'load')
  expect(form.data.phase === 'form' && form.data.kind === 'normal', 'evidence form')
  cover('P11')
  var recent = open('pages/evidence/index', { name: encodeURIComponent('雾港来信'), kind: 'recent' }, 'load')
  expect(recent.data.kindText === '近期取证', 'recent evidence')
  cover('P11-recent')
  form.submit()
  expect(form.data.phase === 'submitted', 'submitted evidence')
  cover('P11-submitted')
  form.submit()
  expect(form.data.phase === 'duplicate', 'duplicate evidence')
  cover('P11-duplicate')

  invite = open('pages/invite/index', {}, 'load')
  expect(invite.data.phase === 'bound', 'bound invite')
  cover('P16-bound')

  boot('guest')
  profile = open('pages/profile/index', {}, 'show')
  expect(profile.data.guest, 'guest profile')
  cover('P13-guest')
  memberPage = open('pages/member/index', {}, 'show')
  expect(memberPage.data.guest && memberPage.data.payLabel === '登录后开通', 'guest member center')
  cover('P18-guest')

  boot('free')
  invite = open('pages/invite/index', {}, 'load')
  expect(invite.data.phase === 'form', 'invite form')
  cover('P16')
  function redeem(code) {
    var page = open('pages/invite/index', {}, 'load')
    page.onCode({ detail: { value: code } })
    page.submit()
    flush()
    return page
  }
  expect(redeem('B7C4D6E8').data.message.indexOf('无效') !== -1, 'invalid code')
  cover('P16-invalid')
  expect(redeem('C9D5E7F2').data.message.indexOf('过期') !== -1, 'expired code')
  cover('P16-expired')
  expect(redeem('D4E6F8G3').data.message.indexOf('使用') !== -1, 'used code')
  cover('P16-used')
  var success = redeem('A8K3N2P7')
  expect(success.data.phase === 'success' && !success.data.hasVip, 'success without vip')
  cover('P16-success')
  boot('free')
  var rate = open('pages/invite/index', {}, 'load')
  for (var i = 0; i < 5; i++) {
    rate.onCode({ detail: { value: 'B7C4D6E8' } })
    rate.submit()
    flush()
  }
  rate.onCode({ detail: { value: 'B7C4D6E8' } })
  rate.submit()
  flush()
  expect(rate.data.phase === 'rate', 'rate limit')
  cover('P16-rate')

  app.pages.forEach(function (rel) {
    var page = instantiate(rel)
    if (page.onLoad) page.onLoad({ id: '1', name: encodeURIComponent('星河旧梦'), product: 'membership_vip' })
    if (page.onShow) page.onShow()
    if (page.onUnload) page.onUnload()
  })
} catch (err) {
  fail('smoke: ' + (err && err.stack ? err.stack : err))
}

var screens = {
  'P01': 'pages/home/index showSplash',
  'P02': 'pages/home/index showAnnounce',
  'P03': 'pages/home/index showDone multi',
  'P03-single': 'pages/home/index showDone single',
  'P04': 'pages/home/index member examples',
  'P04-examples-fail': 'pages/home/index examplesError',
  'P04-guest': 'pages/home/index banner guest',
  'P04-nomember': 'pages/home/index banner free',
  'P05-empty': 'pages/input/index phase edit empty',
  'P05-fail-col': 'pages/input/index fail col',
  'P05-fail-emptyfile': 'pages/input/index fail empty',
  'P05-fail-read': 'pages/input/index fail read',
  'P05-fail-rows': 'pages/input/index fail rows',
  'P05-fail-size': 'pages/input/index fail size',
  'P05-fail-xlsx': 'pages/input/index fail xlsx',
  'P05-input': 'pages/input/index phase edit filled',
  'P05-limit': 'pages/input/index fail limit',
  'P05-preview': 'pages/input/index phase preview',
  'P05-recognizing': 'pages/input/index phase recognizing',
  'P07-allfail': 'pages/query/index allFail',
  'P07-allmiss': 'pages/query/index all miss',
  'P07-querying': 'pages/query/index running',
  'P07-queued': 'pages/query/index queued',
  'P07-results': 'pages/query/index done mixed',
  'P07-retry': 'pages/query/index partial failure retry',
  'P08-empty': 'pages/report/index phase empty',
  'P08-expired': 'pages/report/index expired locked',
  'P08-loadfail': 'pages/report/index phase fail',
  'P08-loading': 'pages/report/index phase loading',
  'P08-locked': 'pages/report/index showLocked',
  'P08-unlocked': 'pages/report/index showOpen',
  'P09': 'pages/viewer/index canSave',
  'P09-free': 'pages/viewer/index low',
  'P09-nodl': 'pages/viewer/index programOnly',
  'P10': 'pages/pay/index pending',
  'P10-abnormal': 'pages/pay/index grant_error',
  'P10-cancelled': 'pages/pay/index cancelled',
  'P10-confirming': 'pages/pay/index confirming',
  'P10-covered': 'pages/pay/index covered',
  'P10-failure': 'pages/pay/index failed before check',
  'P10-failure-unpaid': 'pages/pay/index allowRepay',
  'P10-granting': 'pages/pay/index granting',
  'P10-paying': 'pages/pay/index paying',
  'P10-success': 'pages/pay/index granted',
  'P11': 'pages/evidence/index form normal',
  'P11-duplicate': 'pages/evidence/index duplicate',
  'P11-recent': 'pages/evidence/index recent',
  'P11-submitted': 'pages/evidence/index submitted',
  'P12-accepted': 'pages/evidence-detail/index accepted',
  'P12-cancelled': 'pages/evidence-detail/index cancelled',
  'P12-closed': 'pages/evidence-detail/index closed',
  'P12-delivered': 'pages/evidence-detail/index delivered',
  'P12-done': 'pages/evidence-detail/index done',
  'P12-failed': 'pages/evidence-detail/index failed',
  'P12-failed-refunded': 'pages/evidence-detail/index failed refund',
  'P12-pay': 'pages/evidence-detail/index pay',
  'P12-submitted': 'pages/evidence-detail/index submitted',
  'P12-working': 'pages/evidence-detail/index working',
  'P13': 'pages/profile/index vip',
  'P13-empty': 'pages/profile/index free',
  'P13-guest': 'pages/profile/index guest',
  'P14': 'pages/records/index list',
  'P14-empty': 'pages/records/index empty',
  'P15': 'pages/orders/index list',
  'P15-empty': 'pages/orders/index empty',
  'P16': 'pages/invite/index form',
  'P16-bound': 'pages/invite/index bound',
  'P16-expired': 'pages/invite/index expired',
  'P16-invalid': 'pages/invite/index invalid',
  'P16-rate': 'pages/invite/index rate',
  'P16-redeeming': 'pages/invite/index redeeming',
  'P16-success': 'pages/invite/index success',
  'P16-success-vip': 'pages/invite/index success with vip',
  'P16-used': 'pages/invite/index used',
  'P17': 'pages/legal/index',
  'P18': 'pages/member/index vip',
  'P18-empty': 'pages/member/index free',
  'P18-guest': 'pages/member/index guest'
}

var names = Object.keys(screens)
var missing = names.filter(function (id) { return !covered[id] })
console.log('coverage ' + (names.length - missing.length) + '/' + names.length)
console.log('| Screen | Page and state | Covered |')
console.log('| --- | --- | --- |')
names.forEach(function (id) {
  console.log('| ' + id + ' | ' + screens[id] + ' | ' + (covered[id] ? 'yes' : 'NO') + ' |')
})
if (missing.length) {
  console.error('not covered: ' + missing.join(', '))
  failed = true
}
if (names.length !== 78) fail('expected 78 screens, mapped ' + names.length)

if (failed) process.exit(1)
console.log('miniprogram check ok')
