#Requires -Version 7.0
# Usa apenas container/volume descartaveis. Nao acessa o cluster nem o Compose local.
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
& dotnet build (Join-Path $root 'tests/TransportProbe') --nologo --verbosity quiet
if ($LASTEXITCODE) { throw 'Falha ao preparar teste MassTransit (.NET 10 necessario).' }
$id = [guid]::NewGuid().ToString('N')
$container = "gearflow-security-$id"
$fixture = Join-Path $root ".local/shared-tests/$id"
$null = New-Item -ItemType Directory -Path $fixture -Force
$originalConfig = Get-Content (Join-Path $root 'k8s/rabbitmq.conf') -Raw
$configPath = Join-Path $fixture 'rabbitmq.conf'
# API acessivel somente por porta loopback efemera para testar o provisionador.
# Ao final reinicia com a configuracao exata do Kubernetes (API no loopback do Pod).
[IO.File]::WriteAllText($configPath, $originalConfig.Replace('management.tcp.ip = 127.0.0.1', 'management.tcp.ip = 0.0.0.0'))
$oldUser = $env:RABBITMQ_DEFAULT_USER
$oldPassword = $env:RABBITMQ_DEFAULT_PASS
$env:RABBITMQ_DEFAULT_USER = 'gearflow_security_test'
$env:RABBITMQ_DEFAULT_PASS = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24))
$admin = [pscredential]::new($env:RABBITMQ_DEFAULT_USER, (ConvertTo-SecureString $env:RABBITMQ_DEFAULT_PASS -AsPlainText -Force))
$auth = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("$($env:RABBITMQ_DEFAULT_USER):$($env:RABBITMQ_DEFAULT_PASS)"))
$headers = @{ Authorization="Basic $auth" }
$started = $false
function Invoke-Docker {
    $result = & docker @args
    if ($LASTEXITCODE -ne 0) { throw 'Comando Docker falhou no ambiente descartavel.' }
    $result
}
function Wait-Broker {
    for ($i=0; $i -lt 45; $i++) {
        $running = & docker inspect --format '{{.State.Running}}' $container
        if ($running -ne 'true') { throw 'Container de teste encerrou antes de ficar pronto.' }
        & docker exec --user rabbitmq $container rabbitmq-diagnostics -q check_running *> $null
        if ($LASTEXITCODE -eq 0) { return }
        Start-Sleep -Seconds 2
    }
    throw 'Broker de teste nao iniciou.'
}
function Test-Amqp {
    $client = [Net.Sockets.TcpClient]::new('127.0.0.1', $amqpPort)
    $client.ReceiveTimeout = 5000
    $client.SendTimeout = 5000
    $stream = $client.GetStream()
    try {
        # AMQP 0-9-1 sem TLS: o broker responde ao cabecalho com Connection.Start.
        $header = [byte[]]@(65, 77, 81, 80, 0, 0, 9, 1)
        $stream.Write($header, 0, $header.Length)
        $response = [byte[]]::new(11)
        $received = 0
        while ($received -lt $response.Length) {
            $count = $stream.Read($response, $received, $response.Length - $received)
            if ($count -eq 0) { throw 'Broker encerrou a conexao antes de responder AMQP.' }
            $received += $count
        }
        if ($response[0] -ne 1 -or $response[1] -ne 0 -or $response[2] -ne 0 -or
            $response[7] -ne 0 -or $response[8] -ne 10 -or $response[9] -ne 0 -or $response[10] -ne 10) {
            throw 'Resposta diferente de AMQP Connection.Start.'
        }
    } finally { $stream.Dispose(); $client.Dispose() }
}
try {
    $compose = Get-Content (Join-Path $root 'compose.yaml') -Raw
    $image = [regex]::Match($compose, '(?m)^\s+image: (\S+)').Groups[1].Value
    $null = Invoke-Docker run -d --name $container --hostname shared-broker-test `
        --publish 127.0.0.1::5672 --publish 127.0.0.1::15672 `
        --env RABBITMQ_DEFAULT_USER --env RABBITMQ_DEFAULT_PASS --env RABBITMQ_DEFAULT_VHOST=gearflow `
        --mount "type=bind,source=$configPath,target=/etc/rabbitmq/rabbitmq.conf,readonly" $image
    $started = $true
    Wait-Broker
    $inspect = (Invoke-Docker inspect $container | ConvertFrom-Json)[0]
    $port = [int]$inspect.NetworkSettings.Ports.'15672/tcp'[0].HostPort
    $amqpPort = [int]$inspect.NetworkSettings.Ports.'5672/tcp'[0].HostPort
    $api = "http://127.0.0.1:$port/api"
    $ready = $false
    for ($i=0; $i -lt 30; $i++) {
        try { $null = Invoke-RestMethod "$api/overview" -Headers $headers -TimeoutSec 3; $ready=$true; break } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { throw 'API de teste indisponivel.' }
    $definitions = Get-Content (Join-Path $root 'rabbitmq/definitions.json') -Raw
    $null = Invoke-RestMethod "$api/definitions" -Method Post -Headers $headers -ContentType 'application/json' -Body $definitions -TimeoutSec 30
    $credentialsPath = Join-Path $fixture 'service-credentials.json'
    & (Join-Path $PSScriptRoot 'Set-BrokerServiceUsers.ps1') -ManagementPort $port -AdminCredential $admin -CredentialFile $credentialsPath
    $before = [IO.File]::ReadAllText($credentialsPath)
    & (Join-Path $PSScriptRoot 'Set-BrokerServiceUsers.ps1') -ManagementPort $port -AdminCredential $admin -CredentialFile $credentialsPath
    if ($before -cne [IO.File]::ReadAllText($credentialsPath)) { throw 'Execucao repetida alterou senhas.' }
    $policy = Get-Content (Join-Path $root 'rabbitmq/service-permissions.json') -Raw | ConvertFrom-Json
    $serviceCredentials = Get-Content $credentialsPath -Raw | ConvertFrom-Json -AsHashtable
    foreach ($user in $policy.users) {
        $actual = Invoke-RestMethod "$api/users/$($user.name)" -Headers $headers
        @{user=$actual;password=$serviceCredentials[$user.name]} | ConvertTo-Json -Depth 8 -Compress |
            python -c "import json,sys; sys.path.insert(0,sys.argv[1]); from bootstrap_broker import password_matches; data=json.load(sys.stdin); assert password_matches(data['user'],data['password']), 'Hash RabbitMQ divergente'" (Join-Path $root 'scripts/cd')
        if ($LASTEXITCODE) { throw 'Verificacao do formato de hash RabbitMQ falhou.' }
        if (@($actual.tags).Count -ne 0) { throw 'Usuario de servico possui tags.' }
        $permissions = @(Invoke-RestMethod "$api/users/$($user.name)/permissions" -Headers $headers)
        if ($permissions.Count -ne 1 -or $permissions[0].vhost -ne 'gearflow') { throw 'Acesso inesperado a outro vhost.' }
        $serviceAuth = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("$($user.name):$($serviceCredentials[$user.name])"))
        $response = Invoke-WebRequest "$api/overview" -Headers @{ Authorization="Basic $serviceAuth" } -SkipHttpErrorCheck -TimeoutSec 5
        if ([int]$response.StatusCode -notin @(401, 403)) { throw 'Usuario de servico conseguiu acessar a API administrativa.' }
        foreach ($field in @('configure', 'write', 'read')) {
            if ($permissions[0].$field -cne $user.$field) { throw "ACL cadastrada diverge de service-permissions.json: $field" }
        }
    }
    Write-Host 'PASS: usuarios sem tags, ACL por servico, API administrativa negada e repeticao sem rotacao de senhas.'
    @{root=$root;port=$amqpPort;users=$serviceCredentials} | ConvertTo-Json -Depth 8 -Compress |
        dotnet run --project (Join-Path $root 'tests/TransportProbe') --no-build --no-restore
    if ($LASTEXITCODE) { throw 'Teste de contratos/ACL com cliente AMQP e MassTransit falhou.' }
    Test-Amqp
    Write-Host 'PASS: AMQP 0-9-1 responde na porta 5672 sem certificados.'
    [IO.File]::WriteAllText($configPath, $originalConfig)
    $null = Invoke-Docker restart $container
    Wait-Broker
    $inspect = (Invoke-Docker inspect $container | ConvertFrom-Json)[0]
    $amqpPort = [int]$inspect.NetworkSettings.Ports.'5672/tcp'[0].HostPort
    $listeners = Invoke-Docker exec --user rabbitmq $container rabbitmq-diagnostics -q listeners
    if (($listeners -join "`n") -notmatch '127\.0\.0\.1, port: 15672') { throw 'Painel nao esta restrito ao loopback.' }
    if (($listeners -join "`n") -notmatch 'port: 5672,') { throw 'Listener AMQP 5672 ausente.' }
    if (($listeners -join "`n") -match 'port: 5671,') { throw 'Listener TLS ainda esta habilitado.' }
    Test-Amqp
    Write-Host 'PASS: configuracao exata do Kubernetes inicia com AMQP 5672 e painel somente no loopback.'
} catch {
    if ($started) { & docker logs --tail 30 $container }
    throw
} finally {
    if ($started) { $null = Invoke-Docker rm -f -v $container }
    $env:RABBITMQ_DEFAULT_USER = $oldUser
    $env:RABBITMQ_DEFAULT_PASS = $oldPassword
    $headers.Clear()
    # Apenas o diretorio exclusivo deste teste, dentro de .local/shared-tests.
    $expectedParent = [IO.Path]::GetFullPath((Join-Path $root '.local/shared-tests'))
    $resolvedFixture = [IO.Path]::GetFullPath($fixture)
    if ((Split-Path $resolvedFixture -Parent) -ne $expectedParent -or (Split-Path $resolvedFixture -Leaf) -ne $id) { throw 'Diretorio de limpeza inesperado.' }
    Remove-Item -LiteralPath $resolvedFixture -Recurse -Force
}
