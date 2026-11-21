from nadou.program_titles import ProgramTitleGuard


def test_stop_words_never_match():
    for word in ("家庭", "狗", "怪", "远方"):
        assert ProgramTitleGuard.is_billable(word) is False
        assert ProgramTitleGuard.is_billable(word, allow_short=True) is False


def test_short_titles_need_the_later_flag():
    assert ProgramTitleGuard.is_billable("甲乙") is False
    assert ProgramTitleGuard.is_billable("甲") is False
    assert ProgramTitleGuard.is_billable("甲乙", allow_short=True) is True
    assert ProgramTitleGuard.is_billable("西游记") is True


def test_stop_words_are_configurable():
    custom = frozenset({"碎片"})
    assert ProgramTitleGuard.is_billable("碎片", stop_words=custom) is False
    assert ProgramTitleGuard.is_billable("家庭", stop_words=custom, allow_short=True) is True
