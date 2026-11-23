function validStatus(value) {
  return Number.isInteger(value) && value >= 100 && value < 600 ? value : 0
}
function error(kind, stage, status) {
  var result = new Error(kind === 'http' && status === 401 ? '请先登录' : '网络异常，请重试')
  result.kind = kind
  result.stage = stage
  result.status = validStatus(status)
  return result
}
function network(input, stage) {
  var text = String(input && (input.errMsg || input.message) || '').toLowerCase()
  var kind = 'network'
  if (/domain list|合法域名|域名校验/.test(text)) kind = 'domain'
  else if (/certificate|ssl|tls|证书/.test(text)) kind = 'tls'
  else if (/dns|resolve host|name resolution|域名解析/.test(text)) kind = 'dns'
  else if (/timeout|timed.out|超时/.test(text)) kind = 'timeout'
  var result = error(kind, stage)
  ;['errno', 'errCode'].forEach(function (key) {
    var raw = input && input[key]
    if ((typeof raw === 'number' || typeof raw === 'string') && /^-?[0-9]{1,10}$/.test(String(raw))) result[key] = Number(raw)
  })
  return result
}
function summary(stage, failure) {
  var e = failure || {}
  var actual = e.stage || stage
  if (e.sessionChanged || e.kind === 'changed') return actual + '：会话已变更'
  var label = {project:'项目身份不符',unknownProject:'无法核对项目身份',badHealth:'健康回包非预期',code:'未取得登录码',
    sessionReply:'登录回包缺少有效会话',identity:'本人资料不匹配',http:'服务返回错误',domain:'域名校验未通过',
    tls:'加密连接失败',dns:'域名解析失败',timeout:'超时',network:'微信请求失败'}
  var tail = actual === '微信取码' || actual === '项目' ? '' : (e.status ? ' HTTP ' + e.status : ' 无 HTTP')
  ;['errno','errCode'].forEach(function (key) { if (Number.isInteger(e[key])) tail += ' ' + key + '=' + e[key] })
  return actual + '：' + (label[e.kind] || '未完成') + tail
}
module.exports = { error:error,network:network,validStatus:validStatus,summary:summary }
