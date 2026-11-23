// Only the scoped export endpoint may supply a downloadable workbook.
var SheetReader = require('./sheet')
var XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

// scope 'program' (default) is the one-program export. scope 'query' is the whole-query export;
// servers before the result-tier release send no scope header there, which is accepted.
function scopedFile(response, scope) {
  var expected = scope === 'query' ? 'query' : 'program'
  var headers = {}
  Object.keys(response.header || {}).forEach(function (key) { headers[key.toLowerCase()] = response.header[key] })
  var type = String(headers['content-type'] || '').split(';')[0].trim().toLowerCase()
  var bytes = response.data instanceof ArrayBuffer ? new Uint8Array(response.data) : null
  var given = headers['x-nadou-export-scope']
  var scoped = given === expected || (expected === 'query' && given === undefined)
  if (!scoped || (type !== XLSX && type !== 'application/zip') ||
      !bytes || bytes.length < 4 || bytes[0] !== 80 || bytes[1] !== 75 || bytes[2] !== 3 || bytes[3] !== 4) {
    return { ok: false, message: expected === 'query' ? '未收到本次查询的报告文件' : '未收到本节目的报告文件' }
  }
  var extension = type === XLSX ? 'xlsx' : 'zip'
  var filename = '汇总.' + extension
  var match = /filename\*=UTF-8''([^;]+)/i.exec(headers['content-disposition'] || '')
  if (match) {
    try {
      var supplied = decodeURIComponent(match[1])
      if (/^汇总(-共导出[0-9]+条)?\.(xlsx|zip)$/.test(supplied) && supplied.endsWith('.' + extension)) filename = supplied
    } catch (error) {}
  }
  var parts = []
  if (extension === 'zip') {
    try {
      parts = Object.keys(SheetReader.unzip(bytes))
      if (!parts.length || !parts.every(function (name) { return /^汇总-[1-9][0-9]*\.xlsx$/.test(name) })) throw new Error()
      parts.sort(function (a, b) { return parseInt(a.slice(3), 10) - parseInt(b.slice(3), 10) })
    } catch (error) { return { ok: false, message: '报告分卷暂不可用' } }
  }
  return { ok: true, bytes: bytes, filename: filename, parts: parts }
}

var active = Object.create(null)
var sequence = 0

function release(folder) {
  if (folder) delete active[folder]
}

// Real report files live under this app-owned directory. A displayed multipart
// report holds its directory until the page releases it or starts a new export.
function prepareDirectory(fs, page, ready, fail) {
  release(page.exportFolder)
  var root = wx.env.USER_DATA_PATH + '/nadou-report-cache'
  var folder = root + '/export-' + Date.now() + '-' + (++sequence)
  page.exportFolder = folder
  active[folder] = true
  function create() {
    if (page.exportFolder !== folder || !active[folder]) return
    fs.mkdir({ dirPath: folder, recursive: true, fail: fail, success: function () { ready(folder) } })
  }
  fs.mkdir({ dirPath: root, recursive: true, fail: fail, success: function () {
    fs.readdir({ dirPath: root, fail: fail, success: function (res) {
      var old = (res.files || []).filter(function (name) {
        return /^export-[0-9]+-[0-9]+$/.test(name) && !active[root + '/' + name]
      })
      if (!old.length) { create(); return }
      var pending = old.length
      old.forEach(function (name) {
        fs.rmdir({ dirPath: root + '/' + name, recursive: true, complete: function () {
          if (--pending === 0) create()
        } })
      })
    } })
  } })
}

module.exports = { scopedFile: scopedFile, prepareDirectory: prepareDirectory, release: release }
