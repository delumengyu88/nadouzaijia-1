// 背景图 750×1624。镜圈上沿按原图像素标定，用来给矮屏留出文字空档。
var IMG_W = 750
var IMG_H = 1624
var GLASS_TOP = 560
var GLASS_DIAM = 340

function layoutSplash(sys, menu) {
  sys = sys || {}
  var w = sys.windowWidth || 375
  var h = sys.windowHeight || 812
  var scale = Math.max(w / IMG_W, h / IMG_H)
  var dispW = IMG_W * scale
  var dispH = IMG_H * scale
  var imgTop = (h - dispH) / 2
  var heightRatio = h / dispH
  if (heightRatio > 1) heightRatio = 1
  var u = w / 750
  var titleSize = 96 * u * heightRatio
  var tagSize = 36 * u * heightRatio
  var tagGap = 20 * u * heightRatio
  var copyTop = 292 * u * heightRatio
  function block() { return titleSize * 1.15 + tagGap + tagSize * 1.3 }
  function glassTop() { return imgTop + GLASS_TOP * scale }
  function glassBottom() { return imgTop + (GLASS_TOP + GLASS_DIAM) * scale }
  var textBottom = copyTop + block()
  var crowded = textBottom - (glassTop() - 28)
  if (crowded > 0) {
    if (glassBottom() + crowded <= h - 2) imgTop += crowded
    else {
      var room = glassTop() - 28 - copyTop
      var shrink = room > 0 ? room / block() : 0.7
      if (shrink < 0.62) shrink = 0.62
      titleSize *= shrink
      tagSize *= shrink
      tagGap *= shrink
    }
  }
  if (glassTop() < 0) imgTop -= glassTop()
  if (glassBottom() > h) imgTop -= glassBottom() - h
  var menuTop = menu && menu.top ? menu.top : (sys.statusBarHeight || 20) + 4
  var menuH = menu && menu.height ? menu.height : 32
  var menuLeft = menu && menu.left ? menu.left : w - 94
  var hitH = menuH > 44 ? menuH : 44
  var extra = (hitH - menuH) / 2
  return {
    splashImgTop: imgTop,
    splashImgW: dispW,
    splashImgH: dispH,
    splashCopyTop: copyTop,
    splashTitleSize: titleSize,
    splashTagSize: tagSize,
    splashTagGap: tagGap,
    splashGlassTop: glassTop(),
    splashGlassBottom: glassBottom(),
    splashTextBottom: copyTop + block(),
    splashSkipTop: menuTop,
    splashSkipHeight: menuH,
    splashSkipHitTop: menuTop - extra,
    splashSkipHitHeight: hitH,
    splashSkipRight: w - menuLeft + 8
  }
}

module.exports = { layoutSplash: layoutSplash, IMG_W: IMG_W, IMG_H: IMG_H }
