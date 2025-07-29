#!/bin/bash
set -e

echo "🌾 OpenWeedLocator Easy Installation"
echo "Detecting system architecture..."

ARCH=$(uname -m)
if [[ "$ARCH" == "aarch64" ]]; then
    echo "✅ ARM64 detected - using optimized builds"
    USE_PREBUILT=true
else
    echo "ℹ️  x86_64 detected - compiling from source"
    USE_PREBUILT=false
fi

if command -v docker &> /dev/null; then
    echo "🐳 Docker installation (recommended)"
    docker pull openweedlocator/owl:latest-arm64 || true
fi

if command -v conda &> /dev/null; then
    echo "🐍 Conda installation"
    conda env create -f environment-rust.yml
fi

echo "📦 Pip installation with Rust acceleration"
pip install openweedlocator[rust]
