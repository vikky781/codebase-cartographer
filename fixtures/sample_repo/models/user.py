"""User model."""
from utils.helpers import validate_email


class User:
    """Represents a user in the system."""
    def __init__(self, name: str, email: str, password_hash: str):
        self.name = name
        self.email = email
        self.password_hash = password_hash
        if not validate_email(email):
            raise ValueError(f"Invalid email: {email}")

    def check_password(self, password_hash: str) -> bool:
        return self.password_hash == password_hash

    def set_password(self, new_hash: str) -> None:
        self.password_hash = new_hash

    def to_dict(self) -> dict:
        return {"name": self.name, "email": self.email}


_users_db = [
    User("Alice", "alice@example.com", "hash1"),
    User("Bob", "bob@example.com", "hash2"),
]


def find_user(username: str) -> User | None:
    for user in _users_db:
        if user.name == username:
            return user
    return None


def list_all_users() -> list:
    return [u.to_dict() for u in _users_db]
