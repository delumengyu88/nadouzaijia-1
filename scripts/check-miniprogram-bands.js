// 结果页分档（方案 B）：服务端 bands 的清洗、时间轴、旧服务端回退、单档解锁入口与确认页、列表页下载报告、首页说明。
var assert = require('assert')
var fs = require('fs')
var path = require('path')
var config = require('../miniprogram/config')
config.mode = 'live'
config.calibrating = false
config.trialMode = false
config.liveOneYuanTest = false
config.apiBase = 'https://api.example.test'

var calls = []
var redirects = []
var modals = []
var toasts = []
var store = {}
var route = function (opt) { opt.success({ statusCode: 200, data: {} }) }
global.getCurrentPages = function () { return [1, 2] }
global.wx = {
  env: { USER_DATA_PATH: 'wxfile://usr' },
  getStorageSync: function (k) { return store[k] },
  setStorageSync: function (k, v) { store[k] = v },
  removeStorageSync: function (k) { delete store[k] },
  getSystemInfoSync: function () { return { statusBarHeight: 20, windowWidth: 375, platform: 'android' } },
  getDeviceInfo: function () { return { platform: 'android' } },
  showToast: function (opt) { toasts.push(opt) },
  showModal: function (opt) { modals.push(opt) },
  navigateTo: function (opt) { redirects.push(opt.url) },
  redirectTo: function (opt) { redirects.push(opt.url) },
  switchTab: function (opt) { redirects.push(opt.url) },
  navigateBack: function () {},
  login: function (opt) { if (opt && opt.success) opt.success({ code: 'bands-code' }) },
  request: function (opt) { calls.push(opt); route(opt) },
  getFileSystemManager: function () {
    return {
      mkdir: function (o) { (o.success || o.complete || function () {})() ; if (o.complete && o.success) o.complete() },
      writeFile: function (o) { o.success && o.success() },
      readdir: function (o) { o.success({ files: [] }) },
      rmdir: function (o) { o.complete && o.complete() },
      unlink: function () {},
      unzip: function (o) { o.success && o.success() }
    }
  }
}
function drain() { return new Promise(function (resolve) { setImmediate(resolve) }) }
global.setInterval = function () { return 1 }
global.clearInterval = function () {}

var defs = {}
function loadPage(rel) {
  global.Page = function (def) { defs[rel] = def }
  var file = require.resolve('../miniprogram/' + rel)
  delete require.cache[file]
  require(file)
}
function makePage(rel) {
  var def = defs[rel]
  var page = { data: Object.assign({}, def.data || {}), query: {} }
  Object.keys(def).forEach(function (key) { if (typeof def[key] === 'function') page[key] = def[key] })
  page.setData = function (value) {
    var self = this
    Object.keys(value).forEach(function (key) {
      var match = /^timeline\.bands\[(\d+)\](?:\.rows\[(\d+)\])?\.(\w+)$/.exec(key)
      if (match && match[2] !== undefined) self.data.timeline.bands[Number(match[1])].rows[Number(match[2])][match[3]] = value[key]
      else if (match) self.data.timeline.bands[Number(match[1])][match[3]] = value[key]
      else self.data[key] = value[key]
    })
  }
  return page
}
loadPage('pages/report/index')
loadPage('pages/pay/index')
loadPage('pages/records/index')
loadPage('pages/query/index')

var shape = require('../miniprogram/api/shape')
var bands = require('../miniprogram/utils/bands')
var state = require('../miniprogram/api/state')
var reportFiles = require('../miniprogram/utils/report-file')
state.setSession({ token: 'bands-token', expires_at: new Date(Date.now() + 86400000).toISOString(), user: { id: 'u-free', tier: 'free' } })

