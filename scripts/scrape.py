#!/usr/bin/env python3
"""
Embed Tester - AI Batch Scraper
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
import hashlib
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
STATE_FILE = "scripts/last_rentry_state.json"
JINA_DELAY = 1.0  # Reduced to 1.0s as requested, using rotating proxies
BATCH_SIZE = 5  # Gemma 256K can easily handle 5+ full provider pages

# Jina Reader headers
JINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "X-Return-Format": "markdown",
    "X-No-Cache": "true"
}

# Global proxy list and rotation counter
WEBSHARE_PROXIES = []
proxy_index = 0

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
                # Ensure the proxy is currently marked valid by Webshare
                if not p.get("valid", True):
                    continue
                # Format: http://username:password@ip:port
                proxy_url = f"http://{p['username']}:{p['password']}@{p['proxy_address']}:{p['port']}"
                WEBSHARE_PROXIES.append(proxy_url)
        print(f"Loaded {len(WEBSHARE_PROXIES)} proxies from Webshare.", flush=True)
    except Exception as e:
        print(f"Error loading Webshare proxies: {e}", flush=True)

def jina_get(url: str) -> str:
    global proxy_index
    req = urllib.request.Request(JINA_BASE + url, headers=JINA_HEADERS)
    
    # Try up to 3 times with different proxies
    attempts_allowed = min(3, len(WEBSHARE_PROXIES)) if WEBSHARE_PROXIES else 0
    
    for attempt in range(attempts_allowed):
        proxy_url = WEBSHARE_PROXIES[proxy_index % len(WEBSHARE_PROXIES)]
        proxy_index += 1
        proxy_handler = urllib.request.ProxyHandler({'http': proxy_url, 'https': proxy_url})
        opener = urllib.request.build_opener(proxy_handler)
        
        try:
            with opener.open(req, timeout=45) as response:
                return response.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            print(f"    [Proxy Attempt {attempt+1} HTTP {e.code}]: {url}", flush=True)
            if e.code in (400, 422): break # Fatal for this URL
        except Exception as e:
            print(f"    [Proxy Attempt {attempt+1} Failed]: {e}", flush=True)
        time.sleep(1.0)
            
    # Fallback to direct (no proxy)
    if WEBSHARE_PROXIES:
        print("    [Falling back to direct connection...]", flush=True)
        
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                return response.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            print(f"  [JINA ERROR] {url}: HTTP {e.code}", flush=True)
            if e.code == 422:
                print(f"    -> Jina cannot process this URL (Unprocessable Entity). Site might be blocking Jina or is too complex.", flush=True)
            elif e.code == 400:
                print(f"    -> Bad Request (Invalid URL or parameters).", flush=True)
            elif e.code == 503:
                # Exponential backoff for Jina 503
                wait_time = (attempt + 1) * 5
                print(f"    -> Jina busy (503). Waiting {wait_time}s and retrying...", flush=True)
                time.sleep(wait_time)
                continue
            break
        except Exception as e:
            print(f"  [JINA ERROR] {url}: {e}", flush=True)
            time.sleep(1)
    return ""

def ask_gemma(prompt: str, model_name: str) -> dict | str | None:
    """Call Gemini API for processing streaming providers."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("  [Error] GEMINI_API_KEY not set.", flush=True)
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
        with urllib.request.urlopen(req, timeout=180) as response:
            result = json.loads(response.read().decode('utf-8'))
            parts = result.get('candidates', [{}])[0].get('content', {}).get('parts', [])
            text = ""
            for part in parts:
                if 'text' in part and not part.get('thought', False):
                    text += part['text']
            
            text = text.strip()
            if '```' in text:
                text = re.sub(r'```(?:json)?\s*', '', text).replace('```', '').strip()
            
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
        # 400/401/403 are fatal. 503 is "High Demand" (retryable).
        if e.code in (400, 401, 403):
            return "FATAL"
        if e.code == 503:
            return "RETRYABLE_BUSY"
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
You are given the scraped text content of {len(batch)} different streaming embed providers, including potential documentation sub-pages.
For EACH provider, produce complete, actionable integration documentation.

