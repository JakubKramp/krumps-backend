from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.security import get_password_hash
from auth.models import User
from recipes.models import Dish, Ingredient
from recipes.nutritional_data import NutritionalAPIClient
from recipes.tests.test_data.example_data import (
    example_create_dish,
    example_ingredient,
    example_tag,
)


@pytest.fixture(name="ingredient")
def ingredient_fixture():
    return example_ingredient.copy()


@pytest_asyncio.fixture(name="db_ingredient")
async def database_ingredient_fixture(session: AsyncSession) -> Ingredient:
    db_ingredient = Ingredient(**example_ingredient)
    session.add(db_ingredient)
    await session.commit()
    await session.refresh(db_ingredient)
    return db_ingredient


@pytest.fixture(autouse=True)
def mock_nutri_client():
    with patch.object(NutritionalAPIClient, "fill_nutritional_values", new_callable=AsyncMock) as mock:
        mock.return_value = None
        yield mock


@pytest_asyncio.fixture(name="auth_headers")
async def auth_headers_fixture(client: AsyncClient, user: User) -> dict[str, str]:
    """Bearer header for the `user` fixture -- creating a dish requires authentication."""
    response = await client.post(
        "/user/login",
        data={"username": user.username, "password": "test_password"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest_asyncio.fixture(name="other_user")
async def other_user_fixture(session: AsyncSession) -> User:
    """A second account, for asserting that ownership checks actually bite."""
    other = User(
        username="otheruser",
        password=get_password_hash("test_password"),
        email="other@test.com",
    )
    session.add(other)
    await session.commit()
    await session.refresh(other)
    return other


@pytest_asyncio.fixture(name="other_auth_headers")
async def other_auth_headers_fixture(client: AsyncClient, other_user: User) -> dict[str, str]:
    response = await client.post(
        "/user/login",
        data={"username": other_user.username, "password": "test_password"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest_asyncio.fixture(name="db_dish")
async def database_dish_fixture(session: AsyncSession) -> Dish:
    dish = Dish(name="Mashed potatoes")
    session.add(dish)
    await session.commit()
    await session.refresh(dish)
    return dish


@pytest.fixture(name="create_dish")
def create_dish_fixture():
    return example_create_dish


@pytest.fixture(name="tag")
def create_tag_fixture():
    return example_tag
