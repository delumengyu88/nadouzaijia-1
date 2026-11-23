// 读取用户上传的 .xlsx 片单：解压（含 deflate）、取第一个工作表、按表头找「节目名称」「版权方」列。
var LEN_BASE = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258]
var LEN_EXTRA = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0]
var DIST_BASE = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049, 3073,
  4097, 6145, 8193, 12289, 16385, 24577]
var DIST_EXTRA = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13]
var CL_ORDER = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15]
var NAME_HEADERS = ['节目名称', '上传节目名', '节目名', '片名', '剧名', '作品名称', '作品名', '名称']
var MAX_ROWS = 500

class SheetReader {
  // ---- inflate (RFC 1951) ----
  static bits(s, n) {
    var v = s.buf
    while (s.cnt < n) {
      if (s.pos >= s.data.length) throw new Error('inflate: unexpected end')
      v |= s.data[s.pos++] << s.cnt
      s.cnt += 8
    }
    s.buf = v >>> n
    s.cnt -= n
    return v & ((1 << n) - 1)
  }
  static huffman(lengths) {
    var counts = new Array(16).fill(0)
    var symbols = []
    var offs = new Array(16).fill(0)
    var i
    for (i = 0; i < lengths.length; i++) counts[lengths[i]]++
    counts[0] = 0
    for (i = 1; i < 16; i++) offs[i] = offs[i - 1] + counts[i - 1]
    for (i = 0; i < lengths.length; i++) if (lengths[i]) symbols[offs[lengths[i]]++] = i
    return { counts: counts, symbols: symbols }
  }
  static decode(s, h) {
    var code = 0
    var first = 0
    var index = 0
    for (var len = 1; len < 16; len++) {
      code |= SheetReader.bits(s, 1)
      var count = h.counts[len]
      if (code - count < first) return h.symbols[index + (code - first)]
      index += count
      first += count
      first <<= 1
      code <<= 1
    }
    throw new Error('inflate: bad code')
  }
  static inflateRaw(data, size) {
    var s = { data: data, pos: 0, buf: 0, cnt: 0 }
    var out = new Uint8Array(size || data.length * 4 + 1024)
    var o = 0
    var put = function (b) {
      if (o >= out.length) { var grown = new Uint8Array(out.length * 2); grown.set(out); out = grown }
      out[o++] = b
    }
    var last
    do {
      last = SheetReader.bits(s, 1)
      var type = SheetReader.bits(s, 2)
      if (type === 0) {
        s.buf = 0
        s.cnt = 0
        var len = data[s.pos] | (data[s.pos + 1] << 8)
        s.pos += 4
        for (var k = 0; k < len; k++) put(data[s.pos++])
        continue
      }
      var lit
      var dist
      if (type === 1) {
        var fixed = []
        for (var f = 0; f < 288; f++) fixed[f] = f < 144 ? 8 : f < 256 ? 9 : f < 280 ? 7 : 8
        lit = SheetReader.huffman(fixed)
        dist = SheetReader.huffman(new Array(30).fill(5))
      } else if (type === 2) {
        var nlen = SheetReader.bits(s, 5) + 257
        var ndist = SheetReader.bits(s, 5) + 1
        var ncode = SheetReader.bits(s, 4) + 4
        var cl = new Array(19).fill(0)
        for (var c = 0; c < ncode; c++) cl[CL_ORDER[c]] = SheetReader.bits(s, 3)
        var clh = SheetReader.huffman(cl)
        var lengths = []
        while (lengths.length < nlen + ndist) {
          var sym = SheetReader.decode(s, clh)
          if (sym < 16) { lengths.push(sym); continue }
          var rep = 0
          var val = 0
          if (sym === 16) { val = lengths[lengths.length - 1]; rep = 3 + SheetReader.bits(s, 2) }
          else if (sym === 17) rep = 3 + SheetReader.bits(s, 3)
          else rep = 11 + SheetReader.bits(s, 7)
          while (rep--) lengths.push(val)
        }
        lit = SheetReader.huffman(lengths.slice(0, nlen))
        dist = SheetReader.huffman(lengths.slice(nlen))
      } else {
        throw new Error('inflate: bad block')
      }
      for (;;) {
        var sym2 = SheetReader.decode(s, lit)
        if (sym2 < 256) { put(sym2); continue }
        if (sym2 === 256) break
        sym2 -= 257
        var n = LEN_BASE[sym2] + SheetReader.bits(s, LEN_EXTRA[sym2])
        var ds = SheetReader.decode(s, dist)
        var back = DIST_BASE[ds] + SheetReader.bits(s, DIST_EXTRA[ds])
        if (back > o) throw new Error('inflate: bad distance')
        while (n--) put(out[o - back])
      }
    } while (!last)
    return out.subarray(0, o)
  }

