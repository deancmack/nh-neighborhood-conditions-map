#!/usr/bin/env python3
"""
New Haven Neighborhood Conditions Map Generator
Pulls SeeClickFix 311 data, generates a Claude narrative,
and writes a self-contained HTML map for iframe embedding.
"""

import json
import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

import requests
import anthropic

# ─────────────────────────────────────────────
# NEIGHBORHOOD BOUNDING BOXES
# Add more neighborhoods here as needed.
# Format: [min_lat, min_lng, max_lat, max_lng]
# ─────────────────────────────────────────────
NEIGHBORHOODS = {
    "Dixwell": {
        "bbox": [41.318, -72.948, 41.338, -72.928],
        "center": [41.328, -72.938],
        "zoom": 15,
    },
    "Newhallville": {
        "bbox": [41.330, -72.960, 41.348, -72.938],
        "center": [41.339, -72.949],
        "zoom": 15,
    },
    "Fair Haven": {
        "bbox": [41.295, -72.915, 41.318, -72.893],
        "center": [41.307, -72.904],
        "zoom": 15,
    },
    "Hill": {
        "bbox": [41.290, -72.945, 41.310, -72.920],
        "center": [41.300, -72.932],
        "zoom": 15,
    },
    "Dwight": {
        "bbox": [41.298, -72.950, 41.315, -72.932],
        "center": [41.306, -72.941],
        "zoom": 15,
    },
    "West River": {
        "bbox": [41.298, -72.965, 41.318, -72.948],
        "center": [41.308, -72.957],
        "zoom": 15,
    },
    "Beaver Hills": {
        "bbox": [41.318, -72.970, 41.338, -72.950],
        "center": [41.328, -72.960],
        "zoom": 15,
    },
    "Westville": {
        "bbox": [41.318, -72.985, 41.340, -72.960],
        "center": [41.329, -72.973],
        "zoom": 15,
    },
    "East Rock": {
        "bbox": [41.318, -72.930, 41.342, -72.910],
        "center": [41.330, -72.920],
        "zoom": 15,
    },
}

# ─────────────────────────────────────────────
# ISSUE CATEGORY CONFIGURATION
# Maps SeeClickFix request type slugs to
# display labels and map styling.
# ─────────────────────────────────────────────
CATEGORY_CONFIG = {
    "blight": {
        "label": "Blight / Vacancy",
        "color": "#c0392b",
        "icon": "⚠",
        "keywords": ["blight", "vacant", "abandon", "demolit", "unsafe structure"],
    },
    "pothole": {
        "label": "Pothole / Pavement",
        "color": "#e67e22",
        "icon": "🔶",
        "keywords": ["pothole", "pavement", "road damage", "street repair", "asphalt"],
    },
    "trash": {
        "label": "Illegal Dumping / Litter",
        "color": "#8e44ad",
        "icon": "🗑",
        "keywords": ["dump", "litter", "trash", "garbage", "bulk waste", "illegal dumping"],
    },
    "graffiti": {
        "label": "Graffiti",
        "color": "#2980b9",
        "icon": "✏",
        "keywords": ["graffiti", "vandal", "tagging"],
    },
    "sidewalk": {
        "label": "Sidewalk / Curb",
        "color": "#16a085",
        "icon": "🚶",
        "keywords": ["sidewalk", "curb", "pedestrian", "crosswalk", "ADA"],
    },
    "lighting": {
        "label": "Street Light",
        "color": "#f39c12",
        "icon": "💡",
        "keywords": ["street light", "light out", "lighting", "lamp"],
    },
    "vegetation": {
        "label": "Trees / Vegetation",
        "color": "#27ae60",
        "icon": "🌳",
        "keywords": ["tree", "branch", "overgrow", "vegetation", "bush"],
    },
    "other": {
        "label": "Other",
        "color": "#7f8c8d",
        "icon": "●",
        "keywords": [],
    },
}


