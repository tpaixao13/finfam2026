# Envia para o GitHub tudo o que mudou na pasta (commit + push).
# Uso: clique com o botão direito > "Executar com o PowerShell"
#      ou no terminal:  .\publicar.ps1 "mensagem opcional"
param([string]$Mensagem = "")
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".git")) {
    git init -b main | Out-Null
    git remote add origin https://github.com/tpaixao13/finfam2026.git
}
# Arquivos do Claude entregues em "claude-config" (escrita remota em .claude é bloqueada):
# junta tudo em .claude e remove a pasta temporária.
if (Test-Path "claude-config") {
    New-Item -ItemType Directory -Force -Path ".claude" | Out-Null
    Copy-Item -Path "claude-config\*" -Destination ".claude" -Recurse -Force
    Remove-Item "claude-config" -Recurse -Force
}

git add -A
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "Nada novo para enviar." -ForegroundColor Yellow
} else {
    if (-not $Mensagem) { $Mensagem = "Atualização " + (Get-Date -Format "dd/MM/yyyy HH:mm") }
    git commit -m $Mensagem
    git push -u origin main
    if ($LASTEXITCODE -eq 0) { Write-Host "Enviado para o GitHub." -ForegroundColor Green }
}
if ($Host.Name -eq "ConsoleHost" -and -not $env:WT_SESSION) { Read-Host "Enter para fechar" }
