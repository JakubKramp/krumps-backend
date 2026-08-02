from typing import TYPE_CHECKING, List

from sqlalchemy import ForeignKey, String, event
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.utils.db import Base
from auth.constants import ActivityFactor, SexEnum
from fridge.models import Fridge
from recipes.models import user_dish

if TYPE_CHECKING:
    from recipes.models import Comment, Dish


class User(Base):
    """
    Table representing a user.
    Relations:
    - Profile (auth) one to one
    - Fridge (fridge) one to one
    """

    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(50), unique=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password: Mapped[str] = mapped_column(String(250))

    profile: Mapped["Profile | None"] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    fridge: Mapped["Fridge | None"] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    favorites: Mapped[List["Dish"]] = relationship(
        secondary=user_dish, back_populates="favorite_of", lazy="selectin"
    )
    recipes: Mapped[List["Dish"]] = relationship(back_populates="author", lazy="selectin")
    # Not lazy="selectin": get_current_user runs on every authenticated request and
    # would otherwise eagerly load every comment the user has ever written.
    comments: Mapped[List["Comment"]] = relationship(back_populates="author", lazy="raise")

    @validates("email")
    def validate_email(self, key, address):
        if "@" not in address:
            raise ValueError("Please enter a valid email address")
        return address

    def __repr__(self) -> str:
        return f"User(id={self.id!r}, name={self.username!r})"


@event.listens_for(User, "init")
def create_fridge(target, args, kwargs):
    """
    Automatically creates an empty fridge when an user is created
    """
    target.fridge = Fridge()


class Profile(Base):
    """
    Table representing a profile. DRI data is scrapped automatically.
    Relations:
    - User (auth) one to one
    """

    __tablename__ = "profile"

    id: Mapped[int] = mapped_column(primary_key=True)

    # BaseProfile
    sex: Mapped[SexEnum] = mapped_column(postgresql.ENUM(SexEnum, name="sexenum", create_type=True))
    activity_factor: Mapped[ActivityFactor] = mapped_column(
        postgresql.ENUM(ActivityFactor, name="activityfactor", create_type=True)
    )
    age: Mapped[int] = mapped_column()
    height: Mapped[int] = mapped_column()
    weight: Mapped[int] = mapped_column()
    smoking: Mapped[bool] = mapped_column()

    # DietaryReferenceIntakes
    calories: Mapped[float | None] = mapped_column()
    carbohydrates: Mapped[float | None] = mapped_column()
    fat: Mapped[float | None] = mapped_column()
    protein: Mapped[float | None] = mapped_column()
    fiber: Mapped[float | None] = mapped_column()
    potassium: Mapped[float | None] = mapped_column()
    sodium: Mapped[float | None] = mapped_column()

    # Relationship
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), default=None)
    user: Mapped["User | None"] = relationship(back_populates="profile", uselist=False, lazy="selectin")
