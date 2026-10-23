#!/bin/bash
# Atualiza o FinFam 2026 no Raspberry (não toca no FinFam antigo).
set -e
DIR="/home/finfam/finfam2026"
cd "$DIR"
git fetch origin
if [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ]; then
  echo "Já está na versão mais recente."; exit 0
fi
git pull origin main
backend/.venv/bin/pip install -q -r backend/requirements.txt
if command -v npm >/dev/null; then (cd frontend && npm ci --silent && npm run build); fi
sudo systemctl restart finfam2026
echo "FinFam 2026 atualizado e reiniciado."