def categorize_issue(summary: str, request_type: str) -> str:
    """Map a SeeClickFix issue to our internal category."""
    text = (summary + " " + request_type).lower()
    for category, config in CATEGORY_CONFIG.items():
        if category == "other":
            continue
        if any(kw in text for kw in config["keywords"]):
            return category
    return "other"


def fetch_scf_issues(bbox: list, days_back: int = 365) -> list:
    """
    Pull issues from SeeClickFix Open311 API for a bounding box.
    Returns a list of issue dicts.
    """
    min_lat, min_lng, max_lat, max_lng = bbox
    after_date = (datetime.utcnow() - timedelta(days=days_back)).strftime(
        "%Y-%m-%dT00:00:00Z"
    )

    all_issues = []
    page = 1

    print(f"  Fetching SeeClickFix issues (bbox: {bbox})...")

    while True:
        url = "https://seeclickfix.com/api/v2/issues"
        params = {
            "min_lat": min_lat,
            "min_lng": min_lng,
            "max_lat": max_lat,
            "max_lng": max_lng,
            "after": after_date,
            "per_page": 100,
            "page": page,
            "status": "open,acknowledged,closed",  # all statuses for density/heat
        }
        headers = {"User-Agent": "NewHaven-NeighborhoodPlanMapper/1.0"}

        try:
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"  ⚠ SCF API error on page {page}: {e}")
            break

        issues = data.get("issues", [])
        if not issues:
            break

        all_issues.extend(issues)
        print(f"    Page {page}: {len(issues)} issues (total: {len(all_issues)})")

        # Check if there are more pages
        metadata = data.get("metadata", {})
        if page >= metadata.get("pages", 1):
            break
        page += 1

    print(f"  ✓ Fetched {len(all_issues)} total issues")

    # Debug: show what statuses are coming back from SCF
    status_counts = defaultdict(int)
    for i in all_issues:
        status_counts[i.get("status", "unknown")] += 1
    print(f"  Status breakdown: {dict(status_counts)}")

    return all_issues


def process_issues(raw_issues: list) -> dict:
    """
    Process raw SCF issues into structured data for mapping and analysis.
    Returns dict with open issues, heat data, and category counts.
    """
    open_issues = []
    all_points = []  # for heatmap (includes closed)
    category_counts = defaultdict(int)
    monthly_counts = defaultdict(int)

    for issue in raw_issues:
        lat = issue.get("lat")
        lng = issue.get("lng")
        if not lat or not lng:
            continue

        summary = issue.get("summary", "")
        request_type = issue.get("request_type", {})
        rt_title = request_type.get("title", "") if isinstance(request_type, dict) else str(request_type)
        status = issue.get("status", "open")
        created_at = issue.get("created_at", "")
        address = issue.get("address", "Unknown location")

        category = categorize_issue(summary, rt_title)
        category_counts[category] += 1

        # Monthly bucketing for sparkline/trend
        if created_at:
            try:
                month_key = created_at[:7]  # "YYYY-MM"
                monthly_counts[month_key] += 1
            except Exception:
                pass

        # All points go to heatmap
        all_points.append([lat, lng, 1])

        # Any non-closed status goes to marker layer
        if status.lower() not in ("closed", "archived"):
            open_issues.append(
                {
                    "lat": lat,
                    "lng": lng,
                    "summary": summary[:120],
                    "category": category,
                    "status": status,
                    "created_at": created_at[:10] if created_at else "",
                    "address": address,
                    "url": issue.get("html_url", ""),
                    "id": issue.get("id", ""),
                }
            )

    # Sort monthly counts for chart
    sorted_months = sorted(monthly_counts.items())

    return {
        "open_issues": open_issues,
        "heat_points": all_points,
        "category_counts": dict(category_counts),
        "monthly_trend": sorted_months,
        "total_all_time": len(raw_issues),
        "total_open": len(open_issues),
    }


