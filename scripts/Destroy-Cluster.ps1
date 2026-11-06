#Requires -Version 7.0
<#
.SYNOPSIS
Gera uma previa de destruicao; somente -Execute -ConfirmClusterName NOME aplica.
.DESCRIPTION
Usa exclusivamente o state deste repositorio. Nao remove volumes de aplicacoes
automaticamente. Bloqueia se encontrar PVCs, PVs, LoadBalancers ou Ingresses.
#>
param(
    [switch]$Execute,
    [string]$ConfirmClusterName,
    [string]$Profile = 'academy',
    [string]$VariablesFile = 'academy.tfvars.json'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$clusterDirectory = Join-Path $root 'infra/cluster'
$previousProfile = $env:AWS_PROFILE
$env:AWS_PROFILE = $Profile
function Invoke-Checked {
    param([string]$Command, [string[]]$Arguments)
    $result = & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Falha em $Command; nenhuma etapa seguinte sera executada." }
    $result
}
try {
    $variablePath = if ([IO.Path]::IsPathRooted($VariablesFile)) { $VariablesFile } else { Join-Path $clusterDirectory $VariablesFile }
    $settings = Get-Content -LiteralPath $variablePath -Raw | ConvertFrom-Json
    $clusterName = $settings.cluster_name
    $region = $settings.aws_region
    if (-not $clusterName -or -not $region) { throw 'Informe cluster_name e aws_region explicitamente no arquivo de variaveis.' }
    if ($Execute -and $ConfirmClusterName -cne $clusterName) { throw 'Para excluir, use -Execute -ConfirmClusterName com o nome exato do cluster.' }
    $identityText = Invoke-Checked aws @('sts', 'get-caller-identity', '--profile', $Profile, '--output', 'json')
    $identity = $identityText | ConvertFrom-Json
    if (($settings.cluster_role_arn -split ':')[4] -ne $identity.Account) { throw 'Conta AWS diferente da configuracao local.' }
    $stateText = Invoke-Checked terraform @("-chdir=$clusterDirectory", 'show', '-json')
    $state = $stateText | ConvertFrom-Json
    $resources = @($state.values.root_module.resources | Where-Object mode -eq managed)
    if (-not $resources.Count) { Write-Host 'State sem recursos gerenciados. Nada para destruir.'; return }
    if ($state.values.root_module.child_modules) { throw 'State contem modulos nao previstos; revisar alvos antes de destruir.' }
    $expected = @('aws_vpc.this', 'aws_internet_gateway.this', 'aws_subnet.public[0]', 'aws_subnet.public[1]',
        'aws_route_table.public', 'aws_route_table_association.public[0]', 'aws_route_table_association.public[1]',
        'aws_eks_cluster.this', 'aws_launch_template.workers', 'aws_eks_node_group.this', 'aws_eks_addon.vpc_cni', 'aws_eks_addon.ebs[0]')
    foreach ($resource in $resources) {
        if ($resource.address -notin $expected) { throw "Recurso nao previsto no state: $($resource.address). Revisar antes de destruir." }
        $arn = $resource.values.arn
        if ($arn -and ($arn -split ':')[4] -ne $identity.Account) { throw 'State aponta para outra conta AWS.' }
    }
    $cluster = $resources | Where-Object address -eq 'aws_eks_cluster.this'
    if ($cluster -and ($cluster.values.name -cne $clusterName -or ($cluster.values.arn -split ':')[3] -ne $region)) {
        throw 'Nome/regiao do cluster no state diferentes da configuracao local.'
    }
    $vpc = $resources | Where-Object address -eq 'aws_vpc.this'
    if ($vpc -and $vpc.values.tags.Name -cne $clusterName) { throw 'VPC no state nao pertence ao cluster informado.' }
    $localDirectory = Join-Path $root '.local/destroy'
    $null = New-Item -ItemType Directory -Path $localDirectory -Force
    $clustersText = Invoke-Checked aws @('eks', 'list-clusters', '--profile', $Profile, '--region', $region, '--output', 'json')
    if ($clusterName -in ($clustersText | ConvertFrom-Json).clusters) {
        if (-not $cluster) { throw 'Cluster existe na conta, mas nao esta no state deste repositorio.' }
        # Contexto isolado: nunca usa/modifica o contexto Kubernetes atual do operador.
        $kubeconfig = Join-Path $localDirectory 'kubeconfig'
        $null = Invoke-Checked aws @('eks', 'update-kubeconfig', '--name', $clusterName, '--region', $region,
            '--profile', $Profile, '--kubeconfig', $kubeconfig, '--alias', 'gearflow-destroy-check')
        $objectsText = Invoke-Checked kubectl @('--kubeconfig', $kubeconfig, '--context', 'gearflow-destroy-check',
            'get', 'pvc,pv,services,ingresses', '--all-namespaces', '-o', 'json', '--request-timeout=30s')
        $objects = $objectsText | ConvertFrom-Json
        $blockers = @($objects.items | Where-Object {
            $_.kind -in @('PersistentVolumeClaim', 'PersistentVolume', 'Ingress') -or
            ($_.kind -eq 'Service' -and $_.spec.type -eq 'LoadBalancer')
        })
        if ($blockers.Count) {
            $blockers | ForEach-Object { Write-Host "$($_.kind): $($_.metadata.namespace)/$($_.metadata.name)" }
            throw 'Revise/remova esses recursos e seus volumes/load balancers antes de destruir o cluster. Nenhum dado foi removido pelo script.'
        }
    }
    Write-Host "Alvo: $clusterName / $region. Gerando plano de destruicao do state local."
    $planPath = Join-Path $localDirectory 'cluster-destroy.tfplan'
    $null = Invoke-Checked terraform @("-chdir=$clusterDirectory", 'plan', '-destroy', "-var-file=$variablePath", "-out=$planPath", '-input=false', '-no-color')
    $planText = Invoke-Checked terraform @("-chdir=$clusterDirectory", 'show', '-json', $planPath)
    $plan = $planText | ConvertFrom-Json
    $changes = @($plan.resource_changes | Where-Object { $_.mode -eq 'managed' -and ($_.change.actions -join ',') -ne 'no-op' })
    foreach ($change in $changes) {
        if ($change.address -notin $expected -or ($change.change.actions -join ',') -ne 'delete') { throw 'Plano contem operacao/alvo nao previsto.' }
    }
    $changes | ForEach-Object { Write-Host "Excluir: $($_.address)" }
    Write-Host "Total: $($changes.Count) recursos Terraform. Instancias e discos root gerenciados sao excluidos pelo node group."
    if (-not $Execute) {
        Write-Host "PREVIA: nada excluido. Para executar: pwsh -File scripts/Destroy-Cluster.ps1 -Execute -ConfirmClusterName $clusterName"
        return
    }
    # Fluxo direto: exibe progresso durante a exclusao, sem acumular ate o final.
    & terraform "-chdir=$clusterDirectory" apply -input=false -no-color $planPath
    if ($LASTEXITCODE -ne 0) { throw 'Destruicao interrompida. Preserve o state e revise o erro antes de repetir.' }
    $remainingText = Invoke-Checked terraform @("-chdir=$clusterDirectory", 'show', '-json')
    $remaining = $remainingText | ConvertFrom-Json
    if (@($remaining.values.root_module.resources | Where-Object mode -eq managed).Count) { throw 'Ainda ha recursos no state; revise antes de encerrar.' }
    Write-Host 'Recursos do state removidos. Confira na AWS discos retidos anteriormente e recursos externos; eles nao sao removidos por este script.'
} finally {
    $env:AWS_PROFILE = $previousProfile
}
