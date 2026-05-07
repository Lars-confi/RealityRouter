#!/usr/bin/env bash

# Exit on error
set -e

# Define colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

ZIP_NAME="reality_router_dist.zip"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   Building Reality Router Distribution   ${NC}"
echo -e "${BLUE}========================================${NC}"

# Check if zip command is installed
if ! command -v zip &> /dev/null; then
    echo "The 'zip' command could not be found. Please install it to continue."
    exit 1
fi

# Remove existing zip if it exists
if [ -f "$ZIP_NAME" ]; then
    echo -e "${GREEN}Removing old distribution file ($ZIP_NAME)...${NC}"
    rm "$ZIP_NAME"
fi

echo -e "${GREEN}Zipping project files...${NC}"
echo "Excluding: testing scripts, venv/, .git/, __pycache__/, and sensitive configurations (.env, DB, etc)."

# Create the zip file, excluding unwanted directories and personal/system files
zip -r "$ZIP_NAME" . \
    -x "venv/*" \
    -x ".env" \
    -x "reality_router.db" \
    -x "disabled_models.json" \
    -x "user_models.json" \
    -x "test_*.py" \
    -x "*/test_*.py" \
    -x "patch_*.py" \
    -x "*/patch_*.py" \
    -x ".pytest_cache/*" \
    -x "*/.pytest_cache/*" \
    -x "*/__pycache__/*" \
    -x "*.pyc" \
    -x ".git/*" \
    -x "*.DS_Store" \
    -x "$ZIP_NAME"

echo -e "\n${BLUE}========================================${NC}"
echo -e "${GREEN}✓ Success! Created distribution package: ${ZIP_NAME}${NC}"
echo -e "${BLUE}========================================${NC}"
echo "You can now safely share this zip file. When another user extracts it"
echo "and runs ./start.sh, the interactive wizard will guide them through"
echo "creating their own .env file, virtual environment, and database."