def generate_narrative(neighborhood: str, processed: dict) -> str:
    """
    Call Claude API to generate a plain-language conditions narrative
    suitable for a neighborhood plan document.
    """
    # ── API key: set ANTHROPIC_API_KEY as an environment variable ────────────
    # In CMD run: setx ANTHROPIC_API_KEY "sk-ant-your-key-here"
    # Then close and reopen CMD before running the script.
    api_key = os.environ.get("ANTHROPIC_API_KEY", "YOUR_ANTHROPIC_API_KEY_HERE")
    if not api_key or api_key == "YOUR_ANTHROPIC_API_KEY_HERE":
        return "Narrative unavailable: set ANTHROPIC_API_KEY as an environment variable."

    client = anthropic.Anthropic(api_key=api_key)

    category_summary = "\n".join(
        f"  - {CATEGORY_CONFIG.get(k, {}).get('label', k)}: {v} issues"
        for k, v in sorted(
            processed["category_counts"].items(), key=lambda x: -x[1]
        )
    )

    # Build trend description
    trend_data = processed["monthly_trend"]
    if trend_data:
        recent_3 = trend_data[-3:]
        trend_str = ", ".join(f"{m}: {c}" for m, c in recent_3)
    else:
        trend_str = "insufficient data"

    prompt = f"""You are an analyst supporting a municipal neighborhood planning process in New Haven, CT.
    
Below is 311 service request data from SeeClickFix for the {neighborhood} neighborhood, covering the past year.

SUMMARY:
- Total issues reported (all time in dataset): {processed['total_all_time']}
- Currently open/unresolved issues: {processed['total_open']}
- Issues by category:
{category_summary}
- Recent monthly trend (last 3 months): {trend_str}

Write a concise conditions assessment (3-4 sentences) suitable for inclusion in a neighborhood plan. 
Focus on: dominant issue types, geographic concentration if inferable, any notable patterns, and what 
the data suggests about infrastructure and maintenance needs. Use plain language appropriate for both 
planning staff and community members. Do not use bullet points. Do not mention SeeClickFix by name — 
refer to it as "resident-reported service requests" or "311 data." End with one sentence about implications 
for the development plan."""

    try:
        message = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception as e:
        return f"Narrative generation error: {e}"


def build_html(neighborhood: str, config: dict, processed: dict, narrative: str) -> str:
    """
    Build a self-contained HTML file with Leaflet map, heatmap layer,
    category markers, and Claude-generated narrative panel.
    Designed for iframe embedding in a neighborhood plan site.
    """
    generated_at = datetime.now().strftime("%B %d, %Y at %I:%M %p")
    open_issues_json = json.dumps(processed["open_issues"])
    heat_points_json = json.dumps(processed["heat_points"])
    category_config_json = json.dumps(
        {k: {"label": v["label"], "color": v["color"]} for k, v in CATEGORY_CONFIG.items()}
    )
    category_counts_json = json.dumps(processed["category_counts"])
    monthly_trend_json = json.dumps(processed["monthly_trend"])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{neighborhood} Neighborhood Conditions — New Haven</title>

<!-- Leaflet -->
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"/>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>

<!-- Leaflet Heatmap -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet.heat/0.2.0/leaflet-heat.js"></script>

<!-- Google Fonts -->
<link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">

