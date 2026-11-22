// 首页公开内容与示例报告：平台数、示例、公告、空记录、空日期和空图。
var assert = require('assert')
var fs = require('fs')
var path = require('path')

var config = require('../miniprogram/config')
config.mode = 'hybrid'
config.calibrating = false
config.trialMode = false
config.apiBase = '/test-api'

var calls = []
var queue = []
var toasts = []
var modals = []
var navigated = []
var storage = {}
global.setTimeout = function () { return 1 }
global.setInterval = function () { return 1 }
global.clearTimeout = function () {}
global.clearInterval = function () {}
global.getCurrentPages = function () { return [{}, {}] }
var appState = {
  globalData: { splashShownThisLaunch: false },
  onLaunch: function () { this.globalData.splashShownThisLaunch = false },
  onShow: function () {}
}
global.getApp = function () { return appState }
global.wx = {
  __instant: true,
  getStorageSync: function (key) { return storage[key] },
  setStorageSync: function (key, value) { storage[key] = value },
  removeStorageSync: function (key) { delete storage[key] },
  getSystemInfoSync: function () { return { statusBarHeight: 44, windowWidth: 375, windowHeight: 812, safeArea: { bottom: 812 } } },
  getMenuButtonBoundingClientRect: function () { return { top: 48, bottom: 80, height: 32, width: 87, left: 281, right: 368 } },
  showToast: function (opt) { toasts.push(opt) },
  showModal: function (opt) { modals.push(opt) },
  navigateTo: function (opt) { navigated.push(opt.url) },
  redirectTo: function () {},
  switchTab: function () {},
  navigateBack: function () {},
  request: function (opt) { calls.push(opt); queue.push(opt) },
  getFileSystemManager: function () { throw new Error('example report must not export') },
  downloadFile: function () { throw new Error('null image must not be requested') }
}
function drain() { return new Promise(function (resolve) { setImmediate(resolve) }) }
function shift() {
  var opt = queue.shift()
  assert(opt, 'expected a request')
  return opt
}
function ok(data) {
  shift().success({ statusCode: 200, data: data })
}
function failReq() { shift().fail() }
function findQueued(part) {
  var idx = -1
  queue.forEach(function (opt, i) {
    if (idx < 0 && String(opt.url).indexOf(part) !== -1) idx = i
  })
  assert(idx >= 0, 'missing ' + part)
  return queue.splice(idx, 1)[0]
}
function fulfill(part, data) {
  findQueued(part).success({ statusCode: 200, data: data })
}
function failPart(part) { findQueued(part).fail() }
function settleHome(data) {
  var hasConfig = queue.some(function (opt) { return String(opt.url).indexOf('/api/v1/app/config') !== -1 })
  if (hasConfig) fulfill('/api/v1/app/config', { splash_enabled: true })
  if (data === null) failPart('/api/v1/app/home')
  else fulfill('/api/v1/app/home', data)
}

var root = path.join(__dirname, '../miniprogram')
var forbidden = ['setClipboardData', 'getClipboardData', 'previewImage', 'chooseImage', 'chooseMedia', 'getLocation', 'getPrivacySetting', 'requirePrivacyAuthorize', 'onNeedPrivacyAuthorization', 'openPrivacyContract']
;['api/http.js', 'pages/home/index.js', 'pages/home/index.wxml', 'pages/report/index.js', 'pages/report/index.wxml', 'pages/viewer/index.js', 'pages/viewer/index.wxml', 'utils/present.js'].forEach(function (rel) {
  var text = fs.readFileSync(path.join(root, rel), 'utf8')
  forbidden.forEach(function (name) {
    assert(text.indexOf(name) === -1, rel + ' must not call ' + name)
  })
})

