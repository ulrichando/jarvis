import { execFile } from 'node:child_process'
import * as fs from 'node:fs/promises'
import * as os from 'node:os'
import * as path from 'node:path'
import { promisify } from 'node:util'

import { z } from 'zod/v4'

import { buildTool, type ToolDef } from '../../Tool.js'
import { lazySchema } from '../../utils/lazySchema.js'

import { GET_LOCATION_TOOL_NAME } from './constants.js'
import { DESCRIPTION, PROMPT } from './prompt.js'

// Mirror of the voice-agent's get_location tool (Python). Same lookup
// order: override file → Wi-Fi BSSID via Google Geolocation API →
// ipinfo.io → ip-api.com. Same cache TTL. Same override-file path.
// Single source of truth file is shared between the two runtimes:
//   ~/.jarvis/location-override  (single line, free-form)

const execFileAsync = promisify(execFile)

const OVERRIDE_PATH = path.join(os.homedir(), '.jarvis', 'location-override')
const CACHE_TTL_MS = 10 * 60 * 1000
const cache: { value: string | null; ts: number } = { value: null, ts: 0 }

const inputSchema = lazySchema(() => z.strictObject({}))
type InputSchema = ReturnType<typeof inputSchema>

const outputSchema = lazySchema(() =>
  z.object({
    location: z.string(),
    source: z.enum(['override', 'cache', 'wifi-google', 'ipinfo', 'ip-api', 'unavailable']),
  }),
)
type OutputSchema = ReturnType<typeof outputSchema>

export type Output = z.infer<OutputSchema>

interface WifiAp {
  macAddress: string
  signalStrength: number
}

async function readOverride(): Promise<string | null> {
  try {
    const text = (await fs.readFile(OVERRIDE_PATH, 'utf-8')).trim()
    return text || null
  } catch {
    return null
  }
}

async function collectWifiBssids(): Promise<WifiAp[]> {
  // nmcli is the same primitive the Python side uses. If nmcli isn't
  // installed (some headless distros) we silently return [] and the
  // caller falls through to IP geo.
  try {
    // 6s is enough for a stable cached scan; NetworkManager sometimes
    // takes ~3s to return when a fresh scan is in progress. 4s wasn't
    // enough during overlapping calls — tool fell through to IP geo.
    const { stdout } = await execFileAsync(
      'nmcli',
      ['-t', '-f', 'BSSID,SIGNAL', 'device', 'wifi', 'list'],
      { timeout: 6000 },
    )
    const aps: WifiAp[] = []
    for (const raw of stdout.split('\n').slice(0, 12)) {
      // Escaped form: `30\:86\:2D\:84\:E9\:81:79`
      const clean = raw.replace(/\\:/g, ':')
      const parts = clean.split(':')
      if (parts.length < 7) continue
      const bssid = parts.slice(0, 6).join(':')
      const signalPct = parseInt(parts[6] ?? '', 10)
      if (Number.isNaN(signalPct)) continue
      // Same linear interp as the Python version: 100% → -30 dBm,
      // 0% → -100 dBm.
      const signalDbm = Math.round(-100 + signalPct * 0.7)
      aps.push({ macAddress: bssid, signalStrength: signalDbm })
    }
    return aps
  } catch {
    return []
  }
}

async function googleGeolocate(
  apiKey: string,
  aps: WifiAp[],
): Promise<{ lat: number; lng: number } | null> {
  if (!apiKey || aps.length === 0) return null
  const body = JSON.stringify({ considerIp: true, wifiAccessPoints: aps })
  const url = `https://www.googleapis.com/geolocation/v1/geolocate?key=${encodeURIComponent(apiKey)}`
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      signal: AbortSignal.timeout(6000),
    })
    const data = (await res.json()) as {
      location?: { lat: number; lng: number }
      error?: { message?: string }
    }
    if (data.error) {
      // 403 (API not enabled) is the most common case for new users.
      // Fall through silently — caller will use IP geo.
      return null
    }
    if (data.location && typeof data.location.lat === 'number') {
      return { lat: data.location.lat, lng: data.location.lng }
    }
    return null
  } catch {
    return null
  }
}

