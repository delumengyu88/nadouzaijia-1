var api = require('../../api/index')
var nav = require('../../utils/nav')
var state = require('../../api/state')
var SheetReader = require('../../utils/sheet')

var FAIL = {
  xlsx: '仅支持 .xlsx 文件',
  empty: '文件为空，请重新选择。',
  size: '文件超过 5MB，请拆分后再上传。',
  col: '未找到「节目名称」列，请确认它在表格前 10 行内',
  rows: '超过 500 行，请删减后再上传。',
  read: '文件读取失败，请重新选择文件',
  limit: '已超过 500 个节目名，仅保留前 500 个。'
}
var RESELECT = { xlsx: 1, col: 1, read: 1 }

Page(require('../../api/splash').page({
  data: { title: '输入片单', showBack: true, phase: 'edit', raw: '', items: [] },
  onLoad: function (query) {
    this.owner = api.accountKind()
    var data = nav.layout('input', this.query)
    data.title = '输入片单'
    data.showBack = true
    data.phase = 'edit'
    data.raw = api.getDraft()
    data.failText = ''
    data.box = ''
    data.fromFile = false
    data.showReselect = false
    this.fromFile = false
    this.setData(data)
    var self = this
    if (require('../../config').mode === 'mock' && query.fail && FAIL[query.fail]) {
      this.showFail(query.fail)
      return
    }
    if (query.file === '1') {
      this.recognizeFile(decodeURIComponent(query.name || ''), Number(query.size || 0), decodeURIComponent(query.path || ''))
      return
    }
    if (query.auto === '1') this.recognizeText()
  },
  onInput: function (e) { this.setData({ raw: e.detail.value }) },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/home/index' })
  },
  showFail: function (code) {
    var key = FAIL[code] ? code : 'read'
    this.setData({
      phase: 'fail',
      fromFile: !!this.fromFile,
      showReselect: !!this.fromFile || !!RESELECT[key],
      raw: '',
      items: [],
      failText: FAIL[key],
      box: ''
    })
  },
  newRecognition: function () {
    var owner = api.accountKind()
    var revision = state.sessionRevision()
    var page = this
    var sequence = this.recognition = (this.recognition || 0) + 1
    return function () {
      if (sequence !== page.recognition) return false
      var now = state.sessionRevision()
      return now === revision || (owner === 'guest' && now === revision + 1 && api.accountKind() !== 'guest')
    }
  },
  recognizeText: function () {
    var current = this.newRecognition()
    var raw = this.data.raw || api.getDraft()
    if (!String(raw).trim()) {
      this.setData({ phase: 'edit', raw: raw })
      return
    }
    this.fromFile = false
    this.setData({ phase: 'recognizing', box: '识别中…', hint: '' })
    var self = this
    nav.later(600, function () { if (current()) self.showParsed(raw) })
  },
  showParsed: function (raw, owners) {
    var self = this
    var parsed = api.parseTitles(raw)
    if (!parsed.items.length) {
      self.showFail('read')
      return
    }
    if (owners) {
      parsed.items.forEach(function (item) { item.owner = owners[item.name] || '' })
    }
    var fromFile = !!self.fromFile
    if (parsed.truncated) {
      self.setData({
        phase: 'limit',
        fromFile: fromFile,
        failText: FAIL.limit,
        raw: parsed.items.map(function (item) { return item.name }).join('\n'),
        count: parsed.items.length,
        items: parsed.items
      })
      return
    }
    var summary = '共 ' + parsed.items.length + ' 条有效节目名'
    if (!parsed.filtered.length) summary += '，无空行。'
    else summary += '，已过滤 ' + parsed.filtered.length + ' 条。'
    if (parsed.merged) summary += '已合并 ' + parsed.merged + ' 个重复。'
    self.setData({ phase: 'preview', fromFile: fromFile, items: parsed.items, summary: summary, raw: raw })
  },
  recognizeFile: function (name, size, path) {
    var current = this.newRecognition()
    this.fromFile = true
    this.setData({ phase: 'recognizing', box: '识别中…', hint: '' })
    var self = this
    var code = SheetReader.precheck(name, size) || (path ? '' : 'read')
    if (code) {
      nav.later(600, function () { if (current()) self.showFail(code) })
      return
    }
    wx.getFileSystemManager().readFile({
      filePath: path,
      success: function (res) {
        if (!current()) return
        var result = SheetReader.read(res.data)
        if (!result.ok) {
          self.showFail(result.code)
          return
        }
        var owners = {}
        result.items.forEach(function (item) { if (!owners[item.name]) owners[item.name] = item.owner })
        self.showParsed(result.items.map(function (item) { return item.name }).join('\n'), owners)
      },
      fail: function () { if (current()) self.showFail('read') }
    })
  },
  start: function () {
    api.setDraft(this.data.raw || '')
    this.recognizeText()
  },
  reset: function () {
    this.fromFile = false
    this.setData({ phase: 'edit', failText: '', fromFile: false, showReselect: false, raw: api.getDraft() })
  },
  remove: function (e) {
    var index = Number(e.currentTarget.dataset.index)
    var items = this.data.items.filter(function (_, i) { return i !== index })
    this.setData({ items: items, summary: '共 ' + items.length + ' 条有效节目名' })
  },
  onShow: function () {
    var current = api.accountKind()
    if (this.owner && this.owner !== 'guest' && this.owner !== current) {
      this.visit = (this.visit || 0) + 1
      this.recognition = (this.recognition || 0) + 1
      this.creating = false
      this.setData({ phase: 'edit', items: [], raw: api.getDraft(), loginError: '', loginLoading: false })
    }
    this.owner = current
  },
  onIdentityReady: function () { this.onShow() },
  onHide: function () {
    this.visit = (this.visit || 0) + 1
    this.creating = false
    this.setData({ loginLoading: false })
  },
  onUnload: function () { this.visit = (this.visit || 0) + 1; this.recognition = (this.recognition || 0) + 1 },
  confirm: function () {
    if (this.owner && this.owner !== 'guest' && this.owner !== api.accountKind()) { this.onShow(); return }
    if (this.creating) return
    if (api.accountKind() === 'guest') {
      var page = this
      var visit = this.visit
      this.creating = true
      this.setData({ loginLoading: true, loginError: '' })
      return api.ensureEntry().then(function (user) {
        if (visit !== page.visit) return
        page.creating = false
        page.setData({ loginLoading: false })
        if (user && user.id && user.id !== require('../../api/state').userId()) return
        if (api.accountKind() !== 'guest') page.confirm()
      }, function (error) {
        if (visit !== page.visit) return
        page.creating = false
        page.setData({ loginLoading: false, loginError: error.sessionChanged ? '' : '连接失败，请重试' })
      })
    }
    var names = (this.data.items || []).map(function (item) { return item.name })
    var created = api.createQuery(names)
    if (!created || typeof created.then !== 'function') {
      this.finishCreate(created)
      return
    }
    this.creating = true
    this.setData({ loginLoading: true })
    var self = this
    var submittedVisit = this.visit
    created.then(function (result) {
      if (submittedVisit !== self.visit) return
      self.creating = false
      self.setData({ loginLoading: false })
      self.finishCreate(result)
    }, function () {
      if (submittedVisit !== self.visit) return
      self.creating = false
      self.setData({ loginLoading: false })
      self.finishCreate({ ok: false, message: '网络异常，请重试' })
    })
  },
  finishCreate: function (created) {
    if (created && created.sessionChanged) return
    if (!created || !created.ok) {
      if (created && (created.statusCode === 401 || created.message === '请先登录')) {
        this.setData({ loginError: '连接已过期，请重新开始查询' })
        return
      }
      wx.showToast({ title: (created && created.message) || '网络异常，请重试', icon: 'none' })
      return
    }
    wx.redirectTo({ url: '/pages/query/index?id=' + created.id })
  },
  upload: function () {
    var self = this
    require('../../api/splash').callNative(wx.chooseMessageFile, {
      count: 1,
      type: 'file',
      extension: ['xlsx'],
      success: function (res) {
        var file = res.tempFiles && res.tempFiles[0]
        if (!file) return
        self.recognizeFile(file.name, file.size, file.path)
      }
    })
  }
}))
