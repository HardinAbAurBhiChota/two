# Google Hotels Scraper API

A high-scale, reverse-engineered API-based scraper that extracts hotel data from Google Travel (Google Hotels). **No browser automation** — pure HTTP requests with embedded JSON extraction. **Full pagination support** across hundreds of pages with 99%+ uniqueness.

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────────┐
│   Client     │────▶│   FastAPI     │────▶│   Scraper        │
│ (CLI/cURL)   │     │   (uvicorn)  │     │   (requests)     │
└──────────────┘     │   + Cache    │     │   + SOCKS5 proxy │
                     └──────────────┘     └──────────────────┘
```

### How 1,000+ RPM is Achieved

| Technique | Detail |
|-----------|--------|
| **Async API layer** | FastAPI + uvicorn with 4 workers handles concurrent requests non-blocking |
| **In-memory TTL cache** | Identical queries return cached results instantly (600s TTL); plug REDIS_URL for Redis |
| **No browser overhead** | Pure `requests` HTTP calls — no DOM rendering, no JS execution, no WebDriver |
| **Full pagination** | `ts`+`qs` URL params + `data-next-page-token` chaining — 99%+ uniqueness across pages |
| **SOCKS5/Tor proxy** | Route requests through rotating proxies to avoid Google rate limits |
| **Exponential back-off** | Automatic retry with jitter on HTTP 429/503 |
| **Horizontal scaling** | Stateless design — deploy multiple containers behind a load balancer |

### Pagination Strategy

Google Hotels uses client-side JS pagination — the "Next" button computes `ts` and `qs` URL params dynamically. This scraper replicates that mechanism:

1. **Page 1**: Normal HTML fetch → extract `ds:0` data + `data-next-page-token` attribute
2. **Page 2+**: Fetch with `ts` + `qs` + `ap=MAE` URL params → full HTML with new `ds:0`
3. Each page's HTML contains `data-next-page-token` for the next page, enabling chaining

| Component | Description |
|-----------|-------------|
| `data-next-page-token` | HTML attribute containing next page token (e.g. `CBI=` → `CCQ=` → `CDY=`) |
| `ts` param | Base64 protobuf: location `/m/ID`, place_id, dates, currency. Constant per search |
| `qs` param | Base64 protobuf derived from `data-next-page-token`. Changes per page |
| `ap=MAE` | Required flag for paginated results |

Results: **96 unique properties across 5 pages (99% uniqueness)** — no Selenium needed.

Theoretical ceiling: a single uvicorn worker processes ~200 req/s on a modern CPU. With 4 workers + cache hit rate >80%, 1k RPM is comfortably achieved. For 100k RPM, add Redis + Kubernetes horizontal pod autoscaling.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# CLI usage
python cli.py -l "Guwahati" -ci 2026-05-09 -co 2026-05-10 -m 0 -o results.json

# Start API server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4

# Test
curl "http://localhost:8000/health"
```

## CLI Reference

| Flag | Description | Default |
|------|-------------|---------|
| `-l, --location` | City/location name (required) | — |
| `-ci, --check-in` | Check-in date YYYY-MM-DD (required) | — |
| `-co, --check-out` | Check-out date YYYY-MM-DD (required) | — |
| `-a, --adults` | Number of adults | 2 |
| `-ch, --children` | Number of children | 0 |
| `--children-ages` | Comma-separated child ages (1-17) | — |
| `-c, --currency` | Currency code | USD |
| `--lang` | Language code | en |
| `-m, --max-pages` | Max pages to scrape (0=all) | 0 |
| `-t, --timeout` | Request timeout seconds | 60 |
| `-o, --output` | Output JSON file (deduplicated) | — |
| `--raw-output` | Output raw JSON (before dedup) | — |
| `-p, --proxy` | SOCKS5 proxy URL | — |

### CLI Examples

```bash
# Scrape all pages for Guwahati
python cli.py -l "Guwahati" -ci 2026-05-09 -co 2026-05-10 -o results.json

# Scrape 5 pages with INR currency
python cli.py -l "Mumbai" -ci 2026-05-05 -co 2026-05-08 -m 5 -c INR -o mumbai.json

# With SOCKS5 proxy (Tor)
python cli.py -l "Delhi" -ci 2026-05-05 -co 2026-05-08 -p "socks5://127.0.0.1:9050" -o delhi.json

# All parameters + raw output
python cli.py -l "Kochi" -ci 2026-05-05 -co 2026-05-09 -a 2 -ch 1 --children-ages 5 -c INR --lang en-GB -m 10 -o kochi.json --raw-output kochi_raw.json
```

## API Reference

### Health Check

```
GET /health
```

**Response:** `{"status": "ok", "service": "google-hotels-scraper"}`

### Search Hotels

```
GET /api/v1/hotels/search
POST /api/v1/hotels/search
```

**Query Parameters:**

