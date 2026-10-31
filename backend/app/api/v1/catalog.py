from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import AdminRole, AnyRole, get_catalog_service
from app.api.responses import AUTHED
from app.repositories.products import DuplicateSkuError
from app.schemas.catalog import ProductCreate, ProductOut, StoreCreate, StoreCreated, StoreOut
from app.schemas.common import ErrorResponse, Page
from app.services.catalog import CatalogService

stores_router = APIRouter(prefix="/stores", tags=["stores"], responses=AUTHED)
products_router = APIRouter(prefix="/products", tags=["products"], responses=AUTHED)

Service = Annotated[CatalogService, Depends(get_catalog_service)]
Limit = Annotated[int, Query(ge=1, le=200)]
Cursor = Annotated[str | None, Query(description="`next_cursor` from the previous page.")]


@stores_router.get("", summary="List stores", response_model=Page[StoreOut])
async def list_stores(
    user: AnyRole, service: Service, limit: Limit = 50, cursor: Cursor = None
) -> Page[StoreOut]:
    return await service.list_stores(user.tenant_id, limit, cursor)


@stores_router.post(
    "",
    summary="Create a store (admin)",
    description="Returns the API key once; only its SHA-256 hash is stored.",
    status_code=status.HTTP_201_CREATED,
    response_model=StoreCreated,
)
async def create_store(user: AdminRole, service: Service, body: StoreCreate) -> StoreCreated:
    return await service.create_store(user.tenant_id, body)


@products_router.get("", summary="List products", response_model=Page[ProductOut])
async def list_products(
    user: AnyRole, service: Service, limit: Limit = 50, cursor: Cursor = None
) -> Page[ProductOut]:
    return await service.list_products(user.tenant_id, limit, cursor)


@products_router.post(
    "",
    summary="Create a product (admin)",
    status_code=status.HTTP_201_CREATED,
    response_model=ProductOut,
    responses={409: {"model": ErrorResponse, "description": "SKU already exists."}},
)
async def create_product(user: AdminRole, service: Service, body: ProductCreate) -> ProductOut:
    try:
        product = await service.create_product(user.tenant_id, body)
    except DuplicateSkuError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="SKU already exists") from None
    return ProductOut.model_validate(product)