CRITICAL RULES:
1. Return ONLY a raw JSON object. No markdown. No explanation. No commentary outside the JSON.
2. The JSON must have a single top-level key: "results" (an array, one entry per provider).
3. For movie_embed: construct the full URL using TMDB ID "{TMDB_MOVIE_ID}".
4. For tv_embed: construct the full URL using TMDB ID "{TMDB_TV_ID}", season "1", episode "1". If it's an Anime site, use tv_embed for the anime URL pattern.
5. If the site uses IMDB IDs instead of TMDB, note it in llm_profile and still produce the URL with the TMDB constant.
6. BE PROACTIVE: Look for "hidden" patterns. Sometimes URLs are mentioned in text as "https://site.com/embed/movie/ID" or similar. Even if you see a button, dropdown, or JS code mentioned, try to deduce the URL structure from the text around it.
7. If a URL pattern cannot be determined from the content (e.g., if the page is just an error, a security update guide, or completely unrelated to streaming APIs), set BOTH movie_embed and tv_embed to empty strings.

For each provider's "llm_profile", write a beautifully formatted, clean markdown document.
Use `###` headers for EVERY section.
Use double newlines between sections.
Use bullet points for lists.
DO NOT use a giant wall of text.

Example of a PERFECT "llm_profile":
### Base URL
https://vidapi.example.com

### Embed Example
https://vidapi.example.com/embed/movie/129

### URL Structure
- **Movies**: `https://vidapi.example.com/embed/movie/{{id}}`
- **TV Series**: `https://vidapi.example.com/embed/tv/{{id}}/{{season}}/{{episode}}`

### Supported IDs
- TMDB ID (Primary)
- IMDB ID (Supported with `tt` prefix)

### Query Parameters
- `ds`: Disable subtitles (0 or 1)
- `auto`: Autoplay (true/false)

### Player Events / PostMessage API
- `vidsrc:play`: Triggered when video starts.
- `vidsrc:error`: Triggered on playback failure.

### Integration Notes
- CORS is enabled. No API key required for embedding.
- Supports mobile devices and responsive containers.

JSON schema (output exactly this shape):
{{
  "results": [
    {{
      "name": "Provider Name (must match the NAME field exactly)",
      "movie_embed": "https://...",
      "tv_embed": "https://...",
      "llm_profile": "Formatted markdown text as requested above. Ensure proper newlines (\\n).",
      "customizations": "One-line summary of key customization options."
    }}
  ]
}}

