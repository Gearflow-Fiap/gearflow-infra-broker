# RabbitMQ compartilhado no EKS — laboratório

RabbitMQ implantado e validado no EKS convencional, com nodes EC2 Linux e EBS CSI (`ebs.csi.aws.com`). Veja [evidências da etapa 3](../docs/broker-validation.md). Não assumir compatibilidade com Fargate ou EKS Auto Mode.

## Configuração

- RabbitMQ `4.2.9-management`, fixado pelo digest OCI em Compose e StatefulSet.
- Uma instância, sem HA. Aumentar réplicas exige implementar clustering.
- EBS gp3 criptografado, 10 GiB, RWO, binding na zona do consumidor, expansão habilitada e `Retain`. Excluir PVC/cluster não garante remover o disco nem encerrar seu custo.
- Requests 500m CPU/1Gi; limites 2 CPU/2Gi. Alarmes de disco e memória em 1GB podem bloquear produtores. Capacidade depende de testes com os serviços reais.
- Serviço interno `rabbitmq.messaging.svc.cluster.local:5671`, vhost `gearflow`, **AMQPS obrigatório**. Não existe listener AMQP 5672 no Kubernetes.
- Painel/API em `127.0.0.1:15672` dentro do Pod, sem porta no Service e sem LoadBalancer/Ingress. Acesso administrativo por `port-forward` autenticado pelo Kubernetes.
- NetworkPolicy permite AMQPS somente de Pods e namespaces explicitamente autorizados. Egress do broker permite apenas DNS no CoreDNS; não libera IMDS. Não altera acesso do EBS CSI às credenciais dos nodes.

O Compose local continua usando 5672 e painel 15672 limitados ao loopback da máquina. A configuração compartilhada usa TLS de servidor e usuário/senha, sem exigir certificado de cliente (mTLS). Clientes devem confiar na CA e validar o hostname; não desabilitar essas verificações.

## Pré-requisitos antes do deploy

1. Aplicar um plano Terraform atualizado. O cluster utilizado tem EKS 1.35, dois workers e addon VPC CNI com `enableNetworkPolicy=true`.
2. Confirmar nodes `Ready`, driver EBS ativo e teste de escrita/leitura em PVC.
3. Confirmar VPC CNI compatível com NetworkPolicy e agente `aws-network-policy-agent` funcionando. O Terraform configura o addon antes dos workers. O modo padrão pode permitir tráfego brevemente durante a inicialização do Pod; TLS e autenticação continuam obrigatórios. Não habilitar modo strict globalmente sem preparar políticas para os demais workloads.
4. Confirmar contexto e permissões Kubernetes. Namespace/labels não substituem RBAC; somente operadores autorizados devem poder conceder labels de acesso, alterar políticas, executar comandos ou fazer port-forward no broker.

```powershell
kubectl config current-context
kubectl get nodes
kubectl get csidriver ebs.csi.aws.com
kubectl -n kube-system get daemonset aws-node
kubectl kustomize k8s
kubectl apply -f k8s/namespace.yaml
```

## Segredos e TLS

PowerShell 7, na raiz do repositório. Criar o usuário administrativo inicial fora do Git:

```powershell
$brokerUser = Read-Host 'Usuario administrativo (nao usar nomes gearflow_os/budget/operation/inventory)'
$brokerPassword = Read-Host 'Senha do broker' -MaskInput
@{
  apiVersion='v1'; kind='Secret'
  metadata=@{name='rabbitmq-credentials'; namespace='messaging'}
  type='Opaque'
  stringData=@{username=$brokerUser; password=$brokerPassword}
} | ConvertTo-Json -Depth 5 | kubectl create -f -
Remove-Variable brokerPassword
```

Não sobrescrever uma instalação existente: mudar o Secret não troca os usuários persistidos. Secret Kubernetes não garante criptografia em repouso por si só.

Criar certificado de laboratório e Secret TLS antes de subir o StatefulSet:

```powershell
pwsh -File scripts/New-BrokerTls.ps1
kubectl -n messaging create secret generic rabbitmq-tls --from-file=ca.crt=.local/tls/ca.crt --from-file=tls.crt=.local/tls/tls.crt --from-file=tls.key=.local/tls/tls.key
```

