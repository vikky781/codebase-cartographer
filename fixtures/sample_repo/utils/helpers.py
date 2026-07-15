"""Utility functions."""
import re
from models.user import User


def hash_password(password: str) -> str:
    import hashlib
    return hashlib.sha256(password.encode()).hexdigest()


def validate_email(email: str) -> bool:
    pattern = r'^[\w\.-]+@[\w\.-]+\.\w+$'
    return bool(re.match(pattern, email))


def format_user_display(user: User) -> str:
    return f"{user.name} <{user.email}>"


def generate_token(length: int = 32) -> str:
    import secrets
    return secrets.token_hex(length)


def unused_helper_function() -> None:
    """This function is never called by anything. It should be detected as dead code."""
    print("I am never called")


def another_unused_function(x: int) -> int:
    """Also never called."""
    return x * 2