  // ---- zip ----
  static unzip(bytes) {
    var u16 = function (p) { return bytes[p] | (bytes[p + 1] << 8) }
    var u32 = function (p) { return (u16(p) | (u16(p + 2) << 16)) >>> 0 }
    var end = -1
    for (var p = bytes.length - 22; p >= 0 && p >= bytes.length - 65557; p--) {
      if (u32(p) === 0x06054b50) { end = p; break }
    }
    if (end < 0) throw new Error('zip: no directory')
    var count = u16(end + 10)
    var at = u32(end + 16)
    var files = {}
    for (var i = 0; i < count; i++) {
      if (u32(at) !== 0x02014b50) throw new Error('zip: bad entry')
      var method = u16(at + 10)
      var csize = u32(at + 20)
      var usize = u32(at + 24)
      var nlen = u16(at + 28)
      var name = SheetReader.utf8(bytes.subarray(at + 46, at + 46 + nlen))
      var local = u32(at + 42)
      var start = local + 30 + u16(local + 26) + u16(local + 28)
      files[name] = { method: method, data: bytes.subarray(start, start + csize), size: usize }
      at += 46 + nlen + u16(at + 30) + u16(at + 32)
    }
    return files
  }
  static entry(files, name) {
    var f = files[name]
    if (!f) return null
    if (f.method === 0) return SheetReader.utf8(f.data)
    if (f.method === 8) return SheetReader.utf8(SheetReader.inflateRaw(f.data, f.size))
    throw new Error('zip: unsupported method')
  }
  static utf8(b) {
    var out = ''
    for (var i = 0; i < b.length;) {
      var c = b[i++]
      if (c >= 0xf0) {
        c = ((c & 7) << 18) | ((b[i++] & 63) << 12) | ((b[i++] & 63) << 6) | (b[i++] & 63)
        c -= 0x10000
        out += String.fromCharCode(0xd800 + (c >> 10), 0xdc00 + (c & 1023))
        continue
      }
      if (c >= 0xe0) c = ((c & 15) << 12) | ((b[i++] & 63) << 6) | (b[i++] & 63)
      else if (c >= 0xc0) c = ((c & 31) << 6) | (b[i++] & 63)
      out += String.fromCharCode(c)
    }
    return out
  }