function rec(id, bucket, view, extra) {
  return Object.assign({ record_id: id, age_bucket: bucket, locked: false, access: view === 'clear' ? 'full' : 'lowres',
    can_download_report: view === 'clear', platform: '陕西联通', network_type: '专网', frame_count: 29,
    timestamps: ['00:00:05', '00:00:06'], checked_at: '2025-10-19T11:00:00+08:00', frame_view: view }, extra || {})
}
function locked(id, bucket) { return { record_id: id, age_bucket: bucket, locked: true, lock_reason: 'x' } }
function freeBody() {
  return {
    program_name: '夏日友人帐', outcome: 'hit', platform_count: 5,
    platform_groups: [{ platform: '陕西联通', records: [rec('r1', 'ge_180', 'blur'), rec('r2', 'ge_180', 'blur', { platform: '河南移动', network_type: '公网', frame_count: 12 })] }],
    locked_records: [locked('r3', 'd90_179'), locked('r4', 'd90_179'), locked('r5', 'd30_89'), locked('r6', 'lt_30')],
    viewer_tier: 'free', frames_ready: true, report_ready: true,
    bands: [
      { band: 'ge_180', label: '180天以上', count: 2, state: 'open', frame_view: 'blur', unlocked_by: 'free' },
      { band: 'd90_179', label: '90–180天', count: 2, state: 'locked', unlock: { product: 'unlock_band_90_180', amount_fen: 1990, price_text: '¥19.9', purchasable: true } },
      { band: 'd30_89', label: '30–90天', count: 1, state: 'locked', unlock: { product: 'unlock_band_30_90', amount_fen: 3990, price_text: '¥39.9', purchasable: false } },
      { band: 'lt_30', label: '30天内', count: 1, state: 'evidence' }
    ]
  }
}

// 1. 清洗：合法 bands 保留并按档位排序；未知档位、重复、非法状态整体丢弃（页面回退原布局）。
var clean = shape.sanitizeSearch(freeBody())
assert.strictEqual(clean.bands.length, 4)
assert.strictEqual(clean.viewer_tier, 'free')
assert.strictEqual(clean.frames_ready, true)
assert.strictEqual(clean.report_ready, true)
assert.strictEqual(clean.records.filter(function (r) { return r.frame_view === 'blur' }).length, 2)
assert.strictEqual(clean.bands[1].unlock.price_text, '¥19.9')
assert.strictEqual(shape.sanitizeBands([{ band: 'd7', state: 'open', count: 1 }]), null)
assert.strictEqual(shape.sanitizeBands([{ band: 'ge_180', state: 'open' }, { band: 'ge_180', state: 'open' }]), null)
assert.strictEqual(shape.sanitizeBands([{ band: 'ge_180', state: 'visible' }]), null)
var forged = shape.sanitizeBands([{ band: 'd90_179', state: 'locked', count: 2, unlock: { product: 'membership_vip', amount_fen: 1 } }])
assert.strictEqual(forged[0].unlock, null, 'only the two band unlock products are accepted')

// 2. 时间轴：表头、档位标题、解锁按钮、委托取证档、模糊/清晰。
var tl = bands.timeline(clean)
assert.strictEqual(tl.line, '共 6 条 · 5 个平台 · 你已看到 2 条')
assert.deepStrictEqual(tl.bands.map(function (b) { return b.title }), ['180天以上 · 2 条', '90–180天 · 2 条', '30–90天 · 1 条', '30天内 · 1 条'])
assert.strictEqual(tl.bands[0].open, true)
assert.strictEqual(tl.bands[0].foldable, true)
assert.strictEqual(tl.bands[0].expanded, false, 'visible bands start collapsed')
assert.strictEqual(tl.bands[0].rows.length, 2)
assert.strictEqual(tl.bands[0].rows[0].expanded, true)
assert.strictEqual(tl.bands[0].rows[1].expanded, false)
assert.strictEqual(tl.bands[1].foldable, false)
assert.strictEqual(tl.bands[2].foldable, false)
assert.strictEqual(tl.bands[3].foldable, false)
assert.strictEqual(tl.bands[3].expanded, false)
assert.strictEqual(tl.bands[0].blur, true)
assert.strictEqual(tl.bands[1].buyText, '¥19.9 解锁')
assert.strictEqual(tl.bands[1].purchasable, true)
assert.strictEqual(tl.bands[2].buyText, '¥39.9 解锁')
assert.strictEqual(tl.bands[2].purchasable, false)
assert.strictEqual(tl.bands[3].evidence, true)
assert.strictEqual(tl.bands[3].rows.length, 0, '30天内 only shows a count')
assert.strictEqual(tl.memberCta, '开通会员')

// 3. 旧服务端（无 bands）：不出时间轴，沿用原平铺列表与锁定蒙层。
var oldBody = freeBody()
delete oldBody.bands; delete oldBody.viewer_tier; delete oldBody.frames_ready; delete oldBody.report_ready
var oldClean = shape.sanitizeSearch(oldBody)
assert.strictEqual(oldClean.bands, null)
assert.strictEqual(bands.timeline(oldClean), null)

