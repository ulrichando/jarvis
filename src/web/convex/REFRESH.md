# How to refresh the typed Convex API in this directory

The schema + functions live at `src/convex/convex/`. This directory
mirrors only the `_generated/` subdir so the web app has typed
`@convex/_generated/api` imports without escaping Turbopack's root.

To refresh after schema changes:

```bash
cd src/convex
ADMIN_KEY=$(grep '^CONVEX_ADMIN_KEY=' ~/.jarvis/convex.env | cut -d= -f2-)
CONVEX_SELF_HOSTED_URL=http://127.0.0.1:3210 \
CONVEX_SELF_HOSTED_ADMIN_KEY="$ADMIN_KEY" \
bunx convex dev --once --typecheck=disable

cp -r convex/_generated/* ../web/convex/_generated/
```

Or wire the second step into `src/convex/package.json` postdeploy.
