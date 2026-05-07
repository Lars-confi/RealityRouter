#!/usr/bin/env bash

# Exit on error
set -e

# Define colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}       Reality Router Initialization      ${NC}"
echo -e "${BLUE}========================================${NC}"

# Define App Home
export REALITY_ROUTER_HOME="${REALITY_ROUTER_HOME:-$HOME/.reality_router}"
mkdir -p "$REALITY_ROUTER_HOME"
mkdir -p "$REALITY_ROUTER_HOME/config"
mkdir -p "$REALITY_ROUTER_HOME/logs"

# Optional: Migrate existing files if they exist in the current directory and not in REALITY_ROUTER_HOME
for file in .env disabled_models.json user_models.json reality_router.db; do
    if [ -f "$file" ]; then
        if [ ! -f "$REALITY_ROUTER_HOME/$file" ]; then
            echo -e "${YELLOW}Migrating $file to $REALITY_ROUTER_HOME...${NC}"
            cp "$file" "$REALITY_ROUTER_HOME/"
        fi
        rm "$file"
    fi
done

if [ -d "reality-router/config" ]; then
    for file in reality-router/config/*.json; do
        if [ -f "$file" ]; then
            if [ ! -f "$REALITY_ROUTER_HOME/config/$(basename "$file")" ]; then
                echo -e "${YELLOW}Migrating $(basename "$file") to $REALITY_ROUTER_HOME/config...${NC}"
                cp "$file" "$REALITY_ROUTER_HOME/config/"
            fi
        fi
    done
    rm -rf "reality-router/config"
fi

if [ -d "logs" ]; then
    for file in logs/*.log; do
        if [ -f "$file" ]; then
            if [ ! -f "$REALITY_ROUTER_HOME/logs/$(basename "$file")" ]; then
                cp "$file" "$REALITY_ROUTER_HOME/logs/"
            fi
            rm "$file"
        fi
    done
    rmdir logs 2>/dev/null || true
fi

# 1. Check for Python 3
if ! command -v python3 &> /dev/null; then
    echo -e "${YELLOW}Python 3 is not installed. Please install Python 3 to continue.${NC}"
    exit 1
fi

# 2. Check/Create Virtual Environment
if [ ! -d "venv" ]; then
    echo -e "${GREEN}Creating Python virtual environment...${NC}"
    python3 -m venv venv
else
    echo -e "${GREEN}Virtual environment found.${NC}"
fi

# 3. Activate Virtual Environment
echo -e "${GREEN}Activating virtual environment...${NC}"
if [ -f "venv/bin/activate" ]; then
    # Mac/Linux
    source venv/bin/activate
elif [ -f "venv/Scripts/activate" ]; then
    # Windows (Git Bash/MSYS2/WSL)
    source venv/Scripts/activate
else
    echo -e "${YELLOW}Warning: Could not find virtual environment activation script.${NC}"
fi

# 4. Install Dependencies
echo -e "${GREEN}Checking and installing dependencies...${NC}"
cd reality-router
# Upgrade pip to prevent older pip issues
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
cd ..

# 5. Launch the Python TUI
echo -e "${GREEN}Launching Reality Router Setup Wizard...${NC}"
export LOG_DIR="$REALITY_ROUTER_HOME/logs"
python3 start_router.py

# Deactivate venv upon exit
deactivate
