#!/usr/bin/env bash
# Atualiza o FinFam 2026 no Raspberry: baixa o código, gera o site e reinicia a API e o bot.
# Uso (como o usuário finfam, SEM sudo):  ./atualizar.sh
# Só o restart pede sudo (a senha é perguntada nessa hora).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICES=(finfam2026 finfam2026-bot)

if [ "$(id -u)" -eq 0 ]; then
  echo "❌ Não rode com sudo: os arquivos ficariam do root e o próximo git pull/npm falharia."
  echo "   Rode só:  ./atualizar.sh"
  exit 1
fi

cd "$DIR"
echo "📥 Baixando o código (main)…"
before="$(git rev-parse HEAD)"
git pull --ff-only origin main
after="$(git rev-parse HEAD)"

if [ "$before" != "$after" ] && git diff --name-only "$before" "$after" | grep -q '^backend/requirements.txt$'; then
  if [ -x backend/.venv/bin/pip ]; then
    echo "📦 Dependências da API mudaram: instalando…"
    backend/.venv/bin/pip install -q -r backend/requirements.txt
  else
    echo "⚠️  backend/requirements.txt mudou, mas não achei backend/.venv — instale as dependências à mão."
  fi
fi

echo "🛠  Gerando o site…"
cd frontend
if [ "$before" != "$after" ] && git diff --name-only "$before" "$after" | grep -q '^frontend/package-lock.json$'; then
  npm ci --no-audit --no-fund
fi
npm run build
cd "$DIR"

echo "🔄 Reiniciando ${SERVICES[*]}…"
sudo systemctl restart "${SERVICES[@]}"
sleep 3
for s in "${SERVICES[@]}"; do
  if systemctl is-active --quiet "$s"; then
    echo "✅ $s no ar"
  else
    echo "❌ $s não subiu. Veja: sudo journalctl -u $s -n 50"
    exit 1
  fi
done

# arquivos de serviço do systemd mudaram no repositório? (não troca sozinho: só avisa)
for unit in finfam2026 finfam2026-bot; do
  if [ -f "/etc/systemd/system/$unit.service" ] && [ -f "deploy/$unit.service" ] && \
     ! diff -q "/etc/systemd/system/$unit.service" "deploy/$unit.service" >/dev/null 2>&1; then
    echo "ℹ️  deploy/$unit.service mudou. Para aplicar (veja o CHANGELOG antes):"
    echo "    sudo cp deploy/$unit.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart $unit"
  fi
done

version="$(grep -oP "^VERSION = '\K[^']+" backend/app/main.py || true)"
echo "🎉 FinFam 2026 ${version:-} atualizado ($(git log -1 --format='%h %s'))."
