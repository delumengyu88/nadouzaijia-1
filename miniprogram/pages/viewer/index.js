var api = require('../../api/index')
var nav = require('../../utils/nav')
var calibration = require('../../utils/calibration')
var present = require('../../utils/present')
var member = require('../../utils/member')

function decode(value) {
  if (!value) return ''
  try { return decodeURIComponent(value) } catch (err) { return value }
}

Page({
  data: { title: '截图', showBack: true, loading: true, missing: false },
  onLoad: function (query) {
    if (calibration.blockReport(query)) return
    this.query = query
    var name = decode(query.name)
    var search = query.example === '1' ? api.exampleReport(name) : api.searchProgram(name)
    var self = this
    if (search && typeof search.then === 'function') {
      this.setData(Object.assign(nav.layout('viewer', this.query), { loading: true, missing: false }))
      search.then(function (result) { self.applySearch(result, query) }).catch(function () {
        self.setData({ loading: false, missing: true })
      })
      return
    }
    this.applySearch(search, query)
  },
  applySearch: function (search, query) {
    if (calibration.blockReport(query)) return
    var row = null
    if (search) {
      search.records.forEach(function (rec) {
        if (!rec.locked && rec.record_id === query.id) row = rec
      })
    }
    var real = api.isLive && query.example !== '1'
    var extra = real ? {} : present.findRow(query.id) || {}
    var prof = api.profile()
    var low = !!(extra.low || (row && row.access === 'lowres') || (!real && (prof.tier === 'free' || prof.tier === 'guest')))
    var canSave = !!(row && row.can_download_screenshot && !low && query.example !== '1')
    var programOnly = row && row.access === 'full' && !row.can_download_screenshot
    if (query.view === 'free') { low = true; canSave = false; programOnly = false }
    if (query.view === 'nodl') { low = false; canSave = false; programOnly = true }
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
      image: real && row && /^https:\/\//.test(row.hd_url || row.thumbnail_url || '') ? (row.hd_url || row.thumbnail_url) : '',
      date: real ? (row && row.checked_at || '').replace('T', ' ').slice(0, 16) : (extra.date || '') + ' ' + (extra.clock || ''),
      time: real ? (row && row.timestamps || []).join('、') : extra.time || '',
      low: low,
      canSave: canSave,
      downloadEntry: offer.downloadEntry,
      blockedReason: offer.blockedReason,
      programOnly: programOnly,
      loading: false,
      missing: !row && query.view !== 'free' && query.view !== 'nodl'
    })
  },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/home/index' })
  },
  save: function () {
    if (!this.data.canSave) return
    if (!this.data.mock) {
      if (!this.data.image) { wx.showToast({ title: '截图暂不可用', icon: 'none' }); return }
      wx.downloadFile({ url: this.data.image, success: function (res) {
        if (res.statusCode === 200) wx.saveImageToPhotosAlbum({ filePath: res.tempFilePath })
        else wx.showToast({ title: '截图下载失败', icon: 'none' })
      }, fail: function () { wx.showToast({ title: '截图下载失败', icon: 'none' }) } })
      return
    }
    wx.saveImageToPhotosAlbum({
      filePath: '/assets/frame.png',
      success: function () { wx.showToast({ title: '已保存到相册', icon: 'none' }) },
      fail: function () { wx.showToast({ title: '未能保存，请允许相册权限', icon: 'none' }) }
    })
  },
  upgrade: function () {
    if (require('../../utils/trial').blockPayment()) return
    wx.switchTab({ url: '/pages/member/index' })
  }
})
