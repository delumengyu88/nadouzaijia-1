var state = require('./state')
var layout = require('../pages/home/splash-layout').layoutSplash
var owner = null
var hidden = false
var nativeReturn = false
var nativePending = 0
var nativeTapUntil = 0

// Explicit WeChat entry points: recent, search, shared cards, QR codes and trial codes.
// Other foreground transitions (including payment return scenes) keep the current page.
var entryScenes = [1001, 1005, 1006, 1007, 1008, 1010, 1011, 1012, 1013, 1017, 1023, 1027,
  1036, 1037, 1044, 1047, 1048, 1049, 1053, 1059, 1065, 1089, 1090, 1096, 1103, 1104, 1106, 1167, 1187, 1194]

function launch() {
  if (typeof getApp !== 'function') return null
  var app = getApp()
  return app && app.globalData
}
function cold(data) {
  owner = null
  hidden = false
  nativeReturn = false
  nativePending = 0
  nativeTapUntil = 0
  data.splashShownThisLaunch = false
}
function background() {
  hidden = true
  nativeReturn = nativePending > 0 || Date.now() < nativeTapUntil
  if (owner) owner.finishSplash()
}
function foreground(data, options) {
  if (hidden && !nativeReturn && options && entryScenes.indexOf(Number(options.scene)) >= 0) {
    data.splashShownThisLaunch = false
  }
  hidden = false
  nativeReturn = false
  nativeTapUntil = 0
}
function nativeTap() { nativeTapUntil = Date.now() + 1200 }
function callNative(method, options) {
  nativePending += 1
  var done = false
  function release() {
    if (done) return
    done = true
    nativePending = Math.max(0, nativePending - 1)
  }
  var wrapped = Object.assign({}, options)
  ;['success', 'fail', 'complete'].forEach(function (key) {
    wrapped[key] = function (result) {
      release()
      if (options[key]) return options[key].call(this, result)
    }
  })
  try { return method.call(wx, wrapped) }
  catch (error) { release(); throw error }
}
function tabHidden() {
  var data = launch()
  return !!(owner || (data && data.splashShownThisLaunch === false))
}
function repaintTab(page) {
  var tab = page.getTabBar && page.getTabBar()
  if (tab && tab.setData) tab.setData({ posterHidden: tabHidden() })
}
function show(page) {
  var data = launch()
  if (!data || data.splashShownThisLaunch !== false) { repaintTab(page); return }
  if (owner && owner !== page) owner.finishSplash()
  data.splashShownThisLaunch = true
  owner = page
  var sys = {}, menu = null
  try { sys = wx.getSystemInfoSync() || {} } catch (err) {}
  try { menu = wx.getMenuButtonBoundingClientRect() } catch (err) {}
  page.setData(Object.assign(layout(sys, menu), { showSplash: true, posterReady: false, seconds: 5 }))
  repaintTab(page)
  page.posterLoadTimer = setTimeout(function () {
    if (owner === page && !page.data.posterReady) page.finishSplash()
  }, 2000)
}
function finish() {
  if (owner !== this) return
  owner = null
  clearTimeout(this.posterLoadTimer)
  if (this.posterTimer) clearInterval(this.posterTimer)
  this.posterTimer = null
  this.setData({ showSplash: false, posterReady: false })
  repaintTab(this)
}
function loaded() {
  if (owner !== this || this.data.posterReady) return
  var page = this
  clearTimeout(page.posterLoadTimer)
  page.setData({ posterReady: true })
  page.posterTimer = setInterval(function () {
    if (owner !== page) return
    if (page.data.seconds <= 1) page.finishSplash()
    else page.setData({ seconds: page.data.seconds - 1 })
  }, 1000)
}
function page(def) {
  var showPage = def.onShow, hidePage = def.onHide, unloadPage = def.onUnload
  def.holdPosterTouch = function () {}
  def.finishSplash = finish
  def.splashLoaded = loaded
  def.markNativeTap = nativeTap
  def.onShow = function () {
    show(this)
    if (showPage) return showPage.call(this)
  }
  def.onHide = function () {
    if (owner === this) finish.call(this)
    if (hidePage) return hidePage.call(this)
  }
  def.onUnload = function () {
    if (owner === this) finish.call(this)
    if (unloadPage) return unloadPage.call(this)
  }
  return def
}
module.exports = {
  cold: cold, background: background, foreground: foreground, page: page, callNative: callNative, tabHidden: tabHidden,
  seen: function () { return !!state.load().splashSeen },
  markSeen: function () { state.load().splashSeen = true; state.save() }
}
