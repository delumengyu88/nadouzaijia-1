// 命中画面只经 GET /api/v1/records/{id}/frame 读取：服务端按权益决定返回清晰图还是已打码的模糊图，
// 客户端不能选择清晰度。走 wx.request（与其他接口同一合法域名），写入小程序自己的目录后显示。
var config = require('../config')
var state = require('../api/state')

var PARALLEL = 3
var LIMIT = 30

function folder() {
  return wx.env.USER_DATA_PATH + '/nadou-frames'
}

function fetchOne(id, view, done) {
  var url = config.apiUrl('/api/v1/records/' + encodeURIComponent(id) + '/frame')
  var token = state.token()
  if (!url || !token) { done(''); return }
  try {
    wx.request({
      url: url,
      method: 'GET',
      timeout: 15000,
      responseType: 'arraybuffer',
      header: { Authorization: 'Bearer ' + token },
      success: function (res) {
        if (state.token() !== token || res.statusCode !== 200 || !(res.data instanceof ArrayBuffer) || res.data.byteLength < 4) { done(''); return }
        var fs = wx.getFileSystemManager()
        var path = folder() + '/' + String(id).replace(/[^A-Za-z0-9_-]/g, '_') + '-' + view + '.jpg'
        fs.mkdir({ dirPath: folder(), recursive: true, complete: function () {
          fs.writeFile({ filePath: path, data: res.data, success: function () { done(path) }, fail: function () { done('') } })
        } })
      },
      fail: function () { done('') }
    })
  } catch (error) { done('') }
}

// rows: [{ id, view }]. onFrame(id, path) runs for each frame that arrived; failures keep the placeholder.
function load(rows, onFrame, alive) {
  if (config.mode !== 'live') return
  var queue = (rows || []).filter(function (row) { return row && row.id && (row.view === 'clear' || row.view === 'blur') }).slice(0, LIMIT)
  var running = 0
  function next() {
    if (alive && !alive()) return
    while (running < PARALLEL && queue.length) {
      var row = queue.shift()
      running += 1
      ;(function (item) {
        fetchOne(item.id, item.view, function (path) {
          running -= 1
          if (path && (!alive || alive())) onFrame(item.id, path)
          next()
        })
      })(row)
    }
  }
  next()
}

module.exports = { load: load }
