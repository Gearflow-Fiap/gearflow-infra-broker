# RabbitMQ compartilhado no EKS — laboratório

RabbitMQ implantado e validado no EKS convencional, com nodes EC2 Linux e EBS CSI (`ebs.csi.aws.com`). Veja [evidências da etapa 3](../docs/broker-validation.md). Não assumir compatibilidade com Fargate ou EKS Auto Mode.

## Configuração

- RabbitMQ `4.2.9-management`, fixado pelo digest OCI em Compose e StatefulSet.
- Uma instância, sem HA. Aumentar réplicas exige implementar clustering.
- EBS gp3 criptografado, 10 GiB, RWO, binding na zona do consumidor, expansão habilitada e `Retain`. Excluir PVC/cluster não garante remover o disco nem encerrar seu custo.
- Requests 500m CPU/1Gi; limites 2 CPU/2Gi. Alarmes de disco e memória em 1GB podem bloquear produtores. Capacidade depende de testes com os serviços reais.
- Serviço interno `rabbitmq.messaging.svc.cluster.local:5672`, vhost `gearflow`, AMQP sem TLS, com usuário/senha por serviço.
- Painel/API em `127.0.0.1:15672` dentro do Pod, sem porta no Service e sem LoadBalancer/Ingress. Acesso administrativo por `port-forward` autenticado pelo Kubernetes.
- NetworkPolicy permite AMQP somente de Pods e namespaces explicitamente autorizados. Egress do broker permite apenas DNS no CoreDNS; não libera IMDS. Não altera acesso do EBS CSI às credenciais dos nodes.

O Compose local usa 5672 e painel 15672 limitados ao loopback da máquina. No Kubernetes, AMQP fica somente na rede interna e o painel somente no loopback do Pod. Por decisão do grupo em 08/11/2026, o laboratório acadêmico usa dados fictícios e dispensa TLS e certificados no RabbitMQ; o enunciado da Fase 4 não os exige. Credenciais e mensagens trafegam sem criptografia AMQP. Usuários, permissões e restrições de rede continuam ativos.

## Pré-requisitos antes do deploy

1. Usar o cluster existente `gearflow`; o CD do broker n�o executa Terraform. A evid�ncia hist�rica abaixo refere-se ao cluster convencional anterior.
2. Confirmar nodes `Ready`, driver EBS ativo e teste de escrita/leitura em PVC.
3. Confirmar VPC CNI compatível com NetworkPolicy e agente `aws-network-policy-agent` funcionando. O Terraform configura o addon antes dos workers. O modo padrão pode permitir tráfego brevemente durante a inicialização do Pod; autenticação continua obrigatória. Não habilitar modo strict globalmente sem preparar políticas para os demais workloads.
4. Confirmar contexto e permissões Kubernetes. Namespace/labels não substituem RBAC; somente operadores autorizados devem poder conceder labels de acesso, alterar políticas, executar comandos ou fazer port-forward no broker.

```powershell
kubectl config current-context
kubectl get nodes
kubectl get csidriver ebs.csi.aws.com
kubectl -n kube-system get daemonset aws-node
kubectl kustomize k8s
kubectl apply -f k8s/namespace.yaml
```

## Credenciais

O caminho recomendado agora e `pwsh -File scripts/Start-ClusterBroker.ps1` ou o job de broker do CD. Eles fazem o bootstrap automaticamente. Os comandos manuais abaixo permanecem como referencia para administracao/recuperacao. Veja [lifecycle](../docs/lifecycle.md), incluindo a migracao da instalacao antiga para Secrets individuais.

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

Não é necessário gerar, distribuir ou renovar certificados. Cada serviço recebe somente sua própria senha por Secret no seu namespace. `.local/` é ignorado pelo Git, mas não é um cofre; restrinja acesso à máquina.

