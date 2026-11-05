#Requires -Version 7.0
param([ValidateRange(1, 365)][int]$Days = 90)
$ErrorActionPreference = 'Stop'
$output = Join-Path (Split-Path $PSScriptRoot -Parent) '.local/tls'
if (Test-Path $output) { throw 'Diretorio .local/tls ja existe. Renovacao exige revisar certificados e clientes; nenhum arquivo foi sobrescrito.' }
$null = New-Item -ItemType Directory -Path $output -Force
$caKey = [Security.Cryptography.RSA]::Create(3072)
$serverKey = [Security.Cryptography.RSA]::Create(3072)
$ca = $null
$server = $null
try {
    $hash = [Security.Cryptography.HashAlgorithmName]::SHA256
    $padding = [Security.Cryptography.RSASignaturePadding]::Pkcs1
    $caRequest = [Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=GearFlow Academy CA', $caKey, $hash, $padding)
    $caRequest.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new($true, $false, 0, $true))
    $caRequest.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new(
        [Security.Cryptography.X509Certificates.X509KeyUsageFlags]::KeyCertSign, $true))
    $start = [DateTimeOffset]::UtcNow.AddMinutes(-5)
    $ca = $caRequest.CreateSelfSigned($start, [DateTimeOffset]::UtcNow.AddDays($Days + 1))
    $request = [Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=rabbitmq.messaging.svc.cluster.local', $serverKey, $hash, $padding)
    $san = [Security.Cryptography.X509Certificates.SubjectAlternativeNameBuilder]::new()
    foreach ($name in @('rabbitmq', 'rabbitmq.messaging', 'rabbitmq.messaging.svc', 'rabbitmq.messaging.svc.cluster.local', 'localhost')) {
        $san.AddDnsName($name)
    }
    $request.CertificateExtensions.Add($san.Build())
    $request.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new($false, $false, 0, $true))
    $request.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new(
        ([Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature -bor [Security.Cryptography.X509Certificates.X509KeyUsageFlags]::KeyEncipherment), $true))
    $eku = [Security.Cryptography.OidCollection]::new()
    $null = $eku.Add([Security.Cryptography.Oid]::new('1.3.6.1.5.5.7.3.1'))
    $request.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]::new($eku, $false))
    $serial = [Security.Cryptography.RandomNumberGenerator]::GetBytes(16)
    $serial[0] = $serial[0] -band 0x7f
    $server = $request.Create($ca, $start, [DateTimeOffset]::UtcNow.AddDays($Days), $serial)
    [IO.File]::WriteAllText((Join-Path $output 'ca.crt'), $ca.ExportCertificatePem())
    [IO.File]::WriteAllText((Join-Path $output 'tls.crt'), $server.ExportCertificatePem())
    [IO.File]::WriteAllText((Join-Path $output 'tls.key'), $serverKey.ExportPkcs8PrivateKeyPem())
    Write-Host "Certificado de laboratorio criado em .local/tls, valido por $Days dias. Distribua somente ca.crt aos clientes."
} finally {
    if ($server) { $server.Dispose() }
    if ($ca) { $ca.Dispose() }
    $serverKey.Dispose()
    $caKey.Dispose()
}