<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --navy:   #1a2744;
    --teal:   #2a7f6f;
    --gold:   #c8a84b;
    --cream:  #f5f0e8;
    --slate:  #4a5568;
    --light:  #eef2f7;
    --white:  #ffffff;
    --radius: 6px;
    --shadow: 0 2px 12px rgba(0,0,0,0.10);
  }}

  html, body {{
    height: 100%;
    width: 100%;
    font-family: 'DM Sans', sans-serif;
    font-size: 13px;
    background: var(--cream);
    color: var(--navy);
    overflow: hidden;
  }}

  /* ── Layout ── */
  #app {{
    display: grid;
    grid-template-rows: auto 1fr auto;
    grid-template-columns: 300px 1fr;
    height: 100vh;
    width: 100vw;
  }}

  #header {{
    grid-column: 1 / -1;
    background: var(--navy);
    color: var(--white);
    padding: 10px 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }}

  #header h1 {{
    font-family: 'DM Serif Display', serif;
    font-size: 16px;
    letter-spacing: 0.02em;
  }}

  #header .subtitle {{
    font-size: 11px;
    color: rgba(255,255,255,0.6);
    font-weight: 300;
  }}

  #header .badge {{
    background: var(--gold);
    color: var(--navy);
    font-size: 10px;
    font-weight: 600;
    padding: 3px 8px;
    border-radius: 20px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    white-space: nowrap;
  }}

  /* ── Sidebar ── */
  #sidebar {{
    grid-column: 1;
    grid-row: 2;
    background: var(--white);
    overflow-y: auto;
    border-right: 1px solid #dde3ed;
    display: flex;
    flex-direction: column;
  }}

  .panel {{
    padding: 14px 16px;
    border-bottom: 1px solid #eef0f4;
  }}

  .panel-title {{
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--slate);
    margin-bottom: 10px;
  }}

  /* Narrative */
  #narrative-text {{
    font-size: 12px;
    line-height: 1.65;
    color: var(--navy);
    font-style: italic;
  }}

  /* Stats row */
  .stats-row {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }}

  .stat-box {{
    background: var(--light);
    border-radius: var(--radius);
    padding: 10px;
    text-align: center;
  }}

  .stat-value {{
    font-family: 'DM Serif Display', serif;
    font-size: 22px;
    color: var(--navy);
    line-height: 1;
  }}

  .stat-label {{
    font-size: 10px;
    color: var(--slate);
    margin-top: 3px;
    font-weight: 500;
  }}

  /* Category legend / filter */
  .category-row {{
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5px 0;
    cursor: pointer;
    border-radius: 4px;
    padding-left: 4px;
    transition: background 0.15s;
  }}

  .category-row:hover {{ background: var(--light); }}
  .category-row.inactive {{ opacity: 0.35; }}

  .cat-dot {{
    width: 10px;
    height: 10px;
    border-radius: 50%;
    flex-shrink: 0;
  }}

  .cat-label {{
    flex: 1;
    font-size: 12px;
  }}

  .cat-count {{
    font-size: 11px;
    font-weight: 600;
    color: var(--slate);
    min-width: 22px;
    text-align: right;
  }}

  /* View toggles */
  .toggle-group {{
    display: flex;
    gap: 6px;
  }}

  .toggle-btn {{
    flex: 1;
    padding: 6px 8px;
    border: 1.5px solid #dde3ed;
    background: var(--white);
    border-radius: var(--radius);
    font-family: 'DM Sans', sans-serif;
    font-size: 11px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.15s;
    color: var(--slate);
  }}

  .toggle-btn.active {{
    background: var(--navy);
    color: var(--white);
    border-color: var(--navy);
  }}

  /* Mini chart */
  #trend-chart {{
    width: 100%;
    height: 60px;
    margin-top: 4px;
  }}

  /* ── Map ── */
  #map {{
    grid-column: 2;
    grid-row: 2;
    width: 100%;
    height: 100%;
    z-index: 1;
  }}

  /* ── Footer ── */
  #footer {{
    grid-column: 1 / -1;
    background: var(--navy);
    color: rgba(255,255,255,0.5);
    font-size: 10px;
    padding: 5px 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}

  /* Leaflet popup override */
  .leaflet-popup-content-wrapper {{
    border-radius: var(--radius) !important;
    box-shadow: var(--shadow) !important;
    font-family: 'DM Sans', sans-serif !important;
    font-size: 12px !important;
    max-width: 220px;
  }}

  .popup-category {{
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 4px;
  }}

  .popup-summary {{
    font-size: 12px;
    line-height: 1.5;
    color: var(--navy);
    margin-bottom: 6px;
  }}

  .popup-meta {{
    font-size: 10px;
    color: var(--slate);
    line-height: 1.5;
  }}

  .popup-link {{
    display: inline-block;
    margin-top: 6px;
    font-size: 10px;
    color: var(--teal);
    text-decoration: none;
    font-weight: 600;
  }}

  /* Scrollbar */
  #sidebar::-webkit-scrollbar {{ width: 4px; }}
  #sidebar::-webkit-scrollbar-track {{ background: transparent; }}
  #sidebar::-webkit-scrollbar-thumb {{ background: #dde3ed; border-radius: 2px; }}
