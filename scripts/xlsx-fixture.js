// 测试用：用 Node 的 zlib 生成真实压缩（deflate）的 .xlsx，内容走 sharedStrings，和 Excel/WPS 导出的结构一致。
var zlib = require('zlib')

class XlsxFixture {
  static escape(text) {
    return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  }
  static crc(buf) {
    var c = ~0
    for (var i = 0; i < buf.length; i++) {
      c ^= buf[i]
      for (var k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1))
    }
    return ~c >>> 0
  }
  static zip(entries) {
    var locals = []
    var centrals = []
    var offset = 0
    Object.keys(entries).forEach(function (name) {
      var raw = Buffer.from(entries[name], 'utf8')
      var data = zlib.deflateRawSync(raw)
      var nameBuf = Buffer.from(name, 'utf8')
      var head = Buffer.alloc(30)
      head.writeUInt32LE(0x04034b50, 0); head.writeUInt16LE(20, 4); head.writeUInt16LE(0x0800, 6)
      head.writeUInt16LE(8, 8); head.writeUInt32LE(XlsxFixture.crc(raw), 14)
      head.writeUInt32LE(data.length, 18); head.writeUInt32LE(raw.length, 22); head.writeUInt16LE(nameBuf.length, 26)
      var central = Buffer.alloc(46)
      central.writeUInt32LE(0x02014b50, 0); central.writeUInt16LE(20, 4); central.writeUInt16LE(20, 6)
      central.writeUInt16LE(0x0800, 8); central.writeUInt16LE(8, 10); central.writeUInt32LE(XlsxFixture.crc(raw), 16)
      central.writeUInt32LE(data.length, 20); central.writeUInt32LE(raw.length, 24); central.writeUInt16LE(nameBuf.length, 28)
      central.writeUInt32LE(offset, 42)
      locals.push(head, nameBuf, data)
      centrals.push(central, nameBuf)
      offset += head.length + nameBuf.length + data.length
    })
    var dir = Buffer.concat(centrals)
    var end = Buffer.alloc(22)
    end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(centrals.length / 2, 8); end.writeUInt16LE(centrals.length / 2, 10)
    end.writeUInt32LE(dir.length, 12); end.writeUInt32LE(offset, 16)
    return Buffer.concat(locals.concat([dir, end]))
  }
  // rows: 二维数组，第一行是表头。返回 ArrayBuffer，和 wx readFile 的 res.data 一样。
  static build(rows) {
    var shared = []
    var sheetRows = rows.map(function (row, r) {
      var cells = row.map(function (value, c) {
        if (value === null || value === undefined || value === '') return ''
        var ref = String.fromCharCode(65 + c) + (r + 1)
        if (typeof value === 'number') return '<c r="' + ref + '"><v>' + value + '</v></c>'
        shared.push(value)
        return '<c r="' + ref + '" t="s"><v>' + (shared.length - 1) + '</v></c>'
      }).join('')
      return '<row r="' + (r + 1) + '">' + cells + '</row>'
    }).join('')
    var buf = XlsxFixture.zip({
      '[Content_Types].xml': '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
      'xl/workbook.xml': '<?xml version="1.0"?><workbook xmlns:r="r"><sheets><sheet name="片单" sheetId="1" r:id="rId1"/></sheets></workbook>',
      'xl/_rels/workbook.xml.rels': '<?xml version="1.0"?><Relationships><Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
      'xl/sharedStrings.xml': '<?xml version="1.0"?><sst>' + shared.map(function (s) { return '<si><t>' + XlsxFixture.escape(s) + '</t></si>' }).join('') + '</sst>',
      'xl/worksheets/sheet1.xml': '<?xml version="1.0"?><worksheet><sheetData>' + sheetRows + '</sheetData></worksheet>'
    })
    return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.length)
  }
}

module.exports = XlsxFixture
