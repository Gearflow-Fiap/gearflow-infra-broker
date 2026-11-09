# Validação local — 28/10/2026

Registro histórico da topologia provisória. Em 08/11/2026, ela foi substituída pelos contratos dos quatro serviços. O teste atual verifica comandos Billing e sua publicação explícita de falha, em vez de DLX automática de OrderCreated. Veja [mensageria atual](messaging.md).

Executada com Docker Desktop, RabbitMQ do Compose e PowerShell.

## Resultados

- Container iniciou com healthcheck saudável.
- Definições iniciais importadas no vhost `gearflow`.
- Script `Test-Broker.ps1 -RestartBroker` concluiu com código de saída 0.
- Mensagem persistente publicada e roteada usando a topologia replicada em vhost temporário.
- Conteúdo e identificador recuperados após reinício controlado do broker.
- Rejeição sem requeue encaminhou a mensagem para a DLQ.
- Mensagem da DLQ consumida e vhost temporário removido.

## Reprodução

Siga a inicialização do README e execute:

```powershell
pwsh -File scripts/Test-Broker.ps1 -RestartBroker
```

## Limites

Teste de infraestrutura usando API HTTP de gerenciamento. Não demonstra integração AMQP real, recuperação após falha abrupta, alta disponibilidade, processamento de negócio, Outbox ou Saga. A persistência verificada corresponde a reinício controlado, com o volume existente.

Próxima etapa: acordar o payload de OrderCreated e integrar os produtores/consumidores de OS e Budget, com testes de indisponibilidade e duplicidade.
