from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.models import User
from recipes.models import Dish, Ingredient, IngredientItem


@pytest.mark.asyncio
async def test_get_ingredient(session: AsyncSession, client: AsyncClient, db_ingredient: Ingredient):
    ingredient = await session.scalar(select(Ingredient).limit(1))
    assert ingredient is not None
    response = await client.get(f"/ingredients/{ingredient.id}")
    assert db_ingredient.id == ingredient.id
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_ingredient_does_not_exist(client: AsyncClient):
    response = await client.get("/ingredients/1")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_ingredients(
    session: AsyncSession, client: AsyncClient, db_ingredient: Ingredient, mock_nutri_client: AsyncMock
):
    db_ingredient1 = Ingredient(name="broccoli")
    session.add(db_ingredient1)
    await session.commit()
    response = await client.get("/ingredients/")
    data = response.json()
    assert response.status_code == 200
    assert len(data) == 2


@pytest.mark.asyncio
async def test_delete_ingredient(client: AsyncClient, db_ingredient: Ingredient):
    response = await client.delete("/ingredients/1")
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_delete_ingredient_does_not_exist(client: AsyncClient):
    response = await client.delete("/ingredients/1")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_dish(
    session: AsyncSession, client: AsyncClient, create_dish: dict, auth_headers: dict[str, str]
):
    response = await client.post("/ingredients/dish/", json=create_dish, headers=auth_headers)
    dish_count = await session.scalar(select(func.count(Dish.id)))
    assert dish_count == 1
    ingredient_item_count = await session.scalar(select(func.count(IngredientItem.id)))
    assert ingredient_item_count == 2
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_create_dish_unauthenticated(
    session: AsyncSession, client: AsyncClient, create_dish: dict
):
    response = await client.post("/ingredients/dish/", json=create_dish)
    assert response.status_code == 401
    assert await session.scalar(select(func.count(Dish.id))) == 0


@pytest.mark.asyncio
async def test_create_dish_sets_author(
    session: AsyncSession,
    client: AsyncClient,
    create_dish: dict,
    user: User,
    auth_headers: dict[str, str],
):
    response = await client.post("/ingredients/dish/", json=create_dish, headers=auth_headers)
    assert response.status_code == 201
    assert response.json()["author_id"] == user.id

    dish = await session.scalar(select(Dish).limit(1))
    assert dish is not None
    assert dish.author_id == user.id


@pytest.mark.asyncio
async def test_create_dish_populates_user_recipes(
    session: AsyncSession,
    client: AsyncClient,
    create_dish: dict,
    user: User,
    auth_headers: dict[str, str],
):
    await client.post("/ingredients/dish/", json=create_dish, headers=auth_headers)

    await session.refresh(user, ["recipes"])
    assert [dish.name for dish in user.recipes] == [create_dish["name"]]


@pytest.mark.asyncio
async def test_list_dishes(client: AsyncClient, create_dish, auth_headers: dict[str, str]):
    await client.post("/ingredients/dish/", json=create_dish, headers=auth_headers)
    response = await client.get("/ingredients/dish/")
    assert len(response.json()) == 1
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_list_dishes_without_author(session: AsyncSession, client: AsyncClient):
    """Dishes created before authorship existed have a NULL author_id and must still serialize."""
    session.add(Dish(name="orphan dish"))
    await session.commit()

    response = await client.get("/ingredients/dish/")
    assert response.status_code == 200
    assert response.json()[0]["author_id"] is None


@pytest.mark.asyncio
async def test_delete_dish(client: AsyncClient, create_dish, auth_headers: dict[str, str]):
    await client.post("/ingredients/dish/", json=create_dish, headers=auth_headers)
    response = await client.delete("/ingredients/dish/1")
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_delete_dish_does_not_exist(client: AsyncClient):
    response = await client.delete("/ingredients/dish/1")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_dish_detail(client: AsyncClient, create_dish, auth_headers: dict[str, str]):
    await client.post("/ingredients/dish/", json=create_dish, headers=auth_headers)
    response = await client.get("/ingredients/dish/1")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_dish_detail_does_not_exist(client: AsyncClient):
    response = await client.get("/ingredients/dish/1")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_dish_add_tag(
    client: AsyncClient, create_dish: dict, tag: dict, auth_headers: dict[str, str]
):
    await client.post("/ingredients/dish/", json=create_dish, headers=auth_headers)
    response = await client.post("/ingredients/dish/1/tag", json=tag)
    assert response.json()["tags"][0]["name"] == tag["name"]
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_dish_add_to_favorites_unauthenticated(
    session: AsyncSession, client: AsyncClient, create_dish: dict, auth_headers: dict[str, str]
):
    await client.post("/ingredients/dish/", json=create_dish, headers=auth_headers)
    response = await client.post("/ingredients/dish/1/favorite")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_dish_add_to_favorites(
    client: AsyncClient, create_dish: dict, user: User, auth_headers: dict[str, str]
):
    await client.post("/ingredients/dish/", json=create_dish, headers=auth_headers)
    response = await client.post("/ingredients/dish/1/favorite", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["is_favorite"] == True


@pytest.mark.asyncio
async def test_dish_remove_from_favorites(
    client: AsyncClient, create_dish: dict, user: User, auth_headers: dict[str, str]
):
    await client.post("/ingredients/dish/", json=create_dish, headers=auth_headers)
    await client.post("/ingredients/dish/1/favorite", headers=auth_headers)
    response = await client.post("/ingredients/dish/1/favorite", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["is_favorite"] == False
