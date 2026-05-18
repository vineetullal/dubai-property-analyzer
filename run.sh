#!/bin/bash
set -e

cd "$(dirname "$0")"

# Create virtualenv if needed
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate

echo "Installing dependencies..."
pip install -q -r requirements.txt

echo "Launching Dubai Property Analyzer..."
streamlit run app.py --server.port 8502 --browser.gatherUsageStats false
