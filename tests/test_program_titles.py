from nadou.program_titles import ProgramTitleGuard


def test_default_stop_words_are_empty():
    ProgramTitleGuard.set_stop_words(frozenset())
    assert ProgramTitleGuard.stop_words() == frozenset()
    assert ProgramTitleGuard.is_billable("狗") is True
    assert ProgramTitleGuard.is_billable("甲") is True
    assert ProgramTitleGuard.is_billable("西游记") is True


def test_configured_stop_words_block_matches():
    words = frozenset({"家庭", "狗", "怪", "远方"})
    for word in words:
        assert ProgramTitleGuard.is_billable(word, stop_words=words) is False
    assert ProgramTitleGuard.is_billable("西游记", stop_words=words) is True
    assert ProgramTitleGuard.is_billable("甲乙", stop_words=words) is True
