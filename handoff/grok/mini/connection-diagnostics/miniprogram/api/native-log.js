var filename = 'nadou-connection-errors-v1.jsonl'
var pending = []
var run = ''

function path() { return wx.env.USER_DATA_PATH + '/' + filename }
function readFile() {
  var fs = wx.getFileSystemManager()
  if (fs.readdirSync(wx.env.USER_DATA_PATH).indexOf(filename) < 0) return ''
  return fs.readFileSync(path(), 'utf8')
}
function flush() {
  if (!pending.length) return true
  try {
    var fs = wx.getFileSystemManager()
    var lines = pending.map(function (row) { return JSON.stringify(row) + '\n' }).join('')
    if (fs.readdirSync(wx.env.USER_DATA_PATH).indexOf(filename) < 0) fs.writeFileSync(path(), lines, 'utf8')
    else fs.appendFileSync(path(), lines, 'utf8')
    pending = []
    return true
  } catch (_) { return false }
}
function record(marker, stage, callback, result, http) {
  var input = result || {}
  var row = { at: new Date().toISOString(), run: marker, stage: stage, callback: callback,
    errMsg: typeof input.errMsg === 'string' ? input.errMsg : null }
  if (Number.isInteger(http)) row.http = http
  if (callback === 'throw' && typeof input.message === 'string') row.exceptionMessage = input.message
  ;['errno','errCode'].forEach(function (key) { if (typeof input[key] === 'number' || typeof input[key] === 'string' && /^-?\d+$/.test(input[key])) row[key] = input[key] })
  pending.push(row)
  flush()
}
function snapshot() {
  var saved = flush(), text = '', readable = true
  try { text = readFile() } catch (_) { readable = false }
  text += pending.map(function (row) { return JSON.stringify(row) + '\n' }).join('')
  var lines = text.split('\n'), latest = null
  for (var i = lines.length - 1; i >= 0; i--) {
    if (!lines[i]) continue
    var row
    try { row = JSON.parse(lines[i]) } catch (_) { continue }
    if (row.callback === 'fail' || row.callback === 'throw' || row.http >= 400) { latest = row; break }
  }
  return { text: text, latest: latest, saved: saved && readable, path: path() }
}
module.exports = { begin: function (id) { run = id }, current: function () { return run }, record: record, snapshot: snapshot }
