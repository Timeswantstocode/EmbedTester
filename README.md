# Embed Tester

A GitHub Pages tool to discover and verify movie embed sources.

## Features
- Scrapes lists via Jina Reader for the latest provider list
- Smart embed URL detection: 50+ known patterns + regex scoring
- LocalStorage persistence — session survives page refreshes
- CSV export of test results

## Usage
1. Open the deployed GitHub Pages URL
2. Click **Load Sources**
3. Wait for discovery to complete
4. Click **Test** on any provider
5. Mark **PASS** or **FAIL** after observing playback

## Sandbox Config
```
sandbox="allow-scripts allow-same-origin allow-forms allow-presentation allow-orientation-lock allow-pointer-lock allow-modals"
allow="autoplay; encrypted-media; picture-in-picture; web-share; fullscreen"
referrerpolicy="no-referrer"
```
