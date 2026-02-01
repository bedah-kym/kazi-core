#!/usr/bin/env bash
set -euo pipefail

# Quick bootstrap for OCI Ampere VM (Ubuntu/Debian). Run as ubuntu user with sudo.

WEB_IMAGE="${WEB_IMAGE:-<region>.ocir.io/<tenancy-namespace>/mathia-web:latest}"

echo "[1/6] Updating packages & installing Docker"
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl gnupg lsb-release
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker "$USER"

echo "[2/6] Docker info"
docker --version
docker compose version

echo "[3/6] Logging into OCIR (you need an auth token as password)"
echo "If this hangs, press Ctrl+C and run: docker login <region>.ocir.io -u '<tenancy-namespace>/<username>'"

if [[ -n "${OCIR_USERNAME:-}" && -n "${OCIR_PASSWORD:-}" ]]; then
  echo "$OCIR_PASSWORD" | docker login -u "$OCIR_USERNAME" --password-stdin "$(echo "$WEB_IMAGE" | cut -d/ -f1)"
fi

echo "[4/6] Create app directory"
mkdir -p ~/mathia && cd ~/mathia

echo "[5/6] Copy project files here (or git clone). Ensure .env is present and WEB_IMAGE is set."
echo "WEB_IMAGE env currently: $WEB_IMAGE"

echo "[6/6] Start stack"
WEB_IMAGE="$WEB_IMAGE" docker compose -f docker-compose.oci.yml up -d

echo "Done. Verify: docker compose ps; web should be on :8000 (put behind HTTPS proxy)."
