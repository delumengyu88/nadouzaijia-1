function crc32(bytes) {
  var c = ~0
  for (var i = 0; i < bytes.length; i++) {
    c ^= bytes[i]
    for (var k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1))
  }
  return ~c >>> 0
}

function utf8(str) {
  var out = []
  var i
  for (i = 0; i < str.length; i++) {
    var c = str.charCodeAt(i)
    if (c < 0x80) out.push(c)
    else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 0x3f))
    else out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 0x3f), 0x80 | (c & 0x3f))
  }
  return out
}

function u16(n) {
  return [n & 255, (n >> 8) & 255]
}

function u32(n) {
  return [n & 255, (n >> 8) & 255, (n >> 16) & 255, (n >> 24) & 255]
}

function concat(parts) {
  var len = 0
  parts.forEach(function (p) { len += p.length })
  var out = new Uint8Array(len)
  var off = 0
  parts.forEach(function (p) {
    out.set(p, off)
    off += p.length
  })
  return out
}

function zipStore(files) {
  var locals = []
  var centrals = []
  var offset = 0
  files.forEach(function (file) {
    var name = utf8(file.name)
    var data = utf8(file.text)
    var crc = crc32(data)
    var local = concat([
      new Uint8Array(u32(0x04034b50)),
      new Uint8Array(u16(20)),
      new Uint8Array(u16(0)),
      new Uint8Array(u16(0)),
      new Uint8Array(u16(0)),
      new Uint8Array(u16(0)),
      new Uint8Array(u32(crc)),
      new Uint8Array(u32(data.length)),
      new Uint8Array(u32(data.length)),
      new Uint8Array(u16(name.length)),
      new Uint8Array(u16(0)),
      new Uint8Array(name),
      new Uint8Array(data)
    ])
    var central = concat([
      new Uint8Array(u32(0x02014b50)),
      new Uint8Array(u16(20)),
      new Uint8Array(u16(20)),
      new Uint8Array(u16(0)),
      new Uint8Array(u16(0)),
      new Uint8Array(u16(0)),
      new Uint8Array(u16(0)),
      new Uint8Array(u32(crc)),
      new Uint8Array(u32(data.length)),
      new Uint8Array(u32(data.length)),
      new Uint8Array(u16(name.length)),
      new Uint8Array(u16(0)),
      new Uint8Array(u16(0)),
      new Uint8Array(u16(0)),
      new Uint8Array(u16(0)),
      new Uint8Array(u32(0)),
      new Uint8Array(u32(offset)),
      new Uint8Array(name)
    ])
    locals.push(local)
    centrals.push(central)
    offset += local.length
  })
  var centralBlob = concat(centrals)
  var end = concat([
    new Uint8Array(u32(0x06054b50)),
    new Uint8Array(u16(0)),
    new Uint8Array(u16(0)),
    new Uint8Array(u16(files.length)),
    new Uint8Array(u16(files.length)),
    new Uint8Array(u32(centralBlob.length)),
    new Uint8Array(u32(offset)),
    new Uint8Array(u16(0))
  ])
  return concat(locals.concat([centralBlob, end]))
}

function esc(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

function cell(col, row, value) {
  return '<c r="' + col + row + '" t="inlineStr"><is><t>' + esc(value) + '</t></is></c>'
}

function build(table) {
  var header = ['平台', '节目类型', '上传节目名', '匹配节目名', '检测日期']
  var lines = []
  lines.push('<row r="1">' + cell('A', 1, table.title) + '</row>')
  lines.push('<row r="2">' + cell('A', 2, '查询范围') + cell('B', 2, table.scope) + '</row>')
  lines.push('<row r="3">' + cell('A', 3, '生成日期') + cell('B', 3, table.day) + '</row>')
  var head = header.map(function (name, i) {
    return cell(String.fromCharCode(65 + i), 5, name)
  }).join('')
  lines.push('<row r="5">' + head + '</row>')
  table.rows.forEach(function (row, idx) {
    var r = idx + 6
    var cols = [row.platform, row.type, row.uploaded, row.matched, row.date]
    var body = cols.map(function (value, i) {
      return cell(String.fromCharCode(65 + i), r, value)
    }).join('')
    lines.push('<row r="' + r + '">' + body + '</row>')
  })
  var sheet = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' +
    lines.join('') + '</sheetData></worksheet>'
  return zipStore([
    {
      name: '[Content_Types].xml',
      text: '<?xml version="1.0" encoding="UTF-8"?>' +
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
        '<Default Extension="xml" ContentType="application/xml"/>' +
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' +
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' +
        '</Types>'
    },
    {
      name: '_rels/.rels',
      text: '<?xml version="1.0" encoding="UTF-8"?>' +
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>' +
        '</Relationships>'
    },
    {
      name: 'xl/workbook.xml',
      text: '<?xml version="1.0" encoding="UTF-8"?>' +
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">' +
        '<sheets><sheet name="命中结果" sheetId="1" r:id="rId1"/></sheets></workbook>'
    },
    {
      name: 'xl/_rels/workbook.xml.rels',
      text: '<?xml version="1.0" encoding="UTF-8"?>' +
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>' +
        '</Relationships>'
    },
    { name: 'xl/worksheets/sheet1.xml', text: sheet }
  ])
}

module.exports = { build: build }
