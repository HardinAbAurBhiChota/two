import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from app.models.schemas import HotelSearchRequest, HotelSearchResponse
from app.services.scraper import scrape_hotels
from app.services.cache import cache_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/hotels/search", response_model=HotelSearchResponse, summary="Search Google Hotels")
async def search_hotels(
    check_in_date: str = Query(..., description="YYYY-MM-DD"),
    check_out_date: str = Query(..., description="YYYY-MM-DD"),
    location: str = Query("Guwahati"),
    adults: int = Query(2, ge=1, le=30),
    children: int = Query(0, ge=0, le=10),
    children_ages: Optional[str] = Query(None),
    currency: str = Query("USD"),
    language: str = Query("en"),
    sort_by: Optional[str] = Query(None),
    price_min: Optional[int] = Query(None),
    price_max: Optional[int] = Query(None),
    hotel_class: Optional[str] = Query(None),
    max_pages: int = Query(1, ge=0, description="0=all pages based on total results"),
    cursor: Optional[str] = Query(None),
    proxy_url: Optional[str] = Query(None),
):
    try:
        req = HotelSearchRequest(
            check_in_date=check_in_date, check_out_date=check_out_date,
            location=location, adults=adults, children=children,
            children_ages=children_ages, currency=currency, language=language,
            sort_by=sort_by, price_min=price_min, price_max=price_max,
            hotel_class=hotel_class, max_pages=max_pages, cursor=cursor,
            proxy_url=proxy_url,
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    cache_key = cache_service.make_key(req.model_dump(exclude={"proxy_url"}))
    cached = await cache_service.get(cache_key)
    if cached:
        logger.info("Cache HIT")
        return HotelSearchResponse(**cached)

    try:
        result = scrape_hotels(
            location=req.location,
            check_in=req.check_in_date,
            check_out=req.check_out_date,
            adults=req.adults,
            children=req.children,
            children_ages=req.children_ages or "",
            currency=req.currency,
            language=req.language,
            max_pages=req.max_pages,
            proxy_url=req.proxy_url,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Scraper error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Scraper error.")

    response = HotelSearchResponse(
        pagination=result["pagination"],
        ads=result["ads"],
        brands=result["brands"],
        properties=result["properties"],
    )
    await cache_service.set(cache_key, response.model_dump())
    return response


@router.post("/hotels/search", response_model=HotelSearchResponse, summary="Search Google Hotels (POST)")
async def search_hotels_post(req: HotelSearchRequest):
    cache_key = cache_service.make_key(req.model_dump(exclude={"proxy_url"}))
    cached = await cache_service.get(cache_key)
    if cached:
        return HotelSearchResponse(**cached)
    try:
        result = scrape_hotels(
            location=req.location,
            check_in=req.check_in_date,
            check_out=req.check_out_date,
            adults=req.adults,
            children=req.children,
            children_ages=req.children_ages or "",
            currency=req.currency,
            language=req.language,
            max_pages=req.max_pages,
            proxy_url=req.proxy_url,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Scraper error.")

    response = HotelSearchResponse(
        pagination=result["pagination"],
        ads=result["ads"],
        brands=result["brands"],
        properties=result["properties"],
    )
    await cache_service.set(cache_key, response.model_dump())
    return response
