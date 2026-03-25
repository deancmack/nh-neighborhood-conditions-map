# New Haven Neighborhood Conditions Map
## SeeClickFix + Claude API + OpenClaw Pipeline

Pulls 311 data from SeeClickFix, generates a Claude-powered conditions
narrative, and writes a self-contained HTML map file for embedding in a
neighborhood plan site.

---

## What It Produces

A single `.html` file (~50–100 KB) containing:
- **Interactive Leaflet map** with open issue markers, color-coded by category
- **Heatmap layer** showing density of all issues over the past year
- **Category filter** — click any category in the sidebar to toggle it
- **Claude-generated narrative** — 3-4 sentence conditions assessment
- **Monthly trend chart** — bar chart of report volume over 12 months
- **At-a-glance stats** — open issues and past-year total

The file is fully self-contained and iframe-safe. No server required.

---

## Setup

### 1. Install dependencies
```bash
pip install requests anthropic
```

### 2. Set environment variable
```bash
export ANTHROPIC_API_KEY=sk-ant-your-key-here
```

### 3. Run manually
```bash
python generate_map.py --neighborhood "Dixwell"
# Output: dixwell_conditions_map.html
```

Other available neighborhoods out of the box:
- Newhallville, Fair Haven, Hill, Dwight, West River,
  Beaver Hills, Westville, East Rock

To add a neighborhood, add an entry to the `NEIGHBORHOODS` dict in
`generate_map.py` with the bounding box, center, and zoom level.

### 4. Test without API key
```bash
python generate_map.py --neighborhood "Dixwell" --no-narrative
```

---

## Embed in Your Site

Drop this wherever you want the map to appear in your neighborhood plan site:

```html
<iframe
  src="/maps/dixwell_conditions_map.html"
  width="100%"
  height="620"
  frameborder="0"
  style="border-radius: 8px; box-shadow: 0 2px 12px rgba(0,0,0,0.1);"
  title="Dixwell Neighborhood Conditions Map"
></iframe>
```

For a full-width responsive embed:
```html
<div style="position: relative; padding-bottom: 56.25%; height: 0; overflow: hidden;">
  <iframe
    src="/maps/dixwell_conditions_map.html"
    style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; border: none;"
    title="Dixwell Neighborhood Conditions Map"
  ></iframe>
</div>
```

---

## Automate with OpenClaw

1. Copy `openclaw_skill.yaml` to your OpenClaw skills directory
2. Update the `command` paths to match your file locations
3. Set `NH_NEIGHBORHOOD` and `NH_MAP_OUTPUT_DIR` in your OpenClaw env
4. The map regenerates every night at 2 AM automatically

You can also trigger manually by messaging your agent:
> "regenerate the neighborhood map"
> "update conditions map for Fair Haven"

---

## Adding Neighborhoods

Edit the `NEIGHBORHOODS` dict in `generate_map.py`:

```python
"Your Neighborhood": {
    "bbox": [min_lat, min_lng, max_lat, max_lng],
    "center": [center_lat, center_lng],
    "zoom": 15,  # 14–16 typically works well for neighborhood scale
},
```

You can get bounding box coordinates from:
- https://bboxfinder.com (draw a box, copy the coords)
- Google Maps (right-click for lat/lng)

---

## Architecture

```
OpenClaw cron (2 AM nightly)
    │
    ▼
generate_map.py
    │
    ├── fetch_scf_issues()     → SeeClickFix Open311 API (public, no key needed)
    │                            Pulls all issues in bounding box, past 365 days
    │
    ├── process_issues()       → Categorizes, buckets by month, separates open vs. all
    │
    ├── generate_narrative()   → Claude API (claude-opus-4-6)
    │                            Returns 3-4 sentence conditions assessment
    │
    └── build_html()           → Self-contained HTML with Leaflet + heatmap
                                 Written to output path, ready to iframe
```

---

## Notes

- SeeClickFix public API requires no authentication for read access
- Rate limit is 100 issues/page; script handles pagination automatically
- The HTML file is overwritten in place each night — no cache-busting needed
- The map uses CartoDB light tiles (free, no API key required)
- Narrative uses `claude-opus-4-6` — swap to `claude-sonnet-4-6` to reduce cost
