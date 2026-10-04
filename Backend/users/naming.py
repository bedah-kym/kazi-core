"""Reserved account names.

The assistant identity (``kazi``) must never be claimable by registration —
otherwise a person could register as the bot and inherit its room identity.
"""
RESERVED_USERNAMES = {"kazi"}

RESERVED_USERNAME_MESSAGE = "That username is reserved."


def is_reserved_username(username: str) -> bool:
    return (username or "").strip().lower() in RESERVED_USERNAMES


def account_has_owner(user) -> bool:
    """True when an account shows signs of a person behind it.

    The assistant's account is created without a password and never logs in.
    An account that holds a reserved name but has an owner predates the
    reservation and must not be adopted as the assistant.
    """
    password = getattr(user, "password", "") or ""
    return bool(
        (password and not password.startswith("!"))
        or getattr(user, "last_login", None)
        or getattr(user, "is_staff", False)
        or getattr(user, "is_superuser", False)
    )