</style>
</head>
<body>

<div id="app">

  <!-- Header -->
  <div id="header">
    <div>
      <h1>{neighborhood} Neighborhood — Conditions Map</h1>
      <div class="subtitle">New Haven Economic Development · 311 Data Analysis</div>
    </div>
    <div class="badge">Updated Nightly</div>
  </div>

  <!-- Sidebar -->
  <div id="sidebar">

    <!-- Narrative -->
    <div class="panel">
      <div class="panel-title">Conditions Assessment</div>
      <div id="narrative-text">{narrative}</div>
    </div>

    <!-- Stats -->
    <div class="panel">
      <div class="panel-title">At a Glance</div>
      <div class="stats-row">
        <div class="stat-box">
          <div class="stat-value" id="stat-open">{processed['total_open']}</div>
          <div class="stat-label">Open Issues</div>
        </div>
        <div class="stat-box">
          <div class="stat-value" id="stat-total">{processed['total_all_time']}</div>
          <div class="stat-label">Past Year Total</div>
        </div>
      </div>
    </div>

    <!-- View Toggle -->
    <div class="panel">
      <div class="panel-title">Map View</div>
      <div class="toggle-group">
        <button class="toggle-btn active" id="btn-markers" onclick="setView('markers')">Open Issues</button>
        <button class="toggle-btn" id="btn-heat" onclick="setView('heat')">Heat Density</button>
      </div>
    </div>

    <!-- Category Filter -->
    <div class="panel">
      <div class="panel-title">Filter by Category</div>
      <div id="category-list"></div>
    </div>

    <!-- Trend -->
    <div class="panel">
      <div class="panel-title">Monthly Trend (Past Year)</div>
      <canvas id="trend-chart"></canvas>
    </div>

  </div>

  <!-- Map -->
  <div id="map"></div>

  <!-- Footer -->
  <div id="footer">
    <span>Source: SeeClickFix / New Haven 311 · AI narrative by Claude (Anthropic)</span>
    <span>Generated {generated_at}</span>
  </div>

</div>

<script>
// ── Data ──────────────────────────────────────────
const OPEN_ISSUES   = {open_issues_json};
const HEAT_POINTS   = {heat_points_json};
const CATEGORY_CFG  = {category_config_json};
const CATEGORY_CNT  = {category_counts_json};
const MONTHLY_TREND = {monthly_trend_json};
const MAP_CENTER    = [{config['center'][0]}, {config['center'][1]}];
const MAP_ZOOM      = {config['zoom']};

// ── State ─────────────────────────────────────────
const activeCategories = new Set(Object.keys(CATEGORY_CFG));
let currentView = 'markers';
let markerLayer = null;
let heatLayer = null;

// ── Map Init ──────────────────────────────────────
const map = L.map('map', {{
  center: MAP_CENTER,
  zoom: MAP_ZOOM,
  zoomControl: true,
}});

L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
  attribution: '© OpenStreetMap, © CARTO',
  subdomains: 'abcd',
  maxZoom: 19,
}}).addTo(map);

// ── Category Legend ───────────────────────────────
function buildCategoryList() {{
  const container = document.getElementById('category-list');
  container.innerHTML = '';

  const sorted = Object.entries(CATEGORY_CNT)
    .sort((a, b) => b[1] - a[1]);

  for (const [cat, count] of sorted) {{
    if (count === 0) continue;
    const cfg = CATEGORY_CFG[cat] || {{ label: cat, color: '#999' }};
    const row = document.createElement('div');
    row.className = 'category-row' + (activeCategories.has(cat) ? '' : ' inactive');
    row.dataset.cat = cat;
    row.innerHTML = `
      <div class="cat-dot" style="background:${{cfg.color}}"></div>
      <div class="cat-label">${{cfg.label}}</div>
      <div class="cat-count">${{count}}</div>
    `;
    row.addEventListener('click', () => toggleCategory(cat, row));
    container.appendChild(row);
  }}
}}

