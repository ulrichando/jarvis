export const DESCRIPTION =
  "Return the user's approximate physical location (city, region, country)."

export const PROMPT = `Use this tool when you need to know where the user is — for weather, "near me" lookups, regional content, time-zone math, or anything location-aware.

## Lookup order (most accurate first)

1. **\`~/.jarvis/location-override\` file** — if it exists, its contents are returned verbatim. The user can pin their location with \`SetLocation\`.
2. **Wi-Fi BSSID → Google Geolocation API** — most accurate (~50m). Requires \`GOOGLE_API_KEY\` in env AND the Geolocation API enabled on the project. Silent fallthrough on 403.
3. **IP-based geolocation** (ipinfo.io → ip-api.com fallback) — coarse city-level. Unreliable on VPNs / Google Fi / mobile carriers (the IP often backhauls through a distant city).

## Output

Returns a single string like:
- \`"Columbus, Ohio, United States"\`
- \`"Tokyo, Tokyo, JP"\`
- \`"Location unavailable"\` if every layer failed

## Tips

- Cached for ~10 minutes within the process; calling repeatedly within a single conversation is cheap.
- If the result looks wrong (e.g. user in Ohio but says NYC), they're probably on a VPN or carrier NAT — call \`SetLocation\` to pin the correct city.
- This is laptop-class geolocation. For street-level accuracy you'd need GPS hardware or HTML5 \`navigator.geolocation\`, neither of which this tool wraps.
`
