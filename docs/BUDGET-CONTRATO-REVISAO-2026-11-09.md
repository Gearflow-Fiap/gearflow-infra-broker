# Revisão do contrato Budget — 09/11/2026

## Resultado

O contrato de mensageria do Budget continua compatível com o infra-broker. Não é necessário alterar filas, exchanges, bindings, permissões ou formatos de mensagem.

Revisado o commit remoto `f787a431c934a0b5501826195af59830ec21022c` da branch `main`, após `git fetch origin main`. A comparação foi feita diretamente com `origin/main`, sem modificar a branch ou os arquivos de trabalho do Budget.

O snapshot anterior era `944bd13`. Foram adicionados três commits:

| Commit | Alteração |
|---|---|
| `08bdea7` | Workflow de validação AWS. |
| `592f13f` | Workflow, documentação e overlay de deploy Academy. |
| `f787a43` | Suporte de armazenamento para EKS Auto Mode. |

As árvores Git de `src/` e `tests/` são idênticas entre os dois snapshots. Também não houve mudança em `docs/rabbitmq.md`, nas definições locais RabbitMQ ou no ConfigMap base do Budget. Isso inclui WireContracts, RabbitOptions, RabbitTopology, publicação, consumo, DLQ, replay e eventos de integração.

## Contratos preservados

| Item | Valor |
|---|---|
| Comandos | GenerateBudget, RequestPayment, CancelBudget, RefundPayment |
| Exchange de comandos | `gearflow.commands.v1` — topic |
| Routing keys | `billing.<Comando>` |
| Fila de comandos | `gearflow.billing.commands.v1` |
| Eventos | BudgetGenerated, BudgetGenerationFailed, BudgetApproved, BudgetRejected, BudgetCanceled, PaymentRequested, PaymentConfirmed, PaymentFailed, PaymentRefunded |
| Exchange de eventos | `gearflow.events.v1` — topic |
| Routing keys / messageType de eventos | `gearflow-budget-service.<Evento>` |
| Binding de eventos | `gearflow-budget-service.*` |
| Assinatura declarada pelo Budget | `gearflow.os.billing.events.v1` |
| Exchange de falhas | `gearflow.deadletters.v1` — topic |
| DLQ | `gearflow.billing.commands.v1.dlq` |
| Formato | JSON camelCase, Content-Type `application/json`, envelope schemaVersion 1 |
| Identificadores | correlationId = serviceOrderId; propriedades AMQP MessageId/CorrelationId correspondem ao envelope |

Os payloads também permanecem iguais: cliente/itens/expiração em GenerateBudget; orçamento/valor/moeda em RequestPayment; orçamento/motivo em CancelBudget; orçamento/pagamento/motivo em RefundPayment. Os nove payloads de eventos não mudaram.

## O que mudou no deploy e requer atenção da equipe do Budget

Os novos `k8s/academy` e `k8s/academy-auto` incluem um RabbitMQ próprio no namespace `gearflow`. O overlay Academy usa porta 5672, TLS desabilitado, filas classic e ambiente Development; o workflow fornece usuário `gearflow`. O overlay Auto Mode herda essa configuração e altera o provisionamento dos volumes.

Esses overlays preparam um ambiente isolado do Budget. Seus nomes de mensagens são compatíveis, mas isso não os conecta automaticamente ao broker compartilhado.

Para integração no ambiente compartilhado, adequar no Budget:

1. Host `rabbitmq.messaging.svc.cluster.local`, porta 5672, vhost `gearflow`, TLS desabilitado.
2. Usuário `gearflow_budget`, senha individual e Secret no namespace da aplicação.
3. `RabbitMq__QueueType=quorum`, igual às filas já preparadas no infra-broker. O ConfigMap base usa quorum; o overlay Academy substitui por classic. Não aplicar esse override ao conectar no broker compartilhado.
4. Labels de acesso no namespace e template de Pods.
5. Retirar do caminho de deploy compartilhado a criação/espera pelo RabbitMQ próprio. O workflow Academy atual aguarda `statefulset/rabbitmq` em `gearflow`; o compartilhado pertence a `messaging` e é administrado pelo infra-broker.
6. Se mantiver o ambiente Production, adequar a validação que exige TLS fora de Development. Essa validação não foi alterada pelos commits recentes. Não mudar globalmente o ambiente apenas para contornar a exigência.

O vhost precisa ser explícito: ele não aparece no ConfigMap base nem nos patches Academy consultados, e `RabbitOptions` usa `/` como padrão. Não presumir que o usuário do broker isolado seja válido no compartilhado.

Essas alterações pertencem ao repositório da aplicação. O infra-broker não deve mudar sua topologia para reproduzir o broker isolado do overlay.

## Alterações e verificações desta revisão

- Atualizada a referência Budget em `rabbitmq/contracts.json` de `944bd13` para `f787a43`.
- Documentação de integração atualizada com as diferenças dos overlays novos.
- `sync_topology.py --check`: topologia e permissões geradas permanecem compatíveis.
- Testes locais do catálogo/ACL executados; não houve mudança no transporte que justificasse repetir os testes completos com Docker ou subir aplicações.
- Nenhum deploy, alteração AWS, credencial ou arquivo de trabalho do Budget foi modificado. Não foram reexecutados testes de negócio do Budget.
