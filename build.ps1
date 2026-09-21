# Gera os dois produtos do StudyIA:
#
#   executable\StudyIA.exe             -> programa portatil, e so abrir
#   installer\StudyIA-Instalador.exe   -> assistente que instala no computador
#
# Uso:  powershell -ExecutionPolicy Bypass -File build.ps1
#       powershell -ExecutionPolicy Bypass -File build.ps1 -Apenas programa
#       powershell -ExecutionPolicy Bypass -File build.ps1 -Apenas instalador

param([ValidateSet("tudo", "programa", "instalador")] [string]$Apenas = "tudo")

$ErrorActionPreference = "Stop"
$raiz = $PSScriptRoot
Set-Location $raiz

$python = Join-Path $raiz ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Rode primeiro: py -3.12 -m venv .venv e instale o requirements.txt" }

function Tamanho($caminho) { "{0} MB" -f [math]::Round((Get-Item $caminho).Length / 1MB, 1) }

# Todos os caminhos sao absolutos: o PyInstaller resolve caminhos relativos a partir
# da pasta do .spec, nao da pasta atual, e isso ja quebrou o build uma vez.
$web      = Join-Path $raiz "web"
$assets   = Join-Path $raiz "assets"
$saidaExe = Join-Path $raiz "executable"
$saidaIns = Join-Path $raiz "installer"
$trabalho = Join-Path $raiz "build"

# --------------------------------------------------------------- o programa
if ($Apenas -in @("tudo", "programa")) {
    Write-Host "[1/2] Empacotando o programa..." -ForegroundColor Cyan

    & $python -m PyInstaller `
        --noconfirm --onefile --noconsole `
        --name StudyIA `
        --distpath $saidaExe `
        --workpath (Join-Path $trabalho "programa") `
        --specpath $trabalho `
        --icon (Join-Path $assets "studyia.ico") `
        --splash (Join-Path $assets "splash.png") `
        --add-data "$web;web" `
        --collect-all webview `
        --collect-all google.genai `
        (Join-Path $raiz "desktop.py")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou no programa (codigo $LASTEXITCODE)" }

    Copy-Item (Join-Path $raiz "packaging\LEIA-ME.txt") (Join-Path $saidaExe "LEIA-ME.txt") -Force
    Write-Host "      executable\StudyIA.exe  ($(Tamanho (Join-Path $saidaExe 'StudyIA.exe')))" -ForegroundColor Green
}

# ------------------------------------------------------------- o instalador
if ($Apenas -in @("tudo", "instalador")) {
    $programa = Join-Path $saidaExe "StudyIA.exe"
    if (-not (Test-Path $programa)) { throw "Gere o programa antes: build.ps1 -Apenas programa" }
    Write-Host "[2/2] Empacotando o instalador..." -ForegroundColor Cyan

    & $python -m PyInstaller `
        --noconfirm --onefile --noconsole `
        --name StudyIA-Instalador `
        --distpath $saidaIns `
        --workpath (Join-Path $trabalho "instalador") `
        --specpath $trabalho `
        --icon (Join-Path $assets "studyia.ico") `
        --add-data "$programa;payload" `
        --add-data ("{0};assets" -f (Join-Path $assets "studyia.ico")) `
        --add-data ("{0};assets" -f (Join-Path $assets "studyia.png")) `
        --add-data ("{0};packaging" -f (Join-Path $raiz "packaging\LEIA-ME.txt")) `
        (Join-Path $raiz "packaging\instalador.py")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou no instalador (codigo $LASTEXITCODE)" }

    Copy-Item (Join-Path $raiz "packaging\LEIA-ME-instalador.txt") (Join-Path $saidaIns "LEIA-ME.txt") -Force
    Write-Host "      installer\StudyIA-Instalador.exe  ($(Tamanho (Join-Path $saidaIns 'StudyIA-Instalador.exe')))" -ForegroundColor Green
}

Write-Host ""
Write-Host "Pronto. Os dois jeitos de entregar o StudyIA:" -ForegroundColor Green
Write-Host "  executable\  -> um .exe portatil, roda de qualquer pasta ou pendrive"
Write-Host "  installer\   -> assistente com atalhos e desinstalador"
Write-Host "Dados do usuario: $env:LOCALAPPDATA\StudyIA\studyia.db" -ForegroundColor DarkGray
