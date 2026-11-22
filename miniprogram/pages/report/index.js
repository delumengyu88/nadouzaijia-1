var api = require('../../api/index')
var nav = require('../../utils/nav')
var calibration = require('../../utils/calibration')
var present = require('../../utils/present').present

function decode(value) {
  if (!value) return ''
  try { return decodeURIComponent(value) } catch (err) { return value }
}

Page({
  data: { title: '', showBack: true, phase: 'loading', downloadLoading: false },
  onLoad: function (query) {
    if (calibration.blockReport(query)) return
    if (api.isLive) query.view = ''
    this.query = query || {}
    this.setData(Object.assign(nav.layout('report', this.query), { showBack: true, phase: 'loading', title: decode(query.name) }))
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
    var self = this
    function apply(rows) {
      var found = (rows || []).filter(function (item) { return normalize(item.name) === normalize(name || '') })[0]
      self.setData({ appliedId: found ? found.id : '' })
    }
    var listed = api.listEvidence()
    if (listed && typeof listed.then === 'function') {
      listed.then(apply, function () { apply([]) })
      return
    }
    apply(listed)
  },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/home/index' })
  },
  load: function () {
    if (calibration.blockReport(this.query)) return
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
    var self = this
    var userId = require('../../api/state').userId()
    function apply(result) {
      if (userId !== require('../../api/state').userId()) return
      self.applySearch(result, name, query)
    }
    if (search && typeof search.then === 'function') {
      this.setData({ phase: 'loading' })
      search.then(apply).catch(function (error) {
        self.setData({ phase: 'fail', error: error.message || '加载失败' })
      })
    } else apply(search)
  },
  applySearch: function (search, name, query) {
    if (calibration.blockReport(query)) return
    if (!search) { this.setData({ phase: 'fail', title: name }); return }
    if (query.view === 'empty' || search.outcome === 'miss') {
      this.setData({ phase: 'empty', title: name, name: name })
      return
    }
    var meta = api.programMeta(name)
    var prof = require('../../config').mode === 'live' ? require('../../api/identity').syncAccess() : api.profile()
    var view = present(search, {
      name: meta.name,
      queried: '2026-11-20',
      canExport: api.isLive && !search.example ? search.records.some(function (r) { return !r.locked && r.can_download_report }) : prof.tier === 'vip' || prof.tier === 'regular',
      expired: query.view === 'expired'
    })
    view.phase = 'ready'
    view.title = meta.name
    view.showBack = true
    var layout = nav.layout('report', this.query)
    view.statusBarHeight = layout.statusBarHeight
    view.navHeight = layout.navHeight
    this.setData(view)
    this.refreshApplication()
  },
  retry: function () { this.load() },
  ask: function () {
    wx.showModal({
      title: '其他平台',
      content: '平台名称无法解析或不在对照表中。该记录仍计为命中，但不计入涉及的平台数。',
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
    if (require('../../utils/trial').blockPayment()) return
    wx.navigateTo({ url: '/pages/pay/index?product=membership_regular&name=' + encodeURIComponent(this.data.name) })
  },
  buySingle: function () {
    if (require('../../utils/trial').blockPayment()) return
    wx.navigateTo({ url: '/pages/pay/index?product=single_program_30d&name=' + encodeURIComponent(this.data.name) })
  },
  applyEvidence: function () {
    if (this.data.appliedId) {
      wx.navigateTo({ url: '/pages/evidence-detail/index?id=' + encodeURIComponent(this.data.appliedId) })
      return
    }
    var platforms = this.data.evidencePlatforms || []
    wx.navigateTo({ url: '/pages/evidence/index?kind=normal&name=' + encodeURIComponent(this.data.evidenceName || '') + '&platform=' + encodeURIComponent(platforms.join('、')) })
  },
  evidence: function () {
    wx.navigateTo({ url: '/pages/evidence/index?name=' + encodeURIComponent(this.data.evidenceName || '') + '&kind=recent' })
  },
  download: function () {
    if (this.data.downloadLoading) return
    this.setData({ downloadLoading: true })
    var book = api.exportBook(this.data.name)
    var self = this
    if (book && typeof book.then === 'function') {
      book.then(function (result) { self.saveBook(result) }).catch(function (error) {
        self.setData({ downloadLoading: false })
        if (error && error.message === '网络异常，请重试') {
          wx.showModal({
            title: '下载失败',
            content: '网络异常，请重试',
            cancelText: '取消',
            confirmText: '重试',
            confirmColor: '#1D4E9E',
            success: function (res) {
              if (res && res.confirm) self.download()
            }
          })
          return
        }
        wx.showToast({ title: (error && error.message) || '导出失败', icon: 'none' })
      })
      return
    }
    this.saveBook(book)
  },
  saveBook: function (book) {
    var self = this
    function stop() { self.setData({ downloadLoading: false }) }
    if (calibration.blockReport(this.query)) { stop(); return }
    if (!book.ok) {
      stop()
      wx.showToast({ title: book.message, icon: 'none' })
      return
    }
    var fs = wx.getFileSystemManager()
    var path = wx.env.USER_DATA_PATH + '/' + book.filename
    fs.writeFile({
      filePath: path,
      data: book.bytes.buffer,
      success: function () {
        stop()
        wx.openDocument({ filePath: path, fileType: 'xlsx', showMenu: true })
      },
      fail: function () {
        stop()
        wx.showToast({ title: '已生成汇总', icon: 'none' })
      }
    })
  }
})
