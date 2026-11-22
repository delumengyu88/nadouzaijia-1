var api = require('../../api/index')
var nav = require('../../utils/nav')

Page({
  data: { title: '查询结果', showBack: true },
  onLoad: function (query) {
    this.id = query.id || ''
    this.view = query.view || ''
    this.setData(Object.assign(nav.layout(), { title: '查询结果', showBack: true }))
    if (this.view === 'queued' || this.view === 'running' || this.view === 'allfail' || this.view === 'allmiss') {
      this.renderSpecial(this.view)
      return
    }
    this.refresh()
  },
  onShow: function () {
    if (this.view) return
    var self = this
    this.tick()
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
  tick: function () {
    if (!this.id) return
    var current = api.getQuery(this.id)
    if (current && (current.queued || current.running)) api.advanceQuery(this.id)
    this.refresh()
  },
  refresh: function () {
    var current = api.getQuery(this.id)
    if (!current) return
    if (current.done) {
      api.markQuerySeen(this.id)
      this.stop()
    }
    this.setData(current)
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
    if (!e.currentTarget.dataset.hit) return
    var name = e.currentTarget.dataset.name
    wx.navigateTo({ url: '/pages/report/index?name=' + encodeURIComponent(name) + '&id=' + (this.id || '') })
  },
  requery: function (e) {
    api.setDraft(e.currentTarget.dataset.name)
    wx.navigateTo({ url: '/pages/input/index' })
  },
  evidence: function (e) {
    wx.navigateTo({ url: '/pages/evidence/index?name=' + encodeURIComponent(e.currentTarget.dataset.name) + '&kind=normal&platform=' + encodeURIComponent(e.currentTarget.dataset.platform || '') })
  },
  retry: function (e) {
    api.retryItem(this.id, e.currentTarget.dataset.name)
    this.refresh()
  },
  retryAll: function () {
    api.retryAll(this.id)
    this.refresh()
  }
})