async function main() {
  // 4. 详情页：新服务端出时间轴，旧服务端保持原布局。
  var report = makePage('pages/report/index')
  report.query = { name: encodeURIComponent('夏日友人帐'), id: 'q-1' }
  report._searchSeq = 1
  report.revision = state.sessionRevision()
  route = function (opt) {
    if (/\/records\/r[12]\/frame$/.test(opt.url)) {
      assert.strictEqual(opt.responseType, 'arraybuffer')
      assert.strictEqual(opt.header.Authorization, 'Bearer bands-token')
      opt.success({ statusCode: 200, data: new Uint8Array([255, 216, 255, 224]).buffer })
      return
    }
    opt.success({ statusCode: 200, data: [] })
  }
  report.applySearch(clean, '夏日友人帐', { id: 'q-1' })
  await drain(); await drain()
  assert.strictEqual(report.data.useTimeline, true)
  assert.strictEqual(report.data.showLocked, false)
  assert.strictEqual(report.data.showOpen, false)
  assert.strictEqual(report.data.header, '共 6 条 · 5 个平台 · 你已看到 2 条')
  assert.strictEqual(report.data.showDownload, true, 'free users can download the report on new servers')
  assert.strictEqual(report.data.timeline.bands[0].expanded, false)
  assert.strictEqual(report.data.timeline.bands[0].foldable, true)
  var frameCalls = calls.filter(function (opt) { return /\/frame$/.test(opt.url) })
  assert.strictEqual(frameCalls.length, 0, 'frames stay unloaded until a band is expanded')

  // 有权限且有记录的档：点卡片展开才加载该档截图；再点收起；无权限/空档/30天内不可展开。
  report.toggleBand({ currentTarget: { dataset: { index: 1 } } })
  report.toggleBand({ currentTarget: { dataset: { index: 3 } } })
  assert.strictEqual(report.data.timeline.bands[1].expanded, false)
  assert.strictEqual(report.data.timeline.bands[3].expanded, false)
  report.toggleBand({ currentTarget: { dataset: { index: 0 } } })
  await drain(); await drain()
  assert.strictEqual(report.data.timeline.bands[0].expanded, true)
  frameCalls = calls.filter(function (opt) { return /\/frame$/.test(opt.url) })
  assert.strictEqual(frameCalls.length, 2)
  assert(report.data.timeline.bands[0].rows[0].src.indexOf('wxfile://usr/nadou-frames/r1-blur.jpg') === 0)
  report.toggleBand({ currentTarget: { dataset: { index: 0 } } })
  assert.strictEqual(report.data.timeline.bands[0].expanded, false)
  assert.strictEqual(calls.filter(function (opt) { return /\/frame$/.test(opt.url) }).length, 2, 'collapse does not reload frames')
  report.toggleBand({ currentTarget: { dataset: { index: 0 } } })
  await drain(); await drain()
  assert.strictEqual(report.data.timeline.bands[0].expanded, true)
  assert.strictEqual(calls.filter(function (opt) { return /\/frame$/.test(opt.url) }).length, 2, 'already-loaded rows are not fetched again')

  // 模糊图不能放大，只提示开通会员。
  report.openFrame({ currentTarget: { dataset: { band: 0, row: 0 } } })
  assert.strictEqual(report.data.zoomSrc, undefined)
  assert.strictEqual(modals[modals.length - 1].title, '模糊预览')

  // 「开通会员」进入会员中心（tabBar 页，不能带节目参数）。
  redirects = []
  report.buyTimelineMember()
  assert.strictEqual(redirects[0], '/pages/member/index')

  // 单档解锁：可购 → 确认页；未上架 → 提示，不跳转。
  redirects = []
  report.unlockBand({ currentTarget: { dataset: { index: 1 } } })
  assert.strictEqual(redirects[0], '/pages/pay/index?source=unlock&product=unlock_band_90_180&name=' + encodeURIComponent('夏日友人帐'))
  report.unlockBand({ currentTarget: { dataset: { index: 2 } } })
  assert.strictEqual(redirects.length, 1)
  assert.strictEqual(modals[modals.length - 1].title, '暂未开放在线购买')
  report.unlockBand({ currentTarget: { dataset: { index: 3 } } })
  assert.strictEqual(redirects.length, 1, 'the evidence band has no unlock')

  var legacy = makePage('pages/report/index')
  legacy.query = { name: encodeURIComponent('夏日友人帐') }
  legacy._searchSeq = 1
  legacy.applySearch(oldClean, '夏日友人帐', {})
  assert.strictEqual(legacy.data.useTimeline, false)
  assert.strictEqual(legacy.data.showLocked, true)
  assert.strictEqual(legacy.data.showOpen, true)

  // 清晰档：点击放大。
  var vip = freeBody()
  vip.viewer_tier = 'vip'
  vip.platform_groups[0].records = [rec('r1', 'ge_180', 'clear')]
  vip.bands[0] = { band: 'ge_180', label: '180天以上', count: 1, state: 'open', frame_view: 'clear', unlocked_by: 'membership' }
  var clear = makePage('pages/report/index')
  clear.query = { name: encodeURIComponent('夏日友人帐') }
  clear._searchSeq = 1
  clear.revision = state.sessionRevision()
  clear.applySearch(shape.sanitizeSearch(vip), '夏日友人帐', {})
  await drain(); await drain()
  assert.strictEqual(clear.data.showDownload, false, 'no query id → no server report entry')
  assert.strictEqual(clear.data.timeline.bands[0].expanded, false)
  clear.toggleBand({ currentTarget: { dataset: { index: 0 } } })
  await drain(); await drain()
  clear.openFrame({ currentTarget: { dataset: { band: 0, row: 0 } } })
  assert(clear.data.zoomSrc.indexOf('r1-clear.jpg') !== -1)

  // 5. 确认页：读取服务端报价；旧服务端 404 → 不可购买。
  calls = []
  route = function (opt) {
    if (opt.url.indexOf('/api/v1/unlocks/offer') !== -1) {
      opt.success({ statusCode: 200, data: { id: 'unlock_band_90_180', amount_fen: 1990, program_name: '夏日友人帐',
        band_label: '90–180天', count: 12, purchasable: true, permanent: true } })
      return
    }
    opt.success({ statusCode: 200, data: {} })
  }
  var pay = makePage('pages/pay/index')
  pay.onLoad({ source: 'unlock', product: 'unlock_band_90_180', name: encodeURIComponent('夏日友人帐') })
  await drain(); await drain()
  var offerCall = calls.filter(function (opt) { return opt.url.indexOf('/api/v1/unlocks/offer') !== -1 })[0]
  assert(offerCall.url.indexOf('product=unlock_band_90_180') !== -1)
  assert(offerCall.url.indexOf('program_name=' + encodeURIComponent('夏日友人帐')) !== -1)
  assert.strictEqual(pay.data.phase, 'pending')
  assert.strictEqual(pay.data.card, '解锁 90–180天 记录')
  assert.strictEqual(pay.data.priceText, '¥19.9')
  assert.strictEqual(pay.data.blurb, '《夏日友人帐》90–180天 · 12 条记录')
  assert.strictEqual(pay.data.renewLine, '永久有效 · 仅限本节目、本时间段')

  route = function (opt) {
    if (opt.url.indexOf('/api/v1/unlocks/offer') !== -1) { opt.success({ statusCode: 404, data: { detail: 'Not Found' } }); return }
    opt.success({ statusCode: 200, data: {} })
  }
  var oldPay = makePage('pages/pay/index')
  oldPay.onLoad({ source: 'unlock', product: 'unlock_band_30_90', name: encodeURIComponent('夏日友人帐') })
  await drain(); await drain()
  assert.strictEqual(oldPay.data.phase, 'unavailable')
  assert.strictEqual(oldPay.data.resultSub, '当前版本暂不支持单节目分档解锁')
  var badPay = makePage('pages/pay/index')
  badPay.onLoad({ source: 'unlock', product: 'membership_vip', name: encodeURIComponent('夏日友人帐') })
  assert.strictEqual(badPay.data.phase, 'unavailable')

  // 6. 整次查询报告：新服务端 scope=query，旧服务端无 scope 头也接受；单节目头不能冒充整次查询。
  var zipHead = new Uint8Array([80, 75, 3, 4, 0, 0]).buffer
  var xlsxType = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
  assert.strictEqual(reportFiles.scopedFile({ header: { 'Content-Type': xlsxType, 'X-Nadou-Export-Scope': 'query' }, data: zipHead }, 'query').ok, true)
  assert.strictEqual(reportFiles.scopedFile({ header: { 'Content-Type': xlsxType }, data: zipHead }, 'query').ok, true)
  assert.strictEqual(reportFiles.scopedFile({ header: { 'Content-Type': xlsxType, 'X-Nadou-Export-Scope': 'program' }, data: zipHead }, 'query').ok, false)
  assert.strictEqual(reportFiles.scopedFile({ header: { 'Content-Type': xlsxType }, data: zipHead }).ok, false, 'program scope still needs its header')

  // 7. 查询记录：命中行有「下载报告」，复用单节目导出。
  var root = path.join(__dirname, '../miniprogram')
  var recordsWxml = fs.readFileSync(path.join(root, 'pages/records/index.wxml'), 'utf8')
  assert(recordsWxml.indexOf('catchtap="downloadReport"') !== -1)
  assert(recordsWxml.indexOf('下载报告') !== -1)
  var records = makePage('pages/records/index')
  records.session = state.token()
  records.data.rows = [{ id: 'q-1', name: '夏日友人帐', hit: true, key: 'q-1:0:夏日友人帐' }]
  calls = []
  var exportCalled = ''
  route = function (opt) {
    exportCalled = opt.url
    opt.success({ statusCode: 403, data: { detail: 'export requires an active membership' } })
  }
  records.downloadReport({ currentTarget: { dataset: { index: 0 } } })
  await drain(); await drain()
  assert(exportCalled.endsWith('/api/v1/queries/q-1/program-export'))
  assert.strictEqual(toasts[toasts.length - 1].title, '开通会员后可下载报告', 'old servers keep the membership message')
  assert.strictEqual(records.data.downloading, '')

  // 8. 查询结果页：整次查询报告入口。
  var queryWxml = fs.readFileSync(path.join(root, 'pages/query/index.wxml'), 'utf8')
  assert(queryWxml.indexOf('bindtap="downloadReport"') !== -1)
  var query = makePage('pages/query/index')
  query.id = 'q-1'
  query.setData({ showReport: true })
  route = function (opt) {
    exportCalled = opt.url
    opt.success({ statusCode: 200, data: zipHead, header: { 'Content-Type': xlsxType, 'X-Nadou-Export-Scope': 'program' } })
  }
  query.downloadReport()
  await drain(); await drain()
  assert(exportCalled.endsWith('/api/v1/queries/q-1/export'))
  assert.strictEqual(toasts[toasts.length - 1].title, '未收到本次查询的报告文件')
  assert.strictEqual(query.data.reportLoading, false)

  // 9. 结果页页脚：会员也看不到 30 天内明细。
  var reportWxml = fs.readFileSync(path.join(root, 'pages/report/index.wxml'), 'utf8')
  assert(reportWxml.indexOf('一次看全部') === -1, 'footer must not say 一次看全部')
  assert(reportWxml.indexOf('或 <text class="tl-link" bindtap="buyTimelineMember">{{timeline.memberCta}}</text> · 30天以上全部可看') !== -1, 'footer limits membership to 30天以上')
  assert(reportWxml.indexOf('bindtap="toggleBand"') !== -1, 'foldable band header toggles open/closed')
  assert(reportWxml.indexOf('band.expanded && band.open && !band.empty') !== -1, 'records render only after the band is expanded')
  assert(reportWxml.indexOf('catchtap="unlockBand"') !== -1, 'unlock tap does not also toggle the card')
  assert(reportWxml.indexOf('catchtap="evidence"') !== -1, 'evidence tap does not also toggle the card')

  // 10. 首页说明：四个四字标签同一行。
  var homeWxml = fs.readFileSync(path.join(root, 'pages/home/index.wxml'), 'utf8')
  var homeWxss = fs.readFileSync(path.join(root, 'pages/home/index.wxss'), 'utf8')
  ;['全网检索', '分档查看', '会员多看', '下载报告'].forEach(function (text) {
    assert(homeWxml.indexOf('class="guide-tag">' + text) !== -1, 'home guide: ' + text)
    assert(text.length === 4, 'home guide tag is 4 characters: ' + text)
  })
  assert(homeWxss.indexOf('flex-wrap: nowrap') !== -1, 'guide stays on one row')
  assert(/\.guide-tag\s*\{[^}]*white-space: nowrap/.test(homeWxss), 'guide tags do not wrap')
  assert(homeWxml.indexOf('最近查询 · {{recent.name}} · 已可看 {{recent.count}} 条') !== -1)
  assert(homeWxml.indexOf('wx:if="{{recent}}"') !== -1)
  assert(homeWxml.indexOf('查看全部 ›') !== -1)

  // 11. 首页最近查询：只展示可见档，锁定和 30 天内不出现；没有可看记录时整块隐藏。
  function previewRec(id, bucket, extra) {
    return Object.assign({
      record_id: id,
      platform: '陕西联通',
      network_type: '专网',
      frame_count: 29,
      checked_at: '2025-10-19T12:00:00+08:00',
      timestamps: ['00:00:05', '00:00:06'],
      age_bucket: bucket,
      locked: false,
      frame_view: 'blur'
    }, extra || {})
  }
  function openBand(key, count, view) {
    return { band: key, label: bands.LABELS[key], count: count, state: 'open', frame_view: view || 'blur', unlocked_by: 'tier' }
  }
  var previewSearch = {
    program_name: '夏日友人帐',
    outcome: 'hit',
    viewer_tier: 'free',
    frames_ready: false,
    records: [
      previewRec('a', 'ge_180'),
      previewRec('b', 'ge_180', { platform: '河南移动', network_type: '公网' }),
      previewRec('c', 'ge_180', { platform: '广东电信', network_type: '公网' }),
      previewRec('d', 'ge_180', { platform: '第四条不应出现', network_type: '公网' }),
      previewRec('lock', 'd90_179', { locked: true }),
      previewRec('recent', 'lt_30', { locked: true })
    ],
    bands: [
      openBand('ge_180', 4, 'blur'),
      { band: 'd90_179', label: '90–180天', count: 12, state: 'locked', frame_view: '', unlock: { product: 'unlock_band_90_180', amount_fen: 1990, price_text: '¥19.9', purchasable: true } },
      { band: 'd30_89', label: '30–90天', count: 8, state: 'locked', frame_view: '' },
      { band: 'lt_30', label: '30天内', count: 4, state: 'evidence', frame_view: '' }
    ]
  }
  var preview = bands.homePreview(previewSearch, 3)
  assert.strictEqual(preview.name, '夏日友人帐')
  assert.strictEqual(preview.count, 4)
  assert.strictEqual(preview.rows.length, 3)
  assert.strictEqual(preview.rows[0].id, 'a')
  assert.strictEqual(preview.rows[2].id, 'c')
  assert.strictEqual(preview.rows[0].expanded, true)
  assert.strictEqual(preview.blur, true)
  assert.strictEqual(preview.framesReady, false)
  assert(JSON.stringify(preview).indexOf('lock') === -1)
  assert(JSON.stringify(preview).indexOf('lt_30') === -1)
  assert(JSON.stringify(preview).indexOf('第四条') === -1)
  assert.strictEqual(bands.homePreview(Object.assign({}, previewSearch, {
    records: previewSearch.records.filter(function (r) { return r.age_bucket !== 'ge_180' }),
    bands: [
      { band: 'ge_180', label: '180天以上', count: 0, state: 'open', frame_view: 'blur' },
      { band: 'd90_179', label: '90–180天', count: 12, state: 'locked', frame_view: '' },
      { band: 'd30_89', label: '30–90天', count: 8, state: 'locked', frame_view: '' },
      { band: 'lt_30', label: '30天内', count: 4, state: 'evidence', frame_view: '' }
    ]
  })), null, 'empty visible bands hide the section')
  assert.strictEqual(bands.homePreview(null), null)
  var oldBackend = bands.homePreview({
    program_name: '夏日友人帐',
    outcome: 'hit',
    records: [
      previewRec('old-open', 'ge_180'),
      previewRec('old-lock', 'd90_179', { locked: true }),
      previewRec('old-recent', 'lt_30'),
      previewRec('old-unknown', 'unknown'),
      previewRec('old-none', '')
    ]
  }, 3)
  assert.strictEqual(oldBackend.count, 1)
  assert.strictEqual(oldBackend.rows[0].id, 'old-open')
  assert.strictEqual(bands.pickRecentHit([]), null)
  assert.deepStrictEqual(bands.pickRecentHit([
    { id: 'q1', name: '未命中片', badgeCls: 'muted' },
    { id: 'q1', name: '夏日友人帐', badgeCls: 'ok' },
    { id: 'q2', name: '另一部', badgeCls: 'ok' }
  ]), { id: 'q1', name: '夏日友人帐' })
  assert.strictEqual(bands.pickRecentHit([
    { id: 'q1', name: '未命中片', badgeCls: 'muted' },
    { id: 'q1', name: '也未命中', badgeCls: 'muted' }
  ]), null)


  // 12. 不得改动的发布开关。
  var configText = fs.readFileSync(path.join(root, 'config.js'), 'utf8')
  assert(configText.indexOf("mode: 'live'") !== -1)
  assert(configText.indexOf('trialMode: false') !== -1)
  console.log('result bands, unlock confirm, list downloads and home guide ok')
}
main().catch(function (error) { console.error(error); process.exitCode = 1 })
