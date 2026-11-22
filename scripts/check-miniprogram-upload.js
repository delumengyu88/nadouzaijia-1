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

// 全角字母会被 parseTitles 收成半角，版权方仍按同一个显示名挂上。
p = page()
load(p, put('/tmp/half.xlsx', [['节目名称', '版权方'], ['星河旧梦Ａ', '云豆传媒']]))
assert.strictEqual(p.data.phase, 'preview')
assert.deepStrictEqual([p.data.items[0].name, p.data.items[0].owner], ['星河旧梦A', '云豆传媒'])

var COL = '未找到「节目名称」列，请确认它在表格前 10 行内'
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

// 3. 两列都没有：提示缺的是「节目名称」列，并说明只在前 10 行找表头。
p = page()
load(p, put('/tmp/none.xlsx', [['片名', '出品方'], ['星河旧梦', '云豆传媒']]))
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

// WPS 共享字符串：<si> 带属性，节目名后面跟 <rPh> 注音。注音不能拼进片名。
function wpsBook() {
  var sst = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    + '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    + '<si><t>节目名称</t></si><si><t>版权方</t></si>'
    + '<si xml:space="preserve"><t>星河旧梦</t><rPh sb="0" eb="4"><t>xinghejiumeng</t></rPh><phoneticPr fontId="1" type="noConversion"/></si>'
    + '<si><r><t>云豆传媒</t></r><rPh sb="0" eb="2"><t>yundou</t></rPh></si></sst>'
  var sheet = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    + '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
    + '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
    + '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" t="s"><v>3</v></c></row>'
    + '</sheetData></worksheet>'
  var buf = XlsxFixture.zip({
    '[Content_Types].xml': '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
    'xl/workbook.xml': '<?xml version="1.0"?><workbook xmlns:r="r"><sheets><sheet name="片单" sheetId="1" r:id="rId1"/></sheets></workbook>',
    'xl/_rels/workbook.xml.rels': '<?xml version="1.0"?><Relationships><Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
    'xl/sharedStrings.xml': sst,
    'xl/worksheets/sheet1.xml': sheet
  })
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.length)
}
files['/tmp/wps.xlsx'] = wpsBook()
p = page()
load(p, { name: 'wps.xlsx', size: files['/tmp/wps.xlsx'].byteLength, path: '/tmp/wps.xlsx' })
assert.strictEqual(p.data.phase, 'preview')
assert.deepStrictEqual([p.data.items[0].name, p.data.items[0].owner], ['星河旧梦', '云豆传媒'])
assert.strictEqual(p.data.items[0].name.indexOf('xinghe'), -1)
assert.strictEqual(p.data.items[0].owner.indexOf('yundou'), -1)

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
load(p, put('/tmp/none2.xlsx', [['片名'], ['星河旧梦']]))
assert.strictEqual(p.data.phase, 'fail')
assert.strictEqual(p.data.fromFile, true)
picks.push(put('/tmp/fixed.xlsx', [['节目名称', '版权方'], ['雾港来信', '云豆传媒']]))
var before = pickerOpened
p.upload()
assert.strictEqual(pickerOpened, before + 1)
assert.strictEqual(p.data.phase, 'preview')
assert.strictEqual(p.data.items[0].name, '雾港来信')
// 取消选择时停在原页，不跳回输入页。
load(p, put('/tmp/none3.xlsx', [['片名'], ['x']]))
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
console.log('upload sheet: name-only, name+owner, missing column, read failures and re-pick checks ok')
