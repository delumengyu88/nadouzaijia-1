import json
from pathlib import Path

import pytest

from nadou.config import get_settings
from nadou.program_titles import ProgramTitleGuard, load_stop_words


def test_approved_batch_is_exact_and_shared_with_miniprogram():
    words = ProgramTitleGuard.stop_words()
    assert len(words) == 102
    assert words == frozenset(json.loads(Path("miniprogram/stop-words.json").read_text()))
    for word in words:
        assert not ProgramTitleGuard.is_billable("　" + word + " ")
        assert ProgramTitleGuard.is_billable(word + "测试片")
    for title in ("家庭", "远方", "狗", "父亲", "母亲", "甲乙", "日期", "名称"):
        assert ProgramTitleGuard.is_billable(title)


def test_configured_words_are_normalized_whole_words(tmp_path, monkeypatch):
    config = tmp_path / "words.json"
    config.write_text(json.dumps([" ＡＢＣ ", "测试词"]))
    monkeypatch.setenv("NADOU_STOP_WORDS_PATH", str(config))
    get_settings.cache_clear()
    try:
        assert not ProgramTitleGuard.is_billable("abc")
        assert not ProgramTitleGuard.is_billable("测试词")
        assert ProgramTitleGuard.is_billable("测试词节目")
        assert ProgramTitleGuard.is_billable("家庭")
    finally:
        get_settings.cache_clear()
        load_stop_words.cache_clear()


@pytest.mark.parametrize("payload", ["{}", "[1]", '"怪"', "broken"])
def test_invalid_configuration_is_not_silently_ignored(tmp_path, payload):
    config = tmp_path / "words.json"
    config.write_text(payload)
    with pytest.raises(ValueError):
        load_stop_words(str(config))
