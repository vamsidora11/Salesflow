import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import AnalystRole, AnyRole, get_analytics_service
from app.api.responses import AUTHED, INVALID_RANGE
from app.schemas.analytics import (
    BreakdownResponse,
    Channel,
    SummaryResponse,
    TimeseriesResponse,
    TopProductsResponse,
)
from app.services.analytics import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["analytics"], responses={**AUTHED, **INVALID_RANGE})

Service = Annotated[AnalyticsService, Depends(get_analytics_service)]
From = Annotated[datetime, Query(alias="from", description="Inclusive start; naive = UTC.")]
To = Annotated[datetime, Query(alias="to", description="Exclusive end; naive = UTC.")]
StoreId = Annotated[uuid.UUID | None, Query(description="Restrict to one store.")]
Region = Annotated[str | None, Query(max_length=100, description="Restrict to one region.")]
ChannelQ = Annotated[Channel | None, Query(description="Restrict to one sales channel.")]


@router.get(
    "/summary",
    summary="KPI summary",
    description="Revenue, units, transactions and average order value from `sales_daily`. "
    "Day buckets are UTC, so the range is applied at day granularity. Viewers may call this.",
    response_model=SummaryResponse,
)
async def summary(
    user: AnyRole,
    service: Service,
    from_: From,
    to: To,
    store_id: StoreId = None,
    region: Region = None,
    channel: ChannelQ = None,
) -> SummaryResponse:
    return await service.summary(user.tenant_id, from_, to, store_id, region, channel)


@router.get(
    "/timeseries",
    summary="Chart time series",
    description="Hourly (max 366 days) or daily series of revenue or units.",
    response_model=TimeseriesResponse,
)
async def timeseries(
    user: AnalystRole,
    service: Service,
    from_: From,
    to: To,
    granularity: Literal["hour", "day"] = "day",
    metric: Literal["revenue", "units"] = "revenue",
    store_id: StoreId = None,
    region: Region = None,
    channel: ChannelQ = None,
) -> TimeseriesResponse:
    return await service.timeseries(
        user.tenant_id, granularity, metric, from_, to, store_id, region, channel
    )


@router.get(
    "/top-products",
    summary="Top products by revenue",
    response_model=TopProductsResponse,
)
async def top_products(
    user: AnalystRole,
    service: Service,
    from_: From,
    to: To,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    store_id: StoreId = None,
    region: Region = None,
    channel: ChannelQ = None,
) -> TopProductsResponse:
    return await service.top_products(user.tenant_id, limit, from_, to, store_id, region, channel)


@router.get("/by-region", summary="Revenue by region", response_model=BreakdownResponse)
async def by_region(
    user: AnalystRole,
    service: Service,
    from_: From,
    to: To,
    store_id: StoreId = None,
    channel: ChannelQ = None,
) -> BreakdownResponse:
    return await service.by_region(user.tenant_id, from_, to, store_id, channel)


@router.get("/by-channel", summary="Revenue by channel", response_model=BreakdownResponse)
async def by_channel(
    user: AnalystRole,
    service: Service,
    from_: From,
    to: To,
    store_id: StoreId = None,
    region: Region = None,
) -> BreakdownResponse:
    return await service.by_channel(user.tenant_id, from_, to, store_id, region)
