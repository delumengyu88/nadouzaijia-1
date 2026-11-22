from nadou.normalize import ProgramNormalizer
from nadou.platform_names import PlatformNames


def test_fold_trims_width_and_case():
    assert ProgramNormalizer().canonical("  ＡＢＣ  ") == "abc"
    assert ProgramNormalizer().canonical("\u3000甲\u3000") == "甲"
    assert ProgramNormalizer().canonical("甲   乙") == "甲 乙"


def test_dedupe_only_folds_identical_titles():
    normalizer = ProgramNormalizer()
    assert normalizer.dedupe(["  Ｂ ", "b", "乙"]) == ["b", "乙"]
    assert normalizer.dedupe(["甲", "甲 ", "乙"]) == ["甲", "乙"]
    assert normalizer.dedupe(["", "   ", "甲"]) == ["甲"]
    assert normalizer.canonical("示例剧集") != normalizer.canonical("示例节目")


def test_platform_parts_and_unrecognized():
    guangdong = PlatformNames.normalize(" GD移动 ")
    assert guangdong.province == "广东"
    assert guangdong.operator == "移动"
    assert guangdong.subtype is None
    assert guangdong.name == "广东移动"
    wide = PlatformNames.normalize("ＧＤ移动")
    assert wide.count_key() == guangdong.count_key()
    ott = PlatformNames.normalize("广东移动OTT")
    bestv = PlatformNames.normalize("广东移动百视通")
    assert ott.count_key() == bestv.count_key() == ("广东", "移动")
    assert ott.subtype == "OTT"
    assert bestv.subtype == "百视通"
    zhejiang = PlatformNames.normalize("浙江移动")
    assert zhejiang.count_key() == ("浙江", "移动")
    assert zhejiang.count_key() != guangdong.count_key()
    assert PlatformNames.normalize("公网广东移动").count_key() == ("广东", "移动")
    unknown = PlatformNames.normalize("没有这个平台")
    assert unknown.unrecognized is True
    assert unknown.count_key() is None
    assert PlatformNames.normalize("  ").unrecognized is True
