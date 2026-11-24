var api = require('../../api/index')
var nav = require('../../utils/nav')
var state = require('../../api/state')
var calibration = require('../../utils/calibration')
var present = require('../../utils/present').present
var config = require('../../config')
var httpApi = require('../../api/http')
var reportFiles = require('../../utils/report-file')
var summaryUnlock = require('../../api/summary-unlock')

function mockMode() {
  return (config.mode || 'mock') === 'mock'
}

function decode(value) {
  if (!value) return ''
  try { return decodeURIComponent(value) } catch (err) { return value }
}

Page(require('../../api/splash').page({
  data: { title: '', showBack: true, phase: 'loading', downloadLoading: false },
  onLoad: function (query) {
    if (calibration.blockReport(query)) return
    if (api.isLive) query.view = ''
    this.revision = state.sessionRevision()
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
  onShow: function () {
    if (this.query && this.query.example !== '1' && (this.revision !== state.sessionRevision() || this.refreshSummary)) {
      this.refreshSummary = false
      this.releaseExport()
      this.setData({ phase: 'loading', reportParts: [], appliedId: '', showDownload: false, downloadLoading: false, groups: [], records: [] })
      this.load()
    }
    this.refreshApplication()
  },
  onHide: function () { this.refreshSummary = !!this.data.summaryHidden || !!this.data.useTimeline },
  onIdentityReady: function () { this.onShow() },
  onUnload: function () { this.disposed = true; this.releaseExport() },
  releaseExport: function () {
    reportFiles.release(this.exportFolder)
    this.exportFolder = ''
    this.partsFolder = ''
  },
  refreshApplication: function () {
    var name = this.data.name
    var normalize = require('../../utils/text').foldKey
    var self = this
    var revision = state.sessionRevision()
    function apply(rows) {
      if (self.disposed || revision !== state.sessionRevision()) return
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
    if (this.disposed || calibration.blockReport(this.query)) return
    var revision = this.revision = state.sessionRevision()
    var seq = this._searchSeq = (this._searchSeq || 0) + 1
    var query = this.query || {}
    var name = decode(query.name)
    if (!name) {
      this.setData({ phase: 'fail' })
      return
    }
    var search = null
    if (query.example === '1' && !mockMode()) search = httpApi.exampleReport(name)
    else if (query.example === '1') search = api.exampleReport(name)
    else if (query.view === 'expired' || query.view === 'locked') search = api.searchProgram(name, { locked: true })
    else search = api.searchProgram(name)
    var self = this
    function current() { return !self.disposed && seq === self._searchSeq && (query.example === '1' || revision === state.sessionRevision()) }
    function apply(result) {
      if (!current()) return
      self.applySearch(result, name, query)
    }
    if (search && typeof search.then === 'function') {
      this.setData({ phase: 'loading' })
      search.then(apply).catch(function (error) {
        if (!current()) return
        self.setData({ phase: 'fail', error: error.message || '加载失败' })
      })
    } else apply(search)
  },
  applySearch: function (search, name, query) {
    if (calibration.blockReport(query)) return
    if (!search) { this.setData({ phase: 'fail', title: name }); return }
    if (search.outcome === 'unverified') {
      this.setData({ phase: 'unverified', name: name, title: name, groups: [], showOpen: false,
        showDownload: false, showLocked: false, showEvidenceLink: false, showEvidence: false, appliedId: '' })
      return
    }
    var publicExample = !!search.publicExample
    if (!publicExample && (query.view === 'empty' || search.outcome === 'miss')) {
      this.setData({ phase: 'empty', title: name, name: name })
      return
    }
    var meta = api.programMeta(name)
    var prof = require('../../config').mode === 'live' ? require('../../api/identity').syncAccess() : api.profile()
    var view = present(search, {
      name: meta.name,
      queried: mockMode() && !publicExample ? '2026-11-20' : '',
      canExport: api.isLive && !search.example ? search.records.some(function (r) { return !r.locked && r.can_download_report }) : prof.tier === 'vip' || prof.tier === 'regular',
      expired: query.view === 'expired'
    })
    view.showEvidenceLink = !search.example
    if (search.example) {
      var facts = require('../../utils/present').exampleFacts(search.records)
      view.exampleEmpty = !facts.hit
      view.showDownload = false
      view.showEvidence = false
      view.evidenceName = ''
      view.evidencePlatforms = []
      view.platformLine = facts.line
      view.header = facts.sub
      view.badge = facts.hit ? '已命中' : '未命中'
      view.badgeCls = facts.hit ? 'ok' : 'miss'
    }
    var timeline = search.example ? null : require('../../utils/bands').timeline(search)
    view.useTimeline = !!timeline
    view.timeline = timeline
    if (timeline) {
      // 新服务端：时间轴替代平铺列表与锁定蒙层；旧服务端没有 bands，保持原布局。
      view.header = timeline.line
      view.showOpen = false
      view.groups = []
      view.showLocked = false
      view.showHint = false
      view.showEvidence = false
      view.showSingleOffer = false
      view.showDownload = timeline.reportReady && !!query.id && timeline.total > 0 && !view.summaryOnlyReport
    }
    view.phase = 'ready'
    view.title = meta.name
    view.showBack = true
    var layout = nav.layout('report', this.query)
    view.statusBarHeight = layout.statusBarHeight
    view.navHeight = layout.navHeight
    this.setData(view)
    if (timeline && timeline.framesReady) this.loadFrames(timeline)
    this.refreshApplication()
    if (view.summaryHidden && view.summaryPlans.length && config.mode === 'live' && !config.liveOneYuanTest) this.loadSummaryPlans(search, name)
    if (config.mode === 'live' && view.showLocked && !require('../../api/live-test-offer').dedicated()) this.loadOffers(name)
  },
  loadSummaryPlans: function (search, name) {
    var page = this, revision = state.sessionRevision(), seq = this._searchSeq
    function current() {
      return !page.disposed && seq === page._searchSeq && revision === state.sessionRevision() && page.data.phase === 'ready' && page.data.name === name
    }
    summaryUnlock.catalog(name).then(function (tiers) {
      if (!current()) return
      var plans = summaryUnlock.options(search.summary_unlocks_by_product, tiers)
      page.setData({ summaryPlans: plans, summaryHelp: plans.some(function (plan) { return !plan.purchasable }) })
    }, function () { if (current()) page.setData({ summaryPlanError: true }) })
  },
  buySummary: function (event) {
    if (!this.data.summaryHidden || this.data.phase !== 'ready' || this.revision !== state.sessionRevision()) return
    var id = event.currentTarget.dataset.id
    var plan = (this.data.summaryPlans || []).find(function (item) { return item.id === id && item.purchasable })
    if (!plan || !summaryUnlock.allowed(id)) return
    wx.navigateTo({ url: '/pages/pay/index?source=summary&product=' + encodeURIComponent(id) + '&name=' + encodeURIComponent(this.data.name) })
  },
  loadOffers: function (name) {
    var page = this
    var revision = state.sessionRevision()
    require('../../api/request')('/api/v1/pay/tiers').then(function (response) {
      if (page.disposed || revision !== state.sessionRevision() || page.data.name !== name || !page.data.showLocked || page.data.phase !== 'ready') return
      var tiers = response && response.tiers
      if (!Array.isArray(tiers)) return
      var member = require('../../utils/member')
      var regular = member.pricedTier(tiers, 'membership_regular')
      var single = member.pricedTier(tiers, 'single_program_30d')
      page.setData({
        showMemberOffer: !!regular, showSingleOffer: !!single,
        memberText: regular ? '开通会员 · ' + member.priceText(regular) : '',
        singleText: single ? '单节目解锁 · ' + member.priceText(single) : ''
      })
    }, function () { /* A missing catalog cannot authorize a purchase. */ })
  },
  loadFrames: function (timeline) {
    var page = this
    var revision = state.sessionRevision()
    var seq = this._searchSeq
    var rows = []
    timeline.bands.forEach(function (band) {
      band.rows.forEach(function (row) { rows.push({ id: row.id, view: row.view }) })
    })
    function alive() {
      return !page.disposed && seq === page._searchSeq && revision === state.sessionRevision() && page.data.useTimeline
    }
    require('../../utils/frame-loader').load(rows, function (id, path) {
      var found = require('../../utils/bands').findRow(page.data.timeline, id)
      if (!found) return
      var update = {}
      update['timeline.bands[' + found.b + '].rows[' + found.r + '].src'] = path
      page.setData(update)
    }, alive)
  },
  timelineRow: function (event) {
    var data = event.currentTarget.dataset
    var bands = (this.data.timeline && this.data.timeline.bands) || []
    var band = bands[Number(data.band)]
    var row = band && band.rows[Number(data.row)]
    return row ? { band: band, row: row, b: Number(data.band), r: Number(data.row) } : null
  },
  toggleRow: function (event) {
    var found = this.timelineRow(event)
    if (!found) return
    var update = {}
    update['timeline.bands[' + found.b + '].rows[' + found.r + '].expanded'] = !found.row.expanded
    this.setData(update)
  },
  openFrame: function (event) {
    var found = this.timelineRow(event)
    if (!found) return
    var row = found.row
    if (row.clear && row.src) {
      this.setData({ zoomSrc: row.src })
      return
    }
    if (row.blur) {
      var page = this
      var cta = (this.data.timeline && this.data.timeline.memberCta) || '开通会员'
      wx.showModal({
        title: '模糊预览',
        content: '图片已在服务端打码，开通会员后可查看清晰截图并放大。',
        cancelText: '知道了',
        confirmText: cta,
        confirmColor: '#1D4E9E',
        success: function (res) { if (res && res.confirm) page.buyTimelineMember() }
      })
    }
  },
  closeZoom: function () { this.setData({ zoomSrc: '' }) },
  unlockBand: function (event) {
    if (this.data.phase !== 'ready' || !this.data.useTimeline) return
    var band = ((this.data.timeline && this.data.timeline.bands) || [])[Number(event.currentTarget.dataset.index)]
    var unlock = require('../../api/band-unlock')
    if (!band || !band.locked || !unlock.known(band.product)) return
    if (require('../../utils/trial').blockPayment()) return
    if (!band.purchasable || !unlock.allowed(band.product)) {
      wx.showModal({ title: '暂未开放在线购买', content: '本节目这一时间段暂不能在线解锁，可联系客服开通。', showCancel: false, confirmText: '知道了' })
      return
    }
    wx.navigateTo({ url: '/pages/pay/index?source=unlock&product=' + encodeURIComponent(band.product) + '&name=' + encodeURIComponent(this.data.name) })
  },
  buyTimelineMember: function () {
    if (this.data.phase !== 'ready' || !this.data.useTimeline) return
    wx.switchTab({ url: '/pages/member/index' })
  },
  imageFailed: function (event) {
    var data = event.currentTarget.dataset
    var groups = this.data.groups || []
    var changed = false
    groups.forEach(function (group) {
      group.rows.forEach(function (row) {
        if (row.id !== data.id || row.image !== data.src) return
        row.image = ''
        row.plainThumb = true
        changed = true
      })
    })
    if (changed) this.setData({ groups: groups })
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
    var groups = this.data.groups || []
    if (!groups.some(function (group) { return group.rows.some(function (row) { return row.id === id && !row.summaryOnly }) })) return
    var example = this.data.example ? '1' : '0'
    wx.navigateTo({ url: '/pages/viewer/index?id=' + id + '&name=' + encodeURIComponent(this.data.name) + '&example=' + example })
  },
  buyMember: function () {
    if ((config.mode === 'live' && !this.data.showMemberOffer) || this.data.phase === 'unverified') return
    if (require('../../utils/trial').blockPayment()) return
    wx.navigateTo({ url: '/pages/pay/index?product=membership_regular&name=' + encodeURIComponent(this.data.name) })
  },
  buySingle: function () {
    if ((config.mode === 'live' && !this.data.showSingleOffer) || this.data.phase === 'unverified') return
    if (require('../../utils/trial').blockPayment()) return
    wx.navigateTo({ url: '/pages/pay/index?product=single_program_30d&name=' + encodeURIComponent(this.data.name) })
  },
  applyEvidence: function () {
    if (this.data.phase === 'unverified') return
    if (this.data.appliedId) {
      wx.navigateTo({ url: '/pages/evidence-detail/index?id=' + encodeURIComponent(this.data.appliedId) })
      return
    }
    var platforms = this.data.evidencePlatforms || []
    wx.navigateTo({ url: '/pages/evidence/index?kind=normal&name=' + encodeURIComponent(this.data.evidenceName || '') + '&platform=' + encodeURIComponent(platforms.join('、')) })
  },
  evidence: function () {
    if (this.data.phase === 'unverified') return
    wx.navigateTo({ url: '/pages/evidence/index?name=' + encodeURIComponent(this.data.evidenceName || '') + '&kind=recent' })
  },
  download: function () {
    var revision = state.sessionRevision()
    if (this.data.summaryOnlyReport || this.data.phase === 'unverified' || this.data.example || this.data.downloadLoading || (this.revision !== undefined && this.revision !== revision)) return
    this.partsFolder = ''
    this.setData({ downloadLoading: true, reportParts: [] })
    var book = api.exportBook(this.data.name, (this.query && this.query.id) || '')
    var self = this
    if (book && typeof book.then === 'function') {
      book.then(function (result) { if (!self.disposed && revision === state.sessionRevision()) self.saveBook(result) }).catch(function (error) {
        if (self.disposed || revision !== state.sessionRevision()) return
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
    var page = this
    var revision = state.sessionRevision()
    var fs = wx.getFileSystemManager()
    var dir = wx.env.USER_DATA_PATH
    var folder = ''
    function current() { return !page.disposed && revision === state.sessionRevision() }
    function failed() {
      if (!current()) return
      stop()
      wx.showToast({ title: '报告保存失败，请重试', icon: 'none' })
    }
    function written(path) {
      if (!current()) return
      if (!book.parts || !book.parts.length) {
        stop()
        require('../../api/splash').callNative(wx.openDocument, { filePath: path, fileType: 'xlsx', showMenu: true, fail: failed })
        return
      }
      fs.unzip({ zipFilePath: path, targetPath: folder, fail: failed, success: function () {
        fs.unlink({ filePath: path, fail: function () {} })
        if (!current()) return
        page.partsFolder = folder
        page.setData({ downloadLoading: false, reportParts: book.parts })
      } })
    }
    function write(path) {
      if (!current()) return
      fs.writeFile({ filePath: path, data: book.bytes.buffer, fail: failed, success: function () { written(path) } })
    }
    if (config.mode === 'mock') { write(dir + '/' + book.filename); return }
    reportFiles.prepareDirectory(fs, page, function (path) { folder = path; write(folder + '/' + book.filename) }, failed)
  },
  openPart: function (event) {
    var name = event.currentTarget.dataset.name
    if (!this.partsFolder || this.disposed || this.revision !== state.sessionRevision() ||
        (this.data.reportParts || []).indexOf(name) === -1) return
    require('../../api/splash').callNative(wx.openDocument, { filePath: this.partsFolder + '/' + name, fileType: 'xlsx', showMenu: true })
  }
}))
