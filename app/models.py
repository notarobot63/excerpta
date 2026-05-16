from datetime import datetime
from typing import Optional, List
import secrets

from sqlmodel import SQLModel, Field, Relationship


class LinkTagLink(SQLModel, table=True):
    __tablename__ = "link_tags"
    link_id: Optional[int] = Field(default=None, foreign_key="links.id", primary_key=True)
    tag_id: Optional[int] = Field(default=None, foreign_key="tags.id", primary_key=True)


class LinkGroupLink(SQLModel, table=True):
    __tablename__ = "link_groups"
    link_id: Optional[int] = Field(default=None, foreign_key="links.id", primary_key=True)
    group_id: Optional[int] = Field(default=None, foreign_key="groups.id", primary_key=True)


class User(SQLModel, table=True):
    __tablename__ = "users"
    id: Optional[int] = Field(default=None, primary_key=True)
    oidc_sub: str = Field(unique=True, index=True)
    email: str = Field(default="")
    name: str = Field(default="")
    api_key: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    theme: str = Field(default="light")
    is_admin: bool = Field(default=False)
    is_active: bool = Field(default=True)
    session_version: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    links: List["Link"] = Relationship(back_populates="user")
    tags: List["Tag"] = Relationship(back_populates="user")
    groups: List["Group"] = Relationship(back_populates="user")


class Tag(SQLModel, table=True):
    __tablename__ = "tags"
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id")
    name: str

    user: Optional[User] = Relationship(back_populates="tags")
    links: List["Link"] = Relationship(back_populates="tags", link_model=LinkTagLink)


class Group(SQLModel, table=True):
    __tablename__ = "groups"
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id")
    name: str
    is_public: bool = Field(default=False)
    parent_id: Optional[int] = Field(default=None)

    user: Optional[User] = Relationship(back_populates="groups")
    links: List["Link"] = Relationship(back_populates="groups", link_model=LinkGroupLink)


class Link(SQLModel, table=True):
    __tablename__ = "links"
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id")
    url: str
    title: str = Field(default="")
    description: str = Field(default="")
    favicon_url: str = Field(default="")
    thumbnail_url: str = Field(default="")
    note: str = Field(default="")
    is_public: bool = Field(default=False)
    archived_url: Optional[str] = Field(default=None)
    archived_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    user: Optional[User] = Relationship(back_populates="links")
    tags: List[Tag] = Relationship(back_populates="links", link_model=LinkTagLink)
    groups: List[Group] = Relationship(back_populates="links", link_model=LinkGroupLink)
