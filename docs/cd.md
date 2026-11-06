# Deploy continuo do RabbitMQ — AWS Academy

O job de broker do coordenador GearFlow CD atualiza o RabbitMQ no cluster existente. Esse job nao executa Terraform, nao cria roles/cluster e nao recebe a senha administrativa. Antes dele, o workflow reutilizavel Cluster CD gera o plano de infraestrutura e, quando aprovado explicitamente, aplica as mudancas usando state HCP. Veja infra-cd.md e hcp-state.md.

## Configuracao na organizacao

Liberar o acesso abaixo somente aos repositorios que executarao deploy:

| Tipo | Nome | Valor/origem |
| --- | --- | --- |
| Variable | AWS_REGION | us-east-1 |
| Variable | EKS_CLUSTER_NAME | gearflow-fase4 |
| Variable | K8S_NAMESPACE | messaging |
| Secret | AWS_ACCESS_KEY_ID | aws_access_key_id da sessao Academy |
| Secret | AWS_SECRET_ACCESS_KEY | aws_secret_access_key da mesma sessao |
| Secret | AWS_SESSION_TOKEN | aws_session_token completo da mesma sessao |

Atualizar os tres secrets juntos quando a sessao expirar. Nao copiar o perfil local academy para o runner, nem versionar credenciais. O workflow verifica a conta Academy atual pelo allowed-account-ids; se a conta do laboratorio mudar, revisar esse limite explicitamente.

## Disparo e seguranca do codigo

- Automatico depois de Broker CI concluir com sucesso em um push na main deste repositorio.
- Manual: Actions > GearFlow CD > Run workflow, selecionando main. O coordenador exige Broker CI bem-sucedido para o mesmo SHA antes de prosseguir e valida o plano de infraestrutura antes do broker.
- PRs e forks nao executam CD nem recebem credenciais AWS. O checkout automatico usa exatamente o SHA aprovado pelo CI, sem acompanhar uma main que possa ter avancado.
- Actions fixadas por SHA; token GitHub com contents:read e actions:read; infraestrutura e broker serializados sob o mesmo coordenador, com cancel-in-progress=false.
- Commits na homolog passam por PR/merge para main antes do CD. O workflow precisa estar publicado na main para os disparos funcionarem.

## Acesso ao EKS

O runner e ubuntu-latest hospedado pelo GitHub. Nao criaremos um runner publico na maquina do operador nem outra EC2.

1. Prepara arquivo com o IPv4 /32 do runner e identidade/data de criacao do cluster, sem guardar CIDRs dos operadores nem credenciais.
2. Publica esse arquivo como artifact antes de alterar o endpoint.
3. Adiciona somente o /32 necessario, preservando as regras existentes e o endpoint privado. Se uma regra existente ja permitir a origem, nao assume propriedade dela.
4. Aguarda a atualizacao AWS e configura kubeconfig temporario.
5. Executa o deploy e, num passo always(), remove somente a regra temporaria que adicionou.
6. Atualiza o artifact com o resultado da limpeza. Retencao de um dia; nenhum state/plan Terraform, kubeconfig ou segredo e enviado.

Permissoes AWS utilizadas: sts:GetCallerIdentity, eks:DescribeCluster, eks:UpdateClusterConfig e eks:DescribeUpdate, alem do acesso Kubernetes da identidade Academy que criou o cluster. A simulacao IAM local retornou allowed para as chamadas EKS. Nao criamos uma nova role nem configuramos GitHub OIDC no Academy.

Nao executar Terraform ou alterar os CIDRs manualmente durante o CD. A alteracao temporaria pode aparecer como drift enquanto o job estiver rodando; nao adicionamos ignore_changes para esconder drift. O grupo de concorrencia vale para este repositorio: nao copiar essa abertura de endpoint para varios repositorios executando simultaneamente sem coordenacao adicional.

## O que o deploy faz

Requer bootstrap da etapa 3: namespace, RabbitMQ existente, Secrets rabbitmq-credentials e rabbitmq-tls, CA valida e quatro usuarios de servico ja criados. Destruir/recriar o cluster exige repetir esse bootstrap antes do CD. O pipeline para com erro se esses requisitos nao existirem; nao inventa nem troca senhas.

scripts/cd/deploy_broker.py confere o endpoint do contexto, valida os manifests no servidor e aplica k8s/. Aguarda o StatefulSet, importa definitions.json e verifica filas/exchanges (nomes, durabilidade, auto-delete, argumentos declarados e tipo de exchange), bindings e policies. Aplica e confere as permissoes dos quatro usuarios existentes e verifica processo/alarmes do broker.

Uma mudanca de imagem/configuracao pode recriar o unico Pod e interromper conexoes. Nao ha HA. O pipeline nao faz rollback automatico de topologia: isso poderia perder mensagens ou alterar dados. Recursos removidos de definitions.json nao sao excluidos automaticamente. Alteracoes incompativeis de filas exigem migracao planejada, mesmo depois da etapa 4.

O teste AMQP completo e de persistencia continua em scripts/Test-ClusterBroker.ps1. Ele nao roda automaticamente em cada CD porque cria clientes de teste e pode interromper conexoes quando recebe -RestartBroker. Health checks e verificacao declarativa do CD nao substituem integracao com os microsservicos.

## Falha e recuperacao do IP temporario

always() tenta limpar mesmo quando o deploy falha, mas encerramento abrupto do runner, cancelamento forcado ou expiracao das credenciais pode impedir a limpeza. O workflow nao garante revogacao nessas situacoes. Baixar o artifact endpoint-access do job afetado e usar uma sessao Academy renovada, antes que expire a retencao:

```powershell
$env:AWS_PROFILE='academy'
python scripts/cd/cluster_access.py close --cluster gearflow-fase4 --region us-east-1 --lease '.local/cd/endpoint-access.json'
```

Use o arquivo do job afetado, nao um arquivo antigo de outra execucao. O script preserva regras dos operadores e recusa alterar um cluster recriado com a mesma denominacao. Se a lista restante ficar vazia ou houver regra /0, ele exige revisao manual. Nao substituir a lista por 0.0.0.0/0 para resolver problemas de acesso.

## Validacao e pendencia de publicacao

- 20 testes locais passaram: preparacao sem mutacao, limpeza idempotente, preservacao de regras existentes/novas, falha parcial de API, cluster recriado, recusa de /0/lista vazia, topologia divergente e aprovacao Terraform.
- Os quatro workflows passaram por parser YAML. O CI passa a executar os testes Python sem credenciais AWS.
- O script de deploy foi executado no EKS atual e passou: server dry-run, apply, rollout, importacao e verificacao de topologia/permissoes e health checks. O teste local de acesso aproveitou o IP ja autorizado, sem adicionar/remover CIDR do operador.
- Ainda falta publicar o workflow na main e observar uma execucao real do GitHub. Isso comprovara os secrets/variables da organizacao e a abertura/remocao do IP novo do runner em runtime. Simulacao IAM e teste local nao comprovam essa etapa remota.

Referencias: [endpoint EKS](https://docs.aws.amazon.com/eks/latest/APIReference/API_UpdateClusterConfig.html), [credenciais temporarias no Actions](https://github.com/aws-actions/configure-aws-credentials), [secrets de organizacao](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).
