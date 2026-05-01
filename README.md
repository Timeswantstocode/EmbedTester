# Generic Iframe & Embed Sandbox Tester

A GitHub Pages tool for developers to verify embed codes and monitor postMessage events in a secure sandbox environment.

## Features
- Securely test iframe embeds with customizable sandbox flags
- Monitor and log postMessage events from embedded frames
- LocalStorage persistence — session survives page refreshes
- CSV export of test results

## Usage
1. Open the deployed GitHub Pages URL
2. Click **Load Sources** to pull the registry
3. Click **Test** on any entry
4. Observe the embed behavior and mark results

## Sandbox Config
```
sandbox="allow-scripts allow-same-origin allow-forms allow-presentation allow-orientation-lock allow-pointer-lock allow-modals"
allow="autoplay; encrypted-media; picture-in-picture; web-share; fullscreen"
referrerpolicy="no-referrer"
```
