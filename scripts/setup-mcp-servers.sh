#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# JARVIS MCP Server Setup
# Installs dependencies and guides credential configuration
# for all configured MCP servers.
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

JARVIS_HOME="${JARVIS_HOME:-$HOME/.jarvis}"
CREDS_DIR="$JARVIS_HOME/credentials"
ENV_FILE="$JARVIS_HOME/.env.mcp"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC} $1"; }
ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()   { echo -e "${RED}[ERROR]${NC} $1"; }

# ── Pre-flight checks ──────────────────────────────────────────
check_prereqs() {
    info "Checking prerequisites..."

    local missing=0
    for cmd in npx node docker; do
        if command -v "$cmd" &>/dev/null; then
            ok "$cmd found: $(command -v "$cmd")"
        else
            warn "$cmd not found — some MCP servers may not work"
            missing=$((missing + 1))
        fi
    done

    if [ $missing -gt 0 ]; then
        warn "$missing tools missing. Continuing anyway..."
    fi
    echo
}

# ── Create directories ─────────────────────────────────────────
setup_dirs() {
    mkdir -p "$CREDS_DIR"
    ok "Credentials directory: $CREDS_DIR"
}

# ── Pre-install npm packages (optional, speeds up first run) ──
preinstall_npm() {
    info "Pre-installing MCP server npm packages (optional, speeds up first use)..."
    echo "  This downloads packages so JARVIS doesn't have to wait on first tool call."
    echo

    local packages=(
        "mcp-remote"
        "@gongrzhe/server-gmail-autoauth-mcp"
        "@cocal/google-calendar-mcp"
        "@modelcontextprotocol/server-gdrive"
        "@modelcontextprotocol/server-slack"
        "@notionhq/notion-mcp-server"
    )

    for pkg in "${packages[@]}"; do
        echo -n "  Installing $pkg ... "
        if npx -y "$pkg" --help &>/dev/null 2>&1; then
            echo -e "${GREEN}cached${NC}"
        else
            # Just trigger the download, ignore runtime errors
            npx -y "$pkg" --version 2>/dev/null || true
            echo -e "${YELLOW}downloaded${NC}"
        fi
    done
    echo
}

# ── Pull GitHub MCP Docker image ──────────────────────────────
pull_github_image() {
    if command -v docker &>/dev/null; then
        info "Pulling GitHub MCP server Docker image..."
        if docker pull ghcr.io/github/github-mcp-server 2>/dev/null; then
            ok "GitHub MCP server image ready"
        else
            warn "Failed to pull GitHub MCP image. Make sure Docker is running."
        fi
    else
        warn "Docker not available — GitHub MCP server won't work. Install Docker or use the npm fallback."
    fi
    echo
}