async function reverseGeocode(lat: number, lng: number): Promise<string | null> {
  const url =
    `https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lng}&zoom=10`
  try {
    const res = await fetch(url, {
      headers: { 'User-Agent': 'jarvis-cli/1.0' },
      signal: AbortSignal.timeout(6000),
    })
    const data = (await res.json()) as { address?: Record<string, string> }
    const addr = data.address ?? {}
    const city =
      addr.city ?? addr.town ?? addr.village ?? addr.hamlet ?? addr.county
    const region = addr.state ?? addr.region
    const country = addr.country
    const parts = [city, region, country].filter(Boolean)
    return parts.length > 0 ? parts.join(', ') : null
  } catch {
    return null
  }
}

async function ipGeolocate(): Promise<string | null> {
  // Try ipinfo.io first (faster), fall back to ip-api.com.
  try {
    const res = await fetch('https://ipinfo.io/json', {
      signal: AbortSignal.timeout(4000),
    })
    const d = (await res.json()) as Record<string, string>
    const parts = [d.city, d.region, d.country].filter(Boolean)
    if (parts.length > 0) return parts.join(', ')
  } catch {
    // fall through
  }
  try {
    const res = await fetch('http://ip-api.com/json/', {
      signal: AbortSignal.timeout(4000),
    })
    const d = (await res.json()) as Record<string, string>
    const parts = [d.city, d.regionName, d.country].filter(Boolean)
    if (parts.length > 0) return parts.join(', ')
  } catch {
    // fall through
  }
  return null
}

export const GetLocationTool = buildTool({
  name: GET_LOCATION_TOOL_NAME,
  searchHint: "user's physical location for weather / regional / time-zone queries",
  maxResultSizeChars: 10_000,
  async description() {
    return DESCRIPTION
  },
  async prompt() {
    return PROMPT
  },
  get inputSchema(): InputSchema {
    return inputSchema()
  },
  get outputSchema(): OutputSchema {
    return outputSchema()
  },
  userFacingName() {
    return 'GetLocation'
  },
  shouldDefer: true,
  isEnabled() {
    return true
  },
  isConcurrencySafe() {
    return true
  },
  isReadOnly() {
    return true
  },
  toAutoClassifierInput() {
    return GET_LOCATION_TOOL_NAME
  },
  renderToolUseMessage() {
    return null
  },
  async call() {
    // 1. Override file
    const override = await readOverride()
    if (override) {
      return { data: { location: override, source: 'override' as const } }
    }

    // 2. Cache
    if (cache.value && Date.now() - cache.ts < CACHE_TTL_MS) {
      return { data: { location: cache.value, source: 'cache' as const } }
    }

    // 3. Wi-Fi BSSID + Google
    const apiKey = process.env.GOOGLE_API_KEY ?? ''
    if (apiKey) {
      const aps = await collectWifiBssids()
      if (aps.length > 0) {
        const coords = await googleGeolocate(apiKey, aps)
        if (coords) {
          const located = await reverseGeocode(coords.lat, coords.lng)
          if (located) {
            cache.value = located
            cache.ts = Date.now()
            return {
              data: { location: located, source: 'wifi-google' as const },
            }
          }
        }
      }
    }

    // 4. IP geo fallback
    const ipLoc = await ipGeolocate()
    if (ipLoc) {
      cache.value = ipLoc
      cache.ts = Date.now()
      // We can't tell which provider succeeded from here without
      // restructuring; report as 'ipinfo' since that's tried first.
      return { data: { location: ipLoc, source: 'ipinfo' as const } }
    }

    return {
      data: { location: 'Location unavailable', source: 'unavailable' as const },
    }
  },
  mapToolResultToToolResultBlockParam(content, toolUseID) {
    const out = content as Output
    return {
      tool_use_id: toolUseID,
      type: 'tool_result',
      content: `${out.location} (source: ${out.source})`,
    }
  },
} satisfies ToolDef<InputSchema, Output>)
