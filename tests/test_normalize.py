from nadou.normalize import ProgramNormalizer
from nadou.platform_names import PlatformNames


def test_fold_trims_width_and_case():
    assert ProgramNormalizer({}).canonical("  ＡＢＣ  ") == "abc"
    assert ProgramNormalizer({}).canonical("\u3000甲\u3000") == "甲"
    assert ProgramNormalizer({}).canonical("甲   乙") == "甲 乙"


def test_program_alias_and_dedupe():
    normalizer = ProgramNormalizer({"乙": "b", "示例剧集": "示例节目"})
    assert normalizer.canonical("示例剧集") == "示例节目"
    assert normalizer.dedupe(["  Ｂ ", "b", "乙"]) == ["b"]
    assert normalizer.dedupe(["甲", "甲 ", "乙"]) == ["甲", "b"]
    assert ProgramNormalizer({}).dedupe(["甲", "甲 ", "乙"]) == ["甲", "乙"]
    assert normalizer.dedupe(["", "   ", "甲"]) == ["甲"]


def test_program_alias_with_several_targets_is_not_collapsed():
    normalizer = ProgramNormalizer(pairs=[("abc剧", "节目1986"), ("ａｂｃ剧", "节目2010")])
    assert normalizer.candidates("ABC剧") == ["节目1986", "节目2010"]
    assert normalizer.canonical("ABC剧") == ""


def test_platform_code_map():
    assert PlatformNames.normalize(" GD移动 ").name == "广东移动"
    assert PlatformNames.normalize("ＧＤ移动").name == "广东移动"
    assert PlatformNames.normalize("广东移动").unrecognized is False
    unknown = PlatformNames.normalize("没有这个平台")
    assert unknown.name is None
    assert unknown.unrecognized is True
    many = PlatformNames.normalize("双解平台")
    assert many.name is None
    assert many.unrecognized is True
    assert PlatformNames.standards("双解平台") == ("广东移动", "示例视频")
    assert PlatformNames.normalize("  ").unrecognized is True