function toggleCategory(cat, row) {{
  if (activeCategories.has(cat)) {{
    activeCategories.delete(cat);
    row.classList.add('inactive');
  }} else {{
    activeCategories.add(cat);
    row.classList.remove('inactive');
  }}
  if (currentView === 'markers') renderMarkers();
}}

// ── Markers ───────────────────────────────────────
function createMarker(issue) {{
  const cfg = CATEGORY_CFG[issue.category] || {{ color: '#999', label: 'Other' }};
  const icon = L.divIcon({{
    className: '',
    html: `<div style="
      width: 10px; height: 10px;
      background: ${{cfg.color}};
      border: 2px solid white;
      border-radius: 50%;
      box-shadow: 0 1px 4px rgba(0,0,0,0.3);
    "></div>`,
    iconSize: [10, 10],
    iconAnchor: [5, 5],
  }});

  const statusBadge = issue.status === 'acknowledged'
    ? '<span style="color:#c8a84b;font-weight:600"> (Acknowledged)</span>' : '';

  const linkHtml = issue.url
    ? `<a class="popup-link" href="${{issue.url}}" target="_blank">View on SeeClickFix ↗</a>` : '';

  const popup = `
    <div class="popup-category" style="color:${{cfg.color}}">${{cfg.label}}${{statusBadge}}</div>
    <div class="popup-summary">${{issue.summary || 'No description'}}</div>
    <div class="popup-meta">
      📍 ${{issue.address}}<br>
      📅 Reported ${{issue.created_at}}
    </div>
    ${{linkHtml}}
  `;

  return L.marker([issue.lat, issue.lng], {{ icon }}).bindPopup(popup);
}}

function renderMarkers() {{
  if (markerLayer) map.removeLayer(markerLayer);
  markerLayer = L.layerGroup();

  const visible = OPEN_ISSUES.filter(i => activeCategories.has(i.category));
  for (const issue of visible) {{
    createMarker(issue).addTo(markerLayer);
  }}

  markerLayer.addTo(map);
}}

// ── Heatmap ───────────────────────────────────────
function renderHeat() {{
  if (heatLayer) map.removeLayer(heatLayer);
  heatLayer = L.heatLayer(HEAT_POINTS, {{
    radius: 18,
    blur: 20,
    maxZoom: 17,
    gradient: {{
      0.2: '#2a7f6f',
      0.5: '#c8a84b',
      0.8: '#e67e22',
      1.0: '#c0392b',
    }},
  }});
  heatLayer.addTo(map);
}}

// ── View Toggle ───────────────────────────────────
function setView(view) {{
  currentView = view;

  if (view === 'markers') {{
    if (heatLayer) map.removeLayer(heatLayer);
    renderMarkers();
    document.getElementById('btn-markers').classList.add('active');
    document.getElementById('btn-heat').classList.remove('active');
  }} else {{
    if (markerLayer) map.removeLayer(markerLayer);
    renderHeat();
    document.getElementById('btn-heat').classList.add('active');
    document.getElementById('btn-markers').classList.remove('active');
  }}
}}

