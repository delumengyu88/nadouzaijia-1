import pytest

from nadou.filename_parser import FilenameParser


@pytest.mark.parametrize(
    ("filename", "network", "platform"),
    [
        ("专网-河南联通-动漫-20260907", "专网", "河南联通"),
        ("公网-小米视频-电影1-2026.09.23", "公网", "小米视频"),
        ("专网 广东移动 电视剧20260706_100010", "专网", "广东移动"),
        ("公网-哈趣影视-电影-2026.08.26", "公网", "哈趣影视"),
        ("公网-示例视频-电影-2026.05.18.mp4", "公网", "示例视频"),
        ("unknown.mp4", None, None),
        ("1905.mp4", None, None),
        ("VID_20260907.mp4", None, None),
        ("VID_abc", None, None),
        ("公网-20260907-电影.mp4", "公网", None),
        ("公网-", "公网", None),
    ],
)
def test_parse_known_shapes(filename, network, platform):
    parsed = FilenameParser.parse(filename)
    assert parsed.network_type == network
    assert parsed.platform == platform
