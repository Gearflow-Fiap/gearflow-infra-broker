#Requires -Version 7.4
param([string]$Profile='academy', [string]$ClusterName='gearflow-fase4', [string]$Region='us-east-1')
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$previousProfile=$env:AWS_PROFILE
$previousKubeconfig=$env:KUBECONFIG
Push-Location $root
try {
    $env:AWS_PROFILE=$Profile
    $null=New-Item -ItemType Directory -Path .local -Force
    $env:KUBECONFIG=Join-Path $root '.local/kubeconfig'
    aws eks update-kubeconfig --name $ClusterName --region $Region --profile $Profile --kubeconfig $env:KUBECONFIG --alias gearflow-fase4
    if($LASTEXITCODE){throw 'Falha ao configurar acesso ao cluster'}
    python scripts/cd/deploy_broker.py --cluster $ClusterName --region $Region --namespace messaging
    if($LASTEXITCODE){throw 'Bootstrap/deploy falhou; credenciais existentes preservadas'}
} finally {
    $env:AWS_PROFILE=$previousProfile
    $env:KUBECONFIG=$previousKubeconfig
    Pop-Location
}
