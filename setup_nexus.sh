#!/bin/bash
set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Setting up Nexus RAG System ===${NC}"

# ─────────────────────────────────────────────
# 1. Check for Python 3
# ─────────────────────────────────────────────
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: python3 is required but not installed.${NC}" >&2
    exit 1
fi
echo -e "${GREEN}✓ Python3 found: $(python3 --version)${NC}"

# ─────────────────────────────────────────────
# 2. Create virtual environment if needed
# ─────────────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment (.venv)..."
    python3 -m venv .venv
fi
echo -e "${GREEN}✓ Virtual environment ready${NC}"

# ─────────────────────────────────────────────
# 2.5. Environment File Setup
# ─────────────────────────────────────────────
if [ ! -f ".env" ]; then
    echo "Copying .env.example to .env..."
    cp .env.example .env
    echo -e "${GREEN}✓ Created .env file${NC}"
else
    echo -e "${GREEN}✓ .env file already exists${NC}"
fi

# ─────────────────────────────────────────────
# 2.6. Configure Ollama Host IP
# ─────────────────────────────────────────────
echo ""
echo -e "${YELLOW}--- Ollama Configuration ---${NC}"
read -p "Enter Ollama host IP (default: localhost): " ollama_ip
if [ -z "$ollama_ip" ]; then
    ollama_ip="localhost"
fi

# Update .env using Python (platform-independent)
python3 -c "
import sys
with open('.env', 'r') as f:
    lines = f.readlines()
has_host = False
with open('.env', 'w') as f:
    for line in lines:
        if line.startswith('OLLAMA_HOST='):
            f.write('OLLAMA_HOST=http://' + sys.argv[1] + ':11434\n')
            has_host = True
        else:
            f.write(line)
    if not has_host:
        f.write('\nOLLAMA_HOST=http://' + sys.argv[1] + ':11434\n')
" "$ollama_ip"
echo -e "${GREEN}✓ Set OLLAMA_HOST to http://${ollama_ip}:11434 in .env${NC}"

# ─────────────────────────────────────────────
# 3. Upgrade pip and install package
# ─────────────────────────────────────────────
echo "Installing package and dependencies..."
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -e . -q
echo -e "${GREEN}✓ Nexus package installed${NC}"

# ─────────────────────────────────────────────
# 4. Docker setup (required for code sandbox)
# ─────────────────────────────────────────────
echo ""
echo -e "${YELLOW}--- Docker Setup ---${NC}"

OS="$(uname -s)"

_ensure_docker_running() {
    # Wait up to 60s for Docker daemon to be ready
    local retries=30
    echo -n "Waiting for Docker daemon"
    while ! docker info &>/dev/null 2>&1; do
        sleep 2
        echo -n "."
        retries=$((retries - 1))
        if [ "$retries" -le 0 ]; then
            echo ""
            echo -e "${RED}Docker daemon did not start in time.${NC}"
            echo "Please open Docker Desktop manually from /Applications/Docker.app and re-run this script."
            return 1
        fi
    done
    echo ""
    echo -e "${GREEN}✓ Docker daemon is running${NC}"
}

# ── Clean up stale Docker symlinks (from a previously uninstalled Docker Desktop) ──
# These broken links at /usr/local/bin/docker block fresh brew installs.
for stale_link in /usr/local/bin/docker \
                  /usr/local/bin/docker-compose \
                  /usr/local/bin/docker-credential-desktop \
                  /usr/local/bin/docker-credential-osxkeychain \
                  /usr/local/bin/kubectl.docker; do
    if [ -L "$stale_link" ] && [ ! -e "$stale_link" ]; then
        echo -e "${YELLOW}Removing stale Docker symlink: $stale_link${NC}"
        sudo rm -f "$stale_link" 2>/dev/null || {
            echo -e "${RED}Could not remove $stale_link (need sudo). Run: sudo rm -f $stale_link${NC}"
        }
    fi
