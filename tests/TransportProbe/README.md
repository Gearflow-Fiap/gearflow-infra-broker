# Probe de transporte

Executado por `scripts/Test-SharedBroker.ps1`, contra RabbitMQ descartavel, com .NET 10 e MassTransit.RabbitMQ 8.5.11. Credenciais entram por stdin e pertencem somente ao teste.

`OperationMessages.cs` e `InventoryMessages.cs` sao copias dos contratos dos servicos, respectivamente `cbcb156` e `915ef73`, extraidas em 08/11/2026. O teste conserva os namespaces/URNs, campos, enums e conversores reais, sem depender dos outros repositorios no CI. Atualizar as copias quando esses contratos mudarem.

O probe declara endpoints como os servicos, monta envelopes de comando como o OS, usa publisher confirms e verifica rotas, desserializacao, erros/skipped/fault e nega acesso indevido. O Billing e representado por clientes RabbitMQ.Client e envelopes JSON; suas declaracoes de startup sao repetidas com QueueType=quorum, conforme seu manifesto Kubernetes.

Os handlers sao artificiais: nao ha banco de dados, EF Outbox/Inbox, MongoDB, pagamentos ou regras da Saga. Isso valida transporte e ACL, nao a integracao de negocio completa dos servicos.
