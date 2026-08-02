"""Render a dish as a PDF from the Jinja template in templates/."""

import mimetypes
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from weasyprint import HTML, default_url_fetcher

from config import settings
from recipes.models import Dish, Image

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
TEMPLATE_NAME = "pdf_template.html"

S3_SCHEME = "s3://"

_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    # StrictUndefined: a typo in the template should fail loudly rather than
    # silently render an empty recipe.
    undefined=StrictUndefined,
    autoescape=select_autoescape(["html"]),
)


def storage_url_fetcher(url: str):
    """
    Resolve `s3://<key>` straight to bytes through the configured uploader.

    Without this WeasyPrint would issue a live HTTP GET for every image while
    rendering: slow, dependent on the worker having egress, and liable to fail
    on a presigned URL that expired mid-render.
    """
    if url.startswith(S3_SCHEME):
        filename = url[len(S3_SCHEME) :]
        mime_type, _ = mimetypes.guess_type(filename)
        return {
            "string": settings.FILE_UPLOADER_CLASS.read_file(filename),
            "mime_type": mime_type or "application/octet-stream",
        }
    return default_url_fetcher(url)


def main_image(dish: Dish) -> Image | None:
    """The dish's main photo. A partial unique index allows at most one."""
    return next((image for image in dish.images if image.is_main), None)


def kcal_per_serving(nutrition: dict | None, servings: int | None) -> int | None:
    """Per-serving calories, or None when either half of the division is missing."""
    if not nutrition or not servings:
        return None
    calories = nutrition.get("calories")
    if calories is None:
        return None
    return round(calories / servings)


def render_dish_pdf(dish: Dish, nutrition: dict | None = None) -> bytes:
    html = _env.get_template(TEMPLATE_NAME).render(
        dish=dish,
        main_image=main_image(dish),
        kcal_per_serving=kcal_per_serving(nutrition, dish.servings),
        logo_src=None,
    )
    # base_url lets relative asset paths in the template resolve against
    # templates/; the s3:// scheme is handled by the fetcher above.
    return HTML(
        string=html, base_url=str(TEMPLATE_DIR), url_fetcher=storage_url_fetcher
    ).write_pdf()
