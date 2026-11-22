from nadou.text import TextNormalizer


class PlatformResolution:
    def __init__(
        self,
        province: str | None,
        operator: str | None,
        subtype: str | None,
        unrecognized: bool,
    ) -> None:
        self.province = province
        self.operator = operator
        self.subtype = subtype
        self.unrecognized = unrecognized

    @property
    def name(self) -> str | None:
        if self.unrecognized or not self.operator:
            return None
        if self.province:
            return f"{self.province}{self.operator}"
        return self.operator

    def count_key(self) -> tuple[str, str] | None:
        if self.unrecognized or not self.operator:
            return None
        return (self.province or "", self.operator)


class PlatformNames:
    """Split a platform into province, operator, and a display-only subtype.

    The map is code, not a database table. 公网/专网, OTT, and 百视通 are tags.
    They do not change the province+operator pair used for 涉及 N 个平台.
    """

    UNRECOGNIZED = "平台未识别"
    _NAME_MAP: tuple[tuple[str, str], ...] = (("gd移动", "广东移动"), ("示例视频平台", "示例视频"))
    _SUBTYPES: tuple[tuple[str, str], ...] = (("百视通", "百视通"), ("ott", "OTT"))
    _NETWORK_TAGS = ("专网", "公网")
    _OPERATORS = ("移动", "联通", "电信", "广电")
    _STANDALONE = ("示例视频", "示例专网")
    _PROVINCES = (
        "黑龙江",
        "内蒙古",
        "北京",
        "天津",
        "上海",
        "重庆",
        "河北",
        "山西",
        "辽宁",
        "吉林",
        "江苏",
        "浙江",
        "安徽",
        "福建",
        "江西",
        "山东",
        "河南",
        "湖北",
        "湖南",
        "广东",
        "海南",
        "四川",
        "贵州",
        "云南",
        "陕西",
        "甘肃",
        "青海",
        "台湾",
        "广西",
        "西藏",
        "宁夏",
        "新疆",
        "香港",
        "澳门",
    )

    @staticmethod
    def normalize(raw: str | None) -> PlatformResolution:
        text = PlatformNames._prepare(raw)
        if not text:
            return PlatformNames._unknown()
        subtype, body = PlatformNames._strip_tags(text)
        parsed = PlatformNames._split_operator(body)
        if parsed is None:
            return PlatformNames._unknown()
        province, operator = parsed
        return PlatformResolution(province, operator, subtype, False)

    @staticmethod
    def _prepare(raw: str | None) -> str:
        if raw is None:
            return ""
        folded = TextNormalizer.fold(raw)
        if not folded:
            return ""
        for source, canonical in PlatformNames._NAME_MAP:
            if folded == TextNormalizer.fold(source):
                return TextNormalizer.fold(canonical)
        return folded

    @staticmethod
    def _strip_tags(text: str) -> tuple[str | None, str]:
        tags: list[str] = []
        while True:
            matched = False
            for needle, label in PlatformNames._SUBTYPES:
                if text.endswith(needle) and len(text) > len(needle):
                    tags.append(label)
                    text = text[: -len(needle)]
                    matched = True
                    break
            if not matched:
                break
        for tag in PlatformNames._NETWORK_TAGS:
            if text.startswith(tag) and len(text) > len(tag):
                text = text[len(tag) :]
                break
        subtype = "".join(reversed(tags)) if tags else None
        return subtype, text

    @staticmethod
    def _split_operator(text: str) -> tuple[str | None, str] | None:
        for province in PlatformNames._PROVINCES:
            if not text.startswith(province) or len(text) == len(province):
                continue
            operator = text[len(province) :]
            if operator in PlatformNames._OPERATORS:
                return province, operator
        if text in PlatformNames._OPERATORS or text in PlatformNames._STANDALONE:
            return None, text
        return None

    @staticmethod
    def _unknown() -> PlatformResolution:
        return PlatformResolution(None, None, None, True)
