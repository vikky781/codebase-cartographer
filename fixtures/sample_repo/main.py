"""Main entry point for the sample app."""
from auth.routes import login, logout
from api.handlers import handle_request


def main():
    """Start the application."""
    login("admin", "password")
    handle_request("/users")
    print("App started")


if __name__ == "__main__":
    main()
