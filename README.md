# GearFlow — Infraestrutura do broker

Infraestrutura compartilhada da Fase 4: cluster EKS e RabbitMQ para OS, Budget, Operation e Inventory.

## Organizacao do cluster compartilhado

- [`infra/cluster/`](infra/cluster/README.md): Terraform do EKS, rede e workers para os quatro servicos e o broker.
- [`k8s/`](k8s/README.md): recursos Kubernetes do RabbitMQ dentro desse cluster.
- Repositorios dos microsservicos: imagens e deploys independentes no cluster existente.

Alterar o RabbitMQ nao recria o cluster. O CI de Terraform valida apenas alteracoes no diretorio de infraestrutura; nenhum workflow aplica Terraform automaticamente. Provisionamento depende da verificacao de IAM, versao EKS, acesso e state do AWS Academy.

## Escopo atual

- RabbitMQ local com painel de administração, volume persistente e healthcheck.
- Topologia inicial proposta para `OrderCreated`, com fila do Budget e fila de erro.
- Configuração sem credenciais versionadas.

A Saga, regras de negócio, Outbox e consumidores pertencem aos microsserviços. Este repositório não implementa essas funcionalidades. O cluster EKS foi criado via Terraform, com dois workers Ready. RabbitMQ implantado com EBS de 10 GiB, TLS e usuários por serviço; comunicação AMQP, permissões, NetworkPolicy, DLQ e persistência após recriação do Pod foram validadas. Deploy automático e alta disponibilidade ainda não estão implementados.

A imagem está fixada em `4.2.9-management` e digest OCI, iguais no Compose e Kubernetes. No ambiente compartilhado, os manifestos exigem TLS na porta 5671, restringem o painel ao loopback do Pod e incluem NetworkPolicy. Foram testados acesso permitido com ambas as labels e bloqueio na ausência da label do Pod ou namespace. Usuários e permissões dos quatro serviços são preparados por script, sem senhas no Git. Veja [implantação compartilhada](k8s/README.md).

Evidências e limites: [validação local](docs/shared-validation.md), [validação do EKS](docs/cluster-validation.md) e [RabbitMQ no EKS](docs/broker-validation.md). O cluster e o volume do broker permanecem ativos e consumindo créditos. Para revisar sua remoção, use `pwsh -File scripts/Destroy-Cluster.ps1`; a exclusão exige opções explícitas e revisão do volume retido, conforme o README de Kubernetes.

## Executar localmente

Pré-requisitos: Docker com containers Linux, Docker Compose e PowerShell 7 (`pwsh`).

Na raiz deste repositório, no PowerShell:

```powershell
pwsh -File scripts/Start-Broker.ps1
```

O script cria `.env` com senha aleatória apenas quando não existe configuração local nem credenciais completas no ambiente. Preserva um `.env` existente, valida os arquivos, inicia o broker, aguarda a API e importa a topologia via API de gerenciamento. Pode ser executado novamente após alterar `definitions.json`. Não remove volumes, não cria consumidores nem publica eventos. Recursos removidos do arquivo não são automaticamente removidos do broker; mudanças incompatíveis exigem migração planejada.

