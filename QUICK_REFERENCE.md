# 🎯 Quick Reference - Google Hotels Scraper API

## ⚡ 5-Minute Deployment

### Step 1: Go to Render
👉 https://dashboard.render.com/

### Step 2: Create Web Service
- Click "New +" → "Web Service"
- Connect GitHub repo: `HardinAbAurBhiChota/two`
- Render auto-detects config from `render.yaml`

### Step 3: Deploy
- Click "Create Web Service"
- Wait 2-3 minutes
- Get your URL: `https://your-app.onrender.com`

## ✅ Test Your API

```bash
# Health check
curl https://your-app.onrender.com/health

# Search hotels
curl "https://your-app.onrender.com/api/v1/hotels/search?location=Guwahati&check_in_date=2026-05-09&check_out_date=2026-05-10&max_pages=1"
```

## 📋 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/api/v1/hotels/search` | GET/POST | Search hotels |

## 🔑 Required Parameters

- `check_in_date`: YYYY-MM-DD (required)
- `check_out_date`: YYYY-MM-DD (required)
- `location`: City name (default: "Guwahati")
- `max_pages`: Number of pages (default: 1, 0=all)

## 📊 Example Response

```json
{
  "pagination": {
    "total_results": 3874
  },
  "ads": [...],
  "properties": [...]
}
```

## 🎓 Full Documentation

See `DEPLOYMENT_GUIDE.md` for complete API documentation, examples, and troubleshooting.

## 🚀 Your API Features

- ✅ Real-time Google Hotels data
- ✅ Pagination support (multiple pages)
- ✅ Cache system (600s TTL)
- ✅ Rate limit handling
- ✅ Multiple currencies
- ✅ Hotel details (price, rating, amenities, images)
- ✅ GPS coordinates
- ✅ Free tier deployment (Render/Railway)

## 📞 Quick Links

- **Render**: https://render.com
- **Railway**: https://railway.app
- **Your Repo**: https://github.com/HardinAbAurBhiChota/two

---

**You're ready to deploy! 🎉**
