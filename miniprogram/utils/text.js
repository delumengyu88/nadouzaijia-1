var STOP_WORDS = require('../stop-words.js')

function foldKey(input) {
  var s = toHalf(String(input || ''))
  s = s.replace(/\u3000/g, ' ')
  s = s.replace(/\s+/g, ' ').trim().toLowerCase()
  return s
}

function toHalf(input) {
  return String(input || '').replace(/[\uFF01-\uFF5E]/g, function (ch) {
    return String.fromCharCode(ch.charCodeAt(0) - 0xFEE0)
  })
}

function displayOf(input) {
  var s = toHalf(String(input || ''))
  s = s.replace(/\u3000/g, ' ')
  s = s.replace(/\s+/g, ' ').trim()
  return s
}

function isStopped(title) {
  var key = foldKey(title)
  return STOP_WORDS.some(function (word) { return foldKey(word) === key })
}

function looksJunk(line) {
  if (!line) return true
  if (isStopped(line)) return true
  if (/^\d+$/.test(line)) return true
  if (/^\d{1,2}:\d{2}(:\d{2})?$/.test(line)) return true
  if (/^\d+\s*(分钟|秒|集)$/.test(line)) return true
  if (/^第\d+集$/.test(line)) return true
  if (/^(导演|主演|序号|时长|日期|集数)\s*[:：]/.test(line)) return true
  if (/^https?:\/\//i.test(line) || /^www\./i.test(line)) return true
  if (/^\d{4}[-/.年]\d{1,2}/.test(line)) return true
  if (line.length > 50) return true
  return false
}

function splitColumns(line) {
  var parts = line.split(/\t+|\|/)
  if (parts.length > 1) return parts[0]
  var multi = line.split(/\s{2,}/)
  if (multi.length > 1) return multi[0]
  return line
}

function parseTitles(raw) {
  var text = String(raw || '').replace(/\r/g, '')
  var lines = []
  text.split('\n').forEach(function (row) {
    var pieces = row.split(/[,，、]/)
    pieces.forEach(function (piece) {
      lines.push(piece)
    })
  })
  var kept = []
  var seen = {}
  var filtered = []
  var merged = 0
  var truncated = false
  lines.forEach(function (row, index) {
    var cell = displayOf(splitColumns(row))
    if (!cell) return
    if (looksJunk(cell)) {
      filtered.push(cell)
      return
    }
    var key = foldKey(cell)
    if (!key) return
    if (seen[key]) {
      merged += 1
      return
    }
    if (kept.length >= 500) {
      truncated = true
      return
    }
    seen[key] = 1
    kept.push({ name: cell, key: key, line: index + 1 })
  })
  return {
    items: kept,
    filtered: filtered,
    merged: merged,
    truncated: truncated
  }
}

var CODE_CHARS = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'

function normalizeCode(input) {
  return toHalf(String(input || '')).toUpperCase().replace(/\s+/g, '')
}

function codeWellFormed(code) {
  if (code.length !== 8) return false
  for (var i = 0; i < code.length; i++) {
    if (CODE_CHARS.indexOf(code.charAt(i)) < 0) return false
  }
  return true
}

module.exports = {
  foldKey: foldKey,
  isStopped: isStopped,
  displayOf: displayOf,
  parseTitles: parseTitles,
  normalizeCode: normalizeCode,
  codeWellFormed: codeWellFormed
}
