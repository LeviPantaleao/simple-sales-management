# build_ssm.ps1 — Empacota o SSM com PyInstaller (onedir, pronto para Inno Setup)
# Uso (PowerShell na pasta C:\ssm, com venv ja criado, app FECHADO):
#   powershell -ExecutionPolicy Bypass -File .\build_ssm.ps1
#
# Assinatura de codigo (OPCIONAL, mas necessaria para o usuario final nao
# ver aviso do SmartScreen nem ser bloqueado pelo Smart App Control):
#   .\build_ssm.ps1 -SignSubject "Nome no certificado"        # cert na loja do Windows
#   .\build_ssm.ps1 -SignPfx caminho.pfx -SignPfxPassword s3nha
# Sem esses parametros o build roda igual, mas gera binarios NAO assinados.
# Ver a secao "Distribuicao / assinatura" no README e a decisao D9 em DECISIONS.md.

param(
    [string]$SignSubject = "",
    [string]$SignPfx = "",
    [string]$SignPfxPassword = "",
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

# --- Assinatura de codigo -------------------------------------------------------
$SignEnabled = [bool]$SignSubject -or [bool]$SignPfx

function Resolve-SignTool {
    $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $kits = "${env:ProgramFiles(x86)}\Windows Kits\10\bin"
    if (Test-Path $kits) {
        $hit = Get-ChildItem -Path $kits -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
               Where-Object { $_.FullName -match '\\x64\\' } |
               Sort-Object FullName -Descending | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

function Invoke-CodeSign {
    param([Parameter(Mandatory)][string[]]$Paths)
    if (-not $SignEnabled) { return }
    $signtool = Resolve-SignTool
    if (-not $signtool) { throw "signtool.exe nao encontrado (instale o Windows SDK) -- necessario porque -Sign* foi passado." }
    $common = @("sign", "/fd", "SHA256", "/tr", $TimestampUrl, "/td", "SHA256", "/v")
    if ($SignPfx) {
        $common += @("/f", $SignPfx)
        if ($SignPfxPassword) { $common += @("/p", $SignPfxPassword) }
    } else {
        $common += @("/n", $SignSubject, "/sm")
    }
    foreach ($p in $Paths) {
        if (-not (Test-Path $p)) { Write-Host "  (pular assinatura, nao existe: $p)" -ForegroundColor DarkYellow; continue }
        & $signtool @common $p
        if ($LASTEXITCODE -ne 0) { throw "signtool falhou ($LASTEXITCODE) em $p" }
        Write-Host "  assinado: $p" -ForegroundColor Green
    }
}

if ($SignEnabled) {
    Write-Host "Assinatura de codigo: HABILITADA" -ForegroundColor Cyan
} else {
    Write-Host "Assinatura de codigo: DESABILITADA -- binarios sairao NAO assinados." -ForegroundColor Yellow
    Write-Host "  O usuario final vera aviso do SmartScreen (clicar 'Executar assim mesmo')," -ForegroundColor Yellow
    Write-Host "  e sera BLOQUEADO em maquinas com Smart App Control. Passe -SignSubject ou -SignPfx." -ForegroundColor Yellow
}

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

# 7c) Assinatura de codigo dos binarios que o projeto gera (so roda se -Sign* foi passado).
#     - ssm.exe: bootloader do PyInstaller, sempre nao assinado -> assinar aqui.
#     - electron.exe / SSM.exe / DLLs do Electron: ja vem assinados pelo projeto
#       Electron. ATENCAO: o ssm_setup.iss roda rcedit no SSM.exe durante a
#       instalacao (nome/icone localizados), o que INVALIDA a assinatura desse
#       arquivo. Em maquinas com Smart App Control isso derruba o launch pos-
#       instalacao. Solucao real = mover o rcedit para o build (perdendo a
#       localizacao do nome no exe) e assinar depois; nao feito aqui.
#     O instalador em si e assinado no passo do Inno (ver SignTool no ssm_setup.iss).
if ($SignEnabled) {
    Write-Host ""
    Write-Host "7c) Assinando binarios..." -ForegroundColor Cyan
    Invoke-CodeSign -Paths @(
        (Join-Path $DistDir "ssm.exe"),
        $RenamedElectron
    )
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host " Build SSM concluido: $DistDir" -ForegroundColor Green
Write-Host "   dist\ssm\ssm.exe / _internal\ / viewables\ / app.ico" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
if (-not $SignEnabled) {
    Write-Host "AVISO: binarios NAO assinados. Ver 'Distribuicao / assinatura' no README." -ForegroundColor Yellow
}
Write-Host "Teste 1 (fluxo normal):  .\dist\ssm\ssm.exe" -ForegroundColor Yellow
Write-Host "Teste 2 (bare-launch):   .\dist\ssm\_internal\node_modules\electron\dist\SSM.exe" -ForegroundColor Yellow
Write-Host ""
Write-Host "Proximo passo: compilar o instalador com o Inno Setup (ssm_setup.iss)." -ForegroundColor Yellow
if ($SignEnabled) {
    $tsa = $TimestampUrl
    if ($SignPfx) {
        $tool = "signtool.exe sign /fd SHA256 /tr $tsa /td SHA256 /f $SignPfx /p $SignPfxPassword `$f"
    } else {
        $tool = "signtool.exe sign /fd SHA256 /tr $tsa /td SHA256 /sm /n `"$SignSubject`" `$f"
    }
    Write-Host "  iscc `"/Sssmsign=$tool`" /DSIGN_ENABLED ssm_setup.iss" -ForegroundColor DarkGray
}
