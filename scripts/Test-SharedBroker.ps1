#Requires -Version 7.0
# Usa apenas container/volume descartaveis. Nao acessa o cluster nem o Compose local.
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$id = [guid]::NewGuid().ToString('N')
$container = "gearflow-security-$id"
$fixture = Join-Path $root ".local/shared-tests/$id"
$tls = Join-Path $root '.local/tls'
$null = New-Item -ItemType Directory -Path $fixture -Force
if (-not (Test-Path $tls)) { & (Join-Path $PSScriptRoot 'New-BrokerTls.ps1') }
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
function Test-Tls([string]$ServerName, [bool]$TrustCa) {
    $client = [Net.Sockets.TcpClient]::new('127.0.0.1', $tlsPort)
    $stream = [Net.Security.SslStream]::new($client.GetStream(), $false)
    $chain = [Security.Cryptography.X509Certificates.X509ChainPolicy]::new()
    $chain.RevocationMode = [Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
    if ($TrustCa) {
        $chain.TrustMode = [Security.Cryptography.X509Certificates.X509ChainTrustMode]::CustomRootTrust
        $null = $chain.CustomTrustStore.Add([Security.Cryptography.X509Certificates.X509Certificate2]::CreateFromPem([IO.File]::ReadAllText((Join-Path $tls 'ca.crt'))))
    }
    $options = [Net.Security.SslClientAuthenticationOptions]::new()
    $options.TargetHost = $ServerName
    $options.CertificateChainPolicy = $chain
    try { $stream.AuthenticateAsClient($options) } finally { $stream.Dispose(); $client.Dispose() }
}
try {
    $compose = Get-Content (Join-Path $root 'compose.yaml') -Raw
    $image = [regex]::Match($compose, '(?m)^\s+image: (\S+)').Groups[1].Value
    $null = Invoke-Docker run -d --name $container --hostname shared-broker-test `
        --publish 127.0.0.1::5671 --publish 127.0.0.1::15672 `
        --env RABBITMQ_DEFAULT_USER --env RABBITMQ_DEFAULT_PASS --env RABBITMQ_DEFAULT_VHOST=gearflow `
        --mount "type=bind,source=$configPath,target=/etc/rabbitmq/rabbitmq.conf,readonly" `
        --mount "type=bind,source=$tls,target=/etc/rabbitmq/tls,readonly" $image
    $started = $true
    Wait-Broker
    $inspect = (Invoke-Docker inspect $container | ConvertFrom-Json)[0]
    $port = [int]$inspect.NetworkSettings.Ports.'15672/tcp'[0].HostPort
    $tlsPort = [int]$inspect.NetworkSettings.Ports.'5671/tcp'[0].HostPort
    $api = "http://127.0.0.1:$port/api"
    $ready = $false
    for ($i=0; $i -lt 30; $i++) {
        try { $null = Invoke-RestMethod "$api/overview" -Headers $headers -TimeoutSec 3; $ready=$true; break } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { throw 'API de teste indisponivel.' }
    $credentialsPath = Join-Path $fixture 'service-credentials.json'
    & (Join-Path $PSScriptRoot 'Set-BrokerServiceUsers.ps1') -ManagementPort $port -AdminCredential $admin -CredentialFile $credentialsPath
    $before = [IO.File]::ReadAllText($credentialsPath)
    & (Join-Path $PSScriptRoot 'Set-BrokerServiceUsers.ps1') -ManagementPort $port -AdminCredential $admin -CredentialFile $credentialsPath
    if ($before -cne [IO.File]::ReadAllText($credentialsPath)) { throw 'Execucao repetida alterou senhas.' }
    $policy = Get-Content (Join-Path $root 'rabbitmq/service-permissions.json') -Raw | ConvertFrom-Json
    $serviceCredentials = Get-Content $credentialsPath -Raw | ConvertFrom-Json -AsHashtable
    foreach ($user in $policy.users) {
        $actual = Invoke-RestMethod "$api/users/$($user.name)" -Headers $headers
        if (@($actual.tags).Count -ne 0) { throw 'Usuario de servico possui tags.' }
        $permissions = @(Invoke-RestMethod "$api/users/$($user.name)/permissions" -Headers $headers)
        if ($permissions.Count -ne 1 -or $permissions[0].vhost -ne 'gearflow') { throw 'Acesso inesperado a outro vhost.' }
        $serviceAuth = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("$($user.name):$($serviceCredentials[$user.name])"))
        $response = Invoke-WebRequest "$api/overview" -Headers @{ Authorization="Basic $serviceAuth" } -SkipHttpErrorCheck -TimeoutSec 5
        if ([int]$response.StatusCode -notin @(401, 403)) { throw 'Usuario de servico conseguiu acessar a API administrativa.' }
        if ('gearflow.events' -notmatch $user.write -or 'amq.default' -match $user.write) { throw 'Permissao de publicacao incorreta.' }
        if ('gearflow.events' -match $user.configure) { throw 'Servico pode administrar topologia.' }
        $service = $user.name.Replace('gearflow_', '')
        if ("gearflow.$service.example.v1" -notmatch $user.read -or "gearflow.$service.example.v1.dlq" -match $user.read) { throw 'Permissao de fila/DLQ incorreta.' }
        foreach ($other in $policy.users | Where-Object name -ne $user.name) {
            if ("gearflow.$($other.name.Replace('gearflow_', '')).example.v1" -match $user.read) { throw 'Servico pode ler fila de outro servico.' }
        }
    }
    Write-Host 'PASS: usuarios sem tags, ACL por servico, API administrativa negada e repeticao sem rotacao de senhas.'
    Test-Tls 'rabbitmq.messaging.svc.cluster.local' $true
    foreach ($negative in @(@{Name='nome-incorreto.invalid'; Trust=$true}, @{Name='rabbitmq.messaging.svc.cluster.local'; Trust=$false})) {
        $denied = $false
        try { Test-Tls $negative.Name $negative.Trust } catch [Security.Authentication.AuthenticationException] { $denied=$true }
        if (-not $denied) { throw 'TLS aceitou hostname ou CA incorretos.' }
    }
    Write-Host 'PASS: TLS com CA/hostname corretos; rejeicao de CA e hostname incorretos.'
    [IO.File]::WriteAllText($configPath, $originalConfig)
    $null = Invoke-Docker restart $container
    Wait-Broker
    $inspect = (Invoke-Docker inspect $container | ConvertFrom-Json)[0]
    $tlsPort = [int]$inspect.NetworkSettings.Ports.'5671/tcp'[0].HostPort
    $listeners = Invoke-Docker exec --user rabbitmq $container rabbitmq-diagnostics -q listeners
    if (($listeners -join "`n") -notmatch '127\.0\.0\.1, port: 15672') { throw 'Painel nao esta restrito ao loopback.' }
    if (($listeners -join "`n") -match 'port: 5672,') { throw 'AMQP sem TLS esta habilitado.' }
    Test-Tls 'rabbitmq.messaging.svc.cluster.local' $true
    Write-Host 'PASS: configuracao exata do Kubernetes inicia com AMQPS e painel somente no loopback.'
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
