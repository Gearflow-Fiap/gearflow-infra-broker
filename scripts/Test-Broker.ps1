param([switch]$RestartBroker)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    $configText = docker compose config --format json
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao ler configuracao Compose.' }
    $config = $configText | ConvertFrom-Json
    $environment = $config.services.rabbitmq.environment
    $port = ($config.services.rabbitmq.ports | Where-Object target -eq 15672).published
    $auth = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(
        ("{0}:{1}" -f $environment.RABBITMQ_DEFAULT_USER, $environment.RABBITMQ_DEFAULT_PASS)))
    $headers = @{ Authorization = "Basic $auth" }
    $base = "http://127.0.0.1:$port/api"
    function Request($method, $path, $body = $null) {
        $args = @{ Method = $method; Uri = "$base/$path"; Headers = $headers; TimeoutSec = 15 }
        if ($null -ne $body) { $args.Body = ConvertTo-Json -Depth 12 -InputObject $body; $args.ContentType = 'application/json' }
        Invoke-RestMethod @args
    }
    # Validacao isolada: replica as definicoes em um vhost exclusivo.
    $vhost = 'gearflow-smoke-' + [guid]::NewGuid().ToString('N')
    $created = $false
    try {
        $null = Request PUT "vhosts/$vhost" @{}
        $created = $true
        $user = [uri]::EscapeDataString($environment.RABBITMQ_DEFAULT_USER)
        $null = Request PUT "permissions/$vhost/$user" @{ configure='.*'; write='.*'; read='.*' }
        $defs = Get-Content rabbitmq/definitions.json -Raw | ConvertFrom-Json
        $defs.vhosts[0].name = $vhost
        foreach ($kind in @('exchanges','queues','bindings','policies')) {
            foreach ($item in $defs.$kind) { $item.vhost = $vhost }
        }
        $null = Request POST 'definitions' $defs
        $queue = 'gearflow.budget.order-created.v1'
        $id = [guid]::NewGuid().ToString()
        $payload = @{ messageId=$id; eventType='OrderCreated'; schemaVersion=1; serviceOrderId=[guid]::NewGuid().ToString(); test=$true } | ConvertTo-Json -Compress
        $published = Request POST "exchanges/$vhost/gearflow.events/publish" @{
            properties=@{ delivery_mode=2; message_id=$id }; routing_key='os.order-created.v1'; payload=$payload; payload_encoding='string'
        }
        if (-not $published.routed) { throw 'Mensagem sem rota.' }
        Write-Host 'PASS: topologia importada e mensagem roteada.'
        if ($RestartBroker) {
            docker compose restart rabbitmq
            if ($LASTEXITCODE -ne 0) { throw 'Falha no restart.' }
            $ready = $false
            for ($i=0; $i -lt 45; $i++) {
                try { $null = Request GET 'overview'; $ready=$true; break } catch { Start-Sleep -Seconds 2 }
            }
            if (-not $ready) { throw 'Broker nao voltou apos restart.' }
        }
        $messages = @(Request POST "queues/$vhost/$queue/get" @{ count=1; ackmode='ack_requeue_true'; encoding='auto'; truncate=50000 })
        if ($messages.Count -ne 1 -or $messages[0].properties.message_id -ne $id) { throw 'Mensagem nao encontrada ou divergente.' }
        Write-Host 'PASS: leitura e conteudo da mensagem persistente.'
        if ($RestartBroker) { Write-Host 'PASS: mensagem preservada apos reinicio do broker.' }
        # Rejeitar sem requeue deve acionar a policy de dead lettering.
        $null = Request POST "queues/$vhost/$queue/get" @{ count=1; ackmode='reject_requeue_false'; encoding='auto' }
        $dead = @()
        for ($i=0; $i -lt 15; $i++) {
            $dead = @(Request POST "queues/$vhost/$queue.dlq/get" @{ count=1; ackmode='ack_requeue_false'; encoding='auto' })
            if ($dead.Count -gt 0) { break }
            Start-Sleep -Seconds 1
        }
        if ($dead.Count -ne 1 -or $dead[0].properties.message_id -ne $id) { throw 'Mensagem nao chegou a DLQ.' }
        Write-Host 'PASS: rejeicao encaminhada para DLQ e consumida.'
    } finally {
        if ($created) { $null = Request DELETE "vhosts/$vhost" }
    }
} finally { Pop-Location }
