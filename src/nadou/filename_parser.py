import re
from dataclasses import dataclass


@dataclass(frozen=True)
class FilenameParts:
    network_type: str | None
    platform: str | None


class FilenameParser:
    """Split a queue filename into network type and platform.

    Real names lead with 公网 or 专网, then the platform, then a title and a date.
    Early names such as ``unknown.mp4``, ``1905.mp4``, and ``VID_...`` do not.
    """

    NETWORKS = ("公网", "专网")
    _EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,8}$")
    _DATE = re.compile(r"\d{8}$|\d{4}[.\-]\d{2}[.\-]\d{2}$")

    @staticmethod
    def parse(filename: str) -> FilenameParts:
        base = FilenameParser._EXTENSION.sub("", filename.strip())
        network: str | None = None
        rest = base
        for name in FilenameParser.NETWORKS:
            if base.startswith(name):
                network = name
                rest = base[len(name) :]
                break
        if network is None:
            return FilenameParts(None, None)
        rest = rest.lstrip("-_\t ")
        if not rest:
            return FilenameParts(network, None)
        token = re.split(r"[-\s]+", rest, maxsplit=1)[0].strip("._")
        if not token or FilenameParser._DATE.fullmatch(token):
            return FilenameParts(network, None)
        return FilenameParts(network, token)
