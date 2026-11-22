"""Test-only sessions: no identity bypass is registered in the application."""

from fastapi.testclient import TestClient

from nadou.auth import UserSessions
from nadou.models import User


class UserClient(TestClient):
    def __init__(self, app, session, clock, *, default_user=False):
        super().__init__(app)
        self.db_session = session
        self.clock = clock
        if default_user:
            session.add(User(id="test-default-free", created_at=clock))
            session.commit()
            self.headers.update(self.auth("test-default-free"))

    def auth(self, user_id):
        if user_id is None:
            return {"Authorization": ""}
        user = self.db_session.get(User, user_id)
        if user is None:
            return {"Authorization": "Bearer test-invalid-session"}
        token = UserSessions.issue(self.db_session, user, self.clock)
        self.db_session.commit()
        return {"Authorization": "Bearer " + token}
