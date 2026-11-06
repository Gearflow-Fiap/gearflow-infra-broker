# Pipeline do EKS e coordenacao com o RabbitMQ

## Fluxo preparado

GearFlow CD (.github/workflows/broker-cd.yml) coordena a infraestrutura e o broker. Cluster CD (.github/workflows/cluster-cd.yml) e reutilizavel e so e chamado pelo coordenador. Todos os jobs desse ambiente usam uma unica fila de concorrencia no coordenador, com cancel-in-progress=false.

1. Broker CI precisa ter passado para o SHA exato da main. PRs/forks executam validacao sem credenciais e nao executam CD.
2. Cluster CD inicializa o HCP e gera plan real com AWS Academy.
3. Sem mudancas de infraestrutura: segue para o job de deploy/verificacao do RabbitMQ.
4. Com mudancas: mostra resumo e identificador do plano e encerra sem apply; o job do RabbitMQ fica pulado.
5. Um operador revisa e dispara GearFlow CD manualmente com operation=apply, identificador revisado e confirm_cluster=gearflow-fase4. O pipeline gera novo plano e so aplica se corresponder ao conteudo/inputs/commit revisados.
6. Depois de um apply bem-sucedido, segue para o RabbitMQ. Falha de infraestrutura impede esse deploy.

O disparo manual com identificador e nome do cluster e a aprovacao deste fluxo. Nao depende de um Environment criado sem regras, nem promete aprovacao por um segundo integrante. Branch protection/review de PRs pode complementar a governanca, mas nao e configurada por estes arquivos.

## Secrets e variables da organizacao

Variables existentes: AWS_REGION=us-east-1, EKS_CLUSTER_NAME=gearflow-fase4, K8S_NAMESPACE=messaging.

Secrets, liberados somente para gearflow-infra-broker:

- AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY e AWS_SESSION_TOKEN: mesma sessao Academy, atualizados juntos.
- TF_API_TOKEN: token HCP autorizado a ler/escrever/bloquear o workspace.
- TF_CLUSTER_CONFIG_JSON: conteudo do arquivo local .local/github-terraform-config.json. Contem os parametros de academy.tfvars.json (roles existentes, CIDRs do operador, versao, nodes e addons), sem chaves AWS. Nao versionar nem imprimir esse JSON.

O usuario informou o cadastro de ambos os secrets Terraform; o consumo pelo GitHub ainda depende da publicacao e primeira execucao. A conta e o cluster esperados sao fixados nos controles; trocar de conta/ambiente exige revisao explicita, nao apenas substituir um secret.

O secret de configuracao e gravado temporariamente no runner, usado com -var-file e removido ao terminar. Alteracoes nele mudam o identificador do plano e exigem nova revisao. O CIDR do operador deve permanecer atualizado; nao salvar o IP efemero do runner como admin_cidrs permanente.

## Como revisar e aplicar

Na main, depois do CI:

1. Abrir Actions > GearFlow CD e conferir o resumo do plano: SHA do commit, recursos/acoes, nomes dos atributos alterados e identificador SHA256.
2. Conferir as alteracoes de codigo e configuracao. Por privacidade, valores completos do plano nao sao publicados no repositorio publico. Para inspeciona-los, executar plan na maquina autorizada, usando o mesmo codigo e configuracao; nao copiar state/plan para comentarios ou artifacts publicos.
3. Em Run workflow, selecionar main, operation=apply, colar expected_plan_sha e digitar gearflow-fase4 em confirm_cluster.
4. Se houver delete ou substituicao, revisar impacto/dados e marcar allow_destructive explicitamente. Sem isso, o script bloqueia.
5. Acompanhar o progresso Terraform, atualizado em tempo real, e depois o deploy do RabbitMQ.

Se o commit, inputs, drift ou acoes mudarem entre revisao e apply, o script recusa. Execute plan novamente e revise o novo identificador. Isso evita armazenar um arquivo binario de plano sensivel entre jobs de um repositorio publico. O apply usa o arquivo exato gerado e conferido dentro do mesmo job; a verificacao normal de state/locking do Terraform permanece ativa.

Nao ha modo destroy neste pipeline. O encerramento do laboratorio continua pelo script Destroy-Cluster.ps1, incluindo a revisao de PVC/PV e EBS Retain. A permissao allow_destructive cobre exclusoes/substituicoes de um plano revisado, nao ignora essa revisao nem executa terraform destroy.

## Limites

- Nao executar Terraform local nem alterar o endpoint enquanto o coordenador estiver em execucao. O grupo GitHub coordena este repositorio; outros repositorios/operadores precisam de coordenacao adicional. O locking do HCP protege o state, mas nao substitui a coordenacao com o job Kubernetes.
- O primeiro bootstrap de um cluster novo ainda exige Secrets TLS/admin e usuarios do RabbitMQ conforme etapa 3. Recriar EKS pelo pipeline nao inventa essas credenciais; o job do broker falha de forma explicita se elas faltarem.
- Apos um apply longo, a sessao Academy pode expirar antes do broker/limpeza do endpoint; atualizar secrets e diagnosticar. A recuperacao do /32 esta em cd.md.
- Nenhum state, plan binario/JSON completo ou configuracao privada Terraform e enviado como artifact. O resumo inclui somente metadados de revisao e hash; o artifact de recuperacao de endpoint nao contem o state Terraform.

## Verificacao realizada

20 testes locais passaram, incluindo aprovacao vinculada ao commit/inputs, recusa de substituicao sem permissao, confirmacao de cluster, redacao de diagnosticos e limpeza de acesso. Os quatro workflows foram lidos por parser YAML. A action setup-terraform foi fixada no SHA oficial v3, Terraform 1.15.8, provider conforme lock file.

O script terraform_cluster.py foi executado com HCP remoto e AWS reais em modo plan: nenhuma mudanca de infraestrutura. Plano e inputs temporarios foram removidos. Nao foi executado apply nessa validacao. Ainda falta publicar na main e validar o coordenador dentro do GitHub Actions, com os secrets da organizacao.
