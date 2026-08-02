from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.responses import JSONResponse, Response

from app.security import get_current_user, get_current_user_optional
from app.utils.db import get_session
from auth.models import User
from recipes.models import (
    Comment,
    Dish,
    Image,
    Ingredient,
    IngredientItem,
    Tag,
)
from recipes.nutritional_data import NutritionalAPIClient
from recipes.schemas import (
    CommentDetail,
    CreateComment,
    CreateDish,
    CreateIngredient,
    CreateTag,
    DishDetail,
    DishFilterParams,
    ImageDetail,
    ListDish,
    ListIngredient,
    NutritionalValues,
    UpdateIngredient,
)

ingredient_router = APIRouter(prefix="/ingredients", tags=["ingredients"])


async def dish_comments(dish_id: int, session: AsyncSession) -> list[Comment]:
    """
    A dish's top-level comments in thread order, each with its replies loaded.

    Assembled here rather than read off Dish.all_comments, which is a flat list of
    every comment and whose replies are lazy -- serialising that would emit IO
    mid-response and blow up under async.
    """
    result = await session.scalars(
        select(Comment)
        .where(Comment.dish_id == dish_id, Comment.parent_id.is_(None))
        .options(selectinload(Comment.replies))
        .order_by(Comment.created_at, Comment.id)
    )
    return list(result.all())


