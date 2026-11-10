# Mensageria dos servicos — 08/11/2026

O broker foi adaptado ao codigo existente dos servicos. Regras da Saga e tratamento de resultados continuam sob responsabilidade das equipes das aplicacoes. Esta adaptacao nao aprova nem altera regras de negocio.

## Fonte e manutencao

`rabbitmq/contracts.json` registra os nomes extraidos de OS `65da498`, Budget `f787a43`, Operation `cbcb156` e Inventory `915ef73`. Budget foi revisto em 09/11/2026: codigo-fonte e testes identicos ao snapshot anterior `944bd13`; mudaram somente deploy/infraestrutura/documentacao. Veja [revisao do Budget](BUDGET-CONTRATO-REVISAO-2026-11-09.md). `scripts/cd/sync_topology.py` gera `definitions.json` e `service-permissions.json` a partir desse catalogo. O CI executa `--check` para impedir divergencia entre catalogo e arquivos aplicados.

```powershell
python scripts/cd/sync_topology.py
python scripts/cd/sync_topology.py --check
```

Os formatos de mensagem sao os implementados nos servicos. O RabbitMQ nao converte nem valida seu JSON; preserva corpo, Content-Type, MessageId e CorrelationId.

## Topologia de base

Vhost `gearflow`, AMQP interno na porta 5672, sem TLS. Sao 41 exchanges duraveis, 14 filas duraveis e 41 bindings que reproduzem os contratos atuais. Recursos auxiliares do MassTransit sao declarados pelo framework sob demanda.

| Origem/destino | Exchange e tipo | Fila / routing key |
|---|---|---|
| OS publica abertura | `gearflow.events` (topic) | `os.created`, `os.order-created.v1`; sem consumidor Budget artificial |
| OS → Budget | `gearflow.commands.v1` (topic) | `gearflow.billing.commands.v1`; `billing.GenerateBudget`, `billing.RequestPayment`, `billing.CancelBudget`, `billing.RefundPayment` |
| Budget → OS | `gearflow.events.v1` (topic) | `gearflow.os-service`; binding `gearflow-budget-service.*` |
| Assinatura declarada pelo Budget | `gearflow.events.v1` (topic) | `gearflow.os.billing.events.v1`; mesmo binding; codigo atual nao apresenta consumidor dessa assinatura |
| Falha de comando Budget | `gearflow.deadletters.v1` (topic) | `gearflow.billing.commands.v1.dlq`; routing key `gearflow.billing.commands.v1` |
| OS → Operation | Exchange default `""` | `operation-start-diagnostic`, `operation-start-execution`, `operation-cancel-operation`; routing key igual a fila |
| Outros comandos Operation | Exchanges dos tipos MassTransit | `operation-update-vehicle`, `operation-deactivate`; consumidor existe, despacho nao encontrado na OS |
| OS → Inventory | Exchange default `""` | `inventory-reserve-stock`, `inventory-release-stock`, `inventory-consume-stock`; routing key igual a fila |
| Operation/Inventory → OS | Exchange fanout por evento | Bindings para `gearflow.os-service`, somente os que o OS atual declara |
| Notificacoes Inventory | `inventory.low-stock-alert`, `inventory.stock-insufficient` (fanout) | `inventory-low-stock-alert-email`, `inventory-stock-insufficient-email` |

As filas/exchanges de endpoint e as exchanges de tipo dos comandos MassTransit tambem estao precriadas com seus bindings exchange→exchange→queue. Assim, comandos do OS podem aguardar o consumidor iniciar. Os nomes completos estao no catalogo. As aplicacoes continuam podendo repetir declaracoes compativeis.

Filas do Budget sao **quorum**, conforme `gearflow-budget-service/k8s/base/configmap.yaml` (`RabbitMq__QueueType=quorum`). Seu padrao em `RabbitOptions`/desenvolvimento e classic: ao conectar ao broker compartilhado, configurar explicitamente quorum. O tipo de uma fila existente nao pode ser trocado por importacao. Um unico broker com quorum nao oferece HA. As demais filas sao classic, compativeis com OS/MassTransit atuais.

## Eventos e envelopes preservados

Budget: `application/json`, campos camelCase `messageId`, `messageType`, `schemaVersion=1`, `occurredOn` UTC, `correlationId=serviceOrderId`, `serviceOrderId`, `causationId`, `producer`, `payload`. Comandos usam messageType simples; eventos e routing keys usam `gearflow-budget-service.<Tipo>`.

Eventos Budget: BudgetGenerated, BudgetGenerationFailed, BudgetApproved, BudgetRejected, BudgetCanceled, PaymentRequested, PaymentConfirmed, PaymentFailed, PaymentRefunded.

Operation/Inventory: `application/vnd.masstransit+json`, objeto `message`, lista `messageType` com URN `urn:message:<namespace>:<Tipo>`. Namespace Operation `GearFlow.Contracts.Operation.V1`; Inventory `GearFlow.Contracts`. Nomes de exchange fanout nao substituem URNs.

Operation publica 13 eventos em `operation.*`; Inventory publica seis em `inventory.*`. O catalogo enumera todos. A OS assina cinco eventos de cada um, exatamente como seu `SagaTopology.cs`; os demais nao recebem consumidores novos por decisao da infraestrutura. Estoque reposto/alerta baixo sao eventos globais sem CorrelationId de OS.

As estruturas dos comandos/eventos MassTransit usadas pelo teste de transporte foram copiadas dos arquivos `Operation.Contracts/Messages.cs` e `GearFlow.Contracts/InventoryMessages.cs`, nos snapshots acima. Devem ser atualizadas com os contratos quando os servicos mudarem. Os campos e conversoes monetarias continuam responsabilidade dos produtores/consumidores.

