export type RiskTier = "low" | "high";

// Patterns that escalate a bash command to "high". Matched against the raw command string.
const HIGH_RISK_BASH_PATTERNS: RegExp[] = [
  /\bsudo\b/,                              // privilege escalation
  /\brm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+|-[a-zA-Z]*f[a-zA-Z]*\s+)/, // rm -r / rm -f etc
  /\brm\s+-rf?\s+\//,                      // rm -rf /
  /\bdd\s/,                                // dd
  /\bmkfs\b/,                              // mkfs.*
  /\bmv\s+.*\s+\//,                        // mv into /
  /\bchmod\s+[0-7]*[0-7]*[0-9][67]\b/,     // world-writable chmods
  /\biptables\b/, /\bnft\b/,               // firewall changes
  // Network offensive tools (need explicit operator approval)
  /\bnmap\b/, /\bmasscan\b/, /\bnikto\b/, /\bwpscan\b/,
  /\bhydra\b/, /\bmedusa\b/, /\bsqlmap\b/, /\bmsfconsole\b/, /\bmsfvenom\b/,
  /\baircrack-ng\b/, /\bwifite\b/, /\breaver\b/,
  /\bresponder\b/, /\bcrackmapexec\b/, /\bimpacket-\w+\b/,
  /\bjohn\b/, /\bhashcat\b/,               // password crackers
  // Network listens / reverse shells
  /\bnc\s+.*-[a-zA-Z]*l[a-zA-Z]*/, /\bncat\s+.*-[a-zA-Z]*l[a-zA-Z]*/,
  /\bbash\s+-i\b/,                         // interactive reshells that could be exfil
  // Writing outside home
  />\s*\/(?!home|tmp|dev\/null)/,          // redirect to root-owned paths
];

export function classifyBash(command: string): RiskTier {
  for (const re of HIGH_RISK_BASH_PATTERNS) {
    if (re.test(command)) return "high";
  }
  return "low";
}

export function classify(toolName: string, input: unknown): RiskTier {
  if (toolName === "bash") {
    const cmd = (input as { command?: string })?.command ?? "";
    return classifyBash(cmd);
  }
  // Tools added in later plans default to low; explicit classification per tool as they're added.
  return "low";
}
