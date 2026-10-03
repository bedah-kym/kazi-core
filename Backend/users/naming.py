"""Reserved account names.

The assistant identity (``kazi``) must never be claimable by registration —
otherwise a person could register as the bot and inherit its room identity.
"""
RESERVED_USERNAMES = {"kazi"}

RESERVED_USERNAME_MESSAGE = "That username is reserved."


def is_reserved_username(username: str) -> bool:
    return (username or "").strip().lower() in RESERVED_USERNAMES