Na atualização de uma instalação antiga, o bootstrap aplica a configuração AMQP e aguarda o rollout. Depois de verificar as credenciais, remove somente o Secret `rabbitmq-tls` e o ConfigMap `rabbitmq-ca` obsoletos em `messaging`. Cópias antigas em `.local/tls` não são utilizadas nem apagadas automaticamente. Atualize também os clientes para AMQP/5672, sem CA nem TLS. O rollout do único Pod interrompe conexões; mensagens e usuários permanecem no PVC.

## Implantar e importar topologia

```powershell
kubectl apply -k k8s
kubectl -n messaging rollout status statefulset/rabbitmq --timeout=300s
kubectl -n messaging get pods,pvc,services
kubectl -n messaging cp rabbitmq/definitions.json rabbitmq-0:/tmp/gearflow-definitions.json
kubectl -n messaging exec rabbitmq-0 -- rabbitmqctl import_definitions /tmp/gearflow-definitions.json
kubectl -n messaging exec rabbitmq-0 -- rabbitmqctl list_queues -p gearflow name messages consumers
```

O import é assíncrono; confirme as filas, repetindo a consulta se necessário. A topologia reproduz os contratos atuais dos serviços, conforme [catálogo e permissões](../docs/messaging.md), sem alterar regras de negócio. Não inclui usuários/senhas. Alterações incompatíveis exigem migração; importar novamente não remove recursos antigos.

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
| `gearflow_os` | `gearflow.events`, `gearflow.commands.v1`, exchange default | `gearflow.os-service` |
| `gearflow_budget` | Exchanges de comandos/eventos/falhas Billing | Comandos Billing, sua DLQ e assinatura de eventos |
| `gearflow_operation` | Exchanges `operation.*` e recursos MassTransit do serviço | Endpoints `operation-*` e auxiliares |
| `gearflow_inventory` | Exchanges `inventory.*` e recursos MassTransit do serviço | Endpoints `inventory-*`, email e auxiliares |

