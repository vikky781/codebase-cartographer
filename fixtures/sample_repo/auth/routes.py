"""Auth route handlers."""
from auth.service import AuthService

_service = AuthService()


def login(username: str, password: str) -> dict:
    success = _service.authenticate(username, password)
    return {"success": success}


def logout(session_id: str) -> dict:
    _service.logout(session_id)
    return {"logged_out": True}


def get_profile(session_id: str) -> dict:
    user = _service.get_current_user(session_id)
    if user:
        return {"name": user.name, "email": user.email}
    return {"error": "not authenticated"}
