from typing import Callable, Optional

from fastapi import Security
from fastapi_azure_auth import SingleTenantAzureAuthorizationCodeBearer
from fastapi_azure_auth.user import User

type AuthSchema = Callable[..., Optional[User]]


def create_auth_scheme(azure_scheme: SingleTenantAzureAuthorizationCodeBearer, disable_auth: bool) -> AuthSchema:
    """
    Create an authentication scheme for the API.

    Args:
        azure_scheme: The Azure AD authentication scheme
        disable_auth: If True, authentication is disabled and always returns None

    Returns:
        A callable that returns the authenticated user or None
    """
    if disable_auth:

        def no_auth() -> Optional[User]:
            return None

        return no_auth
    else:
        # ruff: noqa: B008
        def auth(user: User = Security(azure_scheme)) -> Optional[User]:
            return user

        return auth
