#!/bin/bash
# install_microcore.sh - Install all dependencies for MicroCore

echo "=========================================="
echo "MICROCORE (MCX) DEPENDENCY INSTALLER"
echo "=========================================="
echo ""

# Check Python version
echo "Checking Python version..."
python3 --version
if [ $? -ne 0 ]; then
    echo "ERROR: Python 3 not found. Please install Python 3.7+ first."
    exit 1
fi

# Upgrade pip
echo ""
echo "Upgrading pip..."
python3 -m pip install --upgrade pip

# Install core dependencies
echo ""
echo "Installing core dependencies..."

# WebSocket support
echo "  - websockets"
pip3 install websockets

# Cryptography (ECDSA secp256k1)
echo "  - cryptography"
pip3 install cryptography

# HTTP requests (for IP detection, DEX, etc.)
echo "  - requests"
pip3 install requests

# DNS resolution (for DNS seeds - optional, gossip discovery works without)
echo "  - dnspython"
pip3 install dnspython

# Serial communication (for Arduino Uno bridge)
echo "  - pyserial"
pip3 install pyserial

# JSON handling (already built-in, but ensure)
echo "  - simplejson (fallback)"
pip3 install simplejson 2>/dev/null

# Optional: Better console output
echo "  - colorama (colored output)"
pip3 install colorama

# Optional: Faster JSON
echo "  - ujson (faster JSON)"
pip3 install ujson 2>/dev/null

echo ""
echo "=========================================="
echo "INSTALLATION COMPLETE!"
echo "=========================================="
echo ""
echo "Installed packages:"
pip3 list | grep -E "websockets|cryptography|requests|dnspython|pyserial|colorama"

echo ""
echo "To verify installation, run:"
echo "  python3 -c \"import websockets, cryptography, requests; print('OK')\""
echo ""
echo "To start a node:"
echo "  python3 node_full.py --genesis --username YOUR_NAME"
echo ""
echo "To start a miner:"
echo "  python3 pc_miner.py"
echo ""
