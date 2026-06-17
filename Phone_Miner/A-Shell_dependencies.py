#!/usr/bin/env python3
"""
install_iphone.py - Install MicroCore dependencies on a-Shell (iPhone)
Run: python3 install_iphone.py
"""

import os
import sys

print("=" * 50)
print("MICROCORE (MCX) a-Shell INSTALLER")
print("=" * 50)
print()

# a-Shell has limited pip capabilities
# Install what's available

packages = [
    "websockets",
    "cryptography",
    "requests",
    "dnspython",
]

for pkg in packages:
    print(f"Installing {pkg}...")
    os.system(f"pip install {pkg}")

print()
print("=" * 50)
print("INSTALLATION COMPLETE!")
print("=" * 50)
print()
print("Note: a-Shell has limitations. For best results:")
print("  - Use the simplified phone_node.py for nodes")
print("  - Use phone_miner.py for mining")
print()
print("To run a node:")
print("  python phone_node.py --genesis --username YOUR_NAME")
print()
print("To run a miner:")
print("  python phone_miner.py")
