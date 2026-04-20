# Tool set rationale

`packages.txt` holds the curated Kali-equivalent tool set installed by `02-pentools.sh`. Groups follow a loose attack-lifecycle: recon → web → wireless → exploit → creds → post-ex → RE → traffic → utilities.

## Coverage vs Kali metapackages

Targets parity with `kali-tools-top10` and the most-used entries in `kali-linux-default`. Does not pull in the full `kali-linux-everything` superset (~600 packages, tens of GB, most unused for any single engagement).

| Kali Top 10 | Package here | Repo |
|---|---|---|
| Aircrack-ng | `aircrack-ng` | core |
| Burp Suite | `burpsuite` | blackarch |
| Hydra | `hydra` | core |
| John the Ripper | `john` | core |
| Maltego | *omitted* — GUI license-gated, install manually if needed | — |
| Metasploit | `metasploit` | blackarch |
| Nmap | `nmap` | core |
| OWASP ZAP | `zaproxy` | blackarch |
| SQLmap | `sqlmap` | core |
| Wireshark | `wireshark-qt` | core |

## Adding a tool

1. Append to `packages.txt` in the right group, with a `# comment`.
2. Re-run `02-pentools.sh` inside the VM (idempotent).
3. Snapshot the VM: `scripts/vm/snapshot.sh base-after-<toolname>`.
