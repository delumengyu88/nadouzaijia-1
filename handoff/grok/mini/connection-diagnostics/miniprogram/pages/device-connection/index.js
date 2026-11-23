var auth = require('../../api/auth')
var log = require('../../api/native-log')
var state = require('../../api/state')
var request = require('../../api/request')
var diag = require('../../api/device-diagnostics')
var expected = require('../../config').expectedAppId || ''

function currentProject() {
  try {
    var id = wx.getAccountInfoSync().miniProgram.appId
    return id ? (id === expected ? '匹配' : '不符') : '未知'
  } catch (_) { return '未知' }
}
function networkType(marker) {
  return new Promise(function (resolve) {
    var timer = setTimeout(function () { resolve('未知') }, 1800)
    function done(value) {
      clearTimeout(timer)
      resolve(['wifi','2g','3g','4g','5g','none','ethernet'].indexOf(value) !== -1 ? value : '未知')
    }
    try { wx.getNetworkType({success:function (r) {done(r.networkType)},fail:function (e) {log.record(marker,'网络类型','fail',e);done('未知')}}) }
    catch (e) { log.record(marker,'网络类型','throw',e);done('未知') }
  })
}
function changed() { return diag.error('changed', '本人资料') }
function readMe(token) {
  if (!token || state.token() !== token) return Promise.reject(changed())
  return request('/api/v1/auth/me', null, {exposeStatus:true}).then(function (res) {
    if (!res || res.statusCode !== 200) throw diag.error('http', '本人资料', res && res.statusCode)
    if (state.token() !== token) throw changed()
    var id = res.data && res.data.id
    if (typeof id !== 'string' || !id || id.length > 36 || /[\s\x00-\x1f]/.test(id) || id !== state.userId()) {
      throw diag.error('identity','本人资料',res.statusCode)
    }
    return id
  })
}
Page({
  data: {line:'点上方按钮开始检查。',busy:false,ready:false},
  onLoad: function (query) {
    this.entry = query && query.entry === 'qr' ? '扫码' : (query && query.entry === 'profile' ? '我的' : '无预设参数')
  },
  onShow: function () { this.refreshLog() },
  refreshLog: function () {
    var result = log.snapshot(), latest = result.latest
    var raw = latest && (typeof latest.errMsg === 'string' ? latest.errMsg : latest.exceptionMessage)
    this.setData({ logPath: result.path, logSaved: result.saved, hasLog: !!result.text, hasRaw: typeof raw === 'string',
      raw: typeof raw === 'string' ? raw : '', rawStage: latest ? latest.stage : '', hasFailure: !!latest })
    return result
  },
  copyRaw: function () {
    this.refreshLog()
    if (this.data.hasRaw) wx.setClipboardData({data:this.data.raw})
  },
  copyLog: function () {
    var result = this.refreshLog()
    if (result.text) wx.setClipboardData({data:result.text})
  },
  shareLog: function () {
    var result = this.refreshLog()
    if (!result.saved || !result.text || typeof wx.shareFileMessage !== 'function') {
      wx.showToast({title:'请点复制完整日志',icon:'none'}); return
    }
    wx.shareFileMessage({ filePath:result.path, fileName:'连接诊断.jsonl', fail:function () {
      wx.showToast({title:'可用复制完整日志',icon:'none'})
    } })
  },
  onHide: function () { this.invalidate() },
  onUnload: function () { this.invalidate() },
  invalidate: function () {
    this.seq = (this.seq || 0) + 1
    this.setData({busy:false,ready:false,line:'可重新检查连接。'})
  },
  check: function () {
    if (this.data.busy) return
    this.invalidate()
    var page = this, seq = this.seq
    var marker = 'nd-' + (Math.floor(Math.random()*2176782336)).toString(36).padStart(6,'0')
    log.begin(marker)
    var app = currentProject(), stage = '项目', healthOK = false, cached = false
    var network = '未知'
    function prefix() { return marker + ' · ' + network + ' · 项目' + app + ' · ' + page.entry + ' · ' }
    function active() { if (seq !== page.seq) throw changed() }
    this.setData({busy:true,line:prefix()+'检查中'})
    return networkType(marker).then(function (value) {
      active()
      network=value
      if (app !== '匹配') throw diag.error(app === '未知' ? 'unknownProject':'project',stage)
      stage = '健康'
      page.setData({line:prefix()+'正在检查健康接口'})
      return request('/api/healthz?probe='+marker, null, {public:true,exposeStatus:true,timeout:12000})
    }).then(function (res) {
      active()
      if (!res || res.statusCode !== 200 || !res.data || res.data.status !== 'ok') throw diag.error('badHealth','健康',res && res.statusCode)
      healthOK = true
      stage = '登录'
      cached = !!state.token()
      page.setData({line:prefix()+'健康 HTTP 200 · '+(cached?'复用会话':'正在识别微信身份')})
      return cached ? state.load().auth.user : auth.login()
    }).then(function (user) {
      active()
      stage='本人资料'
      var token=state.token()
      if (!user || user.id !== state.userId()) throw changed()
      return readMe(token).then(function (id) {
        active()
        if (token !== state.token() || id !== state.userId()) throw changed()
        page.setData({ready:true,busy:false,line:prefix()+'健康 HTTP 200 · '+(cached?'复用会话':'微信身份已识别')+' · 本人资料 HTTP 200'})
      })
    }).catch(function (failure) {
      if (seq === page.seq) page.setData({busy:false,ready:false,line:prefix()+(healthOK?'健康 HTTP 200 · ':'')+diag.summary(stage,failure)})
    }).then(function () { if (seq === page.seq) page.refreshLog() })
  }
})
