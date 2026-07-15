"""Authentication service."""
from models.user import User, find_user
from utils.helpers import hash_password, validate_email


class AuthService:
    """Handles all authentication logic."""

    def __init__(self):
        self.sessions = {}
        self.max_attempts = 3

    def authenticate(self, username: str, password: str) -> bool:
        user = find_user(username)
        if user and user.check_password(hash_password(password)):
            self._create_session(user)
            return True
        return False

    def _create_session(self, user: User) -> str:
        import uuid
        session_id = str(uuid.uuid4())
        self.sessions[session_id] = user
        return session_id

    def logout(self, session_id: str) -> None:
        if session_id in self.sessions:
            del self.sessions[session_id]

    def get_current_user(self, session_id: str) -> User | None:
        return self.sessions.get(session_id)

    def refresh_session(self, session_id: str) -> bool:
        if session_id in self.sessions:
            return True
        return False

    def change_password(self, user: User, old_pw: str, new_pw: str) -> bool:
        if user.check_password(hash_password(old_pw)):
            user.set_password(hash_password(new_pw))
            return True
        return False
