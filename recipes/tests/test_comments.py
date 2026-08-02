import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.models import User
from recipes.models import Comment, Dish


async def post_comment(
    client: AsyncClient, dish_id: int, headers: dict[str, str], body: str, parent_id: int | None = None
):
    payload: dict = {"body": body}
    if parent_id is not None:
        payload["parent_id"] = parent_id
    return await client.post(f"/ingredients/dish/{dish_id}/comments", json=payload, headers=headers)


@pytest.mark.asyncio
async def test_create_comment(
    session: AsyncSession, client: AsyncClient, db_dish: Dish, user: User, auth_headers: dict[str, str]
):
    response = await post_comment(client, db_dish.id, auth_headers, "Lovely recipe")

    assert response.status_code == 201
    data = response.json()
    assert data["body"] == "Lovely recipe"
    assert data["author_id"] == user.id
    assert data["parent_id"] is None
    assert data["replies"] == []

    assert await session.scalar(select(func.count(Comment.id))) == 1


@pytest.mark.asyncio
async def test_create_comment_unauthenticated(
    session: AsyncSession, client: AsyncClient, db_dish: Dish
):
    response = await client.post(
        f"/ingredients/dish/{db_dish.id}/comments", json={"body": "Lovely recipe"}
    )

    assert response.status_code == 401
    assert await session.scalar(select(func.count(Comment.id))) == 0


@pytest.mark.asyncio
async def test_create_comment_dish_does_not_exist(client: AsyncClient, auth_headers: dict[str, str]):
    response = await post_comment(client, 999, auth_headers, "Lovely recipe")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_comment_rejects_empty_body(
    client: AsyncClient, db_dish: Dish, auth_headers: dict[str, str]
):
    response = await post_comment(client, db_dish.id, auth_headers, "")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_reply(client: AsyncClient, db_dish: Dish, auth_headers: dict[str, str]):
    parent = await post_comment(client, db_dish.id, auth_headers, "Lovely recipe")
    parent_id = parent.json()["id"]

    response = await post_comment(client, db_dish.id, auth_headers, "Agreed", parent_id=parent_id)

    assert response.status_code == 201
    assert response.json()["parent_id"] == parent_id


@pytest.mark.asyncio
async def test_reply_to_a_reply_attaches_to_the_root(
    client: AsyncClient, db_dish: Dish, auth_headers: dict[str, str]
):
    """Threads are capped at one level: replying to a reply re-points at its parent."""
    root_id = (await post_comment(client, db_dish.id, auth_headers, "Lovely recipe")).json()["id"]
    reply_id = (
        await post_comment(client, db_dish.id, auth_headers, "Agreed", parent_id=root_id)
    ).json()["id"]

    response = await post_comment(client, db_dish.id, auth_headers, "Me too", parent_id=reply_id)

    assert response.status_code == 201
    # Not reply_id -- it was collapsed onto the thread root.
    assert response.json()["parent_id"] == root_id


@pytest.mark.asyncio
async def test_create_reply_parent_does_not_exist(
    client: AsyncClient, db_dish: Dish, auth_headers: dict[str, str]
):
    response = await post_comment(client, db_dish.id, auth_headers, "Agreed", parent_id=999)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_reply_parent_belongs_to_another_dish(
    session: AsyncSession, client: AsyncClient, db_dish: Dish, auth_headers: dict[str, str]
):
    other_dish = Dish(name="Roast potatoes")
    session.add(other_dish)
    await session.commit()
    await session.refresh(other_dish)

    parent_id = (await post_comment(client, db_dish.id, auth_headers, "Lovely recipe")).json()["id"]

    response = await post_comment(client, other_dish.id, auth_headers, "Agreed", parent_id=parent_id)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_dish_detail_nests_comments(
    client: AsyncClient, db_dish: Dish, auth_headers: dict[str, str]
):
    root_id = (await post_comment(client, db_dish.id, auth_headers, "Lovely recipe")).json()["id"]
    await post_comment(client, db_dish.id, auth_headers, "Agreed", parent_id=root_id)
    await post_comment(client, db_dish.id, auth_headers, "Separate thought")

    response = await client.get(f"/ingredients/dish/{db_dish.id}")

    assert response.status_code == 200
    comments = response.json()["comments"]
    # Only top-level comments appear at the top; the reply is nested under its parent.
    assert [c["body"] for c in comments] == ["Lovely recipe", "Separate thought"]
    assert [r["body"] for r in comments[0]["replies"]] == ["Agreed"]
    assert comments[1]["replies"] == []


@pytest.mark.asyncio
async def test_dish_detail_comments_are_public(
    client: AsyncClient, db_dish: Dish, auth_headers: dict[str, str]
):
    await post_comment(client, db_dish.id, auth_headers, "Lovely recipe")

    response = await client.get(f"/ingredients/dish/{db_dish.id}")

    assert response.status_code == 200
    assert len(response.json()["comments"]) == 1


