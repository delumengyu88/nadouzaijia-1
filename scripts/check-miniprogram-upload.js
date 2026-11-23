// 上传片单：只要有「节目名称」列就放行，「版权方」可有可无；读取失败要说清原因；「重新选择」直接重开文件选择器。
var assert = require('assert')
var zlib = require('zlib')
var XlsxFixture = require('./xlsx-fixture')
var SheetReader = require('../miniprogram/utils/sheet')
var config = require('../miniprogram/config')
config.calibrating = false
config.mode = 'mock'

var files = {}
var picks = []
var pickerOpened = 0
global.getCurrentPages = function () { return [1, 2] }
global.wx = {
  __instant: true,
  getStorageSync: function () {}, setStorageSync: function () {},
  getSystemInfoSync: function () { return { statusBarHeight: 20 } },
  getFileSystemManager: function () {
    return { readFile: function (opt) {
      if (files[opt.filePath]) opt.success({ data: files[opt.filePath] })
      else opt.fail({ errMsg: 'readFile:fail no such file' })
    } }
  },
  chooseMessageFile: function (opt) {
    pickerOpened += 1
    assert.deepStrictEqual(opt.extension, ['xlsx'])
    var next = picks.shift()
    if (next) opt.success({ tempFiles: [next] })
  },
  showToast: function () {}, navigateTo: function () {}, redirectTo: function () {}
}
var def
global.Page = function (p) { def = p }
require('../miniprogram/pages/input/index')
function page() {
  var p = Object.assign({}, def, { data: {}, setData: function (v) { Object.assign(this.data, v) } })
  p.onLoad({})
  return p
}
function put(path, rows) {
  files[path] = XlsxFixture.build(rows)
  return { name: path.split('/').pop(), size: files[path].byteLength, path: path }
}
function load(p, file) { p.recognizeFile(file.name, file.size, file.path) }

// 1. 只有节目名称列：放行。
var p = page()
load(p, put('/tmp/only-name.xlsx', [['节目名称'], ['星河旧梦'], ['长街烟火'], [''], [1917]]))
assert.strictEqual(p.data.phase, 'preview')
// 纯数字和粘贴文字一样按无效行过滤。
assert.deepStrictEqual(p.data.items.map(function (i) { return i.name }), ['星河旧梦', '长街烟火'])
assert.strictEqual(p.data.items[0].owner, '')

// 2. 节目名称 + 版权方：一起读出来；表头前有标题行、列顺序不固定也能找到。
p = page()
load(p, put('/tmp/name-owner.xlsx', [['2026 片单'], [], ['序号', '版权方', ' 节目名称 '], [1, '云豆传媒', '星河旧梦'], [2, '', '南岭追光 & 续']]))
assert.strictEqual(p.data.phase, 'preview')
assert.deepStrictEqual(p.data.items.map(function (i) { return [i.name, i.owner] }), [['星河旧梦', '云豆传媒'], ['南岭追光 & 续', '']])

// 自家命中表一部节目可能占多行，复传时保留首次出现的上传名。
var hitHeaders = ['序号', '平台', '节目类型', '上传节目名', '匹配节目名', '匹配度', '检测日期', '命中画面截图']
var hitRows = [['那豆在架 命中结果'], ['查询编号'], ['查询范围'], ['生成日期'], ['说明'], [],
  hitHeaders, [1, '', '', '长街烟火', '匹配甲'], [2, '', '', '星河旧梦', '匹配乙'],
  [3, '', '', '长街烟火', '匹配丙']]
function importedNames(rows) {
  var result = SheetReader.read(XlsxFixture.build(rows))
  assert.strictEqual(result.ok, true, result.code)
  return result.items.map(function (item) { return item.name })
}
assert.deepStrictEqual(importedNames(hitRows), ['长街烟火', '星河旧梦'])
hitRows[1] = ['名称', '查询说明']
hitRows[6] = hitHeaders.concat('节目名称')
hitRows[7] = hitRows[7].concat(['', '', '', '另一列'])
assert.deepStrictEqual(importedNames(hitRows), ['长街烟火', '星河旧梦'])
p = page()
load(p, put('/tmp/命中结果.xlsx', hitRows))
assert.deepStrictEqual(p.data.items.map(function (i) { return i.name }), ['长街烟火', '星河旧梦'])
;['节目名称', '节目名', '上　传 节目名', '片名', '剧名', '作品名称', '作品名', '名称'].forEach(function (alias) {
  assert.deepStrictEqual(importedNames([[alias], ['星河旧梦']]), ['星河旧梦'])
})
assert.deepStrictEqual(importedNames([['名称', '上传节目名', '节目名称'], ['甲', '乙', '丙']]), ['丙'])
assert.deepStrictEqual(importedNames([['剧名', '上传节目名'], ['甲', '乙']]), ['乙'])
// 标题行里的「名称」不能抢走下面真正的「节目名称」表头。
assert.deepStrictEqual(importedNames([['名称', '2026 片单'], ['序号', '节目名称', '版权方'], [1, '星河旧梦', '云豆传媒']]), ['星河旧梦'])
assert.deepStrictEqual(importedNames([['片单'], ['序号', '片名'], [1, '星河旧梦']]), ['星河旧梦'])

var COL = '没找到节目名那一列，请把节目名所在列的表头改成『节目名称』，或改用粘贴。'
var READ = '文件读取失败，请重新选择文件'
var XLSX = '仅支持 .xlsx 文件'
function expectPrompt(page, text) {
  assert.strictEqual(page.data.phase, 'fail')
  assert.strictEqual(page.data.failText, text)
  assert.strictEqual(page.data.showReselect, true)
  var opened = pickerOpened
  page.upload()
  assert.strictEqual(pickerOpened, opened + 1)
  assert.strictEqual(page.data.phase, 'fail')
  assert.strictEqual(page.data.failText, text)
}