  // ---- xlsx ----
  static unescape(text) {
    return String(text).replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos);/gi, function (_, e) {
      var k = e.toLowerCase()
      if (k[0] === '#') return String.fromCodePoint(k[1] === 'x' ? parseInt(k.slice(2), 16) : parseInt(k.slice(1), 10))
      return { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" }[k]
    })
  }
  static attr(tag, name) {
    var m = new RegExp('(?:^|\\s)' + name + '="([^"]*)"').exec(tag)
    return m ? m[1] : ''
  }
  static texts(xml) {
    var out = ''
    var re = /<t(?:\s[^>]*)?>([\s\S]*?)<\/t>/g
    var m
    while ((m = re.exec(xml))) out += SheetReader.unescape(m[1])
    return out
  }
  static firstSheet(files) {
    var book = SheetReader.entry(files, 'xl/workbook.xml') || ''
    var rels = SheetReader.entry(files, 'xl/_rels/workbook.xml.rels') || ''
    var sheet = /<(?:\w+:)?sheet\s[^>]*>/.exec(book)
    var rid = sheet ? SheetReader.attr(sheet[0], 'r:id') : ''
    var re = /<Relationship\s[^>]*>/g
    var m
    while (rid && (m = re.exec(rels))) {
      if (SheetReader.attr(m[0], 'Id') !== rid) continue
      var target = SheetReader.attr(m[0], 'Target')
      var full = target[0] === '/' ? target.slice(1) : 'xl/' + target
      if (files[full]) return full
    }
    return Object.keys(files).filter(function (n) { return /^xl\/worksheets\/[^/]+\.xml$/.test(n) }).sort()[0] || ''
  }
  static column(ref) {
    var letters = /^[A-Z]+/.exec(ref || '')
    if (!letters) return -1
    var n = 0
    for (var i = 0; i < letters[0].length; i++) n = n * 26 + letters[0].charCodeAt(i) - 64
    return n - 1
  }
  static rows(bytes) {
    var files = SheetReader.unzip(bytes)
    var name = SheetReader.firstSheet(files)
    if (!name) throw new Error('xlsx: no sheet')
    var shared = []
    var sst = SheetReader.entry(files, 'xl/sharedStrings.xml') || ''
    var si = /<si>([\s\S]*?)<\/si>|<si\/>/g
    var m
    while ((m = si.exec(sst))) shared.push(SheetReader.texts(m[1] || ''))
    var xml = SheetReader.entry(files, name)
    var rows = []
    var rowRe = /<row\b([^>]*?)(?:\/>|>([\s\S]*?)<\/row>)/g
    var r
    while ((r = rowRe.exec(xml))) {
      var cells = []
      var cellRe = /<c\b([^>]*?)(?:\/>|>([\s\S]*?)<\/c>)/g
      var c
      var next = 0
      while ((c = cellRe.exec(r[2] || ''))) {
        var col = SheetReader.column(SheetReader.attr(c[1], 'r'))
        if (col < 0) col = next
        next = col + 1
        var type = SheetReader.attr(c[1], 't')
        var body = c[2] || ''
        var v = /<v>([\s\S]*?)<\/v>/.exec(body)
        var value = ''
        if (type === 's') value = v ? shared[Number(v[1])] || '' : ''
        else if (type === 'inlineStr') value = SheetReader.texts(body)
        else value = v ? SheetReader.unescape(v[1]) : ''
        cells[col] = value
      }
      // Excel can omit empty rows from the XML. Keep the actual worksheet row number.
      var rowNumber = Number(SheetReader.attr(r[1], 'r')) || rows.length + 1
      if (!Number.isInteger(rowNumber) || rowNumber < 1 || rowNumber > 1048576) throw new Error('xlsx: invalid row')
      rows[rowNumber - 1] = cells
    }
    return rows
  }

  // ---- 片单 ----
  static clean(value) {
    return String(value == null ? '' : value).replace(/[\s\u3000\ufeff]+/g, ' ').trim()
  }
  static header(rows) {
    var first = null
    for (var h = 0; h < Math.min(rows.length, 10); h++) {
      var labels = (rows[h] || []).map(function (cell) {
        return SheetReader.clean(cell).normalize('NFKC').replace(/\s/g, '')
      })
      var uploaded = labels.indexOf('上传节目名')
      var hitExport = uploaded >= 0 && labels.includes('匹配节目名') && labels.includes('命中画面截图')
      var owner = labels.indexOf('版权方')
      if (hitExport) return { row: h, name: uploaded, owner: owner, hitExport: true }
      if (first) continue
      for (var alias of NAME_HEADERS) {
        var column = labels.indexOf(alias)
        if (column < 0) continue
        first = { row: h, name: column, owner: owner, hitExport: false }
        break
      }
    }
    return first
  }
  // 返回 { ok: true, items: [{ name, owner }] } 或 { ok: false, code }。
  static titles(rows) {
    var header = SheetReader.header(rows)
    if (!header) return { ok: false, code: 'col' }
    var items = []
    var seen = header.hitExport ? new Set() : null
    rows.slice(header.row + 1).forEach(function (row) {
      var name = SheetReader.clean(row[header.name])
      if (!name || (seen && seen.has(name))) return
      if (seen) seen.add(name)
      items.push({ name: name, owner: SheetReader.clean(row[header.owner]) })
    })
    if (!items.length) return { ok: false, code: 'empty' }
    if (items.length > MAX_ROWS) return { ok: false, code: 'rows' }
    return { ok: true, items: items }
  }
  static precheck(name, size) {
    if (String(name || '').toLowerCase().slice(-5) !== '.xlsx') return 'xlsx'
    if (!size) return 'empty'
    if (size > 5 * 1024 * 1024) return 'size'
    return ''
  }
  static read(buffer) {
    var rows
    try {
      rows = SheetReader.rows(new Uint8Array(buffer))
    } catch (error) {
      return { ok: false, code: 'read' }
    }
    return SheetReader.titles(rows)
  }
}

module.exports = SheetReader