var homeWxml = fs.readFileSync(path.join(root, 'pages/home/index.wxml'), 'utf8')
var reportWxml = fs.readFileSync(path.join(root, 'pages/report/index.wxml'), 'utf8')
var reportWxss = fs.readFileSync(path.join(root, 'pages/report/index.wxss'), 'utf8')
assert(!homeWxml.includes('已收录平台片单检索，免费'), '首页不再展示片单检索宣传语')
assert(!homeWxml.includes('platformTotal'), '首页不再展示平台总数')
assert(homeWxml.indexOf('wx:if="{{examples.length || examplesError}}"') !== -1)
assert(homeWxml.indexOf('showAnnounce && !showSplash') !== -1)
assert(homeWxml.indexOf('wx:if="{{announcement && showAnnounce && !showSplash && !showPrivacy}}"') !== -1)
  assert(homeWxml.indexOf('showDone && !showAnnounce && !showSplash') !== -1)
  assert(homeWxml.indexOf('thumb-plain') !== -1)
  assert(homeWxml.indexOf('{{item.image}}') !== -1)
  assert(homeWxml.indexOf('announce-dismiss') !== -1)
  assert(homeWxml.indexOf('dateText') === -1)
  assert(reportWxml.indexOf('wx:if="{{showEvidenceLink}}"') !== -1)
  assert(reportWxml.indexOf('{{badgeCls}}') !== -1)
  assert(reportWxml.indexOf('example && platformLine') !== -1)
  assert(reportWxss.indexOf('color: #646A73') !== -1)
  assert(reportWxss.indexOf('.nav-title { top: 0; height: 88rpx; line-height: 88rpx; }') !== -1)
  assert(reportWxss.indexOf('align-items: center') !== -1)