Providers to analyse:
{providers_input}
"""

    models = ["gemini-3.1-flash-lite-preview", "gemma-4-31b-it"]
    for attempt in range(6):
        model = models[attempt % 2]
        print(f"    -> Attempt {attempt + 1}/6 ({model})...", flush=True)
        res = ask_gemma(prompt, model)
        
        if res == "FATAL":
            print(f"    -> Fatal error. Skipping remaining attempts.", flush=True)
            break
        
        if res == "RETRYABLE_BUSY":
            # Exponential backoff for 503 errors
            wait = (attempt + 1) * 10
            print(f"    -> Model busy (503). Waiting {wait}s...", flush=True)
            time.sleep(wait)
            continue

        if res and isinstance(res, dict) and "results" in res:
            print(f"    -> Success on attempt {attempt + 1}!", flush=True)
            return res["results"]

        wait = 5 if attempt < 2 else 10
        print(f"    -> Waiting {wait}s before retry...", flush=True)
        time.sleep(wait)
    print("    -> All attempts exhausted.", flush=True)
    return []

def parse_rentry(text: str) -> list[dict]:
    providers = []
    lines = text.split('\n')

    # regex for [Name](URL)
    link_pattern = re.compile(r'\[([^\]]+)\]\((https?://[^\)]+)\)')

    current_provider = None

    for line in lines:
        line = line.strip()
        if not line.startswith('*'): continue
        
        matches = list(link_pattern.finditer(line))
        if not matches: continue

        # The first link on the line is usually the main provider
        main_name, main_url = matches[0].group(1), matches[0].group(2)

        # Filtering non-provider tools
        name_lower = main_name.lower()
        if any(term in name_lower for term in ["theintrodb", "discord", "telegram", "wyzie subs", "libre subs", "npm package", "warning", "raw", "pdf", "png", "webp", "jpg"]):
            continue
        if any(f in main_url for f in ["rentry.co", "t.me/", "discord.", "npmjs.", "sub.wyzie", "theintrodb", "github.com", "vidsrc.domains"]):
            continue

        # If name is "Docs" or a number like "2", "3", it's a sub-link of the previous provider
        if current_provider and (main_name.lower() in ["docs", "api", "status"] or main_name.isdigit()):
            current_provider['sub_links'].append({"name": main_name, "url": main_url})
            # Also add other links on the same line as sub-links
            for m in matches[1:]:
                current_provider['sub_links'].append({"name": m.group(1), "url": m.group(2)})
        else:
            # New provider
            current_provider = {
                "name": main_name,
                "homepage": main_url,
                "sub_links": []
            }
            # Any additional links on this same line (e.g. ", [2]", ", [Docs]")
            for m in matches[1:]:
                current_provider['sub_links'].append({"name": m.group(1), "url": m.group(2)})
            providers.append(current_provider)
            
    return providers

def load_env():
    """Simple .env loader for local dev without external dependencies."""
    if os.path.exists(".env"):
        with open(".env", "r") as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    key, value = line.strip().split("=", 1)
                    os.environ[key] = value

def get_rentry_hash(providers):
    """Generate a hash of the current provider list to detect changes."""
    data = json.dumps(providers, sort_keys=True).encode('utf-8')
    return hashlib.sha256(data).hexdigest()

def main():
    print("=== Embed Tester — AI Batch Scraper ===", flush=True)
    load_env()
    load_webshare_proxies()
    
    force_run = os.environ.get("FORCE_RUN") == "true"

    if not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: GEMINI_API_KEY not found.", flush=True)
        return

    print(f"Fetching providers from {RENTRY_URL}...", flush=True)
    rentry_text = jina_get(RENTRY_URL)
    if not rentry_text:
        print("ERROR: Failed to fetch Rentry text.", flush=True)
        return
    
    providers = parse_rentry(rentry_text)
    current_hash = get_rentry_hash(providers)

    # Change Detection
    if not force_run and os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
            if state.get("hash") == current_hash:
                print("No changes detected in Rentry providers. Exiting.", flush=True)
                sys.exit(0)

    print(f"Found {len(providers)} providers. Processing in batches of {BATCH_SIZE}...", flush=True)
    time.sleep(JINA_DELAY)
    print(flush=True)

    final_results = []
    for i in range(0, len(providers), BATCH_SIZE):
        batch_slice = providers[i : i + BATCH_SIZE]
        print(f"  [Batch {i//BATCH_SIZE + 1}] Processing {len(batch_slice)} providers...")
        
        batch_data = []
        for p in batch_slice:
            print(f"    Fetching {p['name']}...", end=" ", flush=True)
            text = jina_get(p['homepage'])
            if not text:
                print("Skipped (Jina failed)", flush=True)
                continue
            print("OK", flush=True)
            
            # Smart Sub-page fetching
            docs_links = []

            # Include sub-links from Rentry (like [2], [Docs])
            for sl in p.get('sub_links', []):
                docs_links.append({"url": sl['url'], "reason": f"Rentry sub-link: {sl['name']}"})

            # Regex search for interesting links on the homepage
            keywords = ['api', 'doc', 'dev', 'embed', 'player', 'integrate', 'use']
            for m in re.finditer(r'\[([^\]]+)\]\(([^)]+)\)', text):
                link_text, link_url = m.group(1).lower(), m.group(2)
                
                # Exclude media and non-doc formats
                if any(ext in link_url.lower() for ext in ['.png', '.jpg', '.jpeg', '.webp', '.pdf', '.mp4', '.zip']):
                    continue
                if link_url.startswith('#') or 'javascript:' in link_url:
                    continue

                if any(k in link_text for k in keywords):
                    potential_link = None
                    if link_url.startswith('/'):
                        potential_link = p['homepage'].rstrip('/') + link_url
                    elif link_url.startswith('http'):
                        potential_link = link_url
                        
                    if potential_link:
                        base_home = p['homepage'].split('#')[0].rstrip('/')
                        base_link = potential_link.split('#')[0].rstrip('/')
                        if base_link != base_home and base_link not in [l['url'] for l in docs_links]:
                            docs_links.append({"url": base_link, "reason": f"keyword match: {link_text}"})

            # Heuristic: try common paths
            common_paths = ['/docs', '/api', '/api-docs', '/player-api', '/api/docs', '/documentation', '/embed', '/player', '/dev', '/integration', '/developers']

            # Skip sub-pages if homepage is already very detailed (>10KB) and likely has embed info
            skip_heuristics = len(text) > 10240 and ("tmdb" in text.lower() or "embed" in text.lower())

            if len(docs_links) < 1 and not skip_heuristics:
                for path in common_paths:
                    docs_links.append({"url": p['homepage'].rstrip('/') + path, "reason": "common path heuristic"})

            # Fetch top 2 doc pages
            fetched_count = 0
            for doc_info in docs_links:
                if fetched_count >= 2: break

                # Final check to avoid obviously non-doc URLs (like example movie URLs)
                if any(term in doc_info['url'].lower() for term in ['?imdb=', 'movie_id=', 'tt', TMDB_MOVIE_ID]):
                    if "docs" not in doc_info['url'].lower() and "api" not in doc_info['url'].lower():
                        continue

                print(f"      -> Potential docs at {doc_info['url']} ({doc_info['reason']}), fetching...", end=" ", flush=True)
                time.sleep(JINA_DELAY)
                docs_text = jina_get(doc_info['url'])

                # Check if it's a real page (basic length and content check)
                if docs_text and len(docs_text.strip()) > 300:
                    # Ignore if the "docs" page just returns an image description (Jina sometimes does this for media URLs)
                    if docs_text.strip().startswith('![Image') and len(docs_text.strip()) < 1000:
                        print("Skipped (Looks like an image)", flush=True)
                        continue

                    text += f"\n\n--- SUBPAGE ({doc_info['url']}) ---\n" + docs_text
                    print("OK", flush=True)
                    fetched_count += 1
                    if "embed" in docs_text.lower() or "tmdb" in docs_text.lower():
                        break
                else:
                    print("Skipped/Failed", flush=True)
            
            batch_data.append({**p, "text": text})
            time.sleep(JINA_DELAY)

        if not batch_data:
            continue

        # Step 2: AI extraction
        print(f"    Processing batch with AI...", flush=True)
        ai_results = extract_batch_with_ai(batch_data)

        # Step 3: results
        for p in batch_data:
            match = next((r for r in ai_results if r.get('name') == p['name']), None)
            if not match:
                print(f"      - {p['name']}: Skipped (AI failed to parse)", flush=True)
                continue
                
            movie_embed = match.get('movie_embed', '').strip()
            tv_embed = match.get('tv_embed', '').strip()
            
            if not movie_embed and not tv_embed:
                print(f"      - {p['name']}: Skipped (No embed URLs found in docs)", flush=True)
                continue

            res = {
                "name": p['name'],
                "homepage": p['homepage'],
                "embed": movie_embed,
                "tv_embed": tv_embed,
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

    with open(STATE_FILE, "w") as f:
        json.dump({"hash": current_hash, "updated_at": datetime.now(timezone.utc).isoformat()}, f, indent=2)

    print(f"\nDone. Wrote {len(final_results)} providers to {OUTPUT_FILE}", flush=True)

if __name__ == "__main__":
    main()