// 匹配节目名不能单独作为片单标题。
p = page()
load(p, put('/tmp/none.xlsx', [['匹配节目名', '出品方'], ['星河旧梦', '云豆传媒']]))
expectPrompt(p, COL)

// 只有版权方没有节目名称，同样提示缺「节目名称」列。
p = page()
load(p, put('/tmp/owner-only.xlsx', [['版权方'], ['云豆传媒']]))
expectPrompt(p, COL)

// 第 10 行的表头能找到；第 11 行的表头找不到，提示与「前 10 行」一致。
function headerOnRow(row) {
  var rows = []
  for (var n = 1; n < row; n++) rows.push(['备注'])
  rows.push(['节目名称'])
  rows.push(['星河旧梦'])
  return rows
}
p = page()
load(p, put('/tmp/row10.xlsx', headerOnRow(10)))
assert.strictEqual(p.data.phase, 'preview')
assert.strictEqual(p.data.items[0].name, '星河旧梦')
p = page()
load(p, put('/tmp/row11.xlsx', headerOnRow(11)))
expectPrompt(p, COL)
var sparse = []
sparse[10] = ['节目名称']
sparse[11] = ['星河旧梦']
assert.strictEqual(SheetReader.read(XlsxFixture.build(sparse)).code, 'col')

// 读取失败的其他原因：不是 zip、文件读不到、超过 500 行、只有表头。
files['/tmp/broken.xlsx'] = new Uint8Array([1, 2, 3, 4]).buffer
p = page(); load(p, { name: 'broken.xlsx', size: 4, path: '/tmp/broken.xlsx' })
expectPrompt(p, READ)
p = page(); load(p, { name: 'gone.xlsx', size: 10, path: '/tmp/gone.xlsx' })
assert.strictEqual(p.data.phase, 'fail')
var many = [['节目名称']]
for (var i = 0; i < 501; i++) many.push(['节目' + i])
p = page(); load(p, put('/tmp/many.xlsx', many))
assert.strictEqual(p.data.failText, '超过 500 行，请删减后再上传。')
p = page(); load(p, put('/tmp/header.xlsx', [['节目名称']]))
assert.strictEqual(p.data.failText, '文件为空，请重新选择。')
p = page(); load(p, { name: 'a.xls', size: 10, path: '/tmp/a.xls' })
expectPrompt(p, XLSX)

// 解压：deflate 各种压缩级别和策略都能还原（Excel/WPS 导出都是 deflate）。
var big = Buffer.from(Array.from({ length: 2000 }, function (_, n) { return '<c r="A' + n + '"><v>节目' + (n % 13) + '</v></c>' }).join(''))
;[0, 1, 6, 9].forEach(function (level) {
  ;[zlib.constants.Z_DEFAULT_STRATEGY, zlib.constants.Z_FIXED, zlib.constants.Z_HUFFMAN_ONLY].forEach(function (strategy) {
    var out = SheetReader.inflateRaw(new Uint8Array(zlib.deflateRawSync(big, { level: level, strategy: strategy })), big.length)
    assert.strictEqual(Buffer.compare(Buffer.from(out), big), 0)
  })
})

// 「重新选择」直接重新打开文件选择器，不跳回输入页；选新文件后正常识别。
p = page()
load(p, put('/tmp/none2.xlsx', [['匹配节目名'], ['星河旧梦']]))
assert.strictEqual(p.data.phase, 'fail')
assert.strictEqual(p.data.fromFile, true)
picks.push(put('/tmp/fixed.xlsx', [['节目名称', '版权方'], ['雾港来信', '云豆传媒']]))
var before = pickerOpened
p.upload()
assert.strictEqual(pickerOpened, before + 1)
assert.strictEqual(p.data.phase, 'preview')
assert.strictEqual(p.data.items[0].name, '雾港来信')
// 取消选择时停在原页，不跳回输入页。
load(p, put('/tmp/none3.xlsx', [['匹配节目名'], ['x']]))
p.upload()
assert.strictEqual(p.data.phase, 'fail')
// 「---」是有效片名，会进预览；预览也必须把 fromFile 写成 false，不能留成 undefined。
p = page()
p.onInput({ detail: { value: '   ' } })
p.showParsed('---')
assert.strictEqual(p.data.phase, 'preview')
assert.strictEqual(p.data.fromFile, false)
// 粘贴没有有效节目名时，用的是读取失败文案，下面同样是「重新选择」。
p.showParsed('123')
assert.strictEqual(p.data.fromFile, false)
expectPrompt(p, READ)

// 页面模板：三种失败提示下的「重新选择」都绑定 upload。
var wxml = require('fs').readFileSync(require('path').join(__dirname, '../miniprogram/pages/input/index.wxml'), 'utf8')
assert(/wx:if="\{\{showReselect\}\}" bindtap="upload">重新选择</.test(wxml))
// 预览页「确认查询」是通栏主按钮（原生 button 默认只有 184px 宽）。
assert(wxml.includes('class="btn block input-confirm" bindtap="confirm"'))
var inputWxss = require('fs').readFileSync(require('path').join(__dirname, '../miniprogram/pages/input/index.wxss'), 'utf8')
assert(/button\.btn\.input-confirm\s*\{[^}]*width: calc\(100% - 64rpx\)/.test(inputWxss))
console.log('upload sheet: name-only, name+owner, missing column, read failures and re-pick checks ok')
