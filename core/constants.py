FILES_TO_CREATE = ["__init__.py", "routes.py", "schemas.py", "models.py"]
DIRECTORIES_TO_CREATE = ["tests"]
TEST_FILES_TO_CREATE = ["test_routes.py", "test_models.py", "conftest.py"]

ROUTER_IMPORT_LINE = lambda name: f"from {name}.routes import {name}_router\n"

ROUTES_CONTENT = (
    lambda name: f"""from fastapi import APIRouter

{name}_router = APIRouter(prefix="/{name}", tags=["{name}"])
"""
)

SCHEMAS_CONTENT = (
    lambda name: f"""from pydantic import BaseModel
"""
)

MODELS_CONTENT = (
    lambda name: f"""from sqlalchemy.orm import Mapped, mapped_column
from app.utils.db import Base
"""
)

TEST_ROUTES_CONTENT = (
    lambda name: f"""import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession"""
)

TEST_MODELS_CONTENT = lambda name: f"""import pytest"""


FILE_CONTENTS = {
    "routes.py": ROUTES_CONTENT,
    "schemas.py": SCHEMAS_CONTENT,
    "models.py": MODELS_CONTENT,
    "test_routes.py": TEST_ROUTES_CONTENT,
    "test_models.py": TEST_MODELS_CONTENT,
}
