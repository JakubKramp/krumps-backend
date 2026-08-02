from datetime import datetime, timedelta

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.utils.db import get_session
from auth.exceptions import credentials_exception
from auth.models import User
from config import settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/user/login")
optional_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/user/login", auto_error=False)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password, hashed_password) -> bool:
    """
    Verifies if the password is correct.
    """
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password) -> str:
    """
    Returns the password hash.
    """
    return pwd_context.hash(password)


async def get_user(session: AsyncSession, username: str) -> User | None:
    """
    Returns the user from the database, or None if the user does not exist.
    """
    result = await session.scalars(select(User).where(User.username == username))
    return result.first()


async def authenticate_user(username: str, password: str, session: AsyncSession) -> User | None:
    """
    Returns user from the database, or None if the user does not exist.
    """
    user = await get_user(session, username)
    if not user:
        return None
    if not verify_password(password, user.password):
        return None
    return user


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """
    Encodes username in jwt. By default, the lifetime is defined in settings.
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now() + expires_delta
    else:
        expire = datetime.now() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


async def get_current_user(
    token: str = Depends(oauth2_scheme), session: AsyncSession = Depends(get_session)
) -> User | None:
    """
    Extracts user from encoded JWT.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    result = await session.scalars(
        select(User).where(User.username == username).options(selectinload(User.profile))
    )
    user = result.first()
    return user


async def get_current_user_optional(
    token: str = Depends(optional_oauth2_scheme), session: AsyncSession = Depends(get_session)
) -> User | None:
    if token:
        return await get_current_user(token, session)
    else:
        return None
