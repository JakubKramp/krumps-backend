import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from core.files.file_upload_client import BLANK_JPEG, TestFileUploader
from recipes.models import Dish, Image, Ingredient, IngredientItem
from recipes.pdf import kcal_per_serving, main_image, render_dish_pdf, storage_url_fetcher

PDF_MAGIC = b"%PDF-"


@pytest.fixture(autouse=True)
def use_test_uploader(monkeypatch):
    """
    Pin the uploader. Otherwise these tests follow whatever CLOUD_PROVIDER the
    developer's config/.env happens to set and try to reach a real bucket.
    """
    monkeypatch.setattr("config.settings.FILE_UPLOADER_CLASS", TestFileUploader)


@pytest.mark.asyncio
async def test_dish_pdf(client: AsyncClient, db_dish: Dish):
    response = await client.get(f"/ingredients/dish/{db_dish.id}/pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(PDF_MAGIC)
    # A real render, not an empty stub.
    assert len(response.content) > 1000


@pytest.mark.asyncio
async def test_dish_pdf_filename_from_dish_name(client: AsyncClient, db_dish: Dish):
    response = await client.get(f"/ingredients/dish/{db_dish.id}/pdf")
    assert response.headers["content-disposition"] == 'attachment; filename="mashed-potatoes.pdf"'


@pytest.mark.asyncio
async def test_dish_pdf_does_not_exist(client: AsyncClient):
    response = await client.get("/ingredients/dish/999/pdf")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_dish_pdf_with_ingredients_and_image(
    session: AsyncSession, client: AsyncClient, db_dish: Dish
):
    """The full path: ingredients joined through IngredientItem, plus a main image."""
    ingredient = Ingredient(name="potato", calories=77.0)
    session.add(ingredient)
    await session.flush()
    session.add(IngredientItem(dish_id=db_dish.id, ingredient_id=ingredient.id, amount=700))
    session.add(Image(filename="mash.jpg", is_main=True, dish_id=db_dish.id))
    await session.commit()

    response = await client.get(f"/ingredients/dish/{db_dish.id}/pdf")

    assert response.status_code == 200
    assert response.content.startswith(PDF_MAGIC)


@pytest.mark.asyncio
async def test_dish_pdf_fetches_the_main_image(
    session: AsyncSession, client: AsyncClient, db_dish: Dish, monkeypatch
):
    """
    Asserted on the fetcher rather than the PDF bytes: WeasyPrint logs and skips
    an image it cannot load, so a render that silently dropped the photo would
    still return a valid PDF and pass a size check.
    """
    session.add(Image(filename="mash.jpg", is_main=True, dish_id=db_dish.id))
    session.add(Image(filename="side.jpg", is_main=False, dish_id=db_dish.id))
    await session.commit()

    requested: list[str] = []

    class SpyUploader(TestFileUploader):
        @staticmethod
        def read_file(filename: str) -> bytes:
            requested.append(filename)
            return BLANK_JPEG

    monkeypatch.setattr("config.settings.FILE_UPLOADER_CLASS", SpyUploader)

    response = await client.get(f"/ingredients/dish/{db_dish.id}/pdf")

    assert response.status_code == 200
    # Only the main image, and read straight from storage -- no HTTP, no presigned URL.
    assert requested == ["mash.jpg"]


@pytest.mark.asyncio
async def test_render_dish_pdf_without_optional_data(session: AsyncSession):
    """No image, no ingredients, no recipe text, null servings -- must still render."""
    dish = Dish(name="Untitled", recipe=None, servings=None, prep_time=None)
    session.add(dish)
    await session.commit()
    await session.refresh(dish)

    pdf = render_dish_pdf(dish, None)

    assert pdf.startswith(PDF_MAGIC)


def test_storage_url_fetcher_resolves_s3_scheme():
    """s3:// is served from the uploader, so rendering makes no HTTP request."""
    result = storage_url_fetcher("s3://mash.jpg")

    assert result["mime_type"] == "image/jpeg"
    # TestFileUploader hands back a real 1x1 JPEG, so a renderer can decode it.
    assert result["string"].startswith(b"\xff\xd8\xff")


@pytest.mark.parametrize(
    "nutrition, servings, expected",
    [
        ({"calories": 3339.0}, 4, 835),
        ({"calories": 3339.0}, None, None),  # servings is nullable
        (None, 4, None),  # dish has no ingredients
        ({"calories": None}, 4, None),  # aggregate returned NULL
        ({"calories": 0.0}, 4, 0),
    ],
)
def test_kcal_per_serving(nutrition, servings, expected):
    assert kcal_per_serving(nutrition, servings) == expected


def test_main_image_picks_the_main_one():
    dish = Dish(name="x")
    dish.images = [
        Image(filename="side.jpg", is_main=False),
        Image(filename="hero.jpg", is_main=True),
    ]
    assert main_image(dish).filename == "hero.jpg"


def test_main_image_none_when_no_images():
    dish = Dish(name="x")
    dish.images = []
    assert main_image(dish) is None