Todos ficam restritos ao vhost `gearflow`, sem tags administrativas nem acesso ao painel. Aplicações podem declarar/configurar seus recursos e bindings como o código atual exige. Infraestrutura pré-cria a base; MassTransit administra também seus destinos auxiliares. A permissão configure também permite excluir recursos do escopo. A OS precisa declarar seis filas dos pares e publicar na exchange default; essa autorização não limita publicação por fila. Leia os limites em [mensageria](../docs/messaging.md#permissoes-por-aplicacao).

`contracts.json` registra os contratos extraídos; `definitions.json` contém filas/exchanges/bindings e `service-permissions.json` contém ACLs, gerados por `python scripts/cd/sync_topology.py`. O script de usuários aplica as ACLs; o bootstrap/CD importa a topologia. Não representa aprovação das regras da Saga.

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

As duas labels são necessárias simultaneamente. Namespace sem label ou Pod sem label não deve conectar. Labels não comprovam identidade: RBAC deve impedir workloads não autorizados de conceder acesso a si mesmos. Se houver egress restrito nos serviços, permitir DNS e TCP 5672 para o broker também.

Conexão dos serviços: host `rabbitmq.messaging.svc.cluster.local`, porta `5672`, vhost `gearflow`, TLS desabilitado e usuário próprio. A URI usa esquema `amqp`; não incluir senhas em logs ou documentação. Em opções .NET com esse campo, usar `RabbitMq__Tls=false`. Remover montagens de CA e habilitação explícita de SSL dos deployments dos serviços que as utilizavam.

Pendências nos repositórios das aplicações consultados: Inventory ainda traz porta 5671, TLS e montagem obrigatória de `rabbitmq-ca` nos manifestos; atualizar o ConfigMap e retirar esse volume/mount. Budget também traz 5671/TLS nos manifestos e a validação em `PaymentIntegrationRegistration.cs` exige TLS fora de Development: o responsável precisa adequar essa validação para o laboratório, além de configurar porta 5672, host `rabbitmq.messaging.svc.cluster.local` e vhost `gearflow`. Não trocar o ambiente inteiro para Development apenas para contornar essa validação, pois isso afeta outras configurações. Esses repositórios não foram alterados nesta mudança do infra-broker.

## Validação

Antes de conectar o grupo, testar no EKS: PVC `Bound`; Pod `Ready`; AMQP com cliente real; bloqueio de 5671 e do painel pela rede; acesso permitido somente com ambas as labels; rejeição de credenciais incorretas; permissões de filas; persistência após recriação controlada do Pod. Usar Deployments/Jobs nos testes de rede, não Pods avulsos, conforme limitações do VPC CNI.

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

Esse teste requer .NET 10 e usa container/volume descartáveis. Verifica AMQP sem TLS, autenticação/ACL com clientes RabbitMQ.Client e MassTransit 8.5.11, comandos/eventos atuais, recursos de erro do framework, preservação de senhas e listeners. Não prova regras de negócio, Saga, enforcement da NetworkPolicy ou persistência no EKS.

Para repetir as verificações reais do cluster implantado, use `pwsh -File scripts/Test-ClusterBroker.ps1`. Requer `.local/kubeconfig`, `.local/cluster-admin.json` e acesso ao cluster. O script replica a topologia em vhost exclusivo, cria usuários de teste com as mesmas ACLs, Jobs/Secrets em namespaces temporários e remove esses recursos quando os testes passam. Não consome mensagens das filas das aplicações. Usa cliente Pika 1.3.2 em imagem Python; precisa de acesso ao registro e ao PyPI. Em caso de falha, preserva e identifica os recursos para diagnóstico; revise-os antes de repetir.

Para incluir a recriação do Pod e comprovar persistência, use `pwsh -File scripts/Test-ClusterBroker.ps1 -RestartBroker` somente numa janela sem consumidores. Esse parâmetro interrompe as conexões do broker. Ele não exclui o PVC. As credenciais administrativas geradas nesta implantação estão em `.local/cluster-admin.json`; senhas dos serviços em `.local/service-credentials.json`. Nenhum desses arquivos deve ser versionado ou compartilhado integralmente com os serviços.

## Deploy automatizado

O job do broker no coordenador GearFlow CD aplica estes manifestos depois da verificação/aplicação aprovada da infraestrutura, usando Secrets/usuários já provisionados. Configuração de acesso ao EKS, credenciais Academy e recuperação em falhas estão em [docs/cd.md](../docs/cd.md); o fluxo Terraform está em [docs/infra-cd.md](../docs/infra-cd.md). Não destruir o ambiente enquanto houver deploy em execução.

## Encerrar o laboratório

O EBS de 10 GiB usa `Retain`. Ele contém os dados do RabbitMQ e pode continuar gerando custo depois de excluir o PVC ou cluster. O script `Destroy-Cluster.ps1` bloqueia por padrao, mas oferece `-DiscardRabbitMqData` para remover somente o PVC/PV/EBS comprovado do broker antes do Terraform.

Para descartar os dados e encerrar: `pwsh -File scripts/Destroy-Cluster.ps1 -Execute -ConfirmClusterName gearflow-fase4 -DiscardRabbitMqData`. Para previa, usar somente `-DiscardRabbitMqData`. O script consulta o PV atual, guarda journal e confirma o EBS exato, sem usar inventario antigo como autorizacao de exclusao. Outros volumes continuam bloqueados. Procedimento e limites em [docs/lifecycle.md](../docs/lifecycle.md).

Referências: [permissões RabbitMQ](https://www.rabbitmq.com/docs/access-control), [NetworkPolicy no EKS](https://docs.aws.amazon.com/eks/latest/userguide/cni-network-policy-configure.html).
# EKS existente e Auto Mode

Para instalar no cluster `gearflow`, seguir [o CD do broker](../docs/cd.md).
O deploy seleciona `k8s-auto` quando `computeConfig.enabled=true` e usa o
provisionador `ebs.csi.eks.amazonaws.com`. O manifesto base continua usando o
driver convencional para clusters com managed node groups. Os dois perfis usam
StorageClasses distintas para evitar alterar um provisionador imutavel.
O overlay nao migra nem exclui volumes ja existentes.