# ── Credential setup ──────────────────────────────────────────
setup_credentials() {
    info "Setting up credentials..."
    echo "  We'll create an env file at: $ENV_FILE"
    echo "  Add your API keys/tokens there. JARVIS expands \${VAR} in mcp.json from this file."
    echo

    # Initialize env file if missing
    if [ ! -f "$ENV_FILE" ]; then
        cat > "$ENV_FILE" << 'ENVEOF'
# ─────────────────────────────────────────────────────────────
# JARVIS MCP Server Credentials
# These environment variables are referenced by ~/.jarvis/mcp.json
# Fill in the values for the platforms you want to use.
# ─────────────────────────────────────────────────────────────

# ── GitHub ──────────────────────────────────────────────────
# Create a Personal Access Token at: https://github.com/settings/tokens
# Scopes needed: repo, read:org, read:user, issues, pull_requests
GITHUB_TOKEN=

# ── Slack ───────────────────────────────────────────────────
# Create a Slack App at: https://api.slack.com/apps
# Bot Token Scopes: channels:history, channels:read, chat:write,
#   reactions:write, users:read, groups:read, mpim:read, im:read
SLACK_BOT_TOKEN=
SLACK_TEAM_ID=

# ── Notion ──────────────────────────────────────────────────
# Create an integration at: https://www.notion.so/my-integrations
# Then share your pages/databases with the integration
NOTION_TOKEN=

# ── Google (Gmail, Calendar, Drive) ─────────────────────────
# 1. Go to https://console.cloud.google.com
# 2. Create a project (or use existing)
# 3. Enable: Gmail API, Google Calendar API, Google Drive API
# 4. Create OAuth 2.0 credentials (Desktop Application type)
# 5. Download the JSON and save it to:
#    ~/.jarvis/credentials/gcp-oauth.keys.json
# 6. Fill in the client ID and secret below (for Drive):
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=

# ── Vercel ──────────────────────────────────────────────────
# Vercel uses browser-based OAuth on first connection.
# No token needed here — it will prompt you to log in.
ENVEOF
        ok "Created $ENV_FILE — edit it with your credentials"
    else
        ok "$ENV_FILE already exists"
    fi

    echo
    echo "──────────────────────────────────────────────────────────"
    echo -e "${CYAN}Platform Setup Checklist:${NC}"
    echo "──────────────────────────────────────────────────────────"
    echo
    echo "  1. GitHub     → Add GITHUB_TOKEN to $ENV_FILE"
    echo "  2. Vercel     → Just run — will open browser for OAuth"
    echo "  3. Gmail      → Place gcp-oauth.keys.json in $CREDS_DIR/"
    echo "  4. Calendar   → Same GCP OAuth creds as Gmail"
    echo "  5. Drive      → Same GCP project + fill GOOGLE_OAUTH_CLIENT_ID/SECRET"
    echo "  6. Slack      → Add SLACK_BOT_TOKEN and SLACK_TEAM_ID to $ENV_FILE"
    echo "  7. Notion     → Add NOTION_TOKEN to $ENV_FILE"
    echo
    echo -e "  ${YELLOW}Edit credentials:${NC} nano $ENV_FILE"
    echo -e "  ${YELLOW}Google OAuth:${NC}    Place JSON in $CREDS_DIR/gcp-oauth.keys.json"
    echo
}

# ── Wire env file into JARVIS startup ─────────────────────────
wire_env_loading() {
    # Check if brain.py or the startup script loads this env file
    local jarvis_env="$JARVIS_HOME/.env"
    if [ -f "$jarvis_env" ]; then
        if ! grep -q "\.env\.mcp" "$jarvis_env" 2>/dev/null; then
            info "Tip: Source MCP env in your shell or .env:"
            echo "  echo 'source $ENV_FILE' >> $jarvis_env"
        fi
    fi
}

# ── Verify config ─────────────────────────────────────────────
verify_config() {
    info "Verifying mcp.json..."
    local config="$JARVIS_HOME/mcp.json"
    if [ -f "$config" ]; then
        if python3 -c "import json; json.load(open('$config'))" 2>/dev/null; then
            local count
            count=$(python3 -c "import json; d=json.load(open('$config')); print(len(d.get('mcpServers',{})))")
            ok "mcp.json is valid — $count servers configured"
        else
            err "mcp.json has invalid JSON!"
        fi
    else
        err "mcp.json not found at $config"
    fi
    echo
}

# ── Main ──────────────────────────────────────────────────────
main() {
    echo
    echo "╔═══════════════════════════════════════════════╗"
    echo "║       JARVIS MCP Server Setup                 ║"
    echo "║       7 platforms • 80+ tools                 ║"
    echo "╚═══════════════════════════════════════════════╝"
    echo

    check_prereqs
    setup_dirs
    verify_config

    read -rp "Pre-install npm packages? (speeds up first use) [y/N] " ans
    if [[ "$ans" =~ ^[Yy] ]]; then
        preinstall_npm
    fi

    read -rp "Pull GitHub MCP Docker image? [y/N] " ans
    if [[ "$ans" =~ ^[Yy] ]]; then
        pull_github_image
    fi

    setup_credentials
    wire_env_loading

    echo
    echo "──────────────────────────────────────────────────────────"
    echo -e "${GREEN}Setup complete!${NC}"
    echo
    echo "  Next steps:"
    echo "  1. Fill in your credentials:  nano $ENV_FILE"
    echo "  2. Source the env file:        source $ENV_FILE"
    echo "  3. Start JARVIS:              jarvis"
    echo "  4. Check MCP status:          /mcp list"
    echo "  5. Test a tool:               /rpc mcp__notion__search {\"query\": \"test\"}"
    echo
    echo "  Servers with missing credentials will show errors on /mcp health"
    echo "  You can disable servers by setting \"enabled\": false in mcp.json"
    echo "──────────────────────────────────────────────────────────"
}

main "$@"
