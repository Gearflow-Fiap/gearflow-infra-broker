# Deploy do RabbitMQ no EKS existente

O workflow **GearFlow CD** (`broker-cd.yml`) instala e atualiza somente o broker.
Ele nao cria/substitui cluster, nao chama Terraform e nao usa TF_API_TOKEN nem
TF_CLUSTER_CONFIG_JSON. O fluxo separado **Cluster CD** continua disponivel para
operacoes explicitas do ambiente Terraform anterior; nao o executar para subir
o broker no cluster atual.

## Configuracao do repositorio

Em Settings > Secrets and variables > Actions, cadastrar Repository variables:

| Nome | Valor |
| --- | --- |
| EKS_CLUSTER_NAME | gearflow |
| AWS_REGION | us-east-1 |
| K8S_NAMESPACE | messaging |
| AWS_ACCOUNT_ID | Opcional: ID da conta atual, para restringir a autenticacao |

Variaveis de repositorio sobrescrevem valores herdados da organizacao. Remover
ou atualizar valores antigos como gearflow-fase4. O workflow valida o destino
gearflow/us-east-1/messaging antes de aplicar recursos. Ao trocar de ambiente,
revisar essa validacao explicitamente. AWS_ACCOUNT_ID nao e segredo, mas deve
corresponder a conta das credenciais; nao reaproveitar o ID do laboratorio antigo.

Secrets necessarios: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY e AWS_SESSION_TOKEN,
todos da mesma sessao Academy e conta do cluster gearflow. O workflow nao recebe
senha administrativa RabbitMQ: o bootstrap cria credenciais individuais aleatorias
no Kubernetes e preserva as ja existentes.

## Pre-requisitos de acesso

Cluster ACTIVE, nodes disponiveis e role do Actions com acesso Kubernetes para
aplicar namespace, StorageClass, Secrets, StatefulSet e NetworkPolicy. Os nodes
precisam conseguir baixar a imagem RabbitMQ do Docker Hub.

O endpoint Kubernetes deve ser alcancavel pelo runner GitHub. O CD nao altera
CIDRs nem habilita endpoints publicos; um endpoint privado ou bloqueado exige
um runner/rede com acesso previamente configurado. O helper cluster_access.py
permanece disponivel para procedimentos antigos, mas nao e chamado por este CD.
O kubectl e escolhido pela versao real do cluster, sem versao fixa antiga.

## Executar

1. Merge do PR na main.
2. Aguardar **Broker CI** aprovado para o mesmo commit da main.
3. O **GearFlow CD** inicia automaticamente apos esse CI. Tambem pode executar
   Actions > GearFlow CD > Run workflow > main. Nao ha campos plan/apply.
4. Aguardar o job deploy aprovado: bootstrap, rollout, topologia, permissoes e
   saude do broker sao verificados. Falhas exibem pods, PVCs e eventos.
5. Depois executar **Billing Academy EKS** no repositorio budget-service.

PRs/forks nao recebem credenciais nem disparam CD. O checkout usa exatamente o
commit aprovado pelo CI. Criar o PR nao modifica recursos AWS.

## Armazenamento

O deploy consulta computeConfig.enabled. Auto Mode usa k8s-auto,
StorageClass gearflow-broker-gp3-auto e ebs.csi.eks.amazonaws.com, com topologia
restrita a nodes Auto Mode. Nao requer instalar o driver EBS convencional.
O cluster deve ter o armazenamento Auto Mode habilitado e suas permissoes IAM.
Clusters convencionais usam k8s, gearflow-broker-gp3 e ebs.csi.aws.com, que exige
o add-on EBS CSI e permissoes IAM correspondentes.

A instalacao preserva o disco gp3 de 10 GiB e a politica Retain do repo. Nenhum
PVC, fila ou volume e apagado/migrado automaticamente. Se existir um StatefulSet
com outra StorageClass, interromper e planejar a migracao: esse campo e imutavel.

## Contrato com Billing

- Service rabbitmq.messaging.svc.cluster.local, AMQP interno 5672 sem TLS.
- Vhost gearflow, usuario gearflow_budget, filas quorum conforme definitions.json.
- Secret messaging/rabbitmq-budget-credentials: username/password do Billing.
- Namespace cliente: label gearflow.io/messaging-access=true.
- Pods clientes: label gearflow.io/rabbitmq-client=true.

O Billing copia apenas sua credencial para seu namespace. A topologia, usuarios,
permissoes e disco pertencem ao infra-broker. O RabbitMQ antigo no namespace
gearflow e suas mensagens nao sao removidos por este deploy; revisar drenagem
antes de exclui-lo. Filas classic antigas nao viram quorum por mudanca de config.

## Limites

Uma replica, sem alta disponibilidade e sem migracao entre contas/clusters.
Alterar credenciais/variaveis nao transfere dados existentes. Os recursos
provisionados consomem creditos Academy enquanto existirem.