done

# Locate the docker binary (may not be in PATH yet)
DOCKER_BIN=""
for candidate in "$(command -v docker 2>/dev/null)" \
                 "/usr/local/bin/docker" \
                 "/usr/bin/docker" \
                 "$HOME/.docker/bin/docker"; do
    if [ -x "$candidate" ]; then
        DOCKER_BIN="$candidate"
        break
    fi
done

if [ -n "$DOCKER_BIN" ] && "$DOCKER_BIN" info &>/dev/null 2>&1; then
    # Docker binary found AND daemon already running — nothing to do
    echo -e "${GREEN}✓ Docker is installed and daemon is running${NC}"

elif [ -n "$DOCKER_BIN" ] || [ -d "/Applications/Docker.app" ]; then
    # Docker is installed but daemon is not running — just launch it
    echo -e "${YELLOW}Docker is installed but the daemon is not running.${NC}"
    if [ "$OS" = "Darwin" ]; then
        echo "Starting Docker Desktop..."
        open -a Docker 2>/dev/null || open /Applications/Docker.app 2>/dev/null || true
        _ensure_docker_running
    else
        echo "Starting Docker service..."
        sudo systemctl start docker 2>/dev/null || sudo service docker start 2>/dev/null || true
        _ensure_docker_running
    fi

else
    # Docker is truly absent — install it

    echo -e "${YELLOW}Docker not found. Installing...${NC}"

    if [ "$OS" = "Darwin" ]; then
        if ! command -v brew &>/dev/null; then
            echo -e "${RED}Homebrew is required to install Docker on macOS.${NC}"
            echo "Install Homebrew first: https://brew.sh"
            echo "Or download Docker Desktop manually: https://www.docker.com/products/docker-desktop/"
            exit 1
        fi
        echo "Installing Docker Desktop via Homebrew (this may take a few minutes)..."
        brew install --cask docker
        echo "Launching Docker Desktop (please allow any system prompts)..."
        open -a Docker
        _ensure_docker_running

    elif [ "$OS" = "Linux" ]; then
        if command -v apt-get &>/dev/null; then
            # Detect whether this is Debian or Ubuntu
            DISTRO_ID="$(. /etc/os-release && echo "${ID:-ubuntu}")"
            DISTRO_CODENAME="$(. /etc/os-release && echo "${VERSION_CODENAME:-}")"

            # Normalize: Ubuntu derivatives (e.g. Linux Mint) may set ID_LIKE
            if [ "$DISTRO_ID" != "debian" ] && [ "$DISTRO_ID" != "ubuntu" ]; then
                DISTRO_ID="$(. /etc/os-release && echo "${ID_LIKE:-ubuntu}" | awk '{print $1}')" 
            fi
            # Only 'debian' or 'ubuntu' are valid repo names for Docker
            if [ "$DISTRO_ID" != "debian" ]; then
                DISTRO_ID="ubuntu"
            fi

            echo "Detected ${DISTRO_ID^}. Installing Docker via apt..."

            # Debian testing/unstable codenames (e.g. trixie, sid) have no Docker repo yet.
            # Fall back to the latest stable Debian release (bookworm).
            if [ "$DISTRO_ID" = "debian" ]; then
                STABLE_DEBIAN_CODENAMES=("bookworm" "bullseye" "buster")
                CODENAME_OK=false
                for cn in "${STABLE_DEBIAN_CODENAMES[@]}"; do
                    if [ "$DISTRO_CODENAME" = "$cn" ]; then
                        CODENAME_OK=true
                        break
                    fi
                done
                if [ "$CODENAME_OK" = false ]; then
                    echo -e "${YELLOW}Warning: Debian codename '$DISTRO_CODENAME' has no Docker repo. Falling back to 'bookworm'.${NC}"
                    DISTRO_CODENAME="bookworm"
                fi
            fi

            # Remove any stale docker.list from a previous failed run before updating
            sudo rm -f /etc/apt/sources.list.d/docker.list
            sudo apt-get update -qq
            sudo apt-get install -y -qq ca-certificates curl gnupg
            sudo install -m 0755 -d /etc/apt/keyrings
            # Remove stale GPG key if present so gpg --dearmor doesn't prompt
            sudo rm -f /etc/apt/keyrings/docker.gpg
            curl -fsSL "https://download.docker.com/linux/${DISTRO_ID}/gpg" | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
            sudo chmod a+r /etc/apt/keyrings/docker.gpg
            echo \
              "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${DISTRO_ID} ${DISTRO_CODENAME} stable" | \
              sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
            sudo apt-get update -qq
            sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin
            sudo systemctl enable --now docker
            sudo usermod -aG docker "$USER" 2>/dev/null || true
            _ensure_docker_running

        elif command -v dnf &>/dev/null; then
            echo "Detected Fedora/RHEL. Installing Docker via dnf..."
            sudo dnf -y install dnf-plugins-core
            sudo dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
            sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin
            sudo systemctl enable --now docker
            sudo usermod -aG docker "$USER" 2>/dev/null || true
            _ensure_docker_running

        else
            echo -e "${RED}Unsupported Linux distro. Install Docker manually: https://docs.docker.com/engine/install/${NC}"
            exit 1
        fi

    else
        echo -e "${RED}Unsupported OS: $OS. Install Docker manually: https://www.docker.com/products/docker-desktop/${NC}"
        exit 1
    fi