@ingredient_router.post("/", response_model=ListIngredient, status_code=201)
async def create_ingredient(
    ingredient: CreateIngredient,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    result = await session.scalar(select(Ingredient).where(Ingredient.name == ingredient.name.lower()))
    if result:
        await session.refresh(result)
        return result
    else:
        db_ingredient = Ingredient(**ingredient.model_dump())
        session.add(db_ingredient)
        await session.commit()
        await session.refresh(db_ingredient)
        nutri_client = NutritionalAPIClient()
        background_tasks.add_task(nutri_client.fill_nutritional_values, db_ingredient, session)
        return db_ingredient


@ingredient_router.get("/{ingredient_id}", response_model=ListIngredient, status_code=200)
async def ingredient_detail(ingredient_id: int, session: AsyncSession = Depends(get_session)):
    ingredient = await session.get(Ingredient, ingredient_id)
    if not ingredient:
        raise HTTPException(status_code=404, detail="Ingredient not found")
    return ingredient


@ingredient_router.get("/", response_model=list[ListIngredient], status_code=200)
async def ingredient_list(session: AsyncSession = Depends(get_session)):
    result = await session.scalars(select(Ingredient))
    ingredients = result.all()
    return ingredients


@ingredient_router.patch("/{ingredient_id}", response_model=ListIngredient, status_code=200)
async def update_ingredient(
    ingredient_id: int,
    ingredient_data: UpdateIngredient,
    session: AsyncSession = Depends(get_session),
):
    ingredient = await session.get(Ingredient, ingredient_id)
    if not ingredient:
        raise HTTPException(status_code=404, detail="Ingredient not found")
    ingredient_data = ingredient_data.model_dump(exclude_unset=True)
    for key, value in ingredient_data.items():
        setattr(ingredient, key, value)
    session.add(ingredient)
    await session.commit()
    await session.refresh(ingredient)
    return ingredient


@ingredient_router.delete("/{ingredient_id}", status_code=204)
async def delete_ingredient(ingredient_id: int, session: AsyncSession = Depends(get_session)):
    ingredient = await session.get(Ingredient, ingredient_id)
    if not ingredient:
        raise HTTPException(status_code=404, detail="Ingredient not found")
    await session.delete(ingredient)
    await session.commit()
    return Response(content="", status_code=204)


@ingredient_router.post(
    "/dish/",
    response_model=DishDetail,
    status_code=201,
    summary="Create a dish",
    response_description="Created dish",
)
async def create_dish(
    dish_data: CreateDish,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    dish_dict = dish_data.model_dump()
    ingredients = dish_dict.pop("ingredients")

    dish = Dish(**dish_dict, author_id=user.id)
    session.add(dish)
    await session.flush()

    existing = await session.scalars(
        select(Ingredient).where(Ingredient.name.in_([i["name"] for i in ingredients]))
    )
    existing_ingredients = {i.name: i for i in existing.all()}

    # bulk create missing ingredients
    new_ingredients = [
        Ingredient(name=i["name"]) for i in ingredients if i["name"] not in existing_ingredients
    ]
    if new_ingredients:
        session.add_all(new_ingredients)
        await session.flush()
        existing_ingredients.update({i.name: i for i in new_ingredients})

    # bulk create ingredient items
    session.add_all(
        [
            IngredientItem(
                dish_id=dish.id, ingredient_id=existing_ingredients[i["name"]].id, amount=i["amount"]
            )
            for i in ingredients
        ]
    )

    await session.commit()
    await session.refresh(dish)
    return dish


@ingredient_router.get("/dish/", response_model=list[ListDish], status_code=200)
async def dish_list(
    filter_params: Annotated[DishFilterParams, Query()],
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(get_current_user_optional),
):
    filters = []
    if filter_params.favorites:
        if user:
            filters.append(Dish.favorite_of.any(User.id == user.id))
        else:
            return HTTPException(status_code=400, detail="Anonymous users can't have favorites")
    if filter_params.tag_id:
        filters.append(Dish.tags.any(Tag.id.in_(filter_params.tag_id)))
    if filter_params.tag_name:
        filters.append(Dish.tags.any(Tag.name.in_(filter_params.tag_name)))

    result = await session.scalars(select(Dish).where(*filters))
    dishes = result.all()
    return dishes


@ingredient_router.delete("/dish/{dish_id}", status_code=204)
async def delete_dish(dish_id: int, session: AsyncSession = Depends(get_session)):
    dish = await session.get(Dish, dish_id)
    if not dish:
        raise HTTPException(status_code=404, detail="Dish not found")
    await session.delete(dish)
    await session.commit()
    return JSONResponse(content="", status_code=204)


@ingredient_router.get("/dish/{dish_id}", response_model=DishDetail, status_code=200)
async def dish_detail(dish_id: int, session: AsyncSession = Depends(get_session)):
    dish = await session.get(Dish, dish_id)
    if not dish:
        raise HTTPException(status_code=404, detail="Dish not found")

    nut_values = list(NutritionalValues.model_fields.keys())
    nut_expressions = [
        func.sum(getattr(Ingredient, param) * IngredientItem.amount / 100).label(param)
        for param in nut_values
    ]

    nut_query = await session.execute(
        select(*nut_expressions)
        .join(IngredientItem, Ingredient.id == IngredientItem.ingredient_id)
        .where(IngredientItem.dish_id == dish_id)
        .group_by(IngredientItem.dish_id)
    )
    nut_row = nut_query.first()

    return {
        **dish.__dict__,
        "nutritional_values": dict(zip(nut_values, nut_row)) if nut_row else None,
        "comments": await dish_comments(dish_id, session),
    }


@ingredient_router.post("/dish/{dish_id}/tag", response_model=DishDetail, status_code=200)
async def dish_add_tag(dish_id: int, tag_data: CreateTag, session: AsyncSession = Depends(get_session)):
    dish = await session.get(Dish, dish_id)
    if not dish:
        raise HTTPException(status_code=404, detail="Dish not found")

    result = await session.scalars(select(Tag).where(Tag.name == tag_data.name))
    tag = result.first()

    if not tag:
        tag = Tag(**tag_data.model_dump())
        session.add(tag)
        await session.flush()

    if tag not in dish.tags:
        dish.tags.append(tag)

    await session.commit()
    await session.refresh(dish)
    return {**dish.__dict__, "comments": await dish_comments(dish_id, session)}


@ingredient_router.post("/dish/{dish_id}/favorite", response_model=DishDetail, status_code=200)
async def dish_toggle_favorites(
    dish_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    dish = await session.scalar(
        select(Dish).where(Dish.id == dish_id).options(selectinload(Dish.favorite_of))
    )
    if not dish:
        raise HTTPException(status_code=404, detail="Dish not found")

    if user not in dish.favorite_of:
        dish.favorite_of.append(user)
    else:
        dish.favorite_of.remove(user)

    await session.commit()
    await session.refresh(dish)
    dish_data = DishDetail.model_validate(dish)
    dish_data.is_favorite = user in dish.favorite_of if user else False
    dish_data.comments = [
        CommentDetail.model_validate(comment) for comment in await dish_comments(dish_id, session)
    ]
    return dish_data


@ingredient_router.post("/dish/{dish_id}/picture", response_model=ImageDetail)
async def upload_image(
    dish_id: int,
    is_main: bool = Form(...),
    session: AsyncSession = Depends(get_session),
    file: UploadFile | None = None,
):
    if not file:
        raise HTTPException(status_code=400, detail="No file provided")

    image = await Image.create(file, dish_id, is_main, session)
    await session.commit()
    await session.refresh(image)
    return image


@ingredient_router.delete("/image/{image_id}")
async def delete_image(image_id: int, session: AsyncSession = Depends(get_session)):
    image = await session.get(Image, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    await session.delete(image)
    await session.commit()
    return JSONResponse(content="", status_code=204)


@ingredient_router.post(
    "/dish/{dish_id}/comments",
    response_model=CommentDetail,
    status_code=201,
    summary="Comment on a dish, or reply to a comment",
    response_description="Created comment",
)
async def create_comment(
    dish_id: int,
    comment_data: CreateComment,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    dish = await session.get(Dish, dish_id)
    if not dish:
        raise HTTPException(status_code=404, detail="Dish not found")

    parent_id = comment_data.parent_id
    if parent_id is not None:
        parent = await session.get(Comment, parent_id)
        if not parent or parent.dish_id != dish_id:
            raise HTTPException(status_code=404, detail="Parent comment not found")
        # Threads are one level deep: replying to a reply attaches to its parent.
        # Every stored comment already satisfies that, so parent.parent_id is a root.
        parent_id = parent.parent_id or parent.id

    comment = Comment(
        body=comment_data.body, dish_id=dish_id, author_id=user.id, parent_id=parent_id
    )
    session.add(comment)
    await session.commit()

    # Re-select rather than refresh(): the commit expired the columns and `replies`
    # is lazy by default (see the model), so this loads both in one statement and
    # serialising the response cannot trigger implicit IO.
    return await session.scalar(
        select(Comment).where(Comment.id == comment.id).options(selectinload(Comment.replies))
    )


@ingredient_router.delete("/comment/{comment_id}", status_code=204)
async def delete_comment(
    comment_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    # Replies are loaded so the delete-orphan cascade does not lazy-load mid-flush.
    comment = await session.get(Comment, comment_id, options=[selectinload(Comment.replies)])
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    if comment.author_id != user.id:
        raise HTTPException(status_code=403, detail="You can only delete your own comments")
    await session.delete(comment)
    await session.commit()
