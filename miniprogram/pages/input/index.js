var api = require('../../api/index')
var nav = require('../../utils/nav')
var SheetReader = require('../../utils/sheet')

var FAIL = {
  xlsx: '仅支持 .xlsx，请另存后再上传。',
  empty: '文件为空，请重新选择。',
  size: '文件超过 5MB，请拆分后再上传。',
  col: '未找到「节目名称」列，请检查表头后重试。',
  rows: '超过 500 行，请删减后再上传。',
  read: '文件读取失败，请重新选择文件。',
  limit: '已超过 500 个节目名，仅保留前 500 个。'
}

Page({
  data: { title: '输入片单', showBack: true, phase: 'edit', raw: '', items: [] },
  onLoad: function (query) {
    var data = nav.layout('input', this.query)
    data.title = '输入片单'
    data.showBack = true
    data.phase = 'edit'
    data.raw = api.getDraft()
    data.failText = ''
    data.box = ''
    data.fromFile = false
    this.fromFile = false
    this.setData(data)
    var self = this
    if (query.fail && FAIL[query.fail]) {
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
    this.setData({
      phase: 'fail',
      fromFile: !!this.fromFile,
      raw: '',
      items: [],
      failText: FAIL[code] || FAIL.read,
      box: ''
    })
  },
  recognizeText: function () {
    var raw = this.data.raw || api.getDraft()
    if (!String(raw).trim()) {
      this.setData({ phase: 'edit', raw: raw })
      return
    }
    this.fromFile = false
    this.setData({ phase: 'recognizing', box: '识别中…', hint: '' })
    var self = this
    nav.later(600, function () { self.showParsed(raw) })
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
    this.fromFile = true
    this.setData({ phase: 'recognizing', box: '识别中…', hint: '' })
    var self = this
    var code = SheetReader.precheck(name, size) || (path ? '' : 'read')
    if (code) {
      nav.later(600, function () { self.showFail(code) })
      return
    }
    wx.getFileSystemManager().readFile({
      filePath: path,
      success: function (res) {
        var result = SheetReader.read(res.data)
        if (!result.ok) {
          self.showFail(result.code)
          return
        }
        var owners = {}
        result.items.forEach(function (item) { if (!owners[item.name]) owners[item.name] = item.owner })
        self.showParsed(result.items.map(function (item) { return item.name }).join('\n'), owners)
      },
      fail: function () { self.showFail('read') }
    })
  },
  start: function () {
    api.setDraft(this.data.raw || '')
    this.recognizeText()
  },
  reset: function () {
    this.fromFile = false
    this.setData({ phase: 'edit', failText: '', fromFile: false, raw: api.getDraft() })
  },
  remove: function (e) {
    var index = Number(e.currentTarget.dataset.index)
    var items = this.data.items.filter(function (_, i) { return i !== index })
    this.setData({ items: items, summary: '共 ' + items.length + ' 条有效节目名' })
  },
  confirm: function () {
    if (api.accountKind() === 'guest') {
      return require('../../utils/login')(this, function () {})
    }
    var names = (this.data.items || []).map(function (item) { return item.name })
    var created = api.createQuery(names)
    if (!created.ok) {
      wx.showToast({ title: created.message || '请先登录', icon: 'none' })
      return
    }
    wx.redirectTo({ url: '/pages/query/index?id=' + created.id })
  },
  upload: function () {
    var self = this
    wx.chooseMessageFile({
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
})
