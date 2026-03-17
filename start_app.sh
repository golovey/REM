#!/bin/bash
# Terminal UI Web App - Complete Startup Script
# This script handles everything: venv creation, Flask installation, and app startup

cd "$(dirname "$0")"

echo "============================================================"
echo "Terminal UI Web App - Starting..."
echo "============================================================"
echo ""

# Check if virtual environment exists, create if not
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
    echo "✅ Virtual environment created"
    echo ""
fi

PYTHON=venv/bin/python3

echo "🔧 Activating virtual environment..."

# Check if Flask is installed, install if not
if ! $PYTHON -c "import flask" 2>/dev/null; then
    echo "📥 Installing Flask..."
    $PYTHON -m pip install flask
    echo "✅ Flask installed"
    echo ""
fi

# Check if PyYAML is installed, install if not
if ! $PYTHON -c "import yaml" 2>/dev/null; then
    echo "📥 Installing PyYAML..."
    $PYTHON -m pip install pyyaml
    echo "✅ PyYAML installed"
    echo ""
fi

# Check if port 5000 is already in use
if lsof -Pi :5000 -sTCP:LISTEN -t >/dev/null 2>&1 ; then
    echo "============================================================"
    echo "✅ App is already running!"
    echo "============================================================"
    echo ""
    echo "Opening browser at: http://localhost:5000"
    echo ""
    echo "To stop the existing instance, find the terminal window"
    echo "where it's running and press Ctrl+C"
    echo "============================================================"
    echo ""

    # Open browser to existing instance
    if command -v open >/dev/null 2>&1; then
        # macOS
        open http://localhost:5000
    elif command -v xdg-open >/dev/null 2>&1; then
        # Linux
        xdg-open http://localhost:5000
    else
        echo "Please manually open: http://localhost:5000"
    fi

    exit 0
fi

# Start the app
echo "============================================================"
echo "🚀 Starting Terminal UI Web App"
echo "============================================================"
echo ""
echo "The app will open in your browser at:"
echo "  http://localhost:5000"
echo ""
echo "Press Ctrl+C to stop the server"
echo "============================================================"
echo ""

$PYTHON terminal_ui_app_web.py
