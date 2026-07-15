"""API request handlers."""
from auth.service import AuthService
from models.user import User, find_user, list_all_users

_auth = AuthService()


def handle_request(path: str) -> dict:
    if path == "/users":
        return {"users": list_all_users()}
    elif path == "/login":
        return {"message": "use POST"}
    return {"error": "not found"}


def handle_admin_request(path: str, session_id: str) -> dict:
    user = _auth.get_current_user(session_id)
    if user and user.name == "admin":
        return {"admin": True, "path": path}
    return {"error": "unauthorized"}
