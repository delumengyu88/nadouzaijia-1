// 隐私授权：低版本没有 onNeedPrivacyAuthorization 不能报错；触发后弹窗出现；同意 resolve agree；拒绝 resolve disagree 并提示。
var assert = require('assert')
var fs = require('fs')
var path = require('path')

var toasts = []
var listeners = []
global.wx = {
  getStorageSync: function () { return '' },
  setStorageSync: function () {},
  removeStorageSync: function () {},
  showToast: function (opt) { toasts.push(opt) },
  openPrivacyContract: function () { wx.openedContract = (wx.openedContract || 0) + 1 }
}
var appDef
global.App = function (def) { appDef = def }
var compDef
global.Component = function (def) { compDef = def }
global.Page = function () {}

assert.strictEqual(typeof wx.onNeedPrivacyAuthorization, 'undefined')
require('../miniprogram/app.js')
assert.doesNotThrow(function () { appDef.onLaunch() })

wx.onNeedPrivacyAuthorization = function (cb) { listeners.push(cb) }
appDef.onLaunch()
assert.strictEqual(listeners.length, 1, 'listener registered once')

require('../miniprogram/components/privacy-popup/index.js')

function mount(def) {
  var inst = { data: Object.assign({}, def.data) }
  inst.setData = function (patch) {
    Object.keys(patch || {}).forEach(function (key) { inst.data[key] = patch[key] })
  }
  Object.keys(def.methods || {}).forEach(function (key) {
    inst[key] = def.methods[key].bind(inst)
  })
  def.lifetimes.attached.call(inst)
  return inst
}

var popup = mount(compDef)
assert.strictEqual(popup.data.show, false)

// 1. 触发监听后弹窗显示。
var resolved = []
listeners[0](function (payload) { resolved.push(payload) })
assert.strictEqual(popup.data.show, true, 'popup shows after privacy listener')

// 2. 点同意后 resolve agree，并带上按钮 id。
popup.onAgree({ currentTarget: { id: 'agree-btn' } })
assert.deepStrictEqual(resolved, [{ event: 'agree', buttonId: 'agree-btn' }])
assert.strictEqual(popup.data.show, false)

// 3. 点拒绝后 resolve disagree，并提示需要同意才能选择文件。
listeners[0](function (payload) { resolved.push(payload) })
assert.strictEqual(popup.data.show, true)
popup.onDisagree()
assert.deepStrictEqual(resolved[1], { event: 'disagree' })
assert.strictEqual(popup.data.show, false)
assert.strictEqual(toasts.length, 1)
assert.strictEqual(toasts[0].title, '需要同意隐私指引才能选择文件')
assert.strictEqual(toasts[0].icon, 'none')

// 4. 两个按钮一样大、并排；同意是实心主色并带官方 open-type；拒绝是描边，不是灰色小字。
var root = path.join(__dirname, '../miniprogram')
var wxml = fs.readFileSync(path.join(root, 'components/privacy-popup/index.wxml'), 'utf8')
var wxss = fs.readFileSync(path.join(root, 'components/privacy-popup/index.wxss'), 'utf8')
function rule(css, selector) {
  var escaped = selector.replace(/[.]/g, '\\.')
  var matched = css.match(new RegExp(escaped + '\\s*\\{([^}]*)\\}'))
  assert(matched, 'missing rule ' + selector)
  return matched[1]
}
var actions = rule(wxss, '.privacy-actions')
var button = rule(wxss, '.privacy-btn')
var agree = rule(wxss, '.privacy-agree')
var reject = rule(wxss, '.privacy-reject')
assert(actions.indexOf('display: flex') !== -1, 'buttons sit in a row')
assert(button.indexOf('flex: 1') !== -1, 'buttons share width')
assert(button.indexOf('height: 44px') !== -1)
assert(button.indexOf('min-height: 44px') !== -1)
assert(button.indexOf('font-size: 30rpx') !== -1)
assert(agree.indexOf('background: #1D4E9E') !== -1, 'agree is solid primary')
assert(agree.indexOf('height:') === -1 && agree.indexOf('font-size') === -1, 'agree size comes from the shared button')
assert(reject.indexOf('background: #fff') !== -1)
assert(reject.indexOf('color: #1D4E9E') !== -1)
assert(reject.indexOf('border-color: #1D4E9E') !== -1, 'reject is an outline button')
assert(reject.indexOf('height:') === -1 && reject.indexOf('font-size') === -1, 'reject is not smaller text')
assert(reject.indexOf('#f4f6f8') === -1 && reject.indexOf('#3c4654') === -1 && reject.indexOf('#8b95a3') === -1)
assert(wxml.indexOf('class="privacy-btn privacy-reject"') !== -1)
assert(wxml.indexOf('class="privacy-btn privacy-agree"') !== -1)
assert(wxml.indexOf('open-type="agreePrivacyAuthorization"') !== -1)
assert(wxml.indexOf('bindagreeprivacyauthorization="onAgree"') !== -1)
assert(wxml.indexOf('>拒绝<') !== -1)
assert(wxml.indexOf('>同意<') !== -1)

