# Etapa 3 — RabbitMQ no EKS

Implantacao e validacao concluidas em 05/11/2026 no cluster gearflow-fase4, us-east-1. Sem implantacao dos microsservicos ou aprovacao de contratos de negocio nesta etapa.

## Estado implantado

- StatefulSet RabbitMQ com uma replica Ready, imagem 4.2.9-management fixada por digest.
- PVC data-rabbitmq-0 Bound: EBS gp3 de 10 GiB, criptografado, reclaimPolicy Retain. Inventario do PV/volume/zona salvo em .local/rabbitmq-volume.json, fora do Git.
- Endpoint interno rabbitmq.messaging.svc.cluster.local:5671, vhost gearflow, TLS 1.2/1.3; porta 5672 desabilitada.
- Painel e Prometheus apenas no loopback do Pod. Service ClusterIP sem LoadBalancer/Ingress ou porta administrativa.
- Namespace messaging, NetworkPolicy selecionando broker e permitindo AMQPS somente quando namespace e Pod cliente possuem as labels de acesso.
- Definicoes importadas: gearflow.events, gearflow.dead-letter, fila Budget e DLQ provisorias.
- Usuarios gearflow_os, gearflow_budget, gearflow_operation e gearflow_inventory criados com ACL do repositorio e sem tags administrativas. Administrador separado.

Credenciais administrativas em .local/cluster-admin.json; credenciais dos servicos em .local/service-credentials.json; CA/certificado/chave em .local/tls. Nao exibir senhas, versionar esses arquivos ou entregar a chave privada/conta administrativa aos servicos. Distribuir somente a CA publica e a credencial individual correspondente.

## Validacao real

Jobs Kubernetes usaram Python com cliente AMQP Pika 1.3.2 e filas sinteticas com identificador exclusivo. Nao utilizaram a fila de negocio para mensagens de teste.

1. Cada um dos quatro usuarios publicou em gearflow.events usando publisher confirms, mandatory e delivery_mode=2, consumiu sua fila e confirmou com ack.
2. Operacoes AMQP em filas de outro servico, DLQs, declaracao de recursos e publicacao pela exchange padrao foram rejeitadas com ACCESS_REFUSED (403).
3. TLS com CA/hostname corretos funcionou; CA desconhecida e hostname incorreto foram rejeitados. Senha incorreta rejeitada pelo broker.
4. Portas 5672 e 15672 inacessiveis pela rede de aplicacoes.
5. Origem com ambas as labels conectou; Job sem label de Pod e Job em namespace sem label foram bloqueados na porta 5671.
6. Mensagem rejeitada por basic_nack(requeue=False) chegou a DLQ sintetica; conteudo conferido pelo operador via API administrativa.
7. Mensagem persistente publicada com confirm; Pod RabbitMQ excluido controladamente e recriado com UID diferente e mesmo PV; mensagem recuperada e confirmada depois do restart.
8. Filas sinteticas, namespaces de teste, Jobs, ConfigMaps e Secret temporario removidos. Restaram somente as duas filas provisorias, com zero mensagens/consumidores na conferencia final.

A readiness inicial falhava porque check_port_connectivity tentava acessar por hostname os listeners administrativos restritos a 127.0.0.1. Foi corrigida para check_running e check_local_alarms; a recriacao controlada aplicou a nova sonda, e o Pod ficou Ready. Liveness e startup continuam configuradas.

## Repeticao e operacao

scripts/Test-ClusterBroker.ps1 contem o procedimento; k8s/tests/amqp-smoke.py contem os testes AMQP. O script usa kubeconfig e credenciais locais desta instalacao; requer registro de imagens e PyPI para preparar o cliente. Sem -RestartBroker, nao recria o broker; com esse parametro testa persistencia e interrompe conexoes, portanto exige janela sem consumidores. Em falha, preserva os recursos temporarios para diagnostico e impede reutilizar namespaces existentes sem revisao.

Para abrir o painel, manter o comando abaixo em terminal proprio, na raiz do repositorio:

```powershell
kubectl --kubeconfig .local/kubeconfig -n messaging port-forward pod/rabbitmq-0 15673:15672 --address 127.0.0.1
```

Acessar http://localhost:15673 com o administrador local. O tunel termina quando o Pod e recriado e precisa ser aberto novamente.

## Limites e encerramento

No unico sem HA; volume restrito a uma zona. Testes sinteticos nao medem capacidade dos quatro servicos nem validam Outbox, idempotencia de negocio ou Saga. Publicacao na exchange comum ainda permite qualquer routing key. Contratos, retentativas/reprocessamento, integracao real, CD, monitoramento e backend remoto seguem pendentes.

Cluster e EBS permanecem consumindo creditos. Destroy-Cluster.ps1 bloqueia enquanto houver PVC/PV. O EBS Retain exige revisao de dados, eventual backup, parada do broker e exclusao explicita do volume desanexado, com ID conferido; destruir o cluster nao garante remover esse disco. O script de destruicao foi ajustado para mostrar o progresso do Terraform em tempo real, corrigindo o silencio observado na execucao anterior.
