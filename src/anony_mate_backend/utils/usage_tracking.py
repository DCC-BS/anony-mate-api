import hashlib
import hmac

from fastapi_azure_auth.user import User


def get_pseudonymized_user_id(user: User | None, secret_key: str) -> str:
    """
    Generates a consistent, one-way pseudonym for a given user ID.

    This allows tracking user activity for analytics without exposing actual user IDs.

    Args:
        user: The authenticated user object
        secret_key: HMAC secret key for pseudonymization

    Returns:
        A pseudonymized user ID string

    Raises:
        ValueError: If user ID is not found or secret key is not set
    """
    if user is None:
        return "Not authenticated"

    user_id = user.oid or user.sub
    if user_id is None:
        raise ValueError("User ID (oid or sub) not found in user object")
    message = user_id.encode("utf-8")
    if not secret_key or secret_key == "none":
        raise ValueError("HMAC secret is not set")
    signature = hmac.new(secret_key.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return signature
