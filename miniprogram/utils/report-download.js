// 列表页（查询结果、查询记录）的「下载报告」：取服务端报告文件，保存到小程序目录后用系统预览打开。
// 多分卷（zip）时解压后让用户选择打开哪一卷。详情页沿用自己的下载逻辑。
var state = require('../api/state')
var reportFiles = require('./report-file')
var config = require('../config')

function messageOf(error) {
  if (error && error.statusCode === 403) return '开通会员后可下载报告'
  if (error && error.statusCode === 404) return '记录不存在'
  return (error && error.message) || '导出失败'
}

function openFile(path) {
  require('../api/splash').callNative(wx.openDocument, { filePath: path, fileType: 'xlsx', showMenu: true,
    fail: function () { wx.showToast({ title: '报告打开失败，请重试', icon: 'none' }) } })
}

function choosePart(folder, parts) {
  wx.showActionSheet({
    itemList: parts.slice(0, 6),
    success: function (res) {
      var name = parts[res.tapIndex]
      if (name) openFile(folder + '/' + name)
    }
  })
}

// page: the calling Page; pending: a book or a promise of one ({ ok, bytes, filename, parts, message }).
// done(): always called once, so the page can clear its loading flag.
function run(page, pending, done) {
  var revision = state.sessionRevision()
  var finished = false
  function finish() {
    if (finished) return
    finished = true
    if (done) done()
  }
  function current() { return !page.disposed && revision === state.sessionRevision() }
  function failed(text) {
    finish()
    if (current()) wx.showToast({ title: text || '报告保存失败，请重试', icon: 'none' })
  }
  function save(book) {
    if (!current()) { finish(); return }
    if (!book || !book.ok) { failed((book && book.message) || '导出失败'); return }
    var fs = wx.getFileSystemManager()
    function write(folder) {
      var path = folder + '/' + book.filename
      fs.writeFile({ filePath: path, data: book.bytes.buffer, fail: function () { failed() }, success: function () {
        if (!current()) { finish(); return }
        if (!book.parts || !book.parts.length) { finish(); openFile(path); return }
        fs.unzip({ zipFilePath: path, targetPath: folder, fail: function () { failed() }, success: function () {
          fs.unlink({ filePath: path, fail: function () {} })
          finish()
          if (current()) choosePart(folder, book.parts)
        } })
      } })
    }
    if (config.mode === 'mock') { write(wx.env.USER_DATA_PATH); return }
    reportFiles.prepareDirectory(fs, page, write, function () { failed() })
  }
  if (pending && typeof pending.then === 'function') {
    pending.then(save, function (error) { failed(messageOf(error)) })
    return
  }
  save(pending)
}

module.exports = { run: run, messageOf: messageOf }
