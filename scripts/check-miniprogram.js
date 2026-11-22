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

var banned = ['待确认', '疑似命中', '确认命中', '别名', '匹配方式', '命中类型', '顺延', 'VIP 结束后生效', '13800002610']
walk(root, []).forEach(function (file) {
  if (['.png', '.webp'].indexOf(path.extname(file)) !== -1) return
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

var frontendStopWords = readJson('stop-words.json')
if (JSON.stringify(require(path.join(root, 'stop-words.js'))) !== JSON.stringify(frontendStopWords)) {
  fail('miniprogram/stop-words.js must match stop-words.json')
}
// 小程序运行时 require 只能加载 .js，加载 .json 会白屏。
;(function scan(dir) {
  fs.readdirSync(dir, { withFileTypes: true }).forEach(function (e) {
    var p = path.join(dir, e.name)
    if (e.isDirectory()) return scan(p)
    if (/\.js$/.test(e.name) && /require\(\s*['"][^'"]+\.json['"]\s*\)/.test(fs.readFileSync(p, 'utf8'))) {
      fail('mini program must not require .json: ' + path.relative(root, p))
    }
  })
})(root)
var backendStopWords = JSON.parse(fs.readFileSync(path.join(repo, 'src/nadou/stop_words.json'), 'utf8'))
if (JSON.stringify(frontendStopWords) !== JSON.stringify(backendStopWords)) {
  fail('miniprogram stop words must exactly match backend defaults')
}

var packageBytes = walk(root, []).reduce(function (sum, file) { return sum + fs.statSync(file).size }, 0)
console.log('Main package source bytes: ' + packageBytes)
if (packageBytes >= 2 * 1024 * 1024) fail('main package must be below 2 MB')
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
  var known = Object.assign({}, WXML_TAGS)
  var jsonFile = file.replace(/\.wxml$/, '.json')
  if (fs.existsSync(jsonFile)) {
    var conf = JSON.parse(fs.readFileSync(jsonFile, 'utf8'))
    Object.keys(conf.usingComponents || {}).forEach(function (name) { known[name] = 1 })
  }
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
    if (!known[name]) fail(rel + ': unknown tag <' + name + '>')
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
  login: function (opts) { opts.success({ code: 'check-code' }) },
  request: function (opts) { opts.success({ statusCode: 200, data: {
    token: 'check-token', token_type: 'bearer', expires_at: new Date(Date.now() + 86400000).toISOString(),
    user: { id: 'check-user', tier: 'free' }
  } }) },
  chooseMessageFile: function () {},
  getFileSystemManager: function () { return { writeFile: function (opt) { if (opt && opt.success) opt.success() } } },
  env: { USER_DATA_PATH: '/tmp' },
  openDocument: function () {},
  saveImageToPhotosAlbum: function (opt) { if (opt && opt.success) opt.success() },
  stopPullDownRefresh: function () {},
  hideTabBar: function () {
    if (!wx._tabBarLog) wx._tabBarLog = []
    wx._tabBarLog.push('hide')
  },
  showTabBar: function () {
    if (!wx._tabBarLog) wx._tabBarLog = []
    wx._tabBarLog.push('show')
  }
}
global.getApp = function () {
  if (!global.__app) global.__app = { globalData: {} }
  if (!global.__app.globalData) global.__app.globalData = {}
  return global.__app
}
global.App = function (def) { global.__app = def }
global.Component = function (def) { global.__component = def }
global.getCurrentPages = function () { return [{}, {}] }

var defaults = require(path.join(root, 'config'))
if (defaults.mode !== 'hybrid' || defaults.trialMode !== true || defaults.calibrating !== true) {
  fail('trial defaults must enable hybrid mode, trialMode and calibrating')
}

// Baseline state coverage is explicitly mock; live routing has separate async checks.
require(path.join(root, 'config')).mode = 'mock'
require(path.join(root, 'config')).trialMode = false
require(path.join(root, 'config')).calibrating = false
require(path.join(root, 'config')).apiBase = '/test-api'
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
  getApp().globalData.splashShownThisLaunch = !opts || opts.splash !== false
  if (!opts || opts.announce !== false) api.markAnnounceSeen()
}

function open(rel, query, life) {
  var page = instantiate(rel)
  if (page.onLoad) page.onLoad(query || {})
  if (life !== 'load' && page.onShow) page.onShow()
  return page
}

