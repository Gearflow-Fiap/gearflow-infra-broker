#Requires -Version 7.0
param([switch]$RestartBroker)
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
Set-Location $root
$runId=[guid]::NewGuid().ToString('N').Substring(0,12)
[IO.File]::WriteAllText((Join-Path $root '.local/stage3-run-id'),$runId)
$admin=Get-Content .local/cluster-admin.json -Raw|ConvertFrom-Json
$auth=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("$($admin.username):$($admin.password)"))
$headers=@{Authorization="Basic $auth"}
$allowed='gearflow-broker-validation'
$blocked='gearflow-broker-blocked'
$forward=$null
function K { $result=kubectl --kubeconfig .local/kubeconfig @args; if($LASTEXITCODE){throw 'Comando Kubernetes falhou'}; $result }
function Send-Object($object) { $object|ConvertTo-Json -Depth 30|kubectl --kubeconfig .local/kubeconfig apply -f -; if($LASTEXITCODE){throw 'Falha ao aplicar recurso de teste'} }
function Api($method,$path,$body=$null) {
    $arguments=@{Uri="http://127.0.0.1:$port/api/$path";Method=$method;Headers=$headers;TimeoutSec=15}
    if($null -ne $body){$arguments.Body=ConvertTo-Json -InputObject $body -Depth 20;$arguments.ContentType='application/json'}
    Invoke-RestMethod @arguments
}
function Open-Tunnel {
    if($script:forward -and -not $script:forward.HasExited){$script:forward.Kill();$script:forward.WaitForExit()}
    $listener=[Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,0);$listener.Start();$script:port=$listener.LocalEndpoint.Port;$listener.Stop()
    $script:forward=Start-Process -FilePath (Get-Command kubectl).Source -ArgumentList @('--kubeconfig','.local/kubeconfig','-n','messaging','port-forward','pod/rabbitmq-0',"${port}:15672",'--address','127.0.0.1') -WindowStyle Hidden -PassThru -RedirectStandardOutput .local/stage3-forward.log -RedirectStandardError .local/stage3-forward-error.log
    for($i=0;$i -lt 30;$i++){try{$null=Api GET overview;return}catch{Start-Sleep -Milliseconds 500}}
    throw 'Tunel administrativo indisponivel'
}
function Run-Job($name,$phase,$namespace=$allowed,[bool]$label=$true) {
    $labels=@{app='broker-validation'};if($label){$labels['gearflow.io/rabbitmq-client']='true'}
    $volumes=@(@{name='code';configMap=@{name='broker-validation-code'}})
    $mounts=@(@{name='code';mountPath='/tests';readOnly=$true})
    if($phase -ne 'network-denied'){
        $volumes+=@{name='credentials';secret=@{secretName='broker-validation-users'}}
        $volumes+=@{name='ca';configMap=@{name='broker-validation-ca'}}
        $mounts+=@{name='credentials';mountPath='/credentials';readOnly=$true}
        $mounts+=@{name='ca';mountPath='/ca';readOnly=$true}
    }
    $container=@{name='test';image='python:3.12-alpine';command=@('sh','-ec','if [ "$PHASE" != network-denied ]; then pip install --disable-pip-version-check --quiet --target /tmp/deps pika==1.3.2; fi; PYTHONPATH=/tmp/deps python /tests/amqp-smoke.py');env=@(@{name='PHASE';value=$phase},@{name='RUN_ID';value=$runId});volumeMounts=$mounts;resources=@{requests=@{cpu='50m';memory='64Mi'};limits=@{cpu='250m';memory='192Mi'}}}
    Send-Object @{apiVersion='batch/v1';kind='Job';metadata=@{name=$name;namespace=$namespace};spec=@{backoffLimit=0;activeDeadlineSeconds=240;template=@{metadata=@{labels=$labels};spec=@{restartPolicy='Never';automountServiceAccountToken=$false;containers=@($container);volumes=$volumes}}}}
    for($i=0;$i -lt 60;$i++){
        $job=(K -n $namespace get job $name -o json)|ConvertFrom-Json
        if($job.status.succeeded){K -n $namespace logs "job/$name";return}
        if($job.status.failed){K -n $namespace logs "job/$name";throw "Job falhou: $name"}
        Start-Sleep -Seconds 3
    }
    K -n $namespace get pods
    throw "Timeout do Job: $name"
}
try {
    Open-Tunnel
    $credential=[pscredential]::new($admin.username,(ConvertTo-SecureString $admin.password -AsPlainText -Force))
    & ./scripts/Set-BrokerServiceUsers.ps1 -ManagementPort $port -AdminCredential $credential
    foreach($namespace in @($allowed,$blocked)){
        $existing=K get namespace $namespace --ignore-not-found -o name
        if($existing){throw "Namespace de teste ja existe; revisar antes de reutilizar: $namespace"}
        $labels=@{'gearflow.io/purpose'='broker-validation'};if($namespace -eq $allowed){$labels['gearflow.io/messaging-access']='true'}
        Send-Object @{apiVersion='v1';kind='Namespace';metadata=@{name=$namespace;labels=$labels}}
        K -n $namespace create configmap broker-validation-code --from-file=amqp-smoke.py=k8s/tests/amqp-smoke.py
    }
    $users=Get-Content .local/service-credentials.json -Raw
    Send-Object @{apiVersion='v1';kind='Secret';metadata=@{name='broker-validation-users';namespace=$allowed};type='Opaque';stringData=@{'users.json'=$users}}
    K -n $allowed create configmap broker-validation-ca --from-file=ca.crt=.local/tls/ca.crt
    foreach($service in @('os','budget','operation','inventory')){
        $name="gearflow.$service.smoke.$runId"
        $null=Api PUT "queues/gearflow/$name" @{durable=$true;auto_delete=$false;arguments=@{'x-queue-type'='classic';'x-dead-letter-exchange'='gearflow.dead-letter';'x-dead-letter-routing-key'="smoke.$runId.$service.failed"}}
        $null=Api PUT "queues/gearflow/$name.dlq" @{durable=$true;auto_delete=$false;arguments=@{'x-queue-type'='classic'}}
        $null=Api POST "bindings/gearflow/e/gearflow.events/q/$name" @{routing_key="smoke.$runId.$service";arguments=@{}}
        $null=Api POST "bindings/gearflow/e/gearflow.dead-letter/q/$name.dlq" @{routing_key="smoke.$runId.$service.failed";arguments=@{}}
    }
    Run-Job 'verify-amqp' 'verify'
    $dead=@(Api POST "queues/gearflow/gearflow.budget.smoke.$runId.dlq/get" @{count=1;ackmode='ack_requeue_false';encoding='auto'} | ForEach-Object{$_})
    if($dead.Count -ne 1 -or $dead[0].payload -ne 'dead-letter-smoke'){throw 'DLQ nao recebeu a mensagem rejeitada'}
    Write-Host 'PASS: DLQ recebeu mensagem rejeitada por AMQP.'
    Run-Job 'denied-pod-label' 'network-denied' $allowed $false
    Run-Job 'denied-namespace-label' 'network-denied' $blocked $true
    if ($RestartBroker) {
    Run-Job 'publish-persistence' 'publish-persistence'
    $before=(K -n messaging get pod rabbitmq-0 -o json)|ConvertFrom-Json
    $pvcBefore=(K -n messaging get pvc data-rabbitmq-0 -o json)|ConvertFrom-Json
    K -n messaging delete pod rabbitmq-0 --wait=true
    K -n messaging rollout status statefulset/rabbitmq --timeout=240s
    $after=(K -n messaging get pod rabbitmq-0 -o json)|ConvertFrom-Json
    $pvcAfter=(K -n messaging get pvc data-rabbitmq-0 -o json)|ConvertFrom-Json
    if($before.metadata.uid -eq $after.metadata.uid -or $pvcBefore.spec.volumeName -ne $pvcAfter.spec.volumeName){throw 'Pod/PVC nao corresponde ao teste de persistencia esperado'}
    Run-Job 'consume-persistence' 'consume-persistence'
    } else { Write-Host 'Persistencia nao testada nesta execucao; use -RestartBroker em janela sem consumidores.' }
    Open-Tunnel
    foreach($service in @('os','budget','operation','inventory')){
        $name="gearflow.$service.smoke.$runId"
        $null=Api DELETE "queues/gearflow/$name"
        $null=Api DELETE "queues/gearflow/$name.dlq"
    }
    K delete namespace $allowed $blocked --wait=true --timeout=120s
    Write-Host 'PASS: testes concluidos; filas, namespaces, Secrets e Jobs temporarios removidos.'
} finally {
    if($forward -and -not $forward.HasExited){$forward.Kill();$forward.WaitForExit()}
    $headers.Clear()
}
