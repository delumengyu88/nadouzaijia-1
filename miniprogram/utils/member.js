// Membership copy and the 375×667 layout budget for the member center.
// Prices stay in api/mock PRODUCTS; this module only formats them.

var VIP_PERKS = [
  { ok: true, text: '30–89 天记录' },
  { ok: true, text: '90 天以上记录' },
  { ok: true, text: '高清截图与下载' }
]

var REGULAR_PERKS = [
  { ok: false, text: '30–89 天记录 · 仅 VIP' },
  { ok: true, text: '90 天以上记录' },
  { ok: true, text: '高清截图与下载' }
]

// Vertical pixels at a 375-wide screen (1rpx = 0.5px). Safe area is 0 on 375×667.
// These match pages/member/index.wxss. The check script asserts both.
var LAYOUT = {
  statusBar: 20,
  navBar: 44,
  statusBlock: 34,
  cardBlock: 124,
  payBlock: 56,
  inviteBlock: 32,
  pagePadding: 56,
  minWindow: 667
}

function copyPerks(rows) {
  return rows.map(function (row) { return { ok: row.ok, text: row.text } })
}

function priceText(product) {
  return '¥' + product.price + '/' + product.days + '天'
}

function plansFromProducts(products) {
  var vip = products.membership_vip
  var regular = products.membership_regular
  return [
    {
      id: 'membership_vip',
      name: vip.card,
      price: vip.price,
      days: vip.days,
      priceText: priceText(vip),
      perks: copyPerks(VIP_PERKS)
    },
    {
      id: 'membership_regular',
      name: regular.card,
      price: regular.price,
      days: regular.days,
      priceText: priceText(regular),
      perks: copyPerks(REGULAR_PERKS)
    }
  ]
}

function confirmLabel(price) {
  return '确认开通 ¥' + price
}

function statusLine(tier, until) {
  if (tier === 'vip') return 'VIP 至 ' + until
  if (tier === 'regular') return '普通会员 至 ' + until
  if (tier === 'guest') return '未登录'
  return '免费 · 可看 180 天以上低清记录'
}

function screenUsage(layout) {
  var box = layout || LAYOUT
  var used = box.statusBar + box.navBar + box.statusBlock + box.cardBlock * 2 +
    box.payBlock + box.inviteBlock + box.pagePadding
  return { used: used, limit: box.minWindow, fits: used <= box.minWindow }
}

// Unavailable download controls name the reason. A free caller has no download entry.
function downloadOffer(opts) {
  var options = opts || {}
  if (options.canSave) return { downloadEntry: true, blockedReason: '' }
  if (options.programOnly) return { downloadEntry: false, blockedReason: '' }
  var tier = options.tier || ''
  if (options.low || tier === 'free' || tier === 'guest' || !tier) {
    return { downloadEntry: false, blockedReason: '开通会员后下载' }
  }
  if (tier === 'regular') return { downloadEntry: false, blockedReason: '仅 VIP 可用' }
  return { downloadEntry: false, blockedReason: '' }
}

module.exports = {
  VIP_PERKS: VIP_PERKS,
  REGULAR_PERKS: REGULAR_PERKS,
  LAYOUT: LAYOUT,
  plansFromProducts: plansFromProducts,
  confirmLabel: confirmLabel,
  statusLine: statusLine,
  screenUsage: screenUsage,
  downloadOffer: downloadOffer
}