function checkMemberRules() {
  var member = require(path.join(root, 'utils/member'))
  var plans = api.membershipPlans()
  function texts(plan) {
    return plan.perks.map(function (row) { return (row.ok ? 'y:' : 'n:') + row.text }).join('|')
  }
  expect(plans.length === 3 && plans[0].id === 'membership_vip' && plans[1].id === 'membership_regular' && plans[2].id === 'free', 'vip, regular, then free')
  expect(plans[0].selectable && plans[0].name === 'VIP 会员' && plans[0].price === 599 && plans[0].days === 30, 'vip price from catalog')
  expect(plans[1].selectable && plans[1].name === '普通会员' && plans[1].price === 299 && plans[1].days === 30, 'regular price from catalog')
  expect(!plans[2].selectable && plans[2].name === '免费用户' && plans[2].priceText === '免费', 'free card has no purchase price')
  expect(texts(plans[0]) === 'y:30–89 天记录|y:90 天以上记录|y:高清截图与下载', 'vip perk rows')
  expect(texts(plans[1]) === 'n:30–89 天记录 · 仅 VIP|y:90 天以上记录|y:高清截图与下载', 'regular perk rows')
  expect(texts(plans[2]) === 'y:180 天以上记录（低清）|n:高清截图 · 仅会员|n:下载 · 仅会员', 'free perk rows')
  expect(member.freeCornerLabel('free') === '当前' && member.freeCornerLabel('vip') === '免费' && member.freeCornerLabel('regular') === '免费' && member.freeCornerLabel('guest') === '免费', 'free corner is 当前 only for the free tier')
  plans.forEach(function (plan) {
    expect(plan.perks.length === 3, 'three perk rows')
    plan.perks.forEach(function (row) {
      expect(row.text.indexOf('\u2014') === -1 && row.text.indexOf('✓') === -1, 'perk copy has no em dash or check mark')
    })
  })
  var blocked = plans[1].perks[0]
  expect(!blocked.ok && blocked.text.indexOf('\u2013') !== -1 && blocked.text.indexOf('\u00b7') !== -1, 'unavailable perk is gray copy with a reason')
  expect(member.confirmLabel(plans[0].price) === '确认开通 ¥599', 'vip confirm label')
  expect(member.confirmLabel(plans[1].price) === '确认开通 ¥299', 'regular confirm label')
  expect(member.statusLine('guest') === '未登录', 'guest status')
  expect(member.statusLine('free') === '免费 · 可看 180 天以上低清记录', 'free status names the low-res window')
  expect(member.statusLine('vip', '2026-12-18') === 'VIP 至 2026-12-18', 'vip status')
  expect(member.statusLine('regular', '2026-12-18') === '普通会员 至 2026-12-18', 'regular status')
  var fit = member.screenUsage()
  expect(member.LAYOUT.cardCount === 3 && member.LAYOUT.payBlock === 56 && fit.used === 614, 'free card keeps the confirm slot, used ' + fit.used)
  var css = fs.readFileSync(path.join(root, 'pages/member/index.wxss'), 'utf8')
  expect(css.indexOf('overflow: hidden') === -1, 'member page can scroll')
  expect(css.indexOf('background: #e8ecf1') !== -1 && css.indexOf('box-shadow: none') !== -1, 'free card is flat gray')
  expect(css.indexOf('border: 2px solid transparent') !== -1, 'selection border is reserved at 2px')
  expect(css.indexOf('border-color: #1D4E9E') !== -1 && css.indexOf('background: #E7F0FF') !== -1, 'selected card')
  expect(css.indexOf('background: #1D4E9E') !== -1 && css.indexOf('visibility: hidden') !== -1, 'confirm button slot')
  expect(css.indexOf('#e4570e') === -1, 'member card price is not orange')
  expect(/\.price \{[^}]*color:\s*#1c2430/.test(css) && /\.price \{[^}]*font-weight:\s*700/.test(css), 'member card price is black and bold')
  expect(css.indexOf('.perk-mark.gap') !== -1, 'gray perks keep a hidden check')
  expect(css.indexOf('padding-bottom: calc(56px + env(safe-area-inset-bottom))') !== -1, 'tab clearance')
  var xml = fs.readFileSync(path.join(root, 'pages/member/index.wxml'), 'utf8')
  expect(xml.indexOf('输入邀请码') !== -1 && xml.indexOf('class="invite-link"') !== -1, 'invite is a text link')
  expect(xml.indexOf('class="perk-mark {{perk.ok ? \'\' : \'gap\'}}"') !== -1, 'every perk reserves the check width')
  expect(xml.indexOf('wx:if="{{plan.selectable}}"') !== -1, 'free card has no radio')
  expect(xml.indexOf('choice static') !== -1 || xml.indexOf("plan.selectable ? '' : 'static'") !== -1, 'free card is marked static')
  expect(xml.indexOf('\u2014') === -1 && xml.indexOf('¥599') === -1 && xml.indexOf('¥299') === -1, 'page does not hardcode prices or an em dash')
  expect((xml.match(/class="btn /g) || []).length === 1, 'member page has one solid button')
  var viewerXml = fs.readFileSync(path.join(root, 'pages/viewer/index.wxml'), 'utf8')
  expect(viewerXml.indexOf('wx:if="{{downloadEntry}}"') !== -1, 'download entry is conditional')
  expect(viewerXml.indexOf('{{blockedReason}}') !== -1, 'blocked download writes the reason')
  expect(viewerXml.indexOf('当前为低清水印图，开通会员可看高清</view>') !== -1, 'free screenshot warning has no period')
  expect(viewerXml.indexOf('开通会员可看高清。') === -1 && viewerXml.indexOf('不可放大到高清') === -1, 'old low-res warning is gone')
  var freeOffer = member.downloadOffer({ canSave: false, low: true, tier: 'free' })
  expect(!freeOffer.downloadEntry && freeOffer.blockedReason === '开通会员后下载', 'free user has no download entry')
  var guestOffer = member.downloadOffer({ canSave: false, tier: 'guest' })
  expect(!guestOffer.downloadEntry && guestOffer.blockedReason === '开通会员后下载', 'guest download reason')
  var regularBlock = member.downloadOffer({ canSave: false, tier: 'regular' })
  expect(!regularBlock.downloadEntry && regularBlock.blockedReason === '30–89 天记录 · 仅 VIP', 'regular 30-89 download reason')
  expect(regularBlock.blockedReason === plans[1].perks[0].text, 'download hint matches the gray perk exactly')
  var paid = member.downloadOffer({ canSave: true, tier: 'vip' })
  expect(paid.downloadEntry && paid.blockedReason === '', 'paid download entry')
  var program = member.downloadOffer({ canSave: false, programOnly: true, low: true, tier: 'free' })
  expect(!program.downloadEntry && program.blockedReason === '', 'program grant is not a download entry')
}

async function main() {
try {
  checkMemberRules()

  var titlesUtil = require(path.join(root, 'utils/text'))
  var approvedWords = require(path.join(root, 'stop-words.json'))
  expect(approvedWords.length === 102, '102 approved stop words')
  approvedWords.forEach(function (word) {
    expect(titlesUtil.isStopped('　' + word + ' '), 'normalized stop word: ' + word)
    expect(!titlesUtil.isStopped(word + '测试片'), 'whole words only: ' + word)
  })
  var protectedTitles = ['家庭', '远方', '狗', '父亲', '母亲', '甲乙', '日期', '名称']
  expect(titlesUtil.parseTitles(protectedTitles.join('\n')).items.length === protectedTitles.length,
    'do not filter other short titles')
  var shape = require(path.join(root, 'api/shape'))
  var presenter = require(path.join(root, 'utils/present'))
  var categoryBody = shape.sanitizeSearch({ outcome: 'hit', platform_count: 1,
    platform_groups: [{ records: [{record_id: 'category-open', locked: false, category: '共识类别'},
      {record_id: 'category-empty', locked: false, program_type: '不能替代类别'}] }],
    locked_records: [{record_id: 'category-locked', locked: true, category: '不可泄漏'}] })
  expect(categoryBody.records[0].category === '共识类别', 'category survives API adapter')
  expect(!categoryBody.records[1].category, 'blank category has no type fallback')
  expect(!categoryBody.records[2].category, 'locked category stripped')
  var categoryView = presenter.present(categoryBody)
  expect(categoryView.groups[0].rows[0].category === '共识类别', 'report category')
  var bytes = require(path.join(root, 'utils/xlsx')).build({title: '测试', scope: '', day: '',
    rows: [{ platform: '测试平台', subtype: '测试子类型', category: '共识类别', type: '节目类型值' }]})
  var bookText = Buffer.from(bytes).toString('utf8')
  expect(bookText.indexOf('C5" t="inlineStr"><is><t>类别') !== -1, 'xlsx category header order')
  expect(bookText.indexOf('C6" t="inlineStr"><is><t>共识类别') !== -1, 'xlsx category value')
  expect(bookText.indexOf('D6" t="inlineStr"><is><t>节目类型值') !== -1, 'xlsx program type independent')
  boot('guest', { splash: false, announce: false })
  wx._tabBarLog = []
  var skipAtZero = open('pages/home/index', {}, 'show')
  var readyAtZero = skipAtZero.data.showSplash && skipAtZero.data.seconds === 5 &&
    wx._tabBarLog[wx._tabBarLog.length - 1] === 'hide'
  skipAtZero.finishSplash()
  expect(readyAtZero && !skipAtZero.data.showSplash && wx._tabBarLog[wx._tabBarLog.length - 1] === 'show',
    'skip at second 0 closes splash and restores the tab bar')
  getApp().onShow()
  skipAtZero.onShow()
  var shareReturn = open('pages/home/index', {}, 'show')
  expect(!skipAtZero.data.showSplash && !shareReturn.data.showSplash && getApp().globalData.splashShownThisLaunch,
    'warm start from background or share card does not show splash')

  boot('guest', { splash: false, announce: false })
  wx._tabBarLog = []
  var home = open('pages/home/index', {}, 'show')
  expect(home.data.showSplash, 'splash did not cover home')
  expect(home.data.splashSkipTop === 48 && home.data.splashSkipHeight === 32 && home.data.splashSkipRight === 102,
    'skip matches the capsule height and sits 8px to its left')
  expect(home.data.splashSkipHitHeight >= 44 && home.data.splashSkipHitTop <= home.data.splashSkipTop,
    'skip hit area is at least 44pt without moving the pill')
  expect(wx._tabBarLog[wx._tabBarLog.length - 1] === 'hide', 'splash hides the custom tab bar')
  var homeWxml = fs.readFileSync(path.join(root, 'pages/home/index.wxml'), 'utf8')
  expect(homeWxml.indexOf('showAnnounce && !showSplash') !== -1, 'announcement stays down during splash')
  expect(homeWxml.indexOf('showDone && !showAnnounce && !showSplash') !== -1, 'done dialog stays down during splash')
  expect(homeWxml.indexOf('is-text') === -1 && homeWxml.indexOf('那豆在架') !== -1, 'no text-version splash branch')
  var splashCss = fs.readFileSync(path.join(root, 'app.wxss'), 'utf8')
  var splashAt = splashCss.indexOf('.splash-cover {')
  var splashEnd = splashCss.indexOf('.member-status', splashAt)
  splashCss = splashCss.slice(splashAt, splashEnd)
  expect(splashCss.indexOf('#1D4E9E') === -1 && splashCss.indexOf('#163E80') === -1, 'splash has no bright blue fallback')
  expect(splashCss.indexOf('#0a1630') !== -1 && splashCss.indexOf('#050b1a') !== -1, 'image failure uses the dark fallback')
  expect(homeWxml.indexOf('splash-mark.png') !== -1, 'splash logo is the P01 mark')
  expect(homeWxml.indexOf('splash-skip-hit') !== -1, 'skip hit target wraps the visible pill')
  var layoutSplash = require(path.join(root, 'pages/home/splash-layout')).layoutSplash
  function gapOf(frame) { return frame.splashGlassTop - frame.splashTextBottom }
  var iphone8 = layoutSplash({ windowWidth: 375, windowHeight: 667, statusBarHeight: 20 },
    { top: 24, height: 32, left: 281 })
  expect(gapOf(iphone8) >= 16 && iphone8.splashGlassTop >= 0 && iphone8.splashGlassBottom <= 667,
    'iPhone 8 keeps 16px above the glass and the glass fully visible')
  var tiny = layoutSplash({ windowWidth: 360, windowHeight: 640, statusBarHeight: 20 },
    { top: 24, height: 32, left: 266 })
  expect(gapOf(tiny) >= 16 && tiny.splashGlassTop >= 0 && tiny.splashGlassBottom <= 640,
    '360x640 keeps 16px above the glass and the glass fully visible')
  var iphone14 = layoutSplash({ windowWidth: 390, windowHeight: 844, statusBarHeight: 47 },
    { top: 51, height: 32, left: 296 })
  expect(Math.abs(iphone14.splashTitleSize - 96 * 390 / 750) < 1, 'iPhone 14 keeps the P01 title size')
  var poster = fs.readFileSync(path.join(root, 'assets/p01-splash-bg.webp'))
  expect(poster.length < 200 * 1024, 'splash artwork stays under 200KB')
  var vp8 = poster.indexOf('VP8 ')
  var dim = poster.readUInt16LE(vp8 + 14)
  var dimH = poster.readUInt16LE(vp8 + 16)
  expect((dim & 0x3fff) === 750 && (dimH & 0x3fff) === 1624, 'splash artwork is 750x1624')
  flush()
  expect(home.data.showSplash && !api.splashSeen(), 'image timeout must not write splashSeen')
  home.splashFailed()
  expect(home.data.showSplash && home.data.splashFailed && !api.splashSeen(), 'image error stays on the dark splash')
  cover('P01')
  home.finishSplash()
  expect(!home.data.showSplash && home.data.showAnnounce && !api.splashSeen(), 'skip ends splash without writing storage')
  expect(getApp().globalData.splashShownThisLaunch, 'splash counts for this cold start')
  expect(wx._tabBarLog[wx._tabBarLog.length - 1] === 'show', 'splash end shows the custom tab bar')
  home.onShow()
  expect(!home.data.showSplash, 'returning from background does not show splash again')
  var second = open('pages/home/index', {}, 'show')
  expect(!second.data.showSplash, 'switching back to home does not show splash again')
  getApp().onLaunch()
  expect(!getApp().globalData.splashShownThisLaunch, 'cold start clears the memory flag')
  var cold = open('pages/home/index', {}, 'show')
  expect(cold.data.showSplash && !api.splashSeen(), 'next cold start shows splash without storage')
  cover('P02')
  home.closeAnnounce()

  boot('guest', { splash: false, announce: false })
  var ticks = []
  var savedInterval = global.setInterval
  global.setInterval = function (fn, ms) { ticks.push({ fn: fn, ms: ms }); return 1 }
  try {
    var timed = open('pages/home/index', {}, 'show')
    expect(ticks.length === 1 && ticks[0].ms === 1000, 'splash countdown is one second')
    flush()
    timed.splashFailed()
    expect(timed.data.showSplash && timed.data.splashFailed && !api.splashSeen(), 'failed image keeps the countdown')
    var step
    for (step = 0; step < 4; step++) ticks[0].fn()
    expect(timed.data.showSplash && timed.data.seconds === 1 && !api.splashSeen(), 'countdown has not finished early')
    ticks[0].fn()
    expect(!timed.data.showSplash && !api.splashSeen() && getApp().globalData.splashShownThisLaunch,
      'countdown end hides splash without writing storage')
    var afterCountdown = open('pages/home/index', {}, 'show')
    expect(!afterCountdown.data.showSplash, 'same launch does not show splash after countdown')
  } finally {
    global.setInterval = savedInterval
  }

  var splashApi = require(path.join(root, 'api/splash'))
  boot('guest', { splash: false, announce: false })
  expect(splashApi.shouldShow(true) === true && splashApi.shouldShow(undefined) === true, 'switch on shows splash')
  expect(splashApi.shouldShow(false) === false, 'switch off does not show')
  api.markSplashSeen()
  expect(splashApi.shouldShow(true) === true, 'splashSeen storage is not a display condition')
  boot('guest', { splash: false, announce: false })
  var splashSource = fs.readFileSync(path.join(root, 'api/splash.js'), 'utf8')
  expect(splashSource.indexOf('http://') === -1 && splashSource.indexOf('https://') === -1, 'splash config has no hardcoded host')
  expect(splashSource.indexOf("'/api/v1/app/config'") !== -1, 'splash config path')
  function drain() { return new Promise(function (resolve) { setImmediate(resolve) }) }
  function useSplashRequest(mode, request) {
    useSplashRequest.prev = { mode: defaults.mode, request: wx.request, base: defaults.apiBase }
    defaults.mode = mode
    wx.request = request
  }
  function restoreSplashRequest() {
    defaults.mode = useSplashRequest.prev.mode
    wx.request = useSplashRequest.prev.request
    defaults.apiBase = useSplashRequest.prev.base
  }
  try {
    var calls = 0
    useSplashRequest('mock', function () { calls++ })
    var mocked = open('pages/home/index', {}, 'show')
    await drain()
    expect(calls === 0 && mocked.data.showSplash && !api.splashSeen(), 'mock mode shows splash without a request')
    restoreSplashRequest()

    var seenReq = {}
    useSplashRequest('hybrid', function (opts) {
      seenReq = { url: opts.url, timeout: opts.timeout, auth: opts.header && opts.header.Authorization }
      opts.fail({ errMsg: 'request:fail' })
    })
    var enabled = await splashApi.loadEnabled()
    expect(seenReq.url === '/test-api/api/v1/app/config', 'config uses apiBase and /api/v1/app/config')
    expect(seenReq.timeout === 1500, 'config timeout is 1.5s')
    expect(!seenReq.auth, 'config needs no login')
    expect(enabled === true, 'config error counts as splash enabled')
    restoreSplashRequest()

    useSplashRequest('hybrid', function (opts) { opts.success({ statusCode: 503, data: {} }) })
    expect(await splashApi.loadEnabled() === true, 'config http error counts as splash enabled')
    restoreSplashRequest()

    useSplashRequest('hybrid', function (opts) { opts.success({ statusCode: 200, data: {} }) })
    expect(await splashApi.loadEnabled() === true, 'missing splash_enabled counts as enabled')
    restoreSplashRequest()

    useSplashRequest('hybrid', function (opts) { opts.success({ statusCode: 200, data: { splash_enabled: 'false' } }) })
    expect(await splashApi.loadEnabled() === true, 'only boolean false disables splash')
    restoreSplashRequest()

    useSplashRequest('hybrid', function () {})
    var pendingEnabled = splashApi.loadEnabled()
    flush()
    expect(await pendingEnabled === true, 'config timeout counts as splash enabled')
    restoreSplashRequest()

    var leaked = 0
    useSplashRequest('hybrid', function () { leaked++ })
    defaults.apiBase = ''
    expect(await splashApi.loadEnabled() === true, 'unconfigured base counts as splash enabled')
    expect(leaked === 0, 'unconfigured base must not request')
    restoreSplashRequest()

    useSplashRequest('hybrid', function (opts) {
      opts.success({ statusCode: 200, data: { splash_enabled: false } })
    })
    boot('guest', { splash: false, announce: false })
    var switched = open('pages/home/index', {}, 'show')
    expect(switched.data.showSplash, 'splash shows before config returns')
    await drain()
    expect(!switched.data.showSplash && !api.splashSeen(), 'explicit false hides splash and does not mark seen')
    var switchedAgain = open('pages/home/index', {}, 'show')
    await drain()
    expect(!switchedAgain.data.showSplash && !api.splashSeen(), 'explicit false stays hidden without marking seen')
    restoreSplashRequest()

    useSplashRequest('hybrid', function (opts) { opts.fail({ errMsg: 'request:fail timeout' }) })
    boot('guest', { splash: false, announce: false })
    var errored = open('pages/home/index', {}, 'show')
    await drain()
    expect(errored.data.showSplash && !api.splashSeen(), 'config error still shows splash')
    restoreSplashRequest()

    useSplashRequest('hybrid', function () {})
    boot('guest', { splash: false, announce: false })
    var hung = open('pages/home/index', {}, 'show')
    flush()
    await drain()
    expect(hung.data.showSplash && !api.splashSeen(), 'config timeout still shows splash')
  } finally {
    if (useSplashRequest.prev) restoreSplashRequest()
  }

  boot('guest')
  home = open('pages/home/index', {}, 'show')
  expect(home.data.banner === 'guest' && !home.data.showSplash, 'guest home')
  cover('P04-guest')
  home.setData({ draft: '星河旧梦' })
  home.start()
  expect(home.data.needLogin, 'guest query should ask for login')
  await home.login()
  expect(!api.profile().guest && api.profile().tier === 'free', 'login should enter the free account')
  // Commerce below is an offline prototype flow, separate from real session membership.
  api.switchAccount('free')
  home = open('pages/home/index', {}, 'show')
  expect(home.data.banner === 'free', 'free home banner')
  cover('P04-nomember')
  cover('P18-empty')
  var memberPage = open('pages/member/index', {}, 'show')
  expect(memberPage.data.statusLine === '免费 · 可看 180 天以上低清记录', 'free member center')
  expect(memberPage.data.plans[2].priceText === '当前' && !memberPage.data.plans[2].selectable, 'free tier shows 当前')
  expect(memberPage.data.selected === '' && !memberPage.data.showPay && !memberPage.data.payLabel, 'nothing selected')
  memberPage.pick({ currentTarget: { dataset: { id: 'free' } } })
  expect(memberPage.data.selected === '' && !memberPage.data.showPay, 'free card ignores taps')
  memberPage.pick({ currentTarget: { dataset: { id: 'membership_regular' } } })
  expect(memberPage.data.selected === 'membership_regular' && memberPage.data.showPay, 'tier pick shows confirm')
  expect(memberPage.data.payLabel === '确认开通 ¥' + memberPage.data.plans[1].price, 'regular confirm amount')
  memberPage.pick({ currentTarget: { dataset: { id: 'membership_vip' } } })
  expect(memberPage.data.showPay && memberPage.data.payLabel === '确认开通 ¥' + memberPage.data.plans[0].price, 'vip confirm amount')
  memberPage.pick({ currentTarget: { dataset: { id: 'free' } } })
  expect(memberPage.data.selected === 'membership_vip' && memberPage.data.showPay, 'free card does not clear a selection')

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
  expect(!report.data.showDownload, 'free user has no report download entry')
  cover('P08-locked')

  var limits = []
  for (var n = 0; n < 501; n++) limits.push('节目' + n)
  input = open('pages/input/index', {}, 'load')
  input.onInput({ detail: { value: limits.join('\n') } })
  input.recognizeText()
  flush()
  expect(input.data.phase === 'limit', 'over limit is queryable')
  expect(input.data.failText === '已超过 500 个节目名，仅保留前 500 个。', 'over limit message')
  expect(input.data.items.length === 500 && input.data.count === 500, '500/500 count')
  expect(input.data.raw === limits.slice(0, 500).join('\n'), 'first 500 retained in order')
  input.confirm()
  var retainedQuery = api.listRecords().filter(function (row) { return row.id === api.listRecords()[0].id })
  expect(retainedQuery.length === 500 && retainedQuery[499].name === limits[499],
    'truncated query can start')
  cover('P05-limit')
  var XlsxFixture = require(path.join(__dirname, 'xlsx-fixture'))
  var uploadedSheets = {}
  var writeUpload = wx.getFileSystemManager().writeFile
  wx.getFileSystemManager = function () {
    return {
      writeFile: writeUpload,
      readFile: function (opt) {
        if (Object.prototype.hasOwnProperty.call(uploadedSheets, opt.filePath)) opt.success({ data: uploadedSheets[opt.filePath] })
        else if (opt.fail) opt.fail({ errMsg: 'readFile:fail' })
      }
    }
  }
  function workbook(name, rows) {
    var bytes = XlsxFixture.build(rows)
    var filePath = '/tmp/p05-' + name
    uploadedSheets[filePath] = bytes
    return { name: name, size: bytes.byteLength, path: filePath }
  }
  function expectUploadFail(file, text, screen) {
    input = open('pages/input/index', {}, 'load')
    input.recognizeFile(file.name, file.size, file.path || '')
    flush()
    expect(input.data.phase === 'fail', screen)
    expect(input.data.failText === text, screen + ' message')
    expect(input.data.fromFile === true, screen + ' keeps the file flow')
    cover(screen)
  }
  expectUploadFail({ name: 'a.xls', size: 20 }, '仅支持 .xlsx 文件', 'P05-fail-xlsx')
  expectUploadFail(workbook('empty.xlsx', [['节目名称']]), '文件为空，请重新选择。', 'P05-fail-emptyfile')
  expectUploadFail({ name: 'big.xlsx', size: 6 * 1024 * 1024, path: '/tmp/p05-big.xlsx' }, '文件超过 5MB，请拆分后再上传。', 'P05-fail-size')
  expectUploadFail(workbook('nocol.xlsx', [['片名', '出品方'], ['星河旧梦', '云豆传媒']]), '未找到「节目名称」列，请确认它在表格前 10 行内', 'P05-fail-col')
  var manyRows = [['节目名称']]
  for (var rowIndex = 0; rowIndex < 501; rowIndex++) manyRows.push(['节目' + rowIndex])
  expectUploadFail(workbook('many.xlsx', manyRows), '超过 500 行，请删减后再上传。', 'P05-fail-rows')
  uploadedSheets['/tmp/p05-plain.xlsx'] = new Uint8Array([1, 2, 3, 4]).buffer
  expectUploadFail({ name: 'plain.xlsx', size: 4, path: '/tmp/p05-plain.xlsx' }, '文件读取失败，请重新选择文件', 'P05-fail-read')

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
  expect(shot.data.canSave && shot.data.downloadEntry && !shot.data.blockedReason && !shot.data.low, 'screenshot with download')
  cover('P09')
  shot = open('pages/viewer/index', { id: 'xh1', name: encodeURIComponent('星河旧梦'), view: 'nodl' }, 'load')
  expect(shot.data.programOnly && !shot.data.canSave && !shot.data.downloadEntry, 'no download')
  cover('P09-nodl')
  shot = open('pages/viewer/index', { id: 'xh1', name: encodeURIComponent('星河旧梦'), view: 'free' }, 'load')
  expect(shot.data.low && !shot.data.canSave && !shot.data.downloadEntry, 'free screenshot')
  expect(shot.data.blockedReason === '开通会员后下载', 'free download states the reason')
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
  expect(memberPage.data.statusLine.indexOf('VIP 至 ') === 0, 'vip member center')
  expect(memberPage.data.plans[2].priceText === '免费', 'vip viewer still sees 免费 on the free card')
  expect(!memberPage.data.showPay && memberPage.data.selected === '', 'vip page starts unselected')
  memberPage.pick({ currentTarget: { dataset: { id: 'membership_vip' } } })
  expect(memberPage.data.payLabel === '确认开通 ¥' + memberPage.data.plans[0].price, 'vip confirm amount')
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
  var blankEvidence = open('pages/evidence/index', {}, 'load')
  expect(blankEvidence.data.phone === '' && blankEvidence.data.name === '雾港来信', 'mock sample title only, phone stays empty')
  var form = open('pages/evidence/index', { name: encodeURIComponent('星河旧梦'), kind: 'normal' }, 'load')
  expect(form.data.phase === 'form' && form.data.kind === 'normal' && form.data.phone === '', 'evidence form')
  cover('P11')
  var recent = open('pages/evidence/index', { name: encodeURIComponent('雾港来信'), kind: 'recent' }, 'load')
  expect(recent.data.kindText === '近期取证', 'recent evidence')
  cover('P11-recent')
  form.onPhone({ detail: { value: '13912345678' } })
  form.submit()
  expect(form.data.phase === 'submitted', 'submitted evidence')
  cover('P11-submitted')
  form.submit()
  expect(form.data.showDuplicate && form.data.dupId, 'duplicate evidence')
  cover('P11-duplicate')
  report = open('pages/report/index', { name: encodeURIComponent('星河旧梦') }, 'load')
  flush()
  expect(report.data.appliedId === form.data.newId, 'submitted program shows applied link')
  var navigated = ''
  wx.navigateTo = function (opts) { navigated = opts.url }
  report.applyEvidence()
  expect(navigated === '/pages/evidence-detail/index?id=' + form.data.newId, 'applied link opens P12')
  cover('P08-applied')
  form = open('pages/evidence/index', { name: encodeURIComponent('测试片'), platform: encodeURIComponent('测试平台') }, 'load')
  expect(form.data.name === '测试片' && form.data.platform === '测试平台', 'evidence prefills name and platform')
  query.evidence({ currentTarget: { dataset: { name: '测试片', platform: '测试平台' } } })
  expect(navigated.indexOf('&platform=' + encodeURIComponent('测试平台')) !== -1, 'P07 passes permitted platform')

  invite = open('pages/invite/index', {}, 'load')
  expect(invite.data.phase === 'bound', 'bound invite')
  cover('P16-bound')

  boot('guest')
  profile = open('pages/profile/index', {}, 'show')
  expect(profile.data.guest, 'guest profile')
  cover('P13-guest')
  memberPage = open('pages/member/index', {}, 'show')
  expect(memberPage.data.guest && memberPage.data.statusLine === '未登录' && !memberPage.data.showPay, 'guest member center')
  expect(memberPage.data.plans[2].priceText === '免费', 'guest sees 免费, not 当前')
  memberPage.pick({ currentTarget: { dataset: { id: 'membership_regular' } } })
  expect(memberPage.data.payLabel === '确认开通 ¥' + memberPage.data.plans[1].price, 'guest confirm uses catalog price')
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

  require(path.join(root, 'config')).calibrating = true
  query = open('pages/query/index', { id: 'qmiss' }, 'load')
  expect(query.data.calibrating && query.data.title === '数据校准中' && !query.data.head && !query.data.items.length, 'calibrating results')
  cover('P07-calibrating')
  require(path.join(root, 'config')).calibrating = false

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
  'P05-limit': 'pages/input/index phase limit',
  'P05-preview': 'pages/input/index phase preview',
  'P05-recognizing': 'pages/input/index phase recognizing',
  'P07-calibrating': 'pages/query/index calibrating',
  'P07-allfail': 'pages/query/index allFail',
  'P07-allmiss': 'pages/query/index all miss',
  'P07-querying': 'pages/query/index running',
  'P07-queued': 'pages/query/index queued',
  'P07-results': 'pages/query/index done mixed',
  'P07-retry': 'pages/query/index partial failure retry',
  'P08-applied': 'pages/report/index applied link',
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
if (names.length !== 80) fail('expected 80 screens, mapped ' + names.length)

var trialCheck = cp.spawnSync(process.execPath, [path.join(__dirname, 'check-miniprogram-trial.js')], { encoding: 'utf8' })
console.log(trialCheck.stdout || '')
if (trialCheck.status !== 0) fail(trialCheck.stderr || 'trial check failed')
var queryCheck = cp.spawnSync(process.execPath, [path.join(__dirname, 'check-miniprogram-query.js')], { encoding: 'utf8' })
console.log(queryCheck.stdout || '')
if (queryCheck.status !== 0) fail(queryCheck.stderr || 'query page check failed')
var liveCheck = cp.spawnSync(process.execPath, [path.join(__dirname, 'check-miniprogram-live.js')], { encoding: 'utf8' })
console.log(liveCheck.stdout || '')
if (liveCheck.status !== 0) fail(liveCheck.stderr || 'hybrid check failed')
;['mock', 'hybrid', 'live'].forEach(function (mode) {
  var calibrationCheck = cp.spawnSync(process.execPath, [path.join(__dirname, 'check-miniprogram-calibrating.js'), mode], { encoding: 'utf8' })
  console.log(calibrationCheck.stdout || '')
  if (calibrationCheck.status !== 0) fail(calibrationCheck.stderr || 'calibration check failed: ' + mode)
})
;['mock', 'hybrid', 'live'].forEach(function (mode) {
  var loginCheck = cp.spawnSync(process.execPath, [path.join(__dirname, 'check-miniprogram-login.js'), mode], { encoding: 'utf8' })
  console.log(loginCheck.stdout || '')
  if (loginCheck.status !== 0) fail(loginCheck.stderr || 'login check failed: ' + mode)
})
var uploadCheck = cp.spawnSync(process.execPath, [path.join(__dirname, 'check-miniprogram-upload.js')], { encoding: 'utf8' })
console.log(uploadCheck.stdout || '')
if (uploadCheck.status !== 0) fail(uploadCheck.stderr || uploadCheck.stdout || 'upload check failed')
var evidenceCheck = cp.spawnSync(process.execPath, [path.join(__dirname, 'check-miniprogram-evidence.js')], { encoding: 'utf8' })
console.log(evidenceCheck.stdout || '')
if (evidenceCheck.status !== 0) fail(evidenceCheck.stderr || evidenceCheck.stdout || 'evidence check failed')
var privacyCheck = cp.spawnSync(process.execPath, [path.join(__dirname, 'check-miniprogram-privacy.js')], { encoding: 'utf8' })
console.log(privacyCheck.stdout || '')
if (privacyCheck.status !== 0) fail(privacyCheck.stderr || privacyCheck.stdout || 'privacy check failed')
if (failed) process.exit(1)
console.log('miniprogram check ok')

}
main().catch(function (error) { console.error(error); process.exitCode = 1 })
