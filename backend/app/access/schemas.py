from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


Role = Literal[
    "director", "regional_manager", "franchise_owner", "store_manager", "analyst", "admin"
]
Password = Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=256)]
NewPassword = Annotated[
    str, StringConstraints(strip_whitespace=False, min_length=12, max_length=256)
]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Login(Input):
    email: str = Field(min_length=3, max_length=254)
    password: Password

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.lower()
        if value.count("@") != 1 or " " in value:
            raise ValueError("Enter a valid email")
        return value


class Bootstrap(Login):
    name: str = Field(min_length=1, max_length=120)
    workspace_name: str = Field(min_length=1, max_length=160)
    bootstrap_token: str = ""

    @field_validator("password")
    @classmethod
    def strong_password(cls, value: str) -> str:
        if len(value) < 12:
            raise ValueError("Password must contain at least 12 characters")
        return value


class MemberCreate(Login):
    name: str = Field(min_length=1, max_length=120)
    role: Role
    all_stores: bool = False
    store_ids: list[str] = Field(default_factory=list, max_length=1000)
    data_access: bool = True

    @model_validator(mode="after")
    def technical_admin(self):
        if self.role == "admin" and "data_access" not in self.model_fields_set:
            self.data_access = False
        return self

    @field_validator("password")
    @classmethod
    def strong_password(cls, value: str) -> str:
        if len(value) < 12:
            raise ValueError("Password must contain at least 12 characters")
        return value


class MemberUpdate(Input):
    role: Role | None = None
    all_stores: bool | None = None
    store_ids: list[str] | None = Field(default=None, max_length=1000)
    active: bool | None = None
    data_access: bool | None = None
