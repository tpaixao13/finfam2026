#!/bin/bash
# Atualiza o FinFam no servidor (git pull + dependências + build do front + restart).
set -e
DIR="/home/finfam/finfam2026"
cd "$DIR"
git fetch origin
if [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ]; then
  echo "Já está na versão mais recente."; exit 0
fi
git pull origin main
backend/.venv/bin/pip install -q -r backend/requirements.txt
(cd frontend && npm ci --silent && npm run build)
sudo systemctl restart finfam
echo "FinFam atualizado e reiniciado."
