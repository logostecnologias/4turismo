$ErrorActionPreference = "Stop"

# Ambiente LOCAL do produto de turismo.
#
# Este script nao abre tunel SSH, nao sobe Cloudflare nem fala com a ponte
# WuzAPI. O banco e o MongoDB do docker-compose.yml (container turismo-mongo,
# porta 27019).
#
# Portas proprias (backend 8010, frontend 3010), fora das padrao (8000/3000):
# a limpeza de porta abaixo mata quem estiver escutando, e nas portas padrao
# ela derrubaria outra aplicacao da maquina.

$MongoContainer = "turismo-mongo"
$MongoPort = 27019
$BackendPort = 8010
$FrontendPort = 3010

# Pelo cmd, e nao direto: no PowerShell 5.1 com ErrorActionPreference=Stop, o
# stderr do docker vira excecao (NativeCommandError) e o script morre com um
# erro em vermelho antes de chegar em qualquer aviso nosso.
function Test-Docker {
    cmd /c "docker info >nul 2>&1"
    return ($LASTEXITCODE -eq 0)
}

if (-not (Test-Docker)) {
    $dockerDesktop = @(
        "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe",
        "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1

    if (-not $dockerDesktop) {
        Write-Host "Docker nao esta rodando e o Docker Desktop nao foi encontrado. Abra-o e rode de novo." -ForegroundColor Red
        exit 1
    }

    Write-Host "Docker Desktop fechado. Abrindo e esperando ficar pronto (pode levar 1-2 min)..." -ForegroundColor Cyan
    Start-Process -FilePath $dockerDesktop | Out-Null
    $pronto = $false
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 2
        if (Test-Docker) { $pronto = $true; break }
    }
    if (-not $pronto) {
        Write-Host "Docker Desktop nao ficou pronto a tempo. Espere ele terminar de abrir e rode de novo." -ForegroundColor Red
        exit 1
    }
}

# Processo velho preso nas portas do backend/frontend faria o novo subir em
# outra porta (ou o velho responder no lugar dele). Mata por porta, nao por
# nome, para nao derrubar node/python de outros projetos.
#
# Com `uvicorn --reload`, quem atende a porta e um processo FILHO do reloader,
# e o Windows aponta o reloader como dono do socket. Se o reloader ja morreu, o
# dono aparece como um PID que nao existe, e o filho orfao continua servindo o
# codigo antigo para sempre: foi assim que tres backends ficaram respondendo na
# 8000 ao mesmo tempo (08/10/2026) e uma rota nova dava 404. O filho guarda o
# PID do pai morto em ParentProcessId, e e por ele que o achamos.
foreach ($port in $FrontendPort, $BackendPort) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
        $dono = $_.OwningProcess
        $alvos = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$dono" -ErrorAction SilentlyContinue | ForEach-Object { $_.ProcessId })
        if (Get-Process -Id $dono -ErrorAction SilentlyContinue) { $alvos += $dono }
        foreach ($alvo in $alvos) {
            if ($alvo -ne $PID) {
                $nome = (Get-Process -Id $alvo -ErrorAction SilentlyContinue).ProcessName
                Write-Host "Matando processo preso na porta $port ($nome, PID $alvo)..." -ForegroundColor Yellow
                Stop-Process -Id $alvo -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

# O compose cria na primeira vez e so liga depois; a definicao do container
# mora no docker-compose.yml, nao aqui.
Write-Host "Subindo o MongoDB local ($MongoContainer, porta $MongoPort)..." -ForegroundColor Cyan
cmd /c "docker compose -f `"$PSScriptRoot\docker-compose.yml`" up -d mongo >nul 2>&1"
if ($LASTEXITCODE -ne 0) {
    Write-Host "docker compose falhou. Rode 'docker compose up -d' nesta pasta para ver o erro." -ForegroundColor Red
    exit 1
}

$ok = $false
for ($i = 0; $i -lt 20; $i++) {
    if (Test-NetConnection -ComputerName 127.0.0.1 -Port $MongoPort -WarningAction SilentlyContinue -InformationLevel Quiet) { $ok = $true; break }
    Start-Sleep -Seconds 1
}
if (-not $ok) {
    Write-Host "MongoDB local nao respondeu na porta $MongoPort." -ForegroundColor Red
    exit 1
}

Write-Host "MongoDB local pronto. Subindo backend ($BackendPort) + frontend ($FrontendPort)..." -ForegroundColor Green
Write-Host "Abra http://localhost:$FrontendPort" -ForegroundColor Green

# O npm desta maquina tem script-shell=powershell.exe na config do usuario, e o
# PowerShell 5.1 nao aceita o `&&` dos scripts do package.json. Forca o cmd so
# para esta execucao, sem mexer na config global (outros projetos usam).
$env:npm_config_script_shell = "cmd.exe"
# Na pasta do script, não na de quem chamou: rodado de dentro de frontend/ ou
# backend/, o `npm run dev` procurava o script no package.json errado.
Set-Location $PSScriptRoot
npm run dev