// 5. 正文里的指引只走 wx.openPrivacyContract；失败时轻提示，弹窗不关，也不跳协议页。
listeners[0](function () {})
assert.strictEqual(popup.data.show, true)
var contractCalls = []
var navigated = []
wx.navigateTo = function (opt) { navigated.push(opt && opt.url) }
wx.redirectTo = function (opt) { navigated.push(opt && opt.url) }
wx.openPrivacyContract = function (opt) { contractCalls.push(opt) }
var toastsBefore = toasts.length
popup.openContract()
assert.strictEqual(contractCalls.length, 1)
assert.strictEqual(typeof contractCalls[0].fail, 'function')
assert.strictEqual(popup.data.show, true)
assert.strictEqual(toasts.length, toastsBefore)
assert.deepStrictEqual(navigated, [])
contractCalls[0].fail({ errMsg: 'openPrivacyContract:fail' })
assert.strictEqual(popup.data.show, true, 'popup stays open when the contract fails')
assert.strictEqual(toasts[toasts.length - 1].title, '暂时无法打开隐私指引，请稍后再试')
assert.strictEqual(toasts[toasts.length - 1].icon, 'none')
assert.deepStrictEqual(navigated, [])
var savedOpen = wx.openPrivacyContract
wx.openPrivacyContract = undefined
popup.openContract()
assert.strictEqual(popup.data.show, true)
assert.strictEqual(toasts[toasts.length - 1].title, '暂时无法打开隐私指引，请稍后再试')
assert.deepStrictEqual(navigated, [])
wx.openPrivacyContract = function () { throw new Error('openPrivacyContract failed') }
popup.openContract()
assert.strictEqual(popup.data.show, true)
assert.strictEqual(toasts[toasts.length - 1].title, '暂时无法打开隐私指引，请稍后再试')
assert.deepStrictEqual(navigated, [])
wx.openPrivacyContract = savedOpen
assert(/class="privacy-desc"[^>]*>[^<]*<text class="privacy-link" bindtap="openContract">《那豆在架隐私保护指引》<\/text>/.test(wxml))
var popupJs = fs.readFileSync(path.join(root, 'components/privacy-popup/index.js'), 'utf8')
assert(popupJs.indexOf('legal-doc') === -1)
assert(popupJs.indexOf('navigateTo') === -1)

assert(wxss.indexOf('min-height: 44px') !== -1)
assert(wxss.indexOf('height: 44px') !== -1)
assert(wxss.indexOf('safe-area-inset-bottom') !== -1)
assert(wxss.indexOf('justify-content: center') !== -1)
assert(wxss.indexOf('align-items: center') !== -1)

var appJson = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'))
assert.strictEqual(appJson.__usePrivacyCheck__, true)

function versionAtLeast(value, floor) {
  var a = String(value).split('.').map(Number)
  var b = String(floor).split('.').map(Number)
  for (var i = 0; i < 3; i++) {
    var left = a[i] || 0
    var right = b[i] || 0
    if (left > right) return true
    if (left < right) return false
  }
  return true
}
var project = JSON.parse(fs.readFileSync(path.join(root, 'project.config.json'), 'utf8'))
assert(versionAtLeast(project.libVersion, '2.32.3'), 'libVersion ' + project.libVersion)

;['pages/input/index', 'pages/home/index', 'pages/viewer/index'].forEach(function (rel) {
  var json = JSON.parse(fs.readFileSync(path.join(root, rel + '.json'), 'utf8'))
  assert.strictEqual(json.usingComponents['privacy-popup'], '../../components/privacy-popup/index', rel)
  var page = fs.readFileSync(path.join(root, rel + '.wxml'), 'utf8')
  assert(page.indexOf('<privacy-popup') !== -1, rel + ' missing privacy popup')
})

console.log('privacy authorization checks ok')
