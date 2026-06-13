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
            echo "Detected Debian/Ubuntu. Installing Docker via apt..."
            sudo apt-get update -qq
            sudo apt-get install -y -qq ca-certificates curl gnupg
            sudo install -m 0755 -d /etc/apt/keyrings
            curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
            sudo chmod a+r /etc/apt/keyrings/docker.gpg
            echo \
              "deb [arch=\"$(dpkg --print-architecture)\" signed-by=/etc/apt/keyrings/docker.gpg] \
              https://download.docker.com/linux/ubuntu \
              \"$(. /etc/os-release && echo \"$VERSION_CODENAME\")\" stable" | \
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
# 6. Export configuration instructions
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
