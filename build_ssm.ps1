# build_ssm.ps1 — Empacota o SSM com PyInstaller (onedir, pronto para Inno Setup)
# Uso (PowerShell na pasta C:\ssm, com venv ja criado, app FECHADO):
#   powershell -ExecutionPolicy Bypass -File .\build_ssm.ps1

$ErrorActionPreference = "Stop"
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    throw "venv nao encontrado em .venv - crie com: python -m venv .venv"
}

# 0) Garante que nada esta rodando (evita "acesso negado" ao limpar dist\)
Get-Process ssm, SSM, electron -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500

# 1) Dependencias (fonte unica: requirements.txt -- inclui pyinstaller,
#    fpdf2 dos recibos e pillow do icone)
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $ProjectDir "requirements.txt")

# 2) Electron precisa estar baixado
$ElectronExe = Join-Path $ProjectDir "node_modules\electron\dist\electron.exe"
if (-not (Test-Path $ElectronExe)) {
    throw "Electron nao encontrado em node_modules\electron\dist\electron.exe - rode 'npm install -D electron' antes."
}

# 3) Limpa builds anteriores
foreach ($d in @("build", "dist")) { if (Test-Path $d) { Remove-Item -Recurse -Force $d } }
if (Test-Path "ssm.spec") { Remove-Item -Force "ssm.spec" }

# 4) Icone (opcional)
$IconArgs = @()
$IconPath = Join-Path $ProjectDir "app.ico"
if (Test-Path $IconPath) { $IconArgs = @("--icon", $IconPath); Write-Host "Icone: $IconPath" -ForegroundColor Cyan }

# 5) PyInstaller (onedir)
& $VenvPython -m PyInstaller `
    --noconfirm --clean --onedir --name ssm --noconsole `
    @IconArgs `
    --hidden-import server `
    --hidden-import languages --hidden-import flask_babel --hidden-import fpdf `
    --hidden-import winreg --hidden-import secrets `
    --collect-submodules babel --collect-data babel `
    --add-data "node_modules\.bin;node_modules\.bin" `
    --add-data "node_modules\electron\dist;node_modules\electron\dist" `
    viewer.py

if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou (exit $LASTEXITCODE)." }

$DistDir = Join-Path $ProjectDir "dist\ssm"

# 6) viewables + icone ao lado do exe
Copy-Item -Recurse -Force (Join-Path $ProjectDir "viewables") (Join-Path $DistDir "viewables")
if (Test-Path $IconPath) { Copy-Item -Force $IconPath (Join-Path $DistDir "app.ico") }

# 7) Renomeia o electron.exe empacotado -> SSM.exe
#    (a barra de tarefas do Windows mostra o nome do EXE que criou a janela)
#
#    IMPORTANTE: o icone/nome do SSM.exe (ProductName/FileDescription) NAO
#    sao gravados aqui no build. Isso e feito UMA UNICA VEZ, no instalador
#    (.iss), na hora da instalacao -- porque o nome precisa ser localizado
#    conforme o idioma escolhido no setup, e porque editar o mesmo binario
#    com rcedit em dois momentos separados (build + instalacao) corrompia
#    a secao de icones (bug historico: icone virava o "atomo" do Electron).
#    NAO reintroduzir uma chamada de rcedit aqui -- deixe so no ssm_setup.iss.
$ElectronDistDir  = Join-Path $DistDir "_internal\node_modules\electron\dist"
$PackedElectron   = Join-Path $ElectronDistDir "electron.exe"
$RenamedElectron  = Join-Path $ElectronDistDir "SSM.exe"

if (Test-Path $PackedElectron) {
    Copy-Item -Force $PackedElectron $RenamedElectron
    Write-Host "Electron renomeado para SSM.exe (icone/nome serao gravados so no instalador)" -ForegroundColor Cyan
} else {
    throw "electron.exe empacotado nao encontrado em $PackedElectron -- build interrompido."
}

# 7b) "Assa" um app Electron ESTATICO em resources\app, ao lado do SSM.exe.
#     Isso garante que, mesmo se o SSM.exe for aberto SEM argumentos (ex.:
#     atalho fixado na barra de tarefas apontando direto pro binario do
#     Electron, ou o item do menu do botao direito), o app funcione de
#     verdade -- em vez de mostrar a tela padrao do Electron. A logica
#     embutida no main.js (requestSingleInstanceLock + fallback de URL)
#     cuida do resto (abre nova janela na instancia ja rodando, ou sobe
#     o backend sozinha se nao houver nenhuma instancia).
#
#     CRITICO: se isso falhar, o build TODO falha (throw) -- e melhor nao
#     gerar instalador nenhum do que gerar um sem essa correcao (foi
#     exatamente essa falta que causou a tela do Electron reaparecer).
$ResourcesAppDir = Join-Path $ElectronDistDir "resources\app"
& $VenvPython (Join-Path $ProjectDir "viewer.py") --bake-resources-app "$ResourcesAppDir"
$bakeExit = $LASTEXITCODE

$bakedMainJs  = Join-Path $ResourcesAppDir "main.js"
$bakedPkgJson = Join-Path $ResourcesAppDir "package.json"

if ($bakeExit -ne 0 -or -not (Test-Path $bakedMainJs) -or -not (Test-Path $bakedPkgJson)) {
    Write-Host ""
    Write-Host "ERRO CRITICO: resources\app NAO foi gerado corretamente." -ForegroundColor Red
    Write-Host "  exit code do bake:   $bakeExit" -ForegroundColor Red
    Write-Host "  main.js existe:      $(Test-Path $bakedMainJs)" -ForegroundColor Red
    Write-Host "  package.json existe: $(Test-Path $bakedPkgJson)" -ForegroundColor Red
    throw "Bake de resources\app falhou -- build interrompido de proposito."
}

Write-Host "resources\app gerado e verificado com sucesso em: $ResourcesAppDir" -ForegroundColor Green
Get-ChildItem $ResourcesAppDir | Select-Object Name, Length | Format-Table -AutoSize

Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host " Build SSM concluido: $DistDir" -ForegroundColor Green
Write-Host "   dist\ssm\ssm.exe / _internal\ / viewables\ / app.ico" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
Write-Host "Teste 1 (fluxo normal):  .\dist\ssm\ssm.exe" -ForegroundColor Yellow
Write-Host "Teste 2 (bare-launch):   .\dist\ssm\_internal\node_modules\electron\dist\SSM.exe" -ForegroundColor Yellow