fi

# ─────────────────────────────────────────────
# 4.5. Start Qdrant Database
# ─────────────────────────────────────────────
echo ""
echo -e "${YELLOW}--- Starting Qdrant Database ---${NC}"
if command -v docker-compose &>/dev/null; then
    echo "Starting Qdrant using docker-compose..."
    docker-compose up -d
    echo -e "${GREEN}✓ Qdrant started${NC}"
elif docker compose version &>/dev/null; then
    echo "Starting Qdrant using docker compose..."
    docker compose up -d
    echo -e "${GREEN}✓ Qdrant started${NC}"
else
    echo -e "${RED}Warning: docker-compose or docker compose not found. Could not start Qdrant automatically.${NC}"
    echo "Please make sure Qdrant is running on port 6333."
fi


# ─────────────────────────────────────────────
# 5. Pre-pull the Python sandbox image
# ─────────────────────────────────────────────
echo ""
echo "Pre-pulling Docker sandbox image (python:3.11-slim)..."
if docker pull python:3.11-slim; then
    echo -e "${GREEN}✓ Sandbox image ready: python:3.11-slim${NC}"
else
    echo -e "${YELLOW}Warning: Could not pull python:3.11-slim. Code sandbox tasks may be slow on first run.${NC}"
fi

# ─────────────────────────────────────────────
# 6. Build and Pre-pull MCP Server Docker Images
# ─────────────────────────────────────────────
echo ""
echo -e "${YELLOW}--- Build & Pre-pull MCP Docker Images ---${NC}"

# Build custom Python database server image
if [ -f "docker_scripts/Dockerfile.mcp" ]; then
    echo "Building custom Python MCP server image (nexus-mcp:latest)..."
    if docker build -t nexus-mcp:latest -f docker_scripts/Dockerfile.mcp .; then
        echo -e "${GREEN}✓ Custom Python MCP image built: nexus-mcp:latest${NC}"
    else
        echo -e "${RED}Warning: Failed to build nexus-mcp:latest.${NC}"
    fi
fi

