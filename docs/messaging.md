# Convenções iniciais de mensageria

Status: proposta técnica inicial. Os contratos de negócio precisam ser acordados com os responsáveis pelos serviços.

## Topologia provisionada

| Recurso | Valor |
| --- | --- |
| Virtual host | `gearflow` |
| Exchange de eventos (topic) | `gearflow.events` |
| Routing key de OrderCreated | `os.order-created.v1` |
| Fila consumida pelo Budget | `gearflow.budget.order-created.v1` |
| Exchange de erro (direct) | `gearflow.dead-letter` |
| Fila de erro | `gearflow.budget.order-created.v1.dlq` |

Uma rejeição com `requeue=false` encaminha a mensagem à fila de erro conforme a policy. A DLQ não reprocessa mensagens automaticamente. Requeue repetido não é uma estratégia de retry limitado; essa política será definida na integração.

As filas classic e o nó único são uma base local, sem alta disponibilidade. A topologia de produção e eventual uso de quorum queues deverão ser avaliados antes do deploy. Esta configuração não oferece garantia de entrega exatamente uma vez nem garante ausência de perda em todas as falhas.

Cada serviço interessado em um evento deve ter sua própria fila. Réplicas do mesmo consumidor podem compartilhar a fila para distribuir trabalho.

## Envelope proposto

```json
{
  "messageId": "uuid-unico-da-mensagem",
  "eventType": "OrderCreated",
  "schemaVersion": 1,
  "occurredAt": "2026-10-28T12:00:00Z",
  "correlationId": "uuid-do-processo",
  "serviceOrderId": "uuid-da-os",
  "data": {}
}
```

O conteúdo de `data` ainda será acordado. A estrutura não é um contrato final dos serviços. Preserve o identificador ao reenviar a mesma mensagem e propague correlação entre eventos relacionados.

## Responsabilidades

- Infraestrutura: topologia declarada neste repositório, credenciais por ambiente e operação do broker.
- Produtor: Outbox consistente com a transação local, mensagens persistentes, publisher confirms e detecção de mensagens sem rota.
- Consumidor: validação de contrato, processamento idempotente e confirmação manual após persistir o resultado.
- Participantes da Saga: estados persistidos, ações, compensações, timeouts e correlação.

As aplicações devem utilizar a topologia acordada e não declarar recursos incompatíveis. Se uma biblioteca passar a administrar a topologia, revisar esta responsabilidade antes de integrá-la.

## Decisões pendentes

- Coreografia ou orquestração da Saga.
- Separação dos eventos de aprovação e pagamento.
- Reserva, consumo e liberação de estoque.
- Retentativas, prazos, mensagens atrasadas e compensações.
- Política de acesso por serviço e propagação de traces.

## Verificação manual local

Após importar as definições, abra a exchange `gearflow.events` no painel e publique uma mensagem de teste com routing key `os.order-created.v1`. Confira sua chegada à fila do Budget. Esse teste valida apenas roteamento, não processamento de negócio nem Saga. Use mensagens sintéticas e não as envie para um ambiente compartilhado com consumidores reais.