Painel: [http://localhost:15672](http://localhost:15672), usando usuário e senha do `.env`.

| Parâmetro para serviços executados no host | Valor padrão |
| --- | --- |
| Host | `localhost` |
| Porta AMQP | `5672` |
| Virtual host | `gearflow` |
| Usuário/senha | Valores definidos no `.env` |

Containers na rede deste Compose usam host `rabbitmq` e porta `5672`. Serviços em outros projetos Compose precisam compartilhar uma rede explicitamente; `localhost` dentro de um container aponta para ele próprio. Não há integração automática entre os projetos.

As portas publicadas ficam restritas à máquina local. Cada integrante executa um broker próprio; testes conjuntos exigem os serviços conectados ao mesmo broker.

## Integração contínua

O workflow `.github/workflows/ci.yml` executa em PRs, pushes na `main` e disparo manual. Valida JSON, sintaxe PowerShell, Compose e renderização Kustomize; sobe um RabbitMQ temporário, verifica inicialização repetida e testa roteamento, persistência e DLQ. Também executa um container isolado para verificar TLS, usuários/permissões, preservação de senhas e painel no loopback. As verificações de mensagens usam HTTP de gerenciamento; não substituem integração AMQP dos serviços nem testes de rede no EKS.

O runner usa credenciais aleatórias temporárias, sem secrets cadastrados. Ao finalizar, remove containers e volumes somente do ambiente descartável do CI; localmente os dados continuam preservados. Logs de diagnóstico aparecem em caso de falha. Não há deploy neste workflow.

Após publicar o workflow, configure `Validate RabbitMQ` como check obrigatório na proteção da branch, se desejado. O arquivo sozinho não altera as proteções do GitHub. As filas atuais continuam sendo exemplos provisórios, não contratos de negócio aprovados.

## Validação local automatizada

Com o broker iniciado e PowerShell 7 disponível:

```powershell
pwsh -File scripts/Test-Broker.ps1
# Opcional: reinicia o broker inteiro para testar persistência.
pwsh -File scripts/Test-Broker.ps1 -RestartBroker
```

O script lê as credenciais da configuração Compose sem exibi-las, cria um vhost temporário, replica a topologia e verifica publicação, conteúdo, requeue e encaminhamento para a DLQ. Ao terminar, remove apenas o vhost criado pelo teste. Requer o usuário administrativo local. Se o processo for interrompido ou o broker ficar indisponível durante a limpeza, pode restar um vhost com prefixo `gearflow-smoke-` para revisão manual.

O parâmetro `-RestartBroker` interrompe brevemente todas as conexões deste broker; use no ambiente local sem trabalho concorrente. O teste utiliza a API HTTP de gerenciamento, não um cliente AMQP dos microsserviços. Ele não valida Outbox, publisher confirms AMQP, idempotência de negócio ou Saga. Veja [resultado da validação](docs/local-validation.md).

### Comandos de operação

```powershell
docker compose ps
docker compose logs --tail 100 rabbitmq
docker compose stop
docker compose start
docker compose down
```

`down` preserva o volume. Não utilize `down -v` se precisar preservar mensagens e usuários. A alteração das credenciais no `.env` não troca usuários já gravados no volume; faça a alteração administrativa no broker e atualize os clientes.

## Estrutura

```text
compose.yaml                Ambiente local
.env.example                Variáveis de exemplo
rabbitmq/rabbitmq.conf      Configuração do broker
rabbitmq/definitions.json   Topologia inicial sem usuários/senhas
scripts/Start-Broker.ps1    Inicialização local em um comando
scripts/Test-Broker.ps1     Validação isolada do broker
scripts/New-BrokerTls.ps1   Certificado TLS para o laboratório
scripts/Set-BrokerServiceUsers.ps1  Usuários e ACL dos serviços
scripts/Test-SharedBroker.ps1      Teste TLS/ACL descartável
.github/workflows/ci.yml    Validação automatizada de PRs
docs/messaging.md           Convenções e responsabilidades
k8s/README.md               Próxima etapa de implantação
```

## Próximas entregas

- Validar contratos e nomes com os quatro serviços.
- Integrar publicação/consumo reais com Outbox e idempotência.
- Definir retentativas limitadas e procedimento de reprocessamento.
- Integrar os serviços reais ao RabbitMQ implantado, distribuindo CA e credenciais individuais.
- Implementar CD e monitoramento.
- Validar fluxo completo e compensações da Saga.

Referências: [definições](https://www.rabbitmq.com/docs/definitions), [confiabilidade](https://www.rabbitmq.com/docs/reliability), [confirmações](https://www.rabbitmq.com/docs/confirms).
