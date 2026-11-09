# Subir e encerrar o laboratorio

## Preparar o RabbitMQ em um EKS pronto

Na raiz do repositorio, com Python 3.9+, PowerShell 7.4+, kubectl e sessao Academy valida:

```powershell
pwsh -File scripts/Start-ClusterBroker.ps1
```

Esse comando atualiza um kubeconfig isolado em .local, valida o destino e executa o mesmo caminho usado pelo CD. O cluster precisa existir, estar acessivel e ter nodes/driver EBS prontos. Terraform continua responsavel pelo EKS.

O bootstrap cria o namespace e somente os Secrets de credenciais ausentes; aplica os manifestos; aguarda o broker; cria usuarios ausentes; aplica/importa a topologia e verifica permissoes e saude. O laboratorio usa AMQP interno na porta 5672 sem TLS, sem gerar ou validar certificados.

Secrets canonicos em messaging:

- rabbitmq-credentials: administrador, chaves username/password.
- rabbitmq-os-credentials, rabbitmq-budget-credentials, rabbitmq-operation-credentials, rabbitmq-inventory-credentials: username/password individuais.

Secrets de credenciais existentes sao preservados. O bootstrap verifica se a senha de cada Secret corresponde ao usuario, sem imprimir senhas ou envia-las em argumentos de processos. Inconsistencias param a operacao em vez de trocar credenciais. Senhas locais ficam em .local/cluster-admin.json e .local/service-credentials.json; versoes locais diferentes sao guardadas em .local/credentials-backups antes de atualizar o cache. Nada disso vai para Git ou artifacts.

Apos o rollout e verificacao das credenciais, o bootstrap remove somente os recursos obsoletos rabbitmq-tls (Secret) e rabbitmq-ca (ConfigMap) em messaging. Copias locais antigas de certificados ficam sem uso; nao impedem instalacao nem sao apagadas automaticamente. Clientes da instalacao anterior precisam mudar para porta 5672/TLS desabilitado e remover dependencia de CA. Essa mudanca reinicia o unico Pod, preservando usuarios e mensagens no PVC.

## Migrar a instalacao anterior uma vez

Se o cluster ainda tem os usuarios antigos criados sem Secrets individuais, executar Start-ClusterBroker.ps1 localmente antes de publicar o novo CD. O bootstrap usa .local/service-credentials.json para criar os Secrets, conferindo os hashes reais do broker, sem mudar as senhas. Se esse arquivo se perdeu, recuperar as credenciais antes de migrar; o CD nao tenta adivinhar nem resetar usuarios existentes.

Se o ambiente foi inteiramente descartado, nao ha migracao: a nova instalacao usa os arquivos locais disponiveis ou gera credenciais novas. Um runner GitHub novo normalmente nao tem os caches locais; nesse caso cria credenciais novas no Kubernetes. Integrantes devem configurar seus servicos com os Secrets da instalacao atual, nao com copias antigas.

## CD depois de recriar o EKS

O job do broker em GearFlow CD agora chama o bootstrap automaticamente antes da verificacao de topologia. Apos um apply aprovado da infraestrutura, ele consegue implantar o RabbitMQ num cluster vazio. Nao e necessario cadastrar senhas RabbitMQ adicionais no GitHub. Os secrets AWS/HCP e os inputs de infraestrutura continuam necessarios.

## Destruir com descarte explicito das mensagens

Previa, sem excluir dados ou infraestrutura:

```powershell
pwsh -File scripts/Destroy-Cluster.ps1 -DiscardRabbitMqData
```

Para parar o broker, apagar seu disco/dados e destruir a infraestrutura Terraform:

```powershell
pwsh -File scripts/Destroy-Cluster.ps1 -Execute -ConfirmClusterName gearflow-fase4 -DiscardRabbitMqData
```

Sem -DiscardRabbitMqData, a presenca do PVC/PV continua bloqueando a destruicao. A opcao autoriza perder mensagens, usuarios e configuracao persistidos no volume do RabbitMQ; backups e arquivos locais sao preservados. Nao executar durante um CD ou com outro operador alterando o ambiente.

O script confere conta/state/cluster, kubeconfig e os recursos Kubernetes. Valida o vinculo UID do PVC/PV, StorageClass e driver, alem das tags CSI do EBS. Gera/valida o plano Terraform antes de excluir dados. Depois para o StatefulSet, exclui o PVC, espera o EBS desanexar, exclui somente o volume identificado, confirma sua ausencia e remove o PV. Uma nova consulta bloqueia o Terraform se ainda houver PVCs/PVs, LoadBalancers ou Ingresses.

PVC/PV de outros servicos, outros volumes, recursos sem comprovacao de origem e PVC ainda sem vinculo comprovado continuam bloqueados para revisao. A opcao nao e uma autorizacao para apagar os bancos dos demais microsservicos.

O journal .local/destroy/rabbitmq-cleanup.json e salvo antes da primeira exclusao e permite repetir o mesmo comando depois de uma falha parcial. Ele tambem permite concluir a exclusao do EBS conhecido se o cluster ja nao existir. Nao apagar o journal durante uma limpeza interrompida. Um cluster recriado com o mesmo nome e journal antigo pendente e bloqueado. Discos antigos sem PV e sem journal confiavel continuam exigindo revisao manual, nao uma busca/exclusao ampla na conta.

## Validacao desta alteracao

38 testes Python passaram com AWS/Kubernetes simulados: instalacao nova e repetida, migracao e divergencia de senhas, volume existente sem credencial recuperavel, preservacao de credenciais, remocao de certificados antigos apos rollout, bloqueio de recursos estrangeiros, origem do EBS, ordem de exclusao, retomada de resposta perdida e cluster recriado; tambem incluem contratos/ACL e os controles anteriores de CD/aprovacao Terraform.

Em Docker descartavel, o hash retornado pela API RabbitMQ foi comparado com as senhas dos usuarios usando o mesmo verificador da migracao. Resposta AMQP 0-9-1 sem TLS na porta 5672, ACL, ausencia de listener 5671, painel no loopback e reinicio do container foram validados.

A adaptacao aos servicos tambem foi validada com RabbitMQ.Client/MassTransit 8.5.11: declaracoes repetidas por usuario, comandos/eventos, destinos de erro e acesso negado a filas de comando alheias. Startup repetido, roteamento Billing, DLQ explicita e persistencia apos restart passaram em Compose isolado. Limites em [messaging.md](messaging.md).

Nenhum recurso AWS foi provisionado/excluido para testar esta alteracao. O ciclo novo de criacao/destruicao completo no EKS ainda precisa ser observado numa janela de laboratorio autorizada; testes simulados e Docker nao substituem essa validacao do EBS/CSI em runtime.
