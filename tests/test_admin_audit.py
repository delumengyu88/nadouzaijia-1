import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError

from nadou.admin.audit import AuditWriter
from nadou.models import AppendOnlyViolation, AuditLog


def test_audit_log_is_append_only(session, clock):
    AuditWriter(session, clock, None, "192.0.2.9").write(
        "user",
        "user-1",
        "ban_user",
        None,
        {"ban_type": "permanent"},
    )
    row = session.get(AuditLog, 1)
    row.action = "tamper"
    with pytest.raises(AppendOnlyViolation):
        session.flush()
    session.rollback()
    AuditWriter(session, clock, None, "192.0.2.9").write(
        "user",
        "user-1",
        "ban_user",
        None,
        {"ban_type": "permanent"},
    )
    with pytest.raises(DatabaseError):
        session.execute(text("UPDATE audit_logs SET action = 'tamper'"))
    session.rollback()
    AuditWriter(session, clock, None, "192.0.2.9").write(
        "user",
        "user-1",
        "ban_user",
        None,
        {"ban_type": "permanent"},
    )
    with pytest.raises(DatabaseError):
        session.execute(text("DELETE FROM audit_logs"))
    session.rollback()
    AuditWriter(session, clock, None, "192.0.2.9").write(
        "user",
        "user-1",
        "ban_user",
        None,
        {"ban_type": "permanent"},
    )
    saved = session.scalars(select(AuditLog)).one()
    with pytest.raises(AppendOnlyViolation):
        session.delete(saved)
        session.flush()
    session.rollback()
