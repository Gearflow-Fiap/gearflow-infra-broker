#Requires -Version 7.0
param(
    [ValidateRange(1024, 65535)][int]$ManagementPort = 15673,
    [pscredential]$AdminCredential,
    [string]$CredentialFile = (Join-Path (Split-Path $PSScriptRoot -Parent) '.local/service-credentials.json')
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$policy = Get-Content (Join-Path $root 'rabbitmq/service-permissions.json') -Raw | ConvertFrom-Json
if (-not $AdminCredential) { $AdminCredential = Get-Credential -Message 'Administrador do RabbitMQ (port-forward ativo)' }
if (-not $AdminCredential) { throw 'Credencial administrativa obrigatoria.' }
$base = "http://127.0.0.1:$ManagementPort/api"
$auth = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(
    ('{0}:{1}' -f $AdminCredential.UserName, $AdminCredential.GetNetworkCredential().Password)))
$headers = @{ Authorization = "Basic $auth" }
function Request($method, $path, $body = $null) {
    $arguments = @{ Method=$method; Uri="$base/$path"; Headers=$headers; TimeoutSec=15 }
    if ($null -ne $body) {
        $arguments.Body = ConvertTo-Json -InputObject $body -Depth 8
        $arguments.ContentType = 'application/json'
    }
    Invoke-RestMethod @arguments
}
try {
    # Verifica autenticacao e vhost antes de gerar qualquer segredo local.
    $vhost = [uri]::EscapeDataString($policy.vhost)
    $null = Request GET "vhosts/$vhost"
    $path = [IO.Path]::GetFullPath($CredentialFile)
    $saved = if (Test-Path $path) { Get-Content $path -Raw | ConvertFrom-Json -AsHashtable } else { @{} }
    $newUsers = @()
    # Preflight: nunca adotar/rotacionar silenciosamente um usuario remoto existente.
    foreach ($user in $policy.users) {
        $name = [uri]::EscapeDataString($user.name)
        $remote = $null
        try { $remote = Request GET "users/$name" } catch {
            if ([int]$_.Exception.Response.StatusCode -ne 404) { throw }
        }
        if ($remote) {
            if (-not $saved.ContainsKey($user.name)) { throw "Usuario $($user.name) ja existe sem credencial local. Revise a origem; nenhuma senha sera substituida." }
            if (@($remote.tags).Count -gt 0 -and ($remote.tags -join '') -ne '') { throw "Usuario $($user.name) possui tags administrativas. Revisao manual necessaria." }
            $permissions = @(Request GET "users/$name/permissions" | ForEach-Object { $_ })
            if (@($permissions | Where-Object { $_.vhost -cne $policy.vhost }).Count -gt 0) { throw "Usuario $($user.name) possui acesso a outro vhost. Revisao manual necessaria." }
        } else {
            $newUsers += $user.name
            if (-not $saved.ContainsKey($user.name)) {
                $saved[$user.name] = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
            }
        }
    }
    $null = New-Item -ItemType Directory -Path (Split-Path $path -Parent) -Force
    # Grava antes das chamadas: uma falha parcial pode ser retomada sem perder senhas.
    [IO.File]::WriteAllText($path, (ConvertTo-Json -InputObject $saved))
    foreach ($user in $policy.users) {
        $name = [uri]::EscapeDataString($user.name)
        if ($user.name -in $newUsers) {
            $null = Request PUT "users/$name" @{ password=$saved[$user.name]; tags=@() }
        }
        $null = Request PUT "permissions/$vhost/$name" @{
            configure=$user.configure; write=$user.write; read=$user.read
        }
        $actual = Request GET "permissions/$vhost/$name"
        foreach ($field in @('configure', 'write', 'read')) {
            if ($actual.$field -cne $user.$field) { throw "Permissao $field divergente para $($user.name)." }
        }
        Write-Host "Permissoes verificadas: $($user.name)."
    }
    Write-Host 'Credenciais preservadas no arquivo local. Entregue a cada servico somente sua senha; nunca versione esse arquivo.'
} finally {
    $headers.Clear()
    $auth = $null
    $saved = $null
}
