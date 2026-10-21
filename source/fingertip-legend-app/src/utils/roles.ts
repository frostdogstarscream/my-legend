// These two aliases preserve the same named characters from the original local prototype.
export function normalizeRoleIds(ids: string[], available: string[], localBackend = false): string[] {
  const aliases: Record<string,string> = localBackend ? {'demo-char-001':'legend-moyu','demo-char-002':'legend-lingmo'} : {}
  const allowed = new Set(available)
  return [...new Set(ids.map(id=>aliases[id]||id))].filter(id=>allowed.has(id))
}
