from datetime import datetime, timezone
from typing import Optional, List
import secrets

from sqlmodel import SQLModel, Field, Relationship
# LinkGroupLink supprimé - remplacé par folder_id FK directe sur Link


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class LinkTagLink(SQLModel, table=True):
    __tablename__ = "link_tags"
    link_id: Optional[int] = Field(default=None, foreign_key="links.id", primary_key=True)
    tag_id: Optional[int] = Field(default=None, foreign_key="tags.id", primary_key=True)


class User(SQLModel, table=True):
    __tablename__ = "users"
    id: Optional[int] = Field(default=None, primary_key=True)
    oidc_sub: str = Field(unique=True, index=True)
    email: str = Field(default="")
    name: str = Field(default="")
    api_key: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    api_key_hmac: str = Field(default="")
    theme: str = Field(default="light")
    is_admin: bool = Field(default=False)
    is_active: bool = Field(default=True)
    public_page_title: str = Field(default="Liens publics")
    public_slug: Optional[str] = Field(default=None, unique=True, index=True)
    session_version: int = Field(default=0)
    created_at: datetime = Field(default_factory=_utcnow)

    links: List["Link"] = Relationship(back_populates="user")
    tags: List["Tag"] = Relationship(back_populates="user")
    folders: List["Folder"] = Relationship(back_populates="user")


class Tag(SQLModel, table=True):
    __tablename__ = "tags"
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id")
    name: str

    user: Optional[User] = Relationship(back_populates="tags")
    links: List["Link"] = Relationship(back_populates="tags", link_model=LinkTagLink)


class Folder(SQLModel, table=True):
    __tablename__ = "folders"
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id")
    name: str
    is_public: bool = Field(default=False)
    parent_id: Optional[int] = Field(default=None)
    sort_order: int = Field(default=0)

    user: Optional[User] = Relationship(back_populates="folders")
    links: List["Link"] = Relationship(back_populates="folder")


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
    archive_status: Optional[str] = Field(default=None)  # None | pending | ok | failed
    folder_id: Optional[int] = Field(default=None, foreign_key="folders.id")
    freshrss_item_id: Optional[str] = Field(default=None)
    is_broken: Optional[bool] = Field(default=None)
    check_status: Optional[int] = Field(default=None)
    last_checked_at: Optional[datetime] = Field(default=None)
    reader_html: Optional[str] = Field(default=None)
    reader_title: Optional[str] = Field(default=None)
    reader_extracted_at: Optional[datetime] = Field(default=None)
    reader_failed: bool = Field(default=False)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    user: Optional[User] = Relationship(back_populates="links")
    tags: List[Tag] = Relationship(back_populates="links", link_model=LinkTagLink, sa_relationship_kwargs={"lazy": "selectin"})
    folder: Optional[Folder] = Relationship(back_populates="links", sa_relationship_kwargs={"lazy": "selectin"})


class FreshRSSConfig(SQLModel, table=True):
    __tablename__ = "freshrss_configs"
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", unique=True, index=True)
    freshrss_url: str = Field(default="")
    freshrss_user: str = Field(default="")
    freshrss_token: str = Field(default="")
    group_name: str = Field(default="FreshRSS")
    is_enabled: bool = Field(default=False)
    last_sync: Optional[datetime] = Field(default=None)
    synced_count: int = Field(default=0)