assert(homeWxml.indexOf('<privacy-popup />') !== -1)
assert(reportWxml.indexOf('暂无可展示的示例记录') !== -1)
assert(reportWxml.indexOf('wx:if="{{exampleEmpty}}"') !== -1)
assert(reportWxml.indexOf('wx:if="{{row.ageLabel}}"') !== -1)
assert(reportWxml.indexOf('wx:if="{{row.image}}"') !== -1)
assert(reportWxss.indexOf('.thumb.thumb-plain') !== -1)
assert(!/thumb-plain[^{]*\{[^}]*(width|height)\s*:/.test(reportWxss), 'placeholder keeps the shared thumb size')
assert(fs.readFileSync(path.join(root, 'pages/viewer/index.wxml'), 'utf8').indexOf('<privacy-popup />') !== -1)

var http = require('../miniprogram/api/http')
var state = require('../miniprogram/api/state')
var present = require('../miniprogram/utils/present').present

function homeBody(extra) {
  return Object.assign({ platformTotal: 18, examples: [], announcements: [] }, extra || {})
}

async function main() {
  state.setSession({
    token: 'home-token',
    expires_at: new Date(Date.now() + 86400000).toISOString(),
    user: { id: 'home-user', tier: 'free' }
  })
  var zero = http.homeContent()
  var zeroCall = calls[calls.length - 1]
  assert.strictEqual(zeroCall.url, '/test-api/api/v1/app/home')
  assert.deepStrictEqual(zeroCall.header, {})
  ok({ platformTotal: 0, examples: [{ name: '空卡', line: '涉及 0 个平台', sub: '命中 0 条记录' }], announcements: [] })
  var zeroBody = await zero
  assert.strictEqual(zeroBody.platformTotal, null)
  assert.strictEqual(zeroBody.showPlatformTotal, false)
  assert.strictEqual(zeroBody.examples.length, 1)
  assert.strictEqual(zeroBody.examples[0].dateText, undefined)
  assert.strictEqual(zeroBody.examples[0].sub, '命中 0 条记录')

  var missing = http.homeContent()
  ok({ platformTotal: null, examples: [], announcements: [] })
  var missingBody = await missing
  assert.strictEqual(missingBody.platformTotal, null)
  assert.strictEqual(missingBody.showPlatformTotal, false)
  assert.deepStrictEqual(missingBody.examples, [])
  assert.deepStrictEqual(missingBody.announcements, [])

  var notes = http.homeContent()
  ok(homeBody({
    announcements: [
      { id: 'ann-1', title: '索引更新', body: '本周更新片单。', button_text: '' },
      { id: 'ann-2', title: '第二条', body: '不弹', button_text: null }
    ],
    examples: [
      { name: '海边来信', line: '涉及 2 个平台', sub: '命中 4 条记录 · 2026-08-13' }
    ]
  }))
  var noted = await notes
  assert.strictEqual(noted.platformTotal, 18)
  assert.strictEqual(noted.showPlatformTotal, true)
  assert.deepStrictEqual(noted.examples[0], { name: '海边来信', line: '涉及 2 个平台', sub: '命中 4 条记录 · 2026-08-13' })
  var facts = require('../miniprogram/utils/present').exampleFacts([
    { platform: '广东移动', checked_at: null, thumbnail_url: null, hd_url: null, locked: null },
    { platform: '浙江移动', checked_at: null, thumbnail_url: 'https://thumb.invalid/a.png', hd_url: null, locked: null }
  ])
  assert.strictEqual(facts.line, '涉及 2 个平台')
  assert.strictEqual(facts.sub, '命中 2 条记录')
  assert.strictEqual(facts.hit, true)
  assert.strictEqual(facts.image, 'https://thumb.invalid/a.png')
  assert(facts.sub.indexOf('·') === -1 && facts.sub.indexOf('—') === -1)
  assert.strictEqual(noted.announcements.length, 2)
  assert.strictEqual(noted.announcements[0].buttonText, '知道了')
  assert.strictEqual(noted.announcements[1].buttonText, '知道了')

  var custom = http.homeContent()
  ok(homeBody({ announcements: [{ id: 'ann-btn', title: '有按钮', body: '正文', button_text: '去看看' }] }))
  assert.strictEqual((await custom).announcements[0].buttonText, '去看看')

  var broken = http.homeContent()
  failReq()
  await assert.rejects(broken)

  var blankName = await http.exampleReport('   ')
  assert.strictEqual(blankName, null)
  assert.strictEqual(queue.length, 0)

  var emptyReport = http.exampleReport('空示例')
  var emptyCall = calls[calls.length - 1]
  assert.strictEqual(emptyCall.url, '/test-api/api/v1/app/examples/' + encodeURIComponent('空示例'))
  assert.deepStrictEqual(emptyCall.header, {})
  ok({
    name: '空示例', program_name: '空示例', example: true, outcome: 'miss',
    platform_count: 0, line: '涉及 0 个平台', sub: '命中 0 条记录',
    evidence_platforms: ['广东移动'], evidence_program_name: '空示例', records: []
  })
  var emptyBody = await emptyReport
  assert.deepStrictEqual(emptyBody.records, [])
  assert.strictEqual(emptyBody.publicExample, true)
  assert.strictEqual(emptyBody.evidence_program_name, '')
  assert.deepStrictEqual(emptyBody.evidence_platforms, [])
  assert.strictEqual(emptyBody.outcome, 'miss')

  var nullReport = http.exampleReport('无日期')
  ok({
    name: '无日期', program_name: '无日期', example: true, outcome: 'hit',
    platform_count: 1, line: '涉及 1 个平台', sub: '命中 1 条记录',
    evidence_platforms: ['广东移动'], evidence_program_name: '无日期',
    records: [{
      record_id: 'r-null', platform: '广东移动', platform_subtype: 'OTT', category: '电视剧',
      timestamps: ['00:01:02'], checked_at: null, age_bucket: null,
      thumbnail_url: null, hd_url: null, access: null,
      can_download_report: null, can_download_screenshot: null, locked: null
    }, {
      record_id: 'r-dated', platform: '浙江移动', category: '电视剧',
      timestamps: ['00:02:03'], checked_at: '2026-08-13T11:00:00+08:00', age_bucket: 'd30_89',
      thumbnail_url: null, hd_url: null, access: null,
      can_download_report: null, can_download_screenshot: null, locked: null
    }]
  })
  var nullBody = await nullReport
  assert.strictEqual(nullBody.records[0].checked_at, null)
  assert.strictEqual(nullBody.records[0].age_bucket, null)
  assert.strictEqual(nullBody.records[0].thumbnail_url, null)
  assert.strictEqual(nullBody.records[0].hd_url, null)
  assert.strictEqual(nullBody.records[0].access, null)
  assert.strictEqual(nullBody.records[0].can_download_report, null)
  assert.strictEqual(nullBody.records[0].can_download_screenshot, null)
  assert.strictEqual(nullBody.records[0].locked, null)
  var view = present(nullBody, { name: '无日期' })
  assert.strictEqual(view.groups[0].rows[0].date, '')
  assert.strictEqual(view.groups[0].rows[0].ageLabel, '')
  assert.strictEqual(view.groups[0].rows[0].image, '')
  assert.strictEqual(view.groups[0].rows[0].plainThumb, true)
  assert.strictEqual(view.groups[0].rows[0].badge, '')
  assert.strictEqual(view.groups[0].rows[0].canSave, false)
  assert.strictEqual(view.groups[1].rows[0].date, '2026-08-13')
  assert.strictEqual(view.groups[1].rows[0].ageLabel, '30–89天')
  assert.strictEqual(view.groups[1].rows[0].plainThumb, true)
  assert(!JSON.stringify(view).includes('未知'))
  assert.notStrictEqual(view.groups[0].rows[0].date, new Date().toISOString().slice(0, 10))
  assert.strictEqual(view.showDownload, false)
  assert.deepStrictEqual(view.evidencePlatforms, [])
  assert.strictEqual(view.evidenceName, '')

  state.reset()
  var defs = {}
  global.Page = function (def) { defs.current = def }
  require('../miniprogram/pages/home/index')
  defs.home = defs.current
  require('../miniprogram/pages/report/index')
  defs.report = defs.current
  require('../miniprogram/pages/viewer/index')
  defs.viewer = defs.current
  var api = require('../miniprogram/api')

  function page(def) {
    var inst = { data: Object.assign({}, def.data), getTabBar: function () { return { setData: function () {} } } }
    Object.keys(def).forEach(function (key) {
      if (typeof def[key] === 'function') inst[key] = def[key].bind(inst)
    })
    inst.setData = function (patch) {
      Object.keys(patch || {}).forEach(function (key) { inst.data[key] = patch[key] })
    }
    return inst
  }

  api.reset()
  appState.globalData.splashShownThisLaunch = false
  var before = calls.length
  var home = page(defs.home)
  home.onShow()
  assert.strictEqual(home.data.showPlatformTotal, false)
  assert.deepStrictEqual(home.data.examples, [])
  assert.strictEqual(home.data.announcement, null)
  assert.strictEqual(JSON.stringify(home.data).indexOf('星河旧梦'), -1)
  assert.notStrictEqual(home.data.platformTotal, 26)
  assert.strictEqual(calls.length, before + 2)
  settleHome(homeBody({
    platformTotal: 18,
    examples: [{ name: '海边来信', line: '涉及 0 个平台', sub: '命中 0 条记录' }],
    announcements: []
  }))
  await drain()
  fulfill('/app/examples/' + encodeURIComponent('海边来信'), {
    name: '海边来信', example: true, outcome: 'miss', line: '涉及 0 个平台', sub: '命中 0 条记录',
    records: [
      { record_id: 'a', platform: '广东移动', checked_at: null, thumbnail_url: null, hd_url: null, locked: null },
      { record_id: 'b', platform: '浙江移动', checked_at: null, thumbnail_url: 'https://thumb.invalid/sea.png', hd_url: null, locked: null }
    ]
  })
  await drain()
  assert.strictEqual(home.data.showSplash, true, 'splash still covers the first paint')
  assert.strictEqual(home.data.showPlatformTotal, true)
  assert.strictEqual(home.data.platformTotal, 18)
  assert.strictEqual(home.data.examples[0].name, '海边来信')
  assert.strictEqual(home.data.examples[0].line, '涉及 2 个平台')
  assert.strictEqual(home.data.examples[0].sub, '命中 2 条记录')
  assert.strictEqual(home.data.examples[0].image, 'https://thumb.invalid/sea.png')
  assert(home.data.examples[0].sub.indexOf('—') === -1)
  assert.strictEqual(home.data.announcement, null)
  home.finishSplash()
  assert.strictEqual(home.data.showSplash, false)
  assert.strictEqual(home.data.announcement, null, 'empty announcements render no dialog')
  assert.strictEqual(toasts.length, 0)
  assert.strictEqual(modals.length, 0)

  api.reset()
  appState.globalData.splashShownThisLaunch = false
  var hidden = page(defs.home)
  hidden.onShow()
  settleHome({ platformTotal: 0, examples: [], announcements: [] })
  await drain()
  hidden.finishSplash()
  assert.strictEqual(hidden.data.showPlatformTotal, false)
  assert.notStrictEqual(hidden.data.platformTotal, 0)
  assert.deepStrictEqual(hidden.data.examples, [])
  assert.strictEqual(hidden.data.examplesError, false)

  api.reset()
  appState.globalData.splashShownThisLaunch = false
  var failedHome = page(defs.home)
  failedHome.onShow()
  var toastsBefore = toasts.length
  var modalsBefore = modals.length
  settleHome(null)
  await drain()
  failedHome.finishSplash()
  assert.strictEqual(failedHome.data.showPlatformTotal, false)
  assert.deepStrictEqual(failedHome.data.examples, [])
  assert.strictEqual(failedHome.data.announcement, null)
  assert.strictEqual(failedHome.data.banner, 'guest')
  assert.strictEqual(toasts.length, toastsBefore)
  assert.strictEqual(modals.length, modalsBefore)
  assert.strictEqual(JSON.stringify(failedHome.data).indexOf('星河旧梦'), -1)

  api.reset()
  appState.globalData.splashShownThisLaunch = false
  var announced = page(defs.home)
  announced.onShow()
  assert.strictEqual(announced.data.showSplash, true)
  settleHome(homeBody({
    announcements: [{ id: 'once', title: '索引更新', body: '本周更新片单。', button_text: '去看看' }]
  }))
  await drain()
  assert.strictEqual(announced.data.showSplash, true)
  assert.strictEqual(announced.data.announcement && announced.data.announcement.title, '索引更新')
  announced.finishSplash()
  assert.strictEqual(announced.data.showSplash, false)
  assert.strictEqual(announced.data.showPrivacy, undefined)
  assert.strictEqual(announced.data.announcement.title, '索引更新')
  assert.strictEqual(announced.data.announcement.body, '本周更新片单。')
  assert.strictEqual(announced.data.announcement.buttonText, '去看看')
  assert.strictEqual(announced.data.showAnnounce, true)
  announced.closeAnnounce()
  assert.strictEqual(announced.data.announcement, null)
  announced.onShow()
  settleHome(homeBody({
    announcements: [{ id: 'once', title: '索引更新', body: '本周更新片单。', button_text: '去看看' }]
  }))
  await drain()
  announced.finishSplash()
  assert.strictEqual(announced.data.announcement, null, 'same announcement once per launch')
  announced.onShow()
  settleHome(homeBody({
    announcements: [{ id: 'next', title: '另一条', body: '新公告', button_text: '' }]
  }))
  await drain()
  assert.strictEqual(announced.data.announcement.title, '另一条')
  assert.strictEqual(announced.data.announcement.buttonText, '知道了')

  function openReport(query) {
    var report = page(defs.report)
    report.onLoad(query)
    return report
  }
  var callsBeforeEmpty = calls.length
  var emptyPage = openReport({ example: '1', name: encodeURIComponent('空示例') })
  assert.strictEqual(emptyPage.data.phase, 'loading')
  ok({
    name: '空示例', program_name: '空示例', example: true, outcome: 'miss',
    platform_count: 0, line: '涉及 0 个平台', sub: '命中 0 条记录',
    evidence_platforms: ['广东移动'], evidence_program_name: '空示例', records: []
  })
  await drain()
  assert.strictEqual(emptyPage.data.phase, 'ready')
  assert.notStrictEqual(emptyPage.data.phase, 'fail')
  assert.notStrictEqual(emptyPage.data.phase, 'empty')
  assert.strictEqual(emptyPage.data.exampleEmpty, true)
  assert.strictEqual(emptyPage.data.name, '空示例')
  assert.strictEqual(emptyPage.data.header, '命中 0 条记录')
  assert.strictEqual(emptyPage.data.platformLine, '涉及 0 个平台')
  assert.strictEqual(emptyPage.data.badge, '未命中')
  assert.strictEqual(emptyPage.data.badgeCls, 'miss')
  assert.strictEqual(emptyPage.data.showEvidenceLink, false)
  assert.strictEqual(emptyPage.data.example, true)
  assert.strictEqual(emptyPage.data.showDownload, false)
  assert.strictEqual(emptyPage.data.evidenceName, '')
  assert.deepStrictEqual(emptyPage.data.evidencePlatforms, [])
  assert(!String(emptyPage.data.error || '').includes('加载失败'))
  navigated = []
  emptyPage.applyEvidence()
  assert.strictEqual(navigated[0], '/pages/evidence/index?kind=normal&name=' + encodeURIComponent('') + '&platform=' + encodeURIComponent(''))
  assert(navigated[0].indexOf(encodeURIComponent('空示例')) === -1)
  assert(navigated[0].indexOf(encodeURIComponent('广东移动')) === -1)
  var callsAtDownload = calls.length
  emptyPage.download()
  assert.strictEqual(calls.length, callsAtDownload)
  assert.strictEqual(calls.length, callsBeforeEmpty + 1)

  var nullPage = openReport({ example: '1', name: encodeURIComponent('无日期') })
  ok({
    name: '无日期', program_name: '无日期', example: true, outcome: 'hit',
    platform_count: 1, line: '涉及 1 个平台', sub: '命中 1 条记录',
    records: [{
      record_id: 'r-null', platform: '广东移动', category: '电视剧', timestamps: ['00:01:02'],
      checked_at: null, age_bucket: null, thumbnail_url: null, hd_url: null, access: null,
      can_download_report: null, can_download_screenshot: null, locked: null
    }]
  })
  await drain()
  var row = nullPage.data.groups[0].rows[0]
  assert.strictEqual(row.date, '')
  assert(JSON.stringify(nullPage.data).indexOf('—') === -1)
  assert.strictEqual(row.ageLabel, '')
  assert.strictEqual(row.image, '')
  assert.strictEqual(row.plainThumb, true)
  assert.strictEqual(nullPage.data.header, '命中 1 条记录')
  assert.strictEqual(nullPage.data.platformLine, '涉及 1 个平台')
  assert.strictEqual(nullPage.data.showEvidenceLink, false)
  assert.strictEqual(nullPage.data.showDownload, false)
  assert.strictEqual(nullPage.data.exampleEmpty, false)
  var counted = openReport({ example: '1', name: encodeURIComponent('海边来信') })
  ok({
    name: '海边来信', program_name: '海边来信', example: true, outcome: 'hit',
    platform_count: 9, line: '涉及 2 个平台', sub: '命中 4 条记录 · 2026-08-13',
    records: [
      { record_id: 'a', platform: '广东移动', checked_at: '2026-08-13T11:00:00+08:00', age_bucket: 'd30_89', thumbnail_url: null, hd_url: null, access: null, can_download_report: null, can_download_screenshot: null, locked: null },
      { record_id: 'b', platform: '浙江移动', checked_at: '2026-07-01T11:00:00+08:00', age_bucket: 'd90_179', thumbnail_url: null, hd_url: null, access: null, can_download_report: null, can_download_screenshot: null, locked: null }
    ]
  })
  await drain()
  assert.strictEqual(counted.data.header, '命中 2 条记录 · 2026-08-13')
  assert.strictEqual(counted.data.platformLine, '涉及 2 个平台')
  assert.strictEqual(counted.data.badge, '已命中')
  assert.strictEqual(counted.data.groups.length, 2)
  var undated = openReport({ example: '1', name: encodeURIComponent('无日期示例') })
  ok({
    name: '无日期示例', program_name: '无日期示例', example: true, outcome: 'miss',
    platform_count: 0, line: '涉及 0 个平台', sub: '命中 0 条记录',
    records: [
      { record_id: 'u1', platform: '广东移动', checked_at: null, age_bucket: null, thumbnail_url: null, hd_url: null, locked: null },
      { record_id: 'u2', platform: '浙江移动', checked_at: null, age_bucket: null, thumbnail_url: null, hd_url: null, locked: null }
    ]
  })
  await drain()
  assert.strictEqual(undated.data.badge, '已命中')
  assert.strictEqual(undated.data.platformLine, '涉及 2 个平台')
  assert.strictEqual(undated.data.header, '命中 2 条记录')
  assert.strictEqual(undated.data.exampleEmpty, false)
  assert.strictEqual(undated.data.groups[0].rows.length + undated.data.groups[1].rows.length, 2)
  assert(undated.data.header.indexOf('·') === -1)
  assert(JSON.stringify(undated.data.groups).indexOf('—') === -1)
  assert(!JSON.stringify(nullPage.data).includes('未知'))

  var shot = page(defs.viewer)
  shot.onLoad({ example: '1', name: encodeURIComponent('无日期'), id: 'r-null' })
  ok({
    name: '无日期', program_name: '无日期', example: true, outcome: 'hit', records: [{
      record_id: 'r-null', platform: '广东移动', category: '电视剧', timestamps: ['00:01:02'],
      checked_at: null, age_bucket: null, thumbnail_url: null, hd_url: null, access: null,
      can_download_report: null, can_download_screenshot: null, locked: null
    }]
  })
  await drain()
  assert.strictEqual(shot.data.loading, false)
  assert.strictEqual(shot.data.missing, false)
  assert.strictEqual(shot.data.image, '')
  assert.strictEqual(shot.data.plainShot, true)
  assert.strictEqual(shot.data.canSave, false)
  assert.strictEqual(shot.data.date, '')
  assert.strictEqual(calls.some(function (opt) { return String(opt.url).indexOf('thumbnail') !== -1 }), false)

  var mock = require('../miniprogram/api/mock')
  mock.homeState().examples.forEach(function (card) {
    assert(!/^命中\s*0\s*条/.test(card.sub), card.name)
    var report = mock.exampleReport(card.name)
    assert.strictEqual(report.line, card.line, card.name)
    assert.strictEqual(report.sub, card.sub, card.name)
  })

  console.log('home content and example report checks ok')
}

main().catch(function (error) {
  console.error(error)
  process.exit(1)
})
