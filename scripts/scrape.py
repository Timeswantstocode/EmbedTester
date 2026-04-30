#!/usr/bin/env python3
"""
FAM Source Verifier - AI Scraper
Fetches provider list from rentry.co/onbksdgu via Jina Reader.
Uses Gemma models (via Google GenAI API) to intelligently parse the provider's
page and extract accurate movie/TV embed URLs and customization instructions.
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
TMDB_MOVIE_ID = "129"  # Spirited Away
TMDB_TV_ID = "1399"    # Game of Thrones
OUTPUT_FILE = "sources.json"
JINA_DELAY = 3.2

HEADERS = {
    "User-Agent": "FAMSourceVerifier/2.0 (github-actions AI-mode)",
    "Accept": "text/plain",
}

def jina_get(url: str) -> str:
    """Fetch a URL through Jina Reader and return text."""
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
        print("  [ERROR] GEMINI_API_KEY environment variable is not set.")
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
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode('utf-8'))
            text = result['candidates'][0]['content']['parts'][0]['text']
            # Clean possible markdown wrap
            text = text.replace('```json', '').replace('```', '').strip()
            return json.loads(text)
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode('utf-8')
        print(f"  [Gemma HTTP Error {model_name}]: {e.code} - {err_msg}")
        return None
    except Exception as e:
        print(f"  [Gemma Error {model_name}]: {e}")
        return None

def extract_with_ai(homepage: str, page_text: str) -> dict:
    """Use Gemma models to extract embed URLs and customization instructions."""
    
    prompt = f"""System Instruction:
You are an expert web scraper and media streaming link extractor.
You are given the markdown text of a webpage (from a streaming provider) that lists its API or embed URL formats.
Your task is to extract the exact base URL or template URL used to embed movies and TV shows.

IMPORTANT INSTRUCTIONS:
1. Identify the embed URL template for Movies. Replace any TMDB ID placeholder with "{TMDB_MOVIE_ID}".
2. Identify the embed URL template for TV Shows. Replace TMDB ID with "{TMDB_TV_ID}", Season with "1", and Episode with "1".
3. Check if the provider's documentation mentions color toggles, theme customization, or player configuration via URL parameters (e.g., `&theme=dark`, `&color=ff0000`). If they do, write clear LLM-friendly instructions on how another AI system should construct the URL with these customizations.
4. Output ONLY valid JSON matching the schema below. No markdown backticks.

Expected JSON schema:
{{
  "movie_embed": "https://example.com/embed/movie/{TMDB_MOVIE_ID}",
  "tv_embed": "https://example.com/embed/tv/{TMDB_TV_ID}/1/1",
  "customization_instructions": "To use a dark theme, append &theme=dark to the URL...",
  "confidence_score": 95
}}
If a link type is not found, leave the string empty. If no customizations exist, leave it empty.

User Prompt:
Provider Homepage: {homepage}

Webpage Content:
{page_text}
"""

    models = ["gemma-4-31b-it", "gemma-4-26b-a4b-it"]
    max_attempts = 4
    current_model_idx = 0
    
    for attempt in range(max_attempts):
        model = models[current_model_idx]
        print(f"    -> Attempt {attempt + 1}: Asking {model}...")
        
        result = ask_gemma(prompt, model)
        
        if result and (result.get('movie_embed') or result.get('tv_embed')):
            print(f"    + AI Success ({model})")
            return result
        
        # If we reach here, it either failed (None) or returned empty fields
        print(f"    - Attempt {attempt + 1} failed or empty with {model}")
        
        # Alternate model for next attempt
        current_model_idx = 1 - current_model_idx
        time.sleep(2 + (attempt * 2)) # Increasing backoff
            
    return {}

def fallback_url(homepage: str) -> str:
    """Absolute fallback if both AI models fail or API key is missing."""
    try:
        hostname = urlparse(homepage).hostname or homepage
    except Exception:
        hostname = homepage
    return f"https://{hostname}/embed/movie/{TMDB_MOVIE_ID}"

def parse_rentry(text: str) -> list[dict]:
    """Extract provider entries from the Rentry markdown."""
    providers = []
    seen_urls = set()
    pattern = re.compile(r'\*\s+\[([^\]]+)\]\((https?://[^\)]+)\)')
    for m in pattern.finditer(text):
        name = m.group(1).strip()
        url  = m.group(2).strip()
        skip_fragments = ["rentry.co", "t.me/", "discord.", "npmjs.", "sub.wyzie", "theintrodb", "wyzie-lib"]
        if any(f in url for f in skip_fragments):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        providers.append({"name": name, "homepage": url})
    return providers

def main():
    print("=== FAM Source Verifier — AI Scraper ===")
    print(f"Target TMDB Movie ID: {TMDB_MOVIE_ID}")
    print(f"Rentry URL: {RENTRY_URL}\n")
    
    if not os.environ.get("GEMINI_API_KEY"):
        print("WARNING: GEMINI_API_KEY is not set. AI extraction will fail.")

    print("[1/3] Fetching provider list from Rentry...")
    rentry_text = jina_get(RENTRY_URL)
    if not rentry_text:
        print("FATAL: Could not fetch Rentry page. Aborting.")
        return

    providers = parse_rentry(rentry_text)
    print(f"      Found {len(providers)} providers.\n")
    time.sleep(JINA_DELAY)

    print("[2/3] Resolving embed URLs with Gemma...")
    results = []
    for i, p in enumerate(providers):
        name     = p["name"]
        homepage = p["homepage"]
        print(f"  [{i+1}/{len(providers)}] {name}")

        time.sleep(JINA_DELAY)
        page_text = jina_get(homepage)
        
        embed = ""
        tv_embed = ""
        custom_instructions = ""
        source = "fallback"
        
        if page_text:
            ai_data = extract_with_ai(homepage, page_text)
            if ai_data:
                embed = ai_data.get('movie_embed', '')
                tv_embed = ai_data.get('tv_embed', '')
                custom_instructions = ai_data.get('customization_instructions', '')
                source = "ai_gemma"
                print(f"      Movie: {embed}")
                print(f"      TV:    {tv_embed}")
                if custom_instructions:
                    print(f"      Customization: {custom_instructions[:60]}...")
            else:
                embed = fallback_url(homepage)
                print(f"      ~ fallback -> {embed}")
        else:
            embed = fallback_url(homepage)
            print(f"      ~ fallback (no page) -> {embed}")

        results.append({
            "name":     name,
            "homepage": homepage,
            "embed":    embed,
            "tv_embed": tv_embed,
            "customizations": custom_instructions,
            "source":   source,
        })

    print("\n[3/3] Writing sources.json...")
    output = {
        "generated":  datetime.now(timezone.utc).isoformat(),
        "tmdb_id":    TMDB_MOVIE_ID,
        "count":      len(results),
        "providers":  results,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"      Wrote {len(results)} providers to {OUTPUT_FILE}")
    print("\n=== Done ===")

if __name__ == "__main__":
    main()
