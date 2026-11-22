var api = require('../../api/index')
var nav = require('../../utils/nav')
var present = require('../../utils/present').present

function decode(value) {
  if (!value) return ''
  try { return decodeURIComponent(value) } catch (err) { return value }
}

Page({
  data: { title: '', showBack: true, phase: 'loading' },
  onLoad: function (query) {
    this.query = query || {}
    this.setData(Object.assign(nav.layout(), { showBack: true, phase: 'loading', title: decode(query.name) }))
    if (query.view === 'loading') return
    if (query.view === 'loadfail') {
      this.setData({ phase: 'fail' })
      return
    }
    var self = this
    nav.later(280, function () { self.load() })
  },
  onShow: function () { this.refreshApplication() },
  refreshApplication: function () {
    var name = this.data.name
    var normalize = require('../../utils/text').foldKey
    var found = api.listEvidence().filter(function (item) { return normalize(item.name) === normalize(name || '') })[0]
    this.setData({ appliedId: found ? found.id : '' })
  },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/home/index' })
  },
  load: function () {
    var query = this.query || {}
    var name = decode(query.name)
    if (!name) {
      this.setData({ phase: 'fail' })
      return
    }
    var search = null
    if (query.example === '1') search = api.exampleReport(name)
    else if (query.view === 'expired' || query.view === 'locked') search = api.searchProgram(name, { locked: true })
    else search = api.searchProgram(name)
    if (!search) {
      this.setData({ phase: 'fail', title: name })
      return
    }
    if (query.view === 'empty' || search.outcome === 'miss') {
      this.setData({ phase: 'empty', title: name, name: name })
      return
    }
    var meta = api.programMeta(name)
    var prof = api.profile()
    var view = present(search, {
      name: meta.name,
      queried: '2026-11-20',
      canExport: prof.tier === 'vip' || prof.tier === 'regular',
      expired: query.view === 'expired'
    })
    view.phase = 'ready'
    view.title = meta.name
    view.showBack = true
    var layout = nav.layout()
    view.statusBarHeight = layout.statusBarHeight
    view.navHeight = layout.navHeight
    this.setData(view)
    this.refreshApplication()
  },
  retry: function () { this.load() },
  ask: function () {
    wx.showModal({
      title: '平台未识别',
      content: '无法解析或不在对照表中。该记录仍计为命中，但不计入涉及的平台数。',
      showCancel: false,
      confirmText: '知道了'
    })
  },
  openShot: function (e) {
    var id = e.currentTarget.dataset.id
    var example = this.data.example ? '1' : '0'
    wx.navigateTo({ url: '/pages/viewer/index?id=' + id + '&name=' + encodeURIComponent(this.data.name) + '&example=' + example })
  },
  buyMember: function () {
    wx.navigateTo({ url: '/pages/pay/index?product=membership_regular&name=' + encodeURIComponent(this.data.name) })
  },
  buySingle: function () {
    wx.navigateTo({ url: '/pages/pay/index?product=single_program_30d&name=' + encodeURIComponent(this.data.name) })
  },
  applyEvidence: function () {
    if (this.data.appliedId) {
      wx.navigateTo({ url: '/pages/evidence-detail/index?id=' + encodeURIComponent(this.data.appliedId) })
      return
    }
    var platforms = (this.data.groups || []).filter(function (group) { return !group.unknown }).map(function (group) { return group.platform })
    wx.navigateTo({ url: '/pages/evidence/index?kind=normal&name=' + encodeURIComponent(this.data.name) + '&platform=' + encodeURIComponent(platforms.join('、')) })
  },
  evidence: function () {
    wx.navigateTo({ url: '/pages/evidence/index?name=' + encodeURIComponent(this.data.name) + '&kind=recent' })
  },
  download: function () {
    var book = api.exportBook(this.data.name)
    if (!book.ok) {
      wx.showToast({ title: book.message, icon: 'none' })
      return
    }
    var fs = wx.getFileSystemManager()
    var path = wx.env.USER_DATA_PATH + '/' + book.filename
    fs.writeFile({
      filePath: path,
      data: book.bytes.buffer,
      success: function () {
        wx.openDocument({ filePath: path, fileType: 'xlsx', showMenu: true })
      },
      fail: function () {
        wx.showToast({ title: '已生成汇总', icon: 'none' })
      }
    })
  }
})
