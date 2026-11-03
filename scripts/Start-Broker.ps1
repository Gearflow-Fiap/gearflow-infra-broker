#Requires -Version 7.0
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    Get-Command docker -ErrorAction Stop | Out-Null
    # Preserve configuracoes existentes; no CI as credenciais chegam pelo ambiente.
    if (-not (Test-Path .env) -and -not ($env:RABBITMQ_USER -and $env:RABBITMQ_PASSWORD)) {
        $password = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24))
        $content = "RABBITMQ_USER=gearflow_local`nRABBITMQ_PASSWORD=$password`nRABBITMQ_AMQP_PORT=5672`nRABBITMQ_MANAGEMENT_PORT=15672`n"
        [IO.File]::WriteAllText((Join-Path (Get-Location) '.env'), $content)
        Write-Host 'Criado .env com senha aleatoria local (nao exibida).'
    }
    $definitions = Get-Content rabbitmq/definitions.json -Raw | ConvertFrom-Json
    docker compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Configuracao Compose invalida.' }
    docker compose up -d --wait --wait-timeout 180 rabbitmq
    if ($LASTEXITCODE -ne 0) { throw 'RabbitMQ nao iniciou saudavel. Consulte docker compose logs rabbitmq.' }
    $configText = docker compose config --format json
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao ler configuracao.' }
    $config = $configText | ConvertFrom-Json
    $settings = $config.services.rabbitmq.environment
    $port = ($config.services.rabbitmq.ports | Where-Object target -eq 15672).published
    $auth = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(
        ('{0}:{1}' -f $settings.RABBITMQ_DEFAULT_USER, $settings.RABBITMQ_DEFAULT_PASS)))
    $headers = @{ Authorization = "Basic $auth" }
    $base = "http://127.0.0.1:$port/api"
    $ready = $false
    for ($i=0; $i -lt 30; $i++) {
        try {
            $null = Invoke-RestMethod "$base/overview" -Headers $headers -TimeoutSec 5
            $ready = $true
            break
        } catch { Start-Sleep -Seconds 2 }
    }
    if (-not $ready) { throw 'API indisponivel ou credenciais incorretas. Alterar .env nao troca usuarios de um volume existente.' }
    $null = Invoke-RestMethod "$base/definitions" -Method Post -Headers $headers -ContentType 'application/json' -Body ($definitions | ConvertTo-Json -Depth 20) -TimeoutSec 30
    foreach ($queue in $definitions.queues) {
        $vhost = [uri]::EscapeDataString($queue.vhost)
        $name = [uri]::EscapeDataString($queue.name)
        $null = Invoke-RestMethod "$base/queues/$vhost/$name" -Headers $headers -TimeoutSec 10
    }
    Write-Host "RabbitMQ saudavel e topologia importada. Painel: http://localhost:$port"
} finally { Pop-Location }
