#!/bin/bash
# install_termux.sh - Install MicroCore on Termux (Android)

echo "=========================================="
echo "MICROCORE (MCX) TERMUX INSTALLER"
echo "=========================================="
echo ""

# Update packages
echo "Updating Termux packages..."
pkg update -y && pkg upgrade -y

# Install Python and required system packages
echo "Installing Python and dependencies..."
pkg install -y python python-pip openssl libffi

# Install Python packages
echo "Installing Python packages..."

# WebSocket (may have issues on Termux, but try)
pip install websockets

# Cryptography (requires openssl)
pip install cryptography

# Requests
pip install requests

# DNS Python
pip install dnspython

# Colorama for colored output
pip install colorama

echo ""
echo "=========================================="
echo "INSTALLATION COMPLETE!"
echo "=========================================="
echo ""
echo "To run a node on Termux:"
echo "  python phone_node.py --genesis --username YOUR_NAME"
echo ""
echo "To run a miner on Termux:"
echo "  python phone_miner.py"
echo ""
