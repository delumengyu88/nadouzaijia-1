var fs = require('fs')
var path = require('path')
var cp = require('child_process')

var root = path.join(__dirname, '..', 'miniprogram')
var banned = ['待确认', '疑似命中', '确认命中', '别名', '匹配方式', '命中类型', '顺延', 'VIP 结束后生效']
var failed = false

function walk(dir, out) {
  fs.readdirSync(dir).forEach(function (name) {
    var full = path.join(dir, name)
    var stat = fs.statSync(full)
    if (stat.isDirectory()) walk(full, out)
    else out.push(full)
  })
  return out
}

var files = walk(root, [])
files.forEach(function (file) {
  if (path.extname(file) === '.png') return
  var text = fs.readFileSync(file, 'utf8')
  banned.forEach(function (word) {
    if (text.indexOf(word) !== -1) {
      console.error('banned string ' + word + ' in ' + path.relative(root, file))
      failed = true
    }
  })
  if (path.extname(file) === '.json') {
    try {
      JSON.parse(text)
    } catch (err) {
      console.error('invalid json ' + path.relative(root, file) + ': ' + err.message)
      failed = true
    }
  }
  if (path.extname(file) === '.js') {
    var check = cp.spawnSync(process.execPath, ['--check', file], { encoding: 'utf8' })
    if (check.status !== 0) {
      console.error(check.stderr || check.stdout)
      failed = true
    }
  }
})

var app = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'))
var tabs = app.tabBar.list.map(function (item) { return item.text })
if (tabs.join('/') !== '首页/会员中心/个人中心') {
  console.error('tab bar texts are ' + tabs.join('/'))
  failed = true
}
var project = JSON.parse(fs.readFileSync(path.join(root, 'project.config.json'), 'utf8'))
if (project.appid !== 'touristappid') {
  console.error('appid is ' + project.appid)
  failed = true
}

var api = require(path.join(root, 'api/index'))
var present = require(path.join(root, 'utils/present')).present
api.reset()
api.switchAccount('free')
var locked = api.searchProgram('星河旧梦')
locked.records.forEach(function (rec) {
  if (!rec.locked) return
  var keys = Object.keys(rec).sort().join(',')
  if (keys !== 'age_bucket,lock_reason,locked,record_id') {
    console.error('locked record keys ' + keys)
    failed = true
  }
})
var lockedView = present(locked, { name: '星河旧梦', queried: '2026-11-20' })
if (lockedView.header.indexOf('2024') !== -1 || lockedView.header.indexOf('电视剧') !== -1) {
  console.error('locked header leaked year or type: ' + lockedView.header)
  failed = true
}
api.switchAccount('member')
var open = api.searchProgram('星河旧梦')
var openView = present(open, { name: '星河旧梦' })
if (openView.header.indexOf('2024') !== -1 || openView.header.indexOf('电视剧') !== -1) {
  console.error('open header leaked year or type: ' + openView.header)
  failed = true
}
var home = api.homeState()
home.examples.forEach(function (card) {
  var blob = (card.line || '') + (card.sub || '') + (card.type || '') + (card.year || '')
  if (blob.indexOf('电视剧') !== -1 || blob.indexOf('综艺') !== -1 || blob.indexOf('电影') !== -1 || /·\s*20\d\d(?!-)/.test(blob)) {
    console.error('example card leaked type or year: ' + blob)
    failed = true
  }
})
var text = fs.readFileSync(path.join(root, 'pages/member/index.wxml'), 'utf8')
if (text.indexOf('能看 90 天前的记录') === -1) {
  console.error('member card missing plain benefit line')
  failed = true
}

if (failed) process.exit(1)
console.log('miniprogram check ok')