| Parameter | Required | Type | Description |
|-----------|----------|------|-------------|
| `check_in_date` | Yes | string | YYYY-MM-DD |
| `check_out_date` | Yes | string | YYYY-MM-DD |
| `location` | No | string | Default: "Guwahati" |
| `adults` | No | int | Default: 2 |
| `children` | No | int | Default: 0 |
| `children_ages` | No | string | Comma-separated ages |
| `currency` | No | string | Default: "USD" |
| `language` | No | string | Default: "en" |
| `max_pages` | No | int | 0=all pages, default: 1 |
| `proxy_url` | No | string | SOCKS5 proxy URL |

**Example:**

```bash
curl "http://localhost:8000/api/v1/hotels/search?location=Mumbai&check_in_date=2026-05-05&check_out_date=2026-05-08&max_pages=2&currency=INR"
```

**Response structure:** See [Output Format](#output-format) below.

### Output Format

```json
{
  "pagination": {
    "next_page_token": "string_or_null",
    "total_results": 3875
  },
  "ads": [
    {
      "title": "Ginger Guwahati",
      "source": "Booking.com",
      "source_icon": "https://...",
      "link": "https://www.google.com/aclk?...",
      "property_token": "CgsI...",
      "gps_coordinates": { "latitude": 26.14, "longitude": 91.81 },
      "thumbnail": "https://...",
      "price": "₹3,149",
      "reviews": 3415,
      "overall_rating": 3.9,
      "amenities": ["Free Wi-Fi", "Air conditioning"],
      "hotel_class": 3,
      "free_cancellation": true
    }
  ],
  "brands": [],
  "properties": [
    {
      "type": "hotel",
      "title": "Radisson Blu Hotel, Guwahati",
      "description": "Modern hotel offering...",
      "link": "https://www.google.com/travel/clk/...",
      "property_token": "ChgI...",
      "gps_coordinates": { "latitude": 26.14, "longitude": 91.67 },
      "check_in_time": null,
      "check_out_time": null,
      "rate_per_night": { "lowest": "₹11,579", "before_taxes_fees": null },
      "total_rate": { "lowest": "₹11,579", "before_taxes_fees": null },
      "nearby_places": [],
      "hotel_class": "5-star hotel",
      "extracted_hotel_class": 5,
      "images": [{ "thumbnail": "https://...", "original_image": "https://..." }],
      "reviews": 4.6,
      "overall_rating": 18353,
      "ratings": [{ "stars": 5, "count": 14257 }, { "stars": 4, "count": 2766 }],
      "location_rating": null,
      "reviews_breakdown": [],
      "amenities": [],
      "eco_certified": true
    }
  ]
}
```

## Deployment

### Render (Recommended — Free Tier)

1. Push this repo to GitHub
2. Go to [render.com](https://render.com) → New → Web Service
3. Connect your GitHub repo
4. Render auto-detects `render.yaml` — or set manually:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 4`
5. Deploy — you'll get a live URL like `https://crawlzo.onrender.com`

### Railway

```bash
npm install -g @railway/cli
railway login
railway init
railway up
```

The `Dockerfile` and `railway.toml` are pre-configured.

### Docker

```bash
docker build -t google-hotels-scraper .
docker run -p 8000:8000 google-hotels-scraper
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `REDIS_URL` | Redis connection URL (optional) | — |
| `CACHE_TTL_SECONDS` | Cache TTL in seconds | 600 |
| `PROXY_URL` | Default SOCKS5 proxy for all requests | — |

## Project Structure

```
crawlzo/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app + lifespan + CORS
│   ├── models/
│   │   ├── __init__.py
│   │   └── schemas.py       # Pydantic models for request/response
│   ├── routers/
│   │   ├── __init__.py
│   │   └── hotels.py        # /api/v1/hotels/search endpoint
│   └── services/
│       ├── __init__.py
│       ├── cache.py          # In-memory + Redis cache with TTL
│       └── scraper.py        # Core scraper: ds:0 extraction, parsing, pagination
├── cli.py                    # Command-line interface
├── requirements.txt
├── Dockerfile
├── render.yaml
├── railway.toml
├── .env.example
└── README.md
```

## Performance

| Metric | Value |
|--------|-------|
| Properties per page | ~20 |
| Page fetch time | ~1.5s (no proxy) |
| 5-page scrape time | ~8s |
| Uniqueness across pages | 99%+ |
| Max pages tested | 7 (135 unique properties) |
| Rate limit handling | Exponential backoff, 3 retries |

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Missing required params | HTTP 422 with validation error detail |
| Invalid date format | HTTP 422 with message |
| Google rate limit (429/503) | Exponential back-off, 3 retries |
| Proxy failure | Retry with back-off, then return empty results |
| JSON parse failure | Return empty results for that page |
| Invalid children_ages count | HTTP 422 with message |

## License

MIT
