#!/usr/bin/env python3
"""
FAM Source Verifier - AI Batch Scraper
Processes streaming providers in batches using Gemma models (256K context).
"""

import re
import json
import time
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone
from urllib.parse import urlparse

JINA_BASE = "https://r.jina.ai/"
RENTRY_URL = "https://rentry.co/onbksdgu"
TMDB_MOVIE_ID = "129"
TMDB_TV_ID = "1399"
OUTPUT_FILE = "sources.json"
JINA_DELAY = 3.2
BATCH_SIZE = 5  # Gemma 256K can easily handle 5+ full provider pages

HEADERS = {
    "User-Agent": "FAMSourceVerifier/3.0 (github-actions AI-batch)",
    "Accept": "text/plain",
}

def jina_get(url: str) -> str:
    try:
        req = urllib.request.Request(JINA_BASE + url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.read().decode('utf-8')
    except Exception as e:
        print(f"  [JINA ERROR] {url}: {e}")
        return ""

def ask_gemma(prompt: str, model_name: str) -> dict | None:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    payload = {
        "contents": [{ "role": "user", "parts": [{"text": prompt}] }],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json"
        }
    }
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            result = json.loads(response.read().decode('utf-8'))
            text = result['candidates'][0]['content']['parts'][0]['text']
            text = text.replace('```json', '').replace('```', '').strip()
            return json.loads(text)
    except Exception as e:
        print(f"  [Gemma Error {model_name}]: {e}")
        return None

def extract_batch_with_ai(batch: list[dict]) -> list[dict]:
    """Process multiple providers in one LLM call to save time and tokens."""
    
    providers_input = ""
    for i, p in enumerate(batch):
        providers_input += f"\n--- PROVIDER {i+1} ---\nNAME: {p['name']}\nHOMEPAGE: {p['homepage']}\nCONTENT:\n{p['text']}\n"

    prompt = f"""System Instruction:
You are an expert web scraper. You are given the text of {len(batch)} different streaming provider websites.
Extract the Movie and TV embed URL templates for EACH provider.

RULES:
1. Movie Embed: Replace TMDB ID placeholder with "{TMDB_MOVIE_ID}".
2. TV Embed: Replace TMDB ID with "{TMDB_TV_ID}", Season with "1", Episode with "1".
3. Customizations: If the docs mention theme/color/config params, write clear LLM-friendly instructions.
4. Return a JSON object with a "results" array.

Expected JSON schema:
{{
  "results": [
    {{
      "name": "Provider Name",
      "movie_embed": "https://...",
      "tv_embed": "https://...",
      "customizations": "..."
    }}
  ]
}}

Providers to analyze:
{providers_input}
"""

    models = ["gemma-4-31b-it", "gemma-4-26b-a4b-it"]
    for attempt in range(3):
        model = models[attempt % 2]
        print(f"    -> Batch Attempt {attempt + 1} ({model})...")
        res = ask_gemma(prompt, model)
        if res and "results" in res:
            return res["results"]
        time.sleep(5)
    return []

def fallback_url(homepage: str) -> str:
    try: hostname = urlparse(homepage).hostname or homepage
    except Exception: hostname = homepage
    return f"https://{hostname}/embed/movie/{TMDB_MOVIE_ID}"

def parse_rentry(text: str) -> list[dict]:
    providers = []
    seen_urls = set()
    pattern = re.compile(r'\*\s+\[([^\]]+)\]\((https?://[^\)]+)\)')
    for m in pattern.finditer(text):
        name, url = m.group(1).strip(), m.group(2).strip()
        if any(f in url for f in ["rentry.co", "t.me/", "discord.", "npmjs.", "sub.wyzie", "theintrodb"]): continue
        if url in seen_urls: continue
        seen_urls.add(url)
        providers.append({"name": name, "homepage": url})
    return providers

def main():
    print("=== FAM Source Verifier — AI Batch Scraper ===")
    if not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: GEMINI_API_KEY not found.")
        return

    rentry_text = jina_get(RENTRY_URL)
    if not rentry_text: return
    providers = parse_rentry(rentry_text)
    print(f"Found {len(providers)} providers. Processing in batches of {BATCH_SIZE}...\n")

    final_results = []
    for i in range(0, len(providers), BATCH_SIZE):
        batch_slice = providers[i : i + BATCH_SIZE]
        print(f"  [Batch {i//BATCH_SIZE + 1}] Processing {len(batch_slice)} providers...")
        
        # Step 1: Fetch all pages in batch via Jina
        batch_data = []
        for p in batch_slice:
            print(f"    Fetching {p['name']}...")
            text = jina_get(p['homepage'])
            batch_data.append({**p, "text": text or "NO CONTENT FOUND"})
            time.sleep(JINA_DELAY)

        # Step 2: AI extraction for the whole batch
        ai_results = extract_batch_with_ai(batch_data)
        
        # Step 3: Merge and fallback
        for p in batch_data:
            match = next((r for r in ai_results if r.get('name') == p['name']), None)
            res = {
                "name": p['name'],
                "homepage": p['homepage'],
                "embed": match.get('movie_embed') if match else "",
                "tv_embed": match.get('tv_embed') if match else "",
                "customizations": match.get('customizations') if match else "",
                "source": "ai_gemma_batch" if match else "fallback"
            }
            if not res["embed"]: res["embed"] = fallback_url(p['homepage'])
            final_results.append(res)
            print(f"      - {p['name']}: {'Success' if match else 'Fallback'}")

    output = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "tmdb_id": TMDB_MOVIE_ID,
        "count": len(final_results),
        "providers": final_results,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nDone. Wrote {len(final_results)} providers to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
