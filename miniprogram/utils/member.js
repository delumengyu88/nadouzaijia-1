// Membership copy and the 375×667 layout budget for the member center.
// Prices stay in api/mock PRODUCTS; this module only formats them.

class MemberCopy {
  static copyPerks(rows) {
    return rows.map(function (row) { return { ok: row.ok, text: row.text } })
  }

  static priceText(product) {
    return '¥' + product.price + '/' + product.days + '天'
  }

  static plansFromProducts(products) {
    var vip = products.membership_vip
    var regular = products.membership_regular
    return [
      {
        id: 'membership_vip',
        name: vip.card,
        price: vip.price,
        days: vip.days,
        selectable: true,
        priceText: MemberCopy.priceText(vip),
        perks: MemberCopy.copyPerks(MemberCopy.VIP_PERKS)
      },
      {
        id: 'membership_regular',
        name: regular.card,
        price: regular.price,
        days: regular.days,
        priceText: MemberCopy.priceText(regular),
        selectable: true,
        perks: MemberCopy.copyPerks(MemberCopy.REGULAR_PERKS)
      },
      {
        id: 'free',
        name: '免费用户',
        selectable: false,
        price: 0,
        days: 0,
        priceText: '免费',
        perks: MemberCopy.copyPerks(MemberCopy.FREE_PERKS)
      }
    ]
  }

  static freeCornerLabel(tier) {
    return tier === 'free' ? '当前' : '免费'
  }

  static decoratePlans(plans, tier) {
    return plans.map(function (plan) {
      if (plan.selectable) return plan
      return Object.assign({}, plan, { priceText: MemberCopy.freeCornerLabel(tier) })
    })
  }

  static confirmLabel(price) {
    return '确认开通 ¥' + price
  }

  static statusLine(tier, until) {
    if (tier === 'vip') return 'VIP 至 ' + until
    if (tier === 'regular') return '普通会员 至 ' + until
    if (tier === 'guest') return '未登录'
    return '免费 · 可看 180 天以上低清记录'
  }

  static screenUsage(layout) {
    var box = layout || MemberCopy.LAYOUT
    var used = box.statusBar + box.navBar + box.statusBlock + box.cardBlock * box.cardCount +
      box.payBlock + box.inviteBlock + box.pagePadding
    return { used: used, limit: box.minWindow, fits: used <= box.minWindow }
  }

  // A regular member can download except for records aged 30–89 days.
  static downloadOffer(opts) {
    var options = opts || {}
    if (options.canSave) return { downloadEntry: true, blockedReason: '' }
    if (options.programOnly) return { downloadEntry: false, blockedReason: '' }
    var tier = options.tier || ''
    if (options.low || tier === 'free' || tier === 'guest' || !tier) {
      return { downloadEntry: false, blockedReason: '开通会员后下载' }
    }
    if (tier === 'regular') {
      return { downloadEntry: false, blockedReason: MemberCopy.REGULAR_PERKS[0].text }
    }
    return { downloadEntry: false, blockedReason: '' }
  }
}

MemberCopy.VIP_PERKS = [
  { ok: true, text: '30–89 天记录' },
  { ok: true, text: '90 天以上记录' },
  { ok: true, text: '高清截图与下载' }
]

MemberCopy.REGULAR_PERKS = [
  { ok: false, text: '30–89 天记录 · 仅 VIP' },
  { ok: true, text: '90 天以上记录' },
  { ok: true, text: '高清截图与下载' }
]

MemberCopy.FREE_PERKS = [
  { ok: true, text: '180 天以上记录（低清）' },
  { ok: false, text: '高清截图 · 仅会员' },
  { ok: false, text: '下载 · 仅会员' }
]

// Vertical pixels at a 375-wide screen (1rpx = 0.5px). Safe area is 0 on 375×667.
// These match pages/member/index.wxss. The check script asserts both.
MemberCopy.LAYOUT = {
  statusBar: 20,
  navBar: 44,
  statusBlock: 34,
  cardBlock: 124,
  cardCount: 3,
  payBlock: 56,
  inviteBlock: 32,
  pagePadding: 56,
  minWindow: 667
}

module.exports = MemberCopy
