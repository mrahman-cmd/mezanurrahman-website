import json
import os
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# Google News RSS search query.
# You can change the words after q= to search for different topics.
FEED_URL = (
    "https://news.google.com/rss/search?"
    "q=plant+science+OR+botany+OR+crop+research+OR+plant+biology"
    "&hl=en-US&gl=US&ceid=US:en"
)

request = urllib.request.Request(
    FEED_URL,
    headers={
        "User-Agent": "Mozilla/5.0 PlantScienceWebsiteNewsTicker/1.0"
    }
)

with urllib.request.urlopen(request, timeout=30) as response:
    xml_data = response.read()

root = ET.fromstring(xml_data)

news_items = []
seen_titles = set()

for item in root.findall(".//item"):
    title = (item.findtext("title") or "").strip()
    link = (item.findtext("link") or "").strip()
    published = (item.findtext("pubDate") or "").strip()

    source_element = item.find("source")
    source = source_element.text.strip() if source_element is not None and source_element.text else "News source"

    if not title or not link or title in seen_titles:
        continue

    seen_titles.add(title)

    news_items.append({
        "title": title,
        "link": link,
        "source": source,
        "published": published
    })

    # Keep the ticker short and readable.
    if len(news_items) >= 10:
        break

output = {
    "updatedAt": datetime.now(timezone.utc).isoformat(),
    "items": news_items
}

os.makedirs("data", exist_ok=True)

with open("data/plant-news.json", "w", encoding="utf-8") as file:
    json.dump(output, file, ensure_ascii=False, indent=2)

print(f"Saved {len(news_items)} plant-science news items.")
