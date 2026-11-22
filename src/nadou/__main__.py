import sys

from nadou.admin.create_admin import CreateAdminCommand
from nadou.admin.indexing import IndexRunCommand
from nadou.review import ReviewExportCommand


class DemoCommand:
    @staticmethod
    def run() -> int:
        from alembic import command
        from alembic.config import Config

        from nadou.config import get_settings
        from nadou.db import Database
        from nadou.text import RecordAge

        settings = get_settings()
        if not settings.allow_dev_seed:
            print("Refusing to seed demo data. Set NADOU_ALLOW_DEV_SEED=1 for a local database.")
            return 2
        command.upgrade(Config("alembic.ini"), "head")
        from datetime import datetime

        from nadou.admin.demo_data import DemoData

        database = Database(settings.database_url)
        session = database.session()
        now = datetime.now(RecordAge.TZ)
        try:
            if settings.data_source == "export":
                DemoData.ensure_admin(session, now)
                session.commit()
                print("export mode: demo business rows were not loaded")
                return 0
            if DemoData.already_loaded(session):
                print("demo data already present")
                return 0
            DemoData.load(session, now)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            database.engine.dispose()
        print("demo admin ready")
        return 0


class NadouCommand:
    @staticmethod
    def main(argv: list[str] | None = None) -> None:
        args = list(sys.argv[1:] if argv is None else argv)
        if args == ["index-run"]:
            raise SystemExit(IndexRunCommand.run())
        if args == ["seed-demo"]:
            raise SystemExit(DemoCommand.run())
        if args == ["export-review"]:
            raise SystemExit(ReviewExportCommand.run())
        if args[:1] == ["create-admin"]:
            raise SystemExit(CreateAdminCommand.run(args[1:]))
        raise SystemExit(
            "usage: python -m nadou index-run | python -m nadou seed-demo | "
            "python -m nadou export-review | python -m nadou create-admin USERNAME"
        )


if __name__ == "__main__":
    NadouCommand.main()
