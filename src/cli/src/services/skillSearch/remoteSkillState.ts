export type DiscoveredRemoteSkill = {
  slug: string
  name?: string
  description?: string
}

const PREFIX = '_canonical_'

const discovered = new Map<string, DiscoveredRemoteSkill>()

export function stripCanonicalPrefix(name: string): string | null {
  return name.startsWith(PREFIX) ? name.slice(PREFIX.length) : null
}

export function getDiscoveredRemoteSkill(
  slug: string,
): DiscoveredRemoteSkill | null {
  return discovered.get(slug) ?? null
}

export function rememberDiscoveredRemoteSkill(skill: DiscoveredRemoteSkill): void {
  discovered.set(skill.slug, skill)
}

export function clearDiscoveredRemoteSkills(): void {
  discovered.clear()
}
