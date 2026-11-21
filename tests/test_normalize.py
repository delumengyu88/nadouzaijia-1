from nadou.normalize import PlatformNormalizer, ProgramNormalizer


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


def test_platform_alias_merges_variants():
    normalizer = PlatformNormalizer(
        {
            "重庆移动百视通OTT": "重庆移动",
            "重庆移动百视通": "重庆移动",
            "重庆移动": "重庆移动",
        }
    )
    for variant in ("重庆移动百视通OTT", "重庆移动百视通", "  重庆移动百视通ＯＴＴ "):
        result = normalizer.normalize(variant)
        assert result.unverified is False
        assert result.name == "重庆移动"
    blank = normalizer.normalize("  ")
    assert blank.unverified is True
    assert blank.name is None
