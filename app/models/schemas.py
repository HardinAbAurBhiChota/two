from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator
from datetime import date


class HotelSearchRequest(BaseModel):
    check_in_date: str = Field(..., description="Check-in date (YYYY-MM-DD)")
    check_out_date: str = Field(..., description="Check-out date (YYYY-MM-DD)")
    adults: int = Field(2, ge=1, le=30)
    children: int = Field(0, ge=0, le=10)
    children_ages: Optional[str] = Field(None, description="Comma-separated ages e.g. 5,8,10")
    location: str = Field("Guwahati", description="Hotel search location")
    currency: str = Field("USD")
    language: str = Field("en")
    sort_by: Optional[str] = Field(None, description="price_low | price_high | rating | relevance")
    price_min: Optional[int] = Field(None)
    price_max: Optional[int] = Field(None)
    hotel_class: Optional[str] = Field(None, description="Comma-separated star ratings e.g. 3,4,5")
    max_pages: int = Field(0, description="Max pages to scrape (0=all based on total results)")
    cursor: Optional[str] = Field(None)
    proxy_url: Optional[str] = Field(None, description="SOCKS5 proxy URL e.g. socks5://host:port")

    @field_validator("check_in_date", "check_out_date")
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError(f"Date must be YYYY-MM-DD, got: {v}")
        return v

    @field_validator("children_ages")
    @classmethod
    def validate_ages(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        for age in v.split(","):
            a = int(age.strip())
            if not (1 <= a <= 17):
                raise ValueError(f"Child age must be 1-17, got: {a}")
        return v

    def model_post_init(self, __context: Any) -> None:
        if self.children_ages:
            if len(self.children_ages.split(",")) != self.children:
                raise ValueError("children_ages count must match children count")


class GPSCoordinates(BaseModel):
    latitude: Optional[float] = None
    longitude: Optional[float] = None

class RatePricing(BaseModel):
    lowest: Optional[str] = None
    before_taxes_fees: Optional[str] = None

class NearbyTransport(BaseModel):
    type: Optional[Any] = None
    duration: Optional[Any] = None

class NearbyPlace(BaseModel):
    name: Optional[str] = None
    transportations: list[NearbyTransport] = []

class HotelImage(BaseModel):
    thumbnail: Optional[str] = None
    original_image: Optional[str] = None

class RatingBreakdown(BaseModel):
    stars: Optional[int] = None
    count: Optional[int] = None

class ReviewBreakdown(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    total_mentioned: Optional[int] = None
    positive: Optional[int] = None
    negative: Optional[int] = None
    neutral: Optional[int] = None

class HotelAd(BaseModel):
    title: Optional[str] = None
    source: Optional[str] = None
    source_icon: Optional[str] = None
    link: Optional[str] = None
    property_token: Optional[str] = None
    gps_coordinates: Optional[GPSCoordinates] = None
    thumbnail: Optional[str] = None
    price: Optional[Any] = None
    reviews: Optional[Any] = None
    overall_rating: Optional[Any] = None
    amenities: list[Any] = []
    hotel_class: Optional[Any] = None
    free_cancellation: Optional[Any] = None

class HotelBrandChild(BaseModel):
    id: Optional[int] = None
    name: Optional[str] = None

class HotelBrand(BaseModel):
    id: Optional[int] = None
    name: Optional[str] = None
    children: list[HotelBrandChild] = []

class HotelProperty(BaseModel):
    type: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    link: Optional[str] = None
    property_token: Optional[str] = None
    gps_coordinates: Optional[GPSCoordinates] = None
    check_in_time: Optional[str] = None
    check_out_time: Optional[str] = None
    rate_per_night: Optional[RatePricing] = None
    total_rate: Optional[RatePricing] = None
    nearby_places: list[NearbyPlace] = []
    hotel_class: Optional[Any] = None
    extracted_hotel_class: Optional[Any] = None
    images: list[HotelImage] = []
    reviews: Optional[Any] = None
    overall_rating: Optional[Any] = None
    ratings: list[RatingBreakdown] = []
    location_rating: Optional[Any] = None
    reviews_breakdown: list[ReviewBreakdown] = []
    amenities: list[Any] = []
    eco_certified: Optional[Any] = None

class PaginationMeta(BaseModel):
    next_page_token: Optional[str] = None
    total_results: Optional[int] = None

class HotelSearchResponse(BaseModel):
    pagination: PaginationMeta
    ads: list[HotelAd] = []
    brands: list[HotelBrand] = []
    properties: list[HotelProperty] = []
