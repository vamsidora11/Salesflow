import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class StoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    region: str
    timezone: str
    is_active: bool
    created_at: datetime


class StoreCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    region: str = Field(min_length=1, max_length=100)
    timezone: str = Field(default="UTC", max_length=64)


class StoreCreated(StoreOut):
    api_key: str = Field(description="Shown once. Only a SHA-256 hash is stored.")


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sku: str
    name: str
    category: str
    list_price: Decimal
    created_at: datetime


class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=100)
    list_price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
