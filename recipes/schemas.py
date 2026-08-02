from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from recipes.tests.test_data.example_data import example_ingredient, example_product, example_tag


class NutritionalValues(BaseModel):
    calories: float = 0.0
    fat_total: float = 0.0
    protein: float = 0.0
    sodium: float = 0.0
    potassium: float = 0.0
    fiber: float = 0.0
    carbohydrates_total: float = 0.0
    sugar: float = 0.0

    class Config:
        json_schema_extra = {
            "example": {
                "calories": 34,
                "fat_total": 0.2,
                "protein": 0.8,
                "sodium": 57,
                "potassium": 30,
                "fiber": 3,
                "carbohydrates_total": 8.3,
                "sugar": 3.4,
            }
        }


class CreateIngredient(BaseModel):
    name: str

    class Config:
        json_schema_extra = {
            "example": {
                "name": "carrot",
            }
        }
        from_attributes = True


class ListIngredient(CreateIngredient, NutritionalValues):
    id: int

    class Config:
        schema_extra = {
            "example": {
                "name": "carrot",
                "calories": 34,
                "fat_total": 0.2,
                "protein": 0.8,
                "sodium": 57,
                "potassium": 30,
                "fiber": 3,
                "carbohydrates_total": 8.3,
                "sugar": 3.4,
            }
        }


class UpdateIngredient(CreateIngredient):
    calories: Optional[float] | None = None
    fat_total: Optional[float] | None = None
    protein: Optional[float] | None = None
    carbohydrates_total: Optional[float] | None = None
    fat_saturated: Optional[float] | None = None
    sodium: Optional[float] | None = None
    potassium: Optional[float] | None = None
    fiber: Optional[float] | None = None
    sugar: Optional[float] | None = None

    class Config:
        json_schema_extra = {"example": example_ingredient}


class CreateIngredientItem(BaseModel):
    name: str
    amount: int


class CreateDish(BaseModel):
    name: str
    recipe: Optional[str]
    ingredients: List[CreateIngredientItem]

    class Config:
        json_schema_extra = {
            "example": {
                "id": 72,
                "name": "Mashed potatoes",
                "recipe": "Mash the potatoes along with the butter. Eat the mashed potatoes",
                "ingredients": [
                    {
                        "amount": 700,
                        "name": "potato",
                    },
                    {
                        "amount": 300,
                        "name": "butter",
                    },
                ],
            }
        }


class ListIngredientItem(BaseModel):
    amount: int
    ingredient: CreateIngredient

    model_config = ConfigDict(from_attributes=True)


class ListDish(BaseModel):
    id: int
    name: str
    recipe: Optional[str]
    servings: int | None
    prep_time: int | None
    ingredients: List[ListIngredientItem]
    author_id: int | None

    class Config:
        json_schema_extra = {
            "example": {
                "id": 72,
                "author_id": 1,
                "name": "Mashed potatoes",
                "recipe": "Mash the potatoes along with the butter. Eat the mashed potatoes",
                "servings": 4,
                "prep_time": 60,
                "ingredients": [
                    {
                        "amount": 700,
                        "ingredient": {"name": "potato"},
                    },
                    {
                        "amount": 300,
                        "ingredient": {"name": "butter"},
                    },
                ],
            }
        }


class CreateTag(BaseModel):
    name: str

    model_config = ConfigDict(from_attributes=True, json_schema_extra={"example": {"name": "French kitchen"}})


class TagSchema(BaseModel):
    id: int
    name: str

    model_config = ConfigDict(from_attributes=True, json_schema_extra={"example": example_tag})


class DishDetail(ListDish):
    nutritional_values: NutritionalValues | None = None
    tags: List[TagSchema] = []
    is_favorite: bool = False
    # Top-level comments, each with its replies nested. Populated by the routes
    # via dish_comments(); Dish has no `comments` attribute to read this from.
    comments: List["CommentDetail"] = []

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Mashed potatoes",
                "recipe": "Mash the potatoes along with the butter. Eat the mashed potatoes",
                "ingredients": [{"amount": 700, "name": "potato"}, {"amount": 300, "name": "butter"}],
                "nutritional_values": {
                    "calories": 3339,
                    "fat_total": 315.04,
                    "protein": 27.23,
                    "sodium": 1533,
                    "potassium": 1533,
                    "fiber": 10.5,
                    "carbohydrates_total": 94.43,
                    "sugar": 10.36,
                },
            }
        }
        from_attributes = True


class CreateProduct(BaseModel):
    name: str
    amount: int
    expires_on: date | None = None

    model_config = ConfigDict(from_attributes=True, json_schema_extra={"example": example_product})


class DishFilterParams(BaseModel):
    favorites: bool = False
    tag_name: List[str] = []
    tag_id: List[int] = []


class ImageDetail(BaseModel):
    id: int
    filename: str
    is_main: bool
    dish_id: int
    url: str

    model_config = ConfigDict(from_attributes=True)


class CreateComment(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    # Omit for a top-level comment. Pointing at a reply attaches to that reply's
    # parent instead -- threads are capped at one level.
    parent_id: int | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"body": "Made this last night, the butter ratio is spot on.", "parent_id": None}
        }
    )


class ReplyDetail(BaseModel):
    id: int
    body: str
    created_at: datetime
    author_id: int | None
    parent_id: int | None

    model_config = ConfigDict(from_attributes=True)


class CommentDetail(ReplyDetail):
    """A top-level comment with its replies. Replies never carry replies of their own."""

    replies: List[ReplyDetail] = []


# DishDetail refers to CommentDetail before it exists; resolve that now rather
# than leaving it to first use.
DishDetail.model_rebuild()
