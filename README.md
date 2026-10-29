# GearFlow — Infraestrutura do broker

Configuração do RabbitMQ para comunicação entre OS, Budget, Operation e Inventory na Fase 4.

## Escopo atual

- RabbitMQ local com painel de administração, volume persistente e healthcheck.
- Topologia inicial proposta para `OrderCreated`, com fila do Budget e fila de erro.
- Configuração sem credenciais versionadas.

A Saga, regras de negócio, Outbox e consumidores pertencem aos microsserviços. Este repositório não implementa essas funcionalidades. Kubernetes, deploy automático e alta disponibilidade ainda não estão implementados.

## Executar localmente

Pré-requisitos: Docker com containers Linux e Docker Compose.

Na raiz deste repositório, no PowerShell:

```powershell
Copy-Item .env.example .env
# Edite .env e escolha uma senha local antes de continuar.
docker compose config --quiet
docker compose up -d --wait
docker compose exec -T rabbitmq rabbitmqctl import_definitions /etc/rabbitmq/definitions.json
docker compose exec -T rabbitmq rabbitmqctl list_queues -p gearflow name messages consumers
```

O import é feito após o boot para permitir a criação inicial de usuário e vhost pelas variáveis de ambiente. Execute-o novamente após alterar `definitions.json`. Não cria consumidores nem publica eventos. Recursos removidos do arquivo não são automaticamente removidos do broker; mudanças incompatíveis exigem migração planejada.

Painel: [http://localhost:15672](http://localhost:15672), usando usuário e senha do `.env`.

| Parâmetro para serviços executados no host | Valor padrão |
| --- | --- |
| Host | `localhost` |
| Porta AMQP | `5672` |
| Virtual host | `gearflow` |
| Usuário/senha | Valores definidos no `.env` |

Containers na rede deste Compose usam host `rabbitmq` e porta `5672`. Serviços em outros projetos Compose precisam compartilhar uma rede explicitamente; `localhost` dentro de um container aponta para ele próprio. Não há integração automática entre os projetos.

As portas publicadas ficam restritas à máquina local. Cada integrante executa um broker próprio; testes conjuntos exigem os serviços conectados ao mesmo broker.

## Operação

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
docs/messaging.md           Convenções e responsabilidades
k8s/README.md               Próxima etapa de implantação
```

## Próximas entregas

- Validar contratos e nomes com os quatro serviços.
- Integrar publicação/consumo reais com Outbox e idempotência.
- Definir retentativas limitadas e procedimento de reprocessamento.
- Fixar versão patch/digest validada da imagem antes do ambiente compartilhado; a tag `4.2-management` acompanha patches dessa linha.
- Implementar Kubernetes, CI/CD, usuários por serviço e monitoramento.
- Validar fluxo completo e compensações da Saga.

Referências: [definições](https://www.rabbitmq.com/docs/definitions), [confiabilidade](https://www.rabbitmq.com/docs/reliability), [confirmações](https://www.rabbitmq.com/docs/confirms).
