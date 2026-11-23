var api = require('../../api/index')
var nav = require('../../utils/nav')
var state = require('../../api/state')
var calibration = require('../../utils/calibration')
var present = require('../../utils/present')
var config = require('../../config')
var httpApi = require('../../api/http')
var member = require('../../utils/member')
var beijingTime = require('../../utils/date').beijingTime

function mockMode() {
  return (config.mode || 'mock') === 'mock'
}

function decode(value) {
  if (!value) return ''
  try { return decodeURIComponent(value) } catch (err) { return value }
}

Page(require('../../api/splash').page({
  data: { title: '截图', showBack: true, loading: true, missing: false },
  onLoad: function (query) {
    if (calibration.blockReport(query)) return
    this.query = query
    this.read()
  },
  onShow: function () {
    if (this.query && this.query.example !== '1' && this.revision !== state.sessionRevision()) this.read()
  },
  onIdentityReady: function () { this.onShow() },
  onUnload: function () { this.disposed = true },
  read: function () {
    var query = this.query
    var revision = this.revision = state.sessionRevision()
    var name = decode(query.name)
    var search = query.example === '1' && !mockMode() ? httpApi.exampleReport(name) : (query.example === '1' ? api.exampleReport(name) : api.searchProgram(name))
    var self = this
    if (search && typeof search.then === 'function') {
      this.setData(Object.assign(nav.layout('viewer', this.query), { loading: true, failed: false, missing: false, image: '', canSave: false, downloadEntry: false }))
      function current() { return !self.disposed && (query.example === '1' || revision === state.sessionRevision()) }
      search.then(function (result) { if (current()) self.applySearch(result, query) }).catch(function () {
        if (!current()) return
        self.setData({ loading: false, failed: true, missing: false })
      })
      return
    }
    this.applySearch(search, query)
  },
  applySearch: function (search, query) {
    if (calibration.blockReport(query)) return
    if (search && search.publicExample) {
      var picked = null
      ;(search.records || []).forEach(function (rec) {
        if (rec && rec.locked !== true && rec.record_id === query.id) picked = rec
      })
      var image = picked && (picked.hd_url || picked.thumbnail_url) || ''
      var checked = picked && picked.checked_at
      var layout = nav.layout('viewer', this.query)
      this.setData({
        statusBarHeight: layout.statusBarHeight,
        navHeight: layout.navHeight,
        title: '截图',
        showBack: true,
        platform: (picked && picked.platform) || '其他平台',
        category: picked ? (picked.category || '') : '',
        unknown: !(picked && picked.platform),
        mock: false,
        plainShot: !image,
        image: image,
        date: beijingTime(checked),
        time: picked && picked.timestamps ? picked.timestamps.join('、') : '',
        low: picked && picked.access === 'lowres',
        quality: picked && picked.access === 'full' && picked.hd_url ? '高清' :
          picked && picked.access === 'lowres' ? '低清' : '',
        canSave: false,
        programOnly: false,
        loading: false,
        missing: !picked
      })
      return
    }
    var row = null
    if (search) {
      search.records.forEach(function (rec) {
        if (!rec.locked && rec.record_id === query.id) row = rec
      })
    }
    if (row && row.summary_only) {
      this.setData({ loading: false, missing: true, image: '', canSave: false, downloadEntry: false })
      return
    }
    var real = !mockMode()
    var extra = real ? {} : present.findRow(query.id) || {}
    var prof = require('../../config').mode === 'live' ? require('../../api/identity').syncAccess() : api.profile()
    var low = !!(extra.low || (row && row.access === 'lowres') || (!real && (prof.tier === 'free' || prof.tier === 'guest')))
    var imageUrl = require('../../api/shape').imageUrl
    var image = row && ((!low && imageUrl(row.hd_url)) || imageUrl(row.thumbnail_url)) || ''
    var canSave = !!(row && row.can_download_screenshot && !low && (!real || image) && query.example !== '1')
    var programOnly = row && row.access === 'full' && !row.can_download_screenshot
    if (mockMode() && query.view === 'free') { low = true; canSave = false; programOnly = false }
    if (mockMode() && query.view === 'nodl') { low = false; canSave = false; programOnly = true }
    var offer = member.downloadOffer({
      canSave: canSave,
      low: low,
      programOnly: programOnly,
      tier: prof.tier
    })
    var layout = nav.layout('viewer', this.query)
    this.setData({
      statusBarHeight: layout.statusBarHeight,
      navHeight: layout.navHeight,
      title: '截图',
      showBack: true,
      platform: (row && row.platform) || extra.platform || '其他平台',
      category: row ? (row.category || '') : '',
      unknown: !(row && row.platform) && !extra.platform,
      mock: !real,
      image: real ? image : '',
      plainShot: real && !image,
      date: real ? beijingTime(row && row.checked_at) : (extra.date || '') + ' ' + (extra.clock || ''),
      time: real ? (row && row.timestamps || []).join('、') : extra.time || '',
      low: low,
      quality: low ? '低清' : (!real || (row && imageUrl(row.hd_url)) ? '高清' : ''),
      canSave: canSave,
      downloadEntry: offer.downloadEntry,
      blockedReason: offer.blockedReason,
      programOnly: programOnly,
      loading: false,
      missing: !row && !(mockMode() && (query.view === 'free' || query.view === 'nodl'))
    })
  },
  imageFailed: function (event) {
    if (event.currentTarget.dataset.src !== this.data.image) return
    this.setData({ image: '', plainShot: true, canSave: false, downloadEntry: false })
  },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/home/index' })
  },
  save: function () {
    var page = this
    var revision = this.revision
    if (!this.data.canSave || revision !== state.sessionRevision()) return
    if (!this.data.mock) {
      if (!this.data.image) { wx.showToast({ title: '截图暂不可用', icon: 'none' }); return }
      wx.downloadFile({ url: this.data.image, success: function (res) {
        if (page.disposed || revision !== state.sessionRevision()) return
        if (res.statusCode === 200) require('../../api/splash').callNative(wx.saveImageToPhotosAlbum, { filePath: res.tempFilePath })
        else wx.showToast({ title: '截图下载失败', icon: 'none' })
      }, fail: function () { wx.showToast({ title: '截图下载失败', icon: 'none' }) } })
      return
    }
    require('../../api/splash').callNative(wx.saveImageToPhotosAlbum, {
      filePath: '/assets/frame.png',
      success: function () { wx.showToast({ title: '已保存到相册', icon: 'none' }) },
      fail: function () { wx.showToast({ title: '未能保存，请允许相册权限', icon: 'none' }) }
    })
  },
  upgrade: function () {
    if (require('../../utils/trial').blockPayment()) return
    wx.switchTab({ url: '/pages/member/index' })
  }
}))