## Permissoes por aplicacao

Cada usuario continua sem tags administrativas e limitado ao vhost `gearflow`. Declaracao automatica, bindings e recursos auxiliares sao permitidos no escopo de nomes necessario ao codigo atual.

| Usuario | Escopo |
|---|---|
| `gearflow_os` | Declara suas exchanges/fila, exchanges de eventos assinaveis e seis filas de comando de seus pares. Publica `gearflow.events`, `gearflow.commands.v1` e na exchange default. Consome `gearflow.os-service`; nao consome filas de comando alheias. |
| `gearflow_budget` | Declara exchanges topic e filas do Billing; publica eventos, falhas e comandos para replay operacional. Le comandos, sua DLQ e assinatura de eventos, como permitido pelo adaptador atual. |
| `gearflow_operation` | Recursos `operation-*`/`operation.*`, tipos `GearFlow.Contracts.Operation.V1:*`, Fault/ReceiveFault e endpoints temporarios do bus. |
| `gearflow_inventory` | Recursos `inventory-*`/`inventory.*`, tipos `GearFlow.Contracts:*`, Fault/ReceiveFault e endpoints temporarios do bus, incluindo consumidores de email. |

Detalhes relevantes do modelo de autorizacao:

- `configure` cobre criacao **e exclusao** dos recursos correspondentes; a ACL padrao nao separa essas duas operacoes. Os servicos nao se tornam administradores do broker.
- Bindings exigem permissoes adicionais: write no destino e read na origem. Por isso read inclui algumas exchanges, alem das filas de consumo.
- Nomes de filas e exchanges compartilham os mesmos padroes de autorizacao. Operation/Inventory podem acessar os proprios destinos `_error`/`_skipped`; bloquear leitura deles impediria os bindings do framework. Budget usa sua DLQ no replay implementado no proprio servico.
- MassTransit sem prefixo customizado gera nomes temporarios `<host>_bus_<id>`. Operation/Inventory precisam desse padrao comum; seus endpoints temporarios nao tem isolamento de nomes por usuario. Nomes unicos evitam colisao normal, mas nao constituem isolamento adicional.
- Permitir `amq.default` ao OS permite publicar em qualquer fila existente desse vhost. A ACL padrao nao limita a routing key dessa exchange. Isso preserva o codigo atual; restringir por fila exigiria mudar o envio da OS ou adotar outro mecanismo de autorizacao. O teste confirma que consumir filas de comando alheias continua negado.
- Nao foram introduzidas permissoes de topico por routing key. Dentro de cada exchange autorizada, valem os bindings atuais.

Referencia: [controle de acesso do RabbitMQ](https://www.rabbitmq.com/docs/access-control).

## Erros, legados e migracao

Budget move explicitamente as falhas para sua DLQ com publisher confirm. Operation/Inventory usam `_error`, `_skipped` e Fault do MassTransit. Nao aplicamos uma DLX global que altere essas estrategias. OS continua com a politica de ack/descarte implementada em seu consumidor; corrigir handlers ausentes e responsabilidade da aplicacao.

A antiga fila `gearflow.budget.order-created.v1`, sua DLQ, exchange `gearflow.dead-letter` e policy de exemplo sairam das definicoes novas. Recursos existentes **nao sao apagados** pelo import/CD. Antes de excluir os legados, conferir mensagens e consumidores. O CD preserva recursos extras e dados existentes.

Na assinatura `gearflow.os.billing.events.v1`, copias podem acumular sem consumidor. Mantivemos a declaracao do Budget, em vez de inventar um consumidor ou remover contrato implementado. O grupo deve decidir seu destino.

Para aplicar em cluster existente, usar o CD ou `scripts/Start-ClusterBroker.ps1`, que importa topologia e atualiza ACL sem trocar senhas. Ha pendencias nos deployments das aplicacoes: TLS desabilitado/porta 5672, vhost `gearflow`, host `rabbitmq.messaging.svc.cluster.local`; Budget precisa adaptar a validacao TLS fora de Development; Inventory precisa remover a montagem de CA. Nenhum repositorio de aplicacao foi modificado nesta adaptacao.

## Validacao e limites

`scripts/Test-SharedBroker.ps1` usa container/volume exclusivos, credenciais temporarias e .NET 10. O cliente em `tests/TransportProbe` usa MassTransit.RabbitMQ 8.5.11, como os dois servicos. Verifica startup e declaracoes do framework, desserializacao dos oito comandos, rotas de quatro comandos e nove eventos Billing, 19 eventos MassTransit, consumidores de email, Fault/_error/_skipped e operacoes negadas. Sao consumidores de teste: nao executam regras, bancos, Outbox, Mercado Pago ou Saga das aplicacoes.

`scripts/Test-Broker.ps1 -RestartBroker` testa importacao, roteamento Billing, persistencia e publicacao explicita de DLQ via API, sempre em vhost temporario. `scripts/Test-ClusterBroker.ps1` usa usuarios/vhost temporarios com as mesmas ACLs, clientes Pika e namespaces/Jobs para conferir AMQP e NetworkPolicy sem consumir filas de negocio. O modo `-RestartBroker` interrompe conexoes para testar persistencia; usar janela sem consumidores. Em falha, recursos de diagnostico ficam identificados para limpeza.

Validacao local passou. A integracao de negocio dos quatro servicos e o deploy/smoke no EKS continuam pendentes. Nenhuma operacao AWS faz parte desta alteracao.