O script usa .NET, não requer OpenSSL e gera CA privada de laboratório e certificado com SANs para o serviço Kubernetes, nomes DNS curtos e `localhost` para testes por túnel. A chave privada da CA é descartada; a chave do servidor fica apenas em `.local/tls/tls.key`. Validade padrão de 90 dias. Não sobrescreve diretório existente.

`.local/` e chaves privadas são ignorados pelo Git, mas os arquivos locais não são um cofre. Restrinja acesso à conta/máquina e guarde as credenciais com segurança. Distribua **somente `ca.crt`** aos serviços, por ConfigMap ou mecanismo equivalente. Cada serviço recebe somente sua própria senha por Secret no seu namespace. Não montar a chave TLS do broker nos clientes.

Renovação exige planejar a nova CA com os clientes, emitir novo certificado, atualizar o Secret, reiniciar controladamente o Pod e validar as conexões antes da expiração. O script não faz rotação automática. Um teste local pode já ter criado `.local/tls`; confira a validade antes de reaproveitar no laboratório.

## Implantar e importar topologia

```powershell
kubectl apply -k k8s
kubectl -n messaging rollout status statefulset/rabbitmq --timeout=300s
kubectl -n messaging get pods,pvc,services
kubectl -n messaging cp rabbitmq/definitions.json rabbitmq-0:/tmp/gearflow-definitions.json
kubectl -n messaging exec rabbitmq-0 -- rabbitmqctl import_definitions /tmp/gearflow-definitions.json
kubectl -n messaging exec rabbitmq-0 -- rabbitmqctl list_queues -p gearflow name messages consumers
```

O import é assíncrono; confirme as filas, repetindo a consulta se necessário. A topologia de negócio continua provisória e não inclui usuários/senhas. Alterações incompatíveis exigem migração; importar novamente não remove recursos antigos.

## Criar usuários dos serviços

Em um terminal, mantenha o túnel administrativo aberto (o destino é o Pod, não o Service):

```powershell
kubectl -n messaging port-forward pod/rabbitmq-0 15673:15672 --address 127.0.0.1
```

Painel: http://localhost:15673. O tráfego do túnel é protegido pelo acesso Kubernetes; não publicar essa porta na rede. Em outro terminal:

```powershell
pwsh -File scripts/Set-BrokerServiceUsers.ps1
```

O script solicita a credencial administrativa, cria quatro usuários sem tags administrativas, aplica `rabbitmq/service-permissions.json` e verifica o resultado. Senhas aleatórias ficam em `.local/service-credentials.json`, sem aparecer no terminal. Reexecuções preservam senhas. Se um usuário já existir sem o arquivo de credenciais correspondente, o script para para revisão, sem trocar sua senha. Guarde esse arquivo: ele é necessário para retomada e entrega das credenciais. Não reutilizar arquivos entre ambientes independentes.

| Usuário | Publicação | Consumo |
| --- | --- | --- |
| `gearflow_os` | `gearflow.events` | `gearflow.os.*`, exceto sufixo `.dlq` |
| `gearflow_budget` | `gearflow.events` | `gearflow.budget.*`, exceto sufixo `.dlq` |
| `gearflow_operation` | `gearflow.events` | `gearflow.operation.*`, exceto sufixo `.dlq` |
| `gearflow_inventory` | `gearflow.events` | `gearflow.inventory.*`, exceto sufixo `.dlq` |

Todos ficam restritos ao vhost `gearflow`, sem criar/excluir/configurar recursos, fazer bindings, publicar diretamente na exchange padrão, consumir DLQs ou acessar o painel. A topologia é administrada pela infraestrutura. Bibliotecas que declaram filas/exchanges automaticamente devem ser configuradas para usar a topologia existente; revisar isso com o grupo.

Esta é uma convenção técnica inicial: a exchange comum ainda permite qualquer routing key. Quando os contratos forem acordados, revisar nomes e adicionar permissões de tópico por produtor se necessário. Não representa aprovação dos eventos/Saga. As filas de Operation/Inventory/OS não são criadas por esse script.

## Autorizar workloads na rede

O operador autoriza cada namespace de aplicação:

```powershell
kubectl label namespace NAMESPACE_DO_SERVICO gearflow.io/messaging-access=true --overwrite
```