# Pull official OCI MCP catalog images
echo "Pre-pulling official OCI MCP catalog images..."
for img in \
    mcp/playwright \
    mcp/filesystem \
    mcp/desktop-commander \
    mcp/git \
    mcp/memory \
    mcp/docker \
    crystaldba/postgres-mcp \
    mcp/context7 \
    mcp/firecrawl \
    mcp/time \
    mcp/sequentialthinking \
    mcp/fetch \
    mcp/duckduckgo; do
    echo "Pulling $img..."
    if docker pull "$img" &>/dev/null; then
        echo -e "${GREEN}✓ Ready: $img${NC}"
    else
        echo -e "${YELLOW}Warning: Could not pull $img.${NC}"
    fi
done

# Pull GitHub official MCP server (hosted on GitHub Container Registry)
echo "Pulling ghcr.io/github/github-mcp-server..."
if docker pull ghcr.io/github/github-mcp-server &>/dev/null; then
    echo -e "${GREEN}✓ Ready: ghcr.io/github/github-mcp-server${NC}"
else
    echo -e "${YELLOW}Warning: Could not pull ghcr.io/github/github-mcp-server. Ensure you are authenticated with ghcr.io.${NC}"
fi

# Pre-fetch SSH MCP server (uses npx, arm64-compatible)
echo "Pre-fetching SSH MCP server via npx..."
if npx -y @idletoaster/ssh-mcp-server@latest --version &>/dev/null 2>&1 || true; then
    echo -e "${GREEN}✓ SSH MCP server available via npx${NC}"
else
    echo -e "${YELLOW}Note: SSH MCP server will be auto-downloaded on first use via npx.${NC}"
fi


# ─────────────────────────────────────────────
# 6.5. Ollama Setup
# ─────────────────────────────────────────────
echo ""
echo -e "${YELLOW}--- Ollama Connection Check ---${NC}"

# Read Ollama Host and Models from .env
OLLAMA_HOST_VAL="http://localhost:11434"
if [ -f ".env" ]; then
    OLLAMA_HOST_VAL=$(grep "^OLLAMA_HOST=" .env | cut -d'=' -f2- | tr -d '\r')
fi

# Fallback if empty
if [ -z "$OLLAMA_HOST_VAL" ]; then
    OLLAMA_HOST_VAL="http://localhost:11434"
fi

echo "Checking Ollama daemon at $OLLAMA_HOST_VAL..."
if curl -s --connect-timeout 2 "$OLLAMA_HOST_VAL" &>/dev/null; then
    echo -e "${GREEN}✓ Ollama daemon is running${NC}"
else
    echo -e "${YELLOW}Warning: Ollama daemon not running at $OLLAMA_HOST_VAL.${NC}"
    echo "Please make sure Ollama is started."
fi

# Show the models the user needs to pull manually
echo ""
echo "Note: Auto-pulling models is disabled. Please ensure the following models are pulled in Ollama:"
if [ -f ".env" ]; then
    grep "OLLAMA_.*_MODEL" .env | while read -r line; do
        m_name=$(echo "$line" | cut -d'=' -f2 | tr -d '\r')
        echo -e "   ${GREEN}ollama pull $m_name${NC}"
    done
else
    echo -e "   ${GREEN}ollama pull llama3.1:8b${NC}"
    echo -e "   ${GREEN}ollama pull nomic-embed-text${NC}"
fi

# ─────────────────────────────────────────────
# 7. Export configuration instructions
# ─────────────────────────────────────────────
BIN_PATH="$(pwd)/.venv/bin"
echo ""
echo -e "${GREEN}=== Setup Complete! ===${NC}"
echo "To run the RAG system by typing 'nexus' from your shell:"
echo ""
echo "1. Add the bin path to your current shell session:"
echo -e "   ${GREEN}export PATH=\"$BIN_PATH:\$PATH\"${NC}"
echo ""
echo "2. Make it permanent by appending to your shell profile:"
echo -e "   ${GREEN}echo 'export PATH=\"$BIN_PATH:\$PATH\"' >> ~/.zshrc${NC}"
echo "   (use ~/.bashrc if you're on bash)"
echo ""
echo "Then just type: nexus"
echo "======================================="
