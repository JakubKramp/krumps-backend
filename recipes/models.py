from datetime import date, datetime
from http.client import HTTPException
from typing import TYPE_CHECKING, List

from fastapi import UploadFile
from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    UniqueConstraint,
    event,
    func,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.utils.db import Base
from config import settings

# Imported for typing only: auth.models and fridge.models both import from this
# module at runtime, so a real import here would be circular.
if TYPE_CHECKING:
    from auth.models import User
    from fridge.models import Fridge


class Ingredient(Base):
    """
    Nutritional values by default refer to 100g serving.
    Relations:
    - IngredientItem(recipes) one to many
    """

    __tablename__ = "ingredient"
    __table_args__ = (UniqueConstraint("name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)

    # NutritionalValues
    calories: Mapped[float] = mapped_column(default=0.0)
    fat_total: Mapped[float] = mapped_column(default=0.0)
    fat_saturated: Mapped[float] = mapped_column(default=0.0)
    protein: Mapped[float] = mapped_column(default=0.0)
    sodium: Mapped[float] = mapped_column(default=0.0)
    potassium: Mapped[float] = mapped_column(default=0.0)
    carbohydrates_total: Mapped[float] = mapped_column(default=0.0)
    fiber: Mapped[float] = mapped_column(default=0.0)
    sugar: Mapped[float] = mapped_column(default=0.0)

    # Relationships
    ingredient_items: Mapped[List["IngredientItem"]] = relationship(
        back_populates="ingredient", lazy="selectin"
    )

    @validates(
        "calories",
        "fat_total",
        "fat_saturated",
        "protein",
        "sodium",
        "potassium",
        "carbohydrates_total",
        "fiber",
        "sugar",
    )
    def validate_email(self, key, value):
        if value < 0:
            raise ValueError(f"{key} can not be less than 0")
        return value

    def __repr__(self):
        return f"Ingredient {self.name} with an ID of {self.id}"


dish_tag = Table(
    "dish_tag",
    Base.metadata,
    Column("dish_id", ForeignKey("dish.id"), primary_key=True),
    Column("tag_id", ForeignKey("tag.id"), primary_key=True),
)

user_dish = Table(
    "user_dish",
    Base.metadata,
    Column("dish_id", ForeignKey("dish.id"), primary_key=True),
    Column("user_id", ForeignKey("user.id"), primary_key=True),
)


class Dish(Base):
    """
    Model that represents a recipe
    Relations:
    - IngredientItem(recipes) one to many
    - Tag(recipes) many to many, through dish_tag
    """

    __tablename__ = "dish"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    recipe: Mapped[str | None] = mapped_column(Text)
    servings: Mapped[int | None] = mapped_column()
    prep_time: Mapped[int | None] = mapped_column()

    ingredients: Mapped[List["IngredientItem"]] = relationship(back_populates="dish", lazy="selectin")
    tags: Mapped[list["Tag"]] = relationship(secondary=dish_tag, back_populates="dish", lazy="selectin")
    images: Mapped[list["Image"]] = relationship(back_populates="dish", lazy="selectin")
    author_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    author: Mapped["User | None"] = relationship(back_populates="recipes", lazy="selectin")
    favorite_of: Mapped[list["User"]] = relationship(
        secondary=user_dish, back_populates="favorites", lazy="selectin"
    )
    # Every comment on the dish, replies included -- this owns the delete cascade.
    # Deliberately not called `comments`: DishDetail exposes a `comments` field
    # holding only top-level comments with their replies nested, assembled by
    # dish_comments() in the routes. A name collision here would let Pydantic
    # serialise this flat list instead and lazy-load each reply mid-response.
    # passive_deletes: comment.dish_id carries ON DELETE CASCADE, so the database
    # removes these. Without it the ORM loads the whole collection during flush to
    # cascade in Python -- IO outside the async context, i.e. MissingGreenlet.
    # lazy="raise" because nothing should ever read this; it exists for the cascade.
    all_comments: Mapped[list["Comment"]] = relationship(
        back_populates="dish",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )


class IngredientItem(Base):
    """
    Proxy between Ingredient and Dish, allowing us to set amount of produce for each dish.
    Amount is in grams.
        Relations:
    - Ingredient(recipes) many to one
    - Dish(recipes) many to one
    - Product(recipes) one to one
    """

    __tablename__ = "ingredientitem"

    id: Mapped[int] = mapped_column(primary_key=True)
    amount: Mapped[int] = mapped_column()

    ingredient_id: Mapped[int | None] = mapped_column(ForeignKey("ingredient.id"))
    ingredient: Mapped["Ingredient | None"] = relationship(back_populates="ingredient_items", lazy="selectin")

    dish_id: Mapped[int | None] = mapped_column(ForeignKey("dish.id"))
    dish: Mapped["Dish | None"] = relationship(back_populates="ingredients", lazy="selectin")


class Product(Base):
    """
    Item with an amount and expiry date.
    Relations:
    - IngredientItem(recipes) many to one
    - Dish(ingredients) many to one
    """

    __tablename__ = "product"
    id: Mapped[int] = mapped_column(primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredient.id"))
    ingredient: Mapped["Ingredient"] = relationship(lazy="selectin")
    amount: Mapped[int] = mapped_column()
    expires_on: Mapped[date] = mapped_column()
    marked_for_delete: Mapped[bool] = mapped_column(default=False)
    fridge_id: Mapped[int | None] = mapped_column(ForeignKey("fridge.id"))
    fridge: Mapped["Fridge"] = relationship(
        back_populates="products",
        lazy="selectin",
        uselist=True,
    )

    @property
    def name(self) -> str:
        return self.ingredient.name

    @name.setter
    def name(self, value: str) -> None:
        pass  # name is set via ingredient, ignore

    def is_expired(self) -> bool:
        return self.expires_on < date.today()

    @classmethod
    def expired(cls):
        return cls.marked_for_delete == True

    @classmethod
    async def create(cls, session: AsyncSession, **kwargs) -> "Product":
        name = kwargs.pop("name")
        ingredient = await session.scalar(select(Ingredient).where(Ingredient.name == name))
        if not ingredient:
            ingredient = Ingredient(name=name)
            session.add(ingredient)
            await session.flush()
        await session.refresh(ingredient)
        product = cls(**kwargs, ingredient=ingredient)
        session.add(product)
        await session.flush()
        return product


class Tag(Base):
    """
    Tags a dish with a specific characteristic.
    - Dish(recipes) many to many, through dish_tag
    """

    __tablename__ = "tag"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    dish: Mapped[List["Dish"]] = relationship(secondary=dish_tag, back_populates="tags", lazy="selectin")


class Image(Base):
    """
    Tags a dish with a specific characteristic.
    - Dish(recipes) many to many, through dish_tag
    """

    __tablename__ = "images"
    __table_args__ = (
        Index(
            "unique_dish_main_picture",
            "dish_id",
            unique=True,
            postgresql_where=text("is_main = true"),
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(unique=True)
    is_main: Mapped[bool] = mapped_column(default=False)
    dish_id: Mapped[int] = mapped_column(ForeignKey("dish.id"))
    dish: Mapped["Dish"] = relationship(back_populates="images", lazy="selectin")

    @property
    def url(self) -> str:
        return settings.FILE_UPLOADER_CLASS.get_url(self.filename)

    @classmethod
    async def create(cls, file: UploadFile, dish_id: int, is_main: bool, session: AsyncSession) -> "Image":
        try:
            file_uploader = settings.FILE_UPLOADER_CLASS(file)
            await file_uploader.upload_file()
            image = cls(filename=file.filename, dish_id=dish_id, is_main=is_main)
            session.add(image)
            await session.flush()
            return image
        except HTTPException as e:
            raise e


class Comment(Base):
    """
    A comment on a dish. A reply is just a Comment with a parent -- nesting is capped
    at one level in create_comment, so a parent is always a top-level comment.
    Relations:
    - Dish(comments) many to one
    - User(comments) many to one
    - Comment(replies) one to many, self-referential
    """

    __tablename__ = "comment"

    id: Mapped[int] = mapped_column(primary_key=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    dish_id: Mapped[int] = mapped_column(ForeignKey("dish.id", ondelete="CASCADE"), index=True)
    dish: Mapped["Dish"] = relationship(back_populates="all_comments")

    # Nullable so deleting an author leaves the thread readable rather than
    # cascading their comments away.
    author_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    # No lazy="selectin" here: only author_id is ever serialised, and pairing it
    # with User.comments would make the two eager-load each other in a cycle.
    author: Mapped["User | None"] = relationship(back_populates="comments")

    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("comment.id", ondelete="CASCADE"), index=True
    )
    # remote_side marks the "one" end of the self-reference.
    parent: Mapped["Comment | None"] = relationship(back_populates="replies", remote_side=[id])
    # Deliberately not lazy="selectin" like the rest of this module: on a
    # self-referential relationship that walks the whole tree, one query per level.
    # The routes load it explicitly with selectinload() to a fixed depth.
    # passive_deletes for the same reason as Dish.all_comments: parent_id carries
    # ON DELETE CASCADE, so deleting a comment must not make the ORM walk the tree.
    replies: Mapped[list["Comment"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"Comment(id={self.id!r}, dish_id={self.dish_id!r}, parent_id={self.parent_id!r})"


from sqlalchemy import event


@event.listens_for(Image, "after_delete")
def delete_image_file(mapper, connection, target):
    file_uploader = settings.FILE_UPLOADER_CLASS
    file_uploader.delete_file(target.filename)
