import sys

from nadou.admin.indexing import IndexRunCommand


class NadouCommand:
    @staticmethod
    def main(argv: list[str] | None = None) -> None:
        args = list(sys.argv[1:] if argv is None else argv)
        if args == ["index-run"]:
            raise SystemExit(IndexRunCommand.run())
        raise SystemExit("usage: python -m nadou index-run")


if __name__ == "__main__":
    NadouCommand.main()
