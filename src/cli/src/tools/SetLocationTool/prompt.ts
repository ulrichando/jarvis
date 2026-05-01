export const DESCRIPTION =
  "Persist a manual location override (city, region, country) used by GetLocation."

export const PROMPT = `Use this tool when the user states their actual location and you need to persist it across calls — typical when IP geolocation gave the wrong city (VPN, mobile carrier NAT, Google Fi, corporate egress, etc).

## When to call

- User says "I'm in Cleveland" / "set my location to Tokyo" / "for weather use Yaoundé"
- GetLocation returned the wrong city and the user corrected you
- User wants to test how JARVIS behaves in another timezone / region

## Args

- \`city\` (required): free-form location string. Examples:
  - \`"Cleveland, Ohio, US"\`
  - \`"Tokyo, Japan"\`
  - \`"Yaoundé, Centre, Cameroon"\`
  - \`""\` (empty string) — clears the override and reverts to auto-detection.

The string is stored verbatim at \`~/.jarvis/location-override\` and read by GetLocation on every call. The voice agent's get_location tool reads the same file, so the override is shared between voice and CLI.

## Tips

- Prefer "City, Region, Country" form so wttr.in / Nominatim / similar services disambiguate correctly.
- Override survives restarts and clones to other JARVIS instances on this machine.
- To clear, call with \`{"city": ""}\`.
`
