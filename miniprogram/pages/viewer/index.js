var api = require('../../api/index')
var nav = require('../../utils/nav')
var present = require('../../utils/present')

function decode(value) {
  if (!value) return ''
  try { return decodeURIComponent(value) } catch (err) { return value }
}

Page({
  data: { title: '截图', showBack: true },
  onLoad: function (query) {
    var name = decode(query.name)
    var search = query.example === '1' ? api.exampleReport(name) : api.searchProgram(name)
    var self = this
    if (search && typeof search.then === 'function') {
      this.setData(Object.assign(nav.layout(), { missing: true }))
      search.then(function (result) { self.applySearch(result, query) }).catch(function () {
        self.setData({ missing: true })
      })
      return
    }
    this.applySearch(search, query)
  },
  applySearch: function (search, query) {
    var row = null
    if (search) {
      search.records.forEach(function (rec) {
        if (!rec.locked && rec.record_id === query.id) row = rec
      })
    }
    var extra = present.findRow(query.id) || {}
    var prof = api.profile()
    var low = !!(extra.low || (row && row.access === 'lowres') || prof.tier === 'free' || prof.tier === 'guest')
    var canSave = !!(row && row.can_download_screenshot && !low && query.example !== '1')
    var programOnly = prof.tier === 'free' && row && row.access === 'full' && !row.can_download_screenshot
    if (query.view === 'free') { low = true; canSave = false; programOnly = false }
    if (query.view === 'nodl') { low = false; canSave = false; programOnly = true }
    var layout = nav.layout()
    this.setData({
      statusBarHeight: layout.statusBarHeight,
      navHeight: layout.navHeight,
      title: '截图',
      showBack: true,
      platform: (row && row.platform) || extra.platform || '平台未识别',
      category: row ? (row.category || '') : '',
      unknown: !extra.platform,
      date: (extra.date || '') + ' ' + (extra.clock || ''),
      time: extra.time || '',
      low: low,
      canSave: canSave,
      programOnly: programOnly,
      missing: !row && query.view !== 'free' && query.view !== 'nodl'
    })
  },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/home/index' })
  },
  save: function () {
    wx.saveImageToPhotosAlbum({
      filePath: '/assets/frame.png',
      success: function () { wx.showToast({ title: '已保存到相册', icon: 'none' }) },
      fail: function () { wx.showToast({ title: '未能保存，请允许相册权限', icon: 'none' }) }
    })
  },
  upgrade: function () {
    wx.navigateTo({ url: '/pages/pay/index?product=membership_vip' })
  }
})