@pytest.mark.asyncio
async def test_dish_detail_without_comments(client: AsyncClient, db_dish: Dish):
    response = await client.get(f"/ingredients/dish/{db_dish.id}")

    assert response.status_code == 200
    assert response.json()["comments"] == []


@pytest.mark.asyncio
async def test_dish_detail_excludes_other_dishes_comments(
    session: AsyncSession, client: AsyncClient, db_dish: Dish, auth_headers: dict[str, str]
):
    other_dish = Dish(name="Roast potatoes")
    session.add(other_dish)
    await session.commit()
    await session.refresh(other_dish)

    await post_comment(client, db_dish.id, auth_headers, "On the mash")
    await post_comment(client, other_dish.id, auth_headers, "On the roast")

    response = await client.get(f"/ingredients/dish/{db_dish.id}")

    assert [c["body"] for c in response.json()["comments"]] == ["On the mash"]


@pytest.mark.asyncio
async def test_dish_add_tag_returns_comments(
    client: AsyncClient, db_dish: Dish, tag: dict, auth_headers: dict[str, str]
):
    """DishDetail is shared by several routes -- comments must be populated in all of them."""
    await post_comment(client, db_dish.id, auth_headers, "Lovely recipe")

    response = await client.post(f"/ingredients/dish/{db_dish.id}/tag", json=tag)

    assert response.status_code == 200
    assert [c["body"] for c in response.json()["comments"]] == ["Lovely recipe"]


@pytest.mark.asyncio
async def test_dish_toggle_favorite_returns_comments(
    client: AsyncClient, db_dish: Dish, auth_headers: dict[str, str]
):
    root_id = (await post_comment(client, db_dish.id, auth_headers, "Lovely recipe")).json()["id"]
    await post_comment(client, db_dish.id, auth_headers, "Agreed", parent_id=root_id)

    response = await client.post(f"/ingredients/dish/{db_dish.id}/favorite", headers=auth_headers)

    assert response.status_code == 200
    comments = response.json()["comments"]
    assert [c["body"] for c in comments] == ["Lovely recipe"]
    assert [r["body"] for r in comments[0]["replies"]] == ["Agreed"]


@pytest.mark.asyncio
async def test_delete_comment(
    session: AsyncSession, client: AsyncClient, db_dish: Dish, auth_headers: dict[str, str]
):
    comment_id = (await post_comment(client, db_dish.id, auth_headers, "Lovely recipe")).json()["id"]

    response = await client.delete(f"/ingredients/comment/{comment_id}", headers=auth_headers)

    assert response.status_code == 204
    assert await session.scalar(select(func.count(Comment.id))) == 0


@pytest.mark.asyncio
async def test_delete_comment_cascades_to_replies(
    session: AsyncSession, client: AsyncClient, db_dish: Dish, auth_headers: dict[str, str]
):
    root_id = (await post_comment(client, db_dish.id, auth_headers, "Lovely recipe")).json()["id"]
    await post_comment(client, db_dish.id, auth_headers, "Agreed", parent_id=root_id)

    response = await client.delete(f"/ingredients/comment/{root_id}", headers=auth_headers)

    assert response.status_code == 204
    assert await session.scalar(select(func.count(Comment.id))) == 0


@pytest.mark.asyncio
async def test_delete_comment_requires_authentication(
    client: AsyncClient, db_dish: Dish, auth_headers: dict[str, str]
):
    comment_id = (await post_comment(client, db_dish.id, auth_headers, "Lovely recipe")).json()["id"]

    response = await client.delete(f"/ingredients/comment/{comment_id}")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_delete_comment_of_another_user(
    session: AsyncSession,
    client: AsyncClient,
    db_dish: Dish,
    auth_headers: dict[str, str],
    other_auth_headers: dict[str, str],
):
    comment_id = (await post_comment(client, db_dish.id, auth_headers, "Lovely recipe")).json()["id"]

    response = await client.delete(f"/ingredients/comment/{comment_id}", headers=other_auth_headers)

    assert response.status_code == 403
    assert await session.scalar(select(func.count(Comment.id))) == 1


@pytest.mark.asyncio
async def test_delete_comment_does_not_exist(client: AsyncClient, auth_headers: dict[str, str]):
    response = await client.delete("/ingredients/comment/999", headers=auth_headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_deleting_a_dish_removes_its_comments(
    session: AsyncSession, client: AsyncClient, db_dish: Dish, auth_headers: dict[str, str]
):
    root_id = (await post_comment(client, db_dish.id, auth_headers, "Lovely recipe")).json()["id"]
    await post_comment(client, db_dish.id, auth_headers, "Agreed", parent_id=root_id)

    response = await client.delete(f"/ingredients/dish/{db_dish.id}")

    assert response.status_code == 204
    assert await session.scalar(select(func.count(Comment.id))) == 0
