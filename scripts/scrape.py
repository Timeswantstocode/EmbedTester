#!/usr/bin/env python3
"""
FAM Source Verifier - AI Batch Scraper
Processes streaming providers in batches using Gemma models (256K context).
"""

import re
import json
import time
import os
import sys
import urllib.request
import urllib.error
import random
from datetime import datetime, timezone
from urllib.parse import urlparse

# Ensure stdout handles UTF-8 (crucial for Windows terminal)
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

JINA_BASE = "https://r.jina.ai/"
RENTRY_URL = "https://rentry.co/onbksdgu"
TMDB_MOVIE_ID = "129"
TMDB_TV_ID = "1399"
OUTPUT_FILE = "sources.json"
JINA_DELAY = 3.2
BATCH_SIZE = 5  # Gemma 256K can easily handle 5+ full provider pages

# Jina Reader headers — default to Markdown (no X-Return-Format: text)
# Rate limit: 20 req/min on free tier = minimum 3s between requests
JINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

# Global proxy list loaded from Webshare API
WEBSHARE_PROXIES = []

def load_webshare_proxies():
    """Fetch proxy list from Webshare API if a key is provided."""
    api_key = os.environ.get("WEBSHARE_API_KEY")
    if not api_key:
        return
    
    print("Fetching proxy list from Webshare API...", flush=True)
    req = urllib.request.Request("https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=1&page_size=100")
    req.add_header("Authorization", f"Token {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode('utf-8'))
            for p in data.get("results", []):
                # Format: http://username:password@ip:port
                proxy_url = f"http://{p['username']}:{p['password']}@{p['proxy_address']}:{p['port']}"
                WEBSHARE_PROXIES.append(proxy_url)
        print(f"Loaded {len(WEBSHARE_PROXIES)} proxies from Webshare.", flush=True)
    except Exception as e:
        print(f"Error loading Webshare proxies: {e}", flush=True)

def jina_get(url: str) -> str:
    try:
        req = urllib.request.Request(JINA_BASE + url, headers=JINA_HEADERS)
        
        # If we have proxies, pick a random one for this request
        if WEBSHARE_PROXIES:
            proxy_url = random.choice(WEBSHARE_PROXIES)
            proxy_handler = urllib.request.ProxyHandler({
                'http': proxy_url,
                'https': proxy_url
            })
            opener = urllib.request.build_opener(proxy_handler)
            response = opener.open(req, timeout=45)
        else:
            response = urllib.request.urlopen(req, timeout=45)
            
        return response.read().decode('utf-8')
    except Exception as e:
        print(f"  [JINA ERROR] {url}: {e}", flush=True)
        return ""

def ask_gemma(prompt: str, model_name: str) -> dict | None:
    """Call Gemini API mirroring FAM's gemini.ts pattern exactly."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("  [Error] GEMINI_API_KEY not set.", flush=True)
        return None
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    
    # No thinkingConfig — including thoughts causes them to be concatenated into
    # the JSON output string, breaking json.loads() every time.
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
        with urllib.request.urlopen(req, timeout=180) as response:
            result = json.loads(response.read().decode('utf-8'))
            # Extract text — skip any thought parts (they have a 'thought' key)
            # to prevent thinking text from being concatenated with the JSON.
            parts = result.get('candidates', [{}])[0].get('content', {}).get('parts', [])
            text = ""
            for part in parts:
                if 'text' in part and not part.get('thought', False):
                    text += part['text']
            
            # Strip markdown code fences if present
            text = text.strip()
            if '```' in text:
                text = re.sub(r'```(?:json)?\s*', '', text).replace('```', '').strip()
            
            # Find JSON object in the text (handles cases where model adds extra commentary)
            json_match = re.search(r'\{[\s\S]*\}', text)
            if json_match:
                return json.loads(json_match.group(0))
            return json.loads(text)
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode('utf-8')[:300]
        except: pass
        print(f"  [Gemma Error {model_name}]: HTTP {e.code}", flush=True)
        if body: print(f"  [Detail]: {body}", flush=True)
        # 400 = bad request (fatal), 401/403 = auth (fatal) — mirror FAM's isFatalError()
        if e.code in (400, 401, 403):
            return "FATAL"
        return None
    except Exception as e:
        print(f"  [Gemma Error {model_name}]: {e}", flush=True)
        return None

def extract_batch_with_ai(batch: list[dict]) -> list[dict]:
    """Process multiple providers in one LLM call to save time and tokens."""
    
    providers_input = ""
    for i, p in enumerate(batch):
        providers_input += f"\n--- PROVIDER {i+1} ---\nNAME: {p['name']}\nHOMEPAGE: {p['homepage']}\nCONTENT:\n{p['text']}\n"

    prompt = f"""You are an expert API documentation engineer analysing streaming embed provider websites.
You are given the scraped text content of {len(batch)} different streaming embed providers.
For EACH provider, produce complete, actionable integration documentation.

CRITICAL RULES:
1. Return ONLY a raw JSON object. No markdown. No explanation. No commentary outside the JSON.
2. The JSON must have a single top-level key: "results" (an array, one entry per provider).
3. For movie_embed: construct the full URL using TMDB ID "{TMDB_MOVIE_ID}".
4. For tv_embed: construct the full URL using TMDB ID "{TMDB_TV_ID}", season "1", episode "1".
5. If the site uses IMDB IDs instead of TMDB, note it in llm_profile and still produce the URL with the TMDB constant.
6. If a URL pattern cannot be determined from the content, set movie_embed and tv_embed to empty strings.

For each provider's "llm_profile", write a structured technical reference covering ALL of the following that appear in the content:
  A. EMBED URL STRUCTURE — exact path pattern for movies and TV (e.g. /embed/movie/{{tmdb_id}} or /embed/tv/{{tmdb_id}}/{{season}}/{{episode}})
  B. SUPPORTED ID TYPES — TMDB, IMDB, TVMaze, AniList, etc.
  C. QUERY PARAMETERS — list every documented parameter with its type, allowed values, and what it controls
  D. PLAYER EVENTS / POSTMESSAGE API — any window.postMessage events the player emits or listens to (e.g. timeupdate, ended, ready)
  E. INTEGRATION NOTES — any iframe sandbox requirements, CORS notes, or authentication requirements
  F. CUSTOMIZATION SUMMARY — a one-line summary of the most useful toggles

JSON schema (output exactly this shape):
{{
  "results": [
    {{
      "name": "Provider Name (must match the NAME field exactly)",
      "movie_embed": "https://...",
      "tv_embed": "https://...",
      "llm_profile": "Structured technical reference as described above.",
      "customizations": "One-line summary of key customization options."
    }}
  ]
}}

Providers to analyse:
{providers_input}
"""

    # Primary: Gemini Flash Lite (fast, structured JSON output)
    # Fallback: Gemma 31B (large context, used if primary fails)
    models = ["gemini-3.1-flash-lite-preview", "gemma-4-31b-it"]
    for attempt in range(6):
        model = models[attempt % 2]
        print(f"    -> Attempt {attempt + 1}/6 ({model})...", flush=True)
        res = ask_gemma(prompt, model)
        
        # Fatal error means the request itself is bad — no point retrying same payload
        if res == "FATAL":
            print(f"    -> Fatal error (400/401/403). Skipping remaining attempts.", flush=True)
            break
        
        if res and isinstance(res, dict) and "results" in res:
            print(f"    -> Success on attempt {attempt + 1}!", flush=True)
            return res["results"]
        wait = 10 if attempt < 2 else 20
        print(f"    -> Waiting {wait}s before retry...", flush=True)
        time.sleep(wait)
    print("    -> All attempts exhausted. Using fallback.", flush=True)
    return []

def fallback_url(homepage: str) -> str:
    try: hostname = urlparse(homepage).hostname or homepage
    except Exception: hostname = homepage
    return f"https://{hostname}/embed/movie/{TMDB_MOVIE_ID}"

def parse_rentry(text: str) -> list[dict]:
    providers = []
    seen_urls = set()
    # Flexible regex to catch [Name](URL) regardless of leading asterisks/bullets
    pattern = re.compile(r'\[([^\]]+)\]\((https?://[^\)]+)\)')
    for m in pattern.finditer(text):
        name, url = m.group(1).strip(), m.group(2).strip()
        # Skip internal/meta links
        if any(f in url for f in ["rentry.co", "t.me/", "discord.", "npmjs.", "sub.wyzie", "theintrodb", "github.com", "vidsrc.domains"]): continue
        if url in seen_urls: continue
        seen_urls.add(url)
        providers.append({"name": name, "homepage": url})
    return providers

def load_env():
    """Simple .env loader for local dev without external dependencies."""
    if os.path.exists(".env"):
        with open(".env", "r") as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    key, value = line.strip().split("=", 1)
                    os.environ[key] = value

def main():
    print("=== FAM Source Verifier — AI Batch Scraper ===", flush=True)
    load_env()
    load_webshare_proxies()
    
    if not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: GEMINI_API_KEY not found.", flush=True)
        return

    print(f"Fetching providers from {RENTRY_URL}...", flush=True)
    rentry_text = jina_get(RENTRY_URL)
    if not rentry_text:
        print("ERROR: Failed to fetch Rentry text.", flush=True)
        return
    
    providers = parse_rentry(rentry_text)
    print(f"Found {len(providers)} providers. Processing in batches of {BATCH_SIZE}...", flush=True)
    time.sleep(JINA_DELAY)
    print(flush=True)

    final_results = []
    for i in range(0, len(providers), BATCH_SIZE):
        batch_slice = providers[i : i + BATCH_SIZE]
        print(f"  [Batch {i//BATCH_SIZE + 1}] Processing {len(batch_slice)} providers...")
        
        # Step 1: Fetch all pages in batch via Jina — skip any that fail
        batch_data = []
        for p in batch_slice:
            print(f"    Fetching {p['name']}...", end=" ", flush=True)
            text = jina_get(p['homepage'])
            if not text:
                print("Skipped (Jina failed)", flush=True)
                continue
            print("OK", flush=True)
            batch_data.append({**p, "text": text})
            time.sleep(JINA_DELAY)  # Stay under 20 req/min free tier limit

        if not batch_data:
            print(f"    All providers in this batch skipped.", flush=True)
            continue

        # Step 2: AI extraction for the whole batch
        print(f"    Processing batch with AI...", flush=True)
        ai_results = extract_batch_with_ai(batch_data)

        # Step 3: Only keep providers the AI successfully verified
        for p in batch_data:
            match = next((r for r in ai_results if r.get('name') == p['name']), None)
            if not match:
                print(f"      - {p['name']}: Skipped (AI failed)", flush=True)
                continue
            res = {
                "name": p['name'],
                "homepage": p['homepage'],
                "embed": match.get('movie_embed', ''),
                "tv_embed": match.get('tv_embed', ''),
                "customizations": match.get('customizations', ''),
                "llm_profile": match.get('llm_profile', ''),
                "source": "ai_gemma_batch"
            }
            final_results.append(res)
            print(f"      - {p['name']}: Success", flush=True)

    output = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "tmdb_id": TMDB_MOVIE_ID,
        "count": len(final_results),
        "providers": final_results,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nDone. Wrote {len(final_results)} providers to {OUTPUT_FILE}", flush=True)

if __name__ == "__main__":
    main()