// ── Mini Trend Chart ──────────────────────────────
function renderTrendChart() {{
  const canvas = document.getElementById('trend-chart');
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;

  const W = canvas.offsetWidth;
  const H = canvas.offsetHeight;
  canvas.width  = W * dpr;
  canvas.height = H * dpr;
  ctx.scale(dpr, dpr);

  if (!MONTHLY_TREND || MONTHLY_TREND.length < 2) {{
    ctx.fillStyle = '#aaa';
    ctx.font = '11px DM Sans';
    ctx.fillText('Not enough data', 10, H / 2);
    return;
  }}

  // Last 12 months
  const data = MONTHLY_TREND.slice(-12);
  const vals  = data.map(d => d[1]);
  const labels = data.map(d => d[0].slice(5)); // "MM"
  const maxVal = Math.max(...vals, 1);

  const pad  = {{ left: 4, right: 4, top: 8, bottom: 16 }};
  const chartW = W - pad.left - pad.right;
  const chartH = H - pad.top - pad.bottom;
  const barW   = chartW / data.length;

  // Bars
  data.forEach((d, i) => {{
    const x  = pad.left + i * barW + barW * 0.1;
    const bw = barW * 0.8;
    const bh = (vals[i] / maxVal) * chartH;
    const y  = pad.top + chartH - bh;

    const alpha = 0.4 + 0.6 * (i / data.length);
    ctx.fillStyle = `rgba(26, 39, 68, ${{alpha}})`;
    ctx.fillRect(x, y, bw, bh);
  }});

  // X labels (every 3rd)
  ctx.fillStyle = '#4a5568';
  ctx.font = `${{9 * dpr / dpr}}px DM Sans`;
  ctx.textAlign = 'center';
  data.forEach((d, i) => {{
    if (i % 3 === 0) {{
      const x = pad.left + i * barW + barW / 2;
      ctx.fillText(labels[i], x, H - 2);
    }}
  }});
}}

// ── Init ──────────────────────────────────────────
buildCategoryList();
renderMarkers();
renderTrendChart();

// Resize canvas on load
window.addEventListener('load', () => setTimeout(renderTrendChart, 100));
</script>
</body>
</html>"""

    return html


def main():
    parser = argparse.ArgumentParser(description="Generate NH neighborhood conditions map")
    parser.add_argument(
        "--neighborhood", "-n",
        default="Fair Haven",
        help="Neighborhood name (must exist in NEIGHBORHOODS dict)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output HTML file path (default: ./<neighborhood>_conditions_map.html)",
    )
    parser.add_argument(
        "--days", "-d",
        type=int,
        default=365,
        help="Number of days back to pull data (default: 365)",
    )
    parser.add_argument(
        "--no-narrative",
        action="store_true",
        help="Skip Claude API call (useful for testing without API key)",
    )
    args = parser.parse_args()

    neighborhood = args.neighborhood
    if neighborhood not in NEIGHBORHOODS:
        available = ", ".join(NEIGHBORHOODS.keys())
        print(f"Error: '{neighborhood}' not found. Available: {available}")
        sys.exit(1)

    config = NEIGHBORHOODS[neighborhood]
    output_path = args.output or f"{neighborhood.lower().replace(' ', '_')}_conditions_map.html"

    print(f"\n{'='*50}")
    print(f"  New Haven Neighborhood Map Generator")
    print(f"  Neighborhood: {neighborhood}")
    print(f"  Output:       {output_path}")
    print(f"  Days back:    {args.days}")
    print(f"{'='*50}\n")

    # 1. Fetch
    print("Step 1/3: Fetching SeeClickFix data...")
    raw_issues = fetch_scf_issues(config["bbox"], days_back=args.days)

    # 2. Process
    print("\nStep 2/3: Processing issues...")
    processed = process_issues(raw_issues)
    print(f"  Open issues:  {processed['total_open']}")
    print(f"  Categories:   {dict(processed['category_counts'])}")

    # 3. Narrative
    print("\nStep 3/3: Generating narrative...")
    if args.no_narrative:
        narrative = f"Conditions data for {neighborhood} loaded. {processed['total_open']} issues currently open across {len(processed['category_counts'])} categories."
    else:
        narrative = generate_narrative(neighborhood, processed)
    print(f"  ✓ Narrative: {narrative[:80]}...")

    # 4. Write HTML
    html = build_html(neighborhood, config, processed, narrative)
    Path(output_path).write_text(html, encoding="utf-8")
    print(f"\n✓ Map written to: {output_path}")
    print(f"  File size: {len(html) / 1024:.1f} KB")
    print(f"\nEmbed in your site with:")
    print(f'  <iframe src="{output_path}" width="100%" height="600" frameborder="0"></iframe>\n')


if __name__ == "__main__":
    main()
