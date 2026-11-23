var api = require('../../api/index')
var nav = require('../../utils/nav')
var config = require('../../config')

Page(require('../../api/splash').page({
  data: { title: '查询结果', showBack: true, calibrating: config.calibrating, items: [], head: '' },
  onLoad: function (query) {
    this.id = query.id || ''
    this.view = config.calibrating || api.isLive ? '' : query.view || ''
    this.setData(Object.assign(nav.layout('query', this.query), { title: config.calibrating ? '数据校准中' : '查询结果', showBack: true }))
    if (this.view === 'queued' || this.view === 'running' || this.view === 'allfail' || this.view === 'allmiss') {
      this.renderSpecial(this.view)
      return
    }
    this.refresh()
  },
  onIdentityReady: function () { this.onShow() },
  onShow: function () {
    require('../../utils/login').onShow(this)
    if (this.view) return
    var self = this
    this.stop()
    this.tick()
    if (!this.id || this.finished || this.remote) return
    this.timer = setInterval(function () { self.tick() }, 700)
  },
  onHide: function () { this.stop() },
  onUnload: function () { this.stop() },
  stop: function () {
    if (this.timer) clearInterval(this.timer)
    this.timer = 0
  },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/home/index' })
  },
  home: function () {
    this.stop()
    wx.switchTab({ url: '/pages/home/index' })
  },
  tick: function () {
    if (!this.id || this.remote) return
    var current = api.getQuery(this.id)
    if (current && typeof current.then === 'function') {
      this.remote = true
      this.loadRemote(current)
      return
    }
    if (current && (current.queued || current.running)) api.advanceQuery(this.id)
    this.applyQuery(api.getQuery(this.id), true)
  },
  refresh: function () {
    if (this.view) return
    var current = api.getQuery(this.id)
    if (current && typeof current.then === 'function') {
      this.remote = true
      this.loadRemote(current)
      return
    }
    this.remote = false
    this.applyQuery(current, true)
  },
  loadRemote: function (pending) {
    var self = this
    var token = this.detailToken = (this.detailToken || 0) + 1
    this.setData({ loading: true, missing: false, loadError: false, needLogin: false })
    pending.then(function (detail) {
      if (self.detailToken !== token) return
      self.remote = false
      self.applyQuery(detail, false)
    }, function (error) {
      if (self.detailToken !== token) return
      self.remote = false
      if (error && error.sessionChanged) self.refresh()
      else self.applyQueryError(error)
    })
  },
  applyQuery: function (current, local) {
    this.finished = !local || !current || current.done || current.networkError
    if (this.finished) this.stop()
    if (current && current.done) api.markQuerySeen(this.id)
    if (config.calibrating) {
      this.setData({ calibrating: true, title: '数据校准中', head: '', items: [],
        queued: false, running: false, done: true, percent: 0, networkError: false,
        showFailBanner: false, showRetryAll: false, allFail: false, showNetworkRetry: false,
        missing: false, loadError: false, needLogin: false, loading: false })
      return
    }
    if (!current) {
      this.setData({ calibrating: false, title: '查询结果', missing: false, loadError: false, needLogin: false, loading: false,
        done: true, queued: false, running: false, networkError: false, showNetworkRetry: false,
        items: [], head: '查询记录不可用，请重新查询。', showFailBanner: false })
      this.stop()
      return
    }
    var data = Object.assign({}, current)
    if (!local) {
      data.networkError = false
      data.showNetworkRetry = false
    }
    if (!data.failBanner) data.showFailBanner = false
    data.calibrating = false
    data.title = '查询结果'
    data.missing = false
    data.loadError = false
    data.needLogin = false
    data.loading = false
    this.setData(data)
  },
  applyQueryError: function (error) {
    this.finished = true
    this.stop()
    var code = error && error.statusCode
    var base = {
      calibrating: false, title: '查询结果', loading: false, items: [], head: '',
      queued: false, running: false, done: true, networkError: false, showNetworkRetry: false,
      showFailBanner: false, showRetryAll: false
    }
    if (code === 401 || (error && error.message === '请先登录')) {
      this.setData(Object.assign(base, { needLogin: true, missing: false, loadError: false }))
      return
    }
    if (code === 404) {
      this.setData(Object.assign(base, { missing: true, needLogin: false, loadError: false }))
      return
    }
    if (code === 403) {
      this.setData(Object.assign(base, { loadError: true, failureText: '当前账户无权查看查询记录', missing: false, needLogin: false }))
      return
    }
    if (!code || code >= 500 || (error && error.message === '网络异常，请重试')) {
      this.setData(Object.assign(base, { loadError: true, failureText: '网络异常，请重试', missing: false, needLogin: false }))
      return
    }
    this.setData(Object.assign(base, { loadError: false, missing: false, needLogin: false, head: '查询记录不可用，请重新查询。' }))
  },
  retryLoad: function () {
    this.remote = false
    this.finished = false
    this.refresh()
  },
  login: function () {
    return require('../../utils/login')(this, this.onShow)
  },
  renderSpecial: function (view) {
    if (view === 'queued') {
      this.setData({ queued: true, running: false, done: false, head: '' })
      return
    }
    if (view === 'running') {
      this.setData({ queued: false, running: true, done: false, head: '已完成 2 / 4', percent: 50 })
      return
    }
    if (view === 'allfail') {
      this.setData({
        done: true, allFail: true, showFailBanner: true, showRetryAll: true, itemRetry: false,
        failBanner: '全部查询失败，失败不扣次数。',
        head: '共 2 条：命中 0 · 未命中 0 · 失败 2',
        items: [
          { name: '星河旧梦', badge: '失败', badgeCls: 'bad', line: '索引暂时不可用。', failed: true },
          { name: '南岭追光', badge: '失败', badgeCls: 'bad', line: '索引暂时不可用。', failed: true }
        ]
      })
      return
    }
    this.setData({
      done: true, allFail: false, showFailBanner: false,
      head: '共 2 条，均未命中。',
      items: [
        { name: '星河旧梦', badge: '未命中', badgeCls: 'muted', line: '当前索引未收录该节目名。', missed: true },
        { name: '雾港来信', badge: '未命中', badgeCls: 'muted', line: '当前索引未收录该节目名。', missed: true }
      ]
    })
  },
  openHit: function (e) {
    if (config.calibrating) return
    if (!e.currentTarget.dataset.hit) return
    var name = e.currentTarget.dataset.name
    wx.navigateTo({ url: '/pages/report/index?name=' + encodeURIComponent(name) + '&id=' + (this.id || '') })
  },
  requery: function (e) {
    api.setDraft(e.currentTarget.dataset.name)
    wx.navigateTo({ url: '/pages/input/index' })
  },
  evidence: function (e) {
    if (config.calibrating) return
    var name = api.isLive ? (e.currentTarget.dataset.evidenceName || '') : e.currentTarget.dataset.name
    wx.navigateTo({ url: '/pages/evidence/index?name=' + encodeURIComponent(name) + '&kind=normal&platform=' + encodeURIComponent(e.currentTarget.dataset.platform || '') })
  },
  retry: function (e) {
    api.retryItem(this.id, e.currentTarget.dataset.name)
    this.onShow()
    this.refresh()
  },
  retryAll: function () {
    api.retryAll(this.id)
    this.onShow()
    this.refresh()
  }
}))