No **template de Pods** do Deployment de cada serviço:

```yaml
spec:
  template:
    metadata:
      labels:
        gearflow.io/rabbitmq-client: "true"
```

As duas labels são necessárias simultaneamente. Namespace sem label ou Pod sem label não deve conectar. Labels não comprovam identidade: RBAC deve impedir workloads não autorizados de conceder acesso a si mesmos. Se houver egress restrito nos serviços, permitir DNS e TCP 5671 para o broker também.

Conexão dos serviços: host `rabbitmq.messaging.svc.cluster.local`, porta `5671`, vhost `gearflow`, TLS com CA/hostname validados e usuário próprio. A URI usa esquema `amqps`; não incluir senhas em logs ou documentação.

## Validação

Antes de conectar o grupo, testar no EKS: PVC `Bound`; Pod `Ready`; AMQPS com cliente real; bloqueio de 5672 e do painel pela rede; acesso permitido somente com ambas as labels; rejeição de CA/hostname/credenciais incorretos; permissões de filas; persistência após recriação controlada do Pod. Usar Deployments/Jobs nos testes de rede, não Pods avulsos, conforme limitações do VPC CNI.

```powershell
kubectl -n messaging get networkpolicy
kubectl -n messaging describe pvc data-rabbitmq-0
kubectl -n messaging logs rabbitmq-0 --tail=100
kubectl -n messaging exec rabbitmq-0 -- rabbitmq-diagnostics listeners
```

PVC `Pending`: revisar CSI/IAM/zona. Pod `Pending`: revisar recursos e afinidade do volume. Kustomize gera ConfigMap com hash e atualiza o Pod quando a configuração muda.

Validação local isolada desta configuração:

```powershell
pwsh -File scripts/Test-SharedBroker.ps1
```

Esse teste usa container/volume descartáveis, verifica TLS, ACL cadastrada, preservação de senhas e listeners. Não prova enforcement da NetworkPolicy, cliente AMQP, Saga nem persistência no EKS. Esses testes seguem para a etapa de implantação.

Para repetir as verificações reais do cluster implantado, use `pwsh -File scripts/Test-ClusterBroker.ps1`. Requer `.local/kubeconfig`, `.local/cluster-admin.json`, certificado/CA e acesso ao cluster. O script cria filas com identificador exclusivo, Jobs/Secrets em namespaces temporários e remove esses recursos quando os testes passam. Usa cliente Pika 1.3.2 em imagem Python; precisa de acesso ao registro e ao PyPI. Em caso de falha, preserva os recursos para diagnóstico; revise-os antes de repetir.

Para incluir a recriação do Pod e comprovar persistência, use `pwsh -File scripts/Test-ClusterBroker.ps1 -RestartBroker` somente numa janela sem consumidores. Esse parâmetro interrompe as conexões do broker. Ele não exclui o PVC. As credenciais administrativas geradas nesta implantação estão em `.local/cluster-admin.json`; senhas dos serviços em `.local/service-credentials.json`. Nenhum desses arquivos deve ser versionado ou compartilhado integralmente com os serviços.

## Encerrar o laboratório

O EBS de 10 GiB usa `Retain`. Ele contém os dados do RabbitMQ e pode continuar gerando custo depois de excluir o PVC ou cluster. O script `Destroy-Cluster.ps1` bloqueia ao encontrar PVC/PV para exigir essa revisão.

Antes de destruir o cluster, registre o PVC/PV e o volume EBS (o inventário desta implantação está em `.local/rabbitmq-volume.json`). Combine a parada e decida se precisa de backup. Se os dados forem descartáveis, escale o StatefulSet a zero, aguarde o Pod terminar e remova os recursos Kubernetes do broker. O PV retido e o EBS precisam de exclusão explícita, com o volume desanexado e o ID conferido. Só depois execute a destruição Terraform. Não remova volumes por filtro amplo nem presuma que `kubectl delete -k k8s` exclua o disco.

Referências: [TLS RabbitMQ](https://www.rabbitmq.com/docs/ssl), [permissões RabbitMQ](https://www.rabbitmq.com/docs/access-control), [NetworkPolicy no EKS](https://docs.aws.amazon.com/eks/latest/userguide/cni-network-policy-configure.html).
