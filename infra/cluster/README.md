# Cluster compartilhado GearFlow — AWS Academy

Cluster criado e validado: [resultado da etapa 2](../../docs/cluster-validation.md) e [revisao do plano](PLAN-REVIEW.md). A configuracao local de teste seleciona LabRole nos nodes e driver EBS via IMDSv2, sem role IRSA. O launch template configura hop limit 2; Pods com acesso ao IMDS podem obter permissoes da role do node.

Apply autorizado e concluido: 12 recursos criados. Dois workers Ready, VPC CNI v1.22.4-eksbuild.10 com enableNetworkPolicy=true e EBS CSI ACTIVE. Escrita/leitura em PVC funcionou; o EBS temporario foi removido. RabbitMQ implantado e validado na etapa 3, com PVC Retain de 10 GiB: veja ../../docs/broker-validation.md. Cluster e volume permanecem ativos e consumindo creditos.

Este diretorio cuida do EKS para OS, Budget, Operation, Inventory e RabbitMQ. `k8s/` na raiz continua responsavel apenas pelo broker. Deploy de uma aplicacao nao executa Terraform nem recria o cluster.

## Escopo e escolhas iniciais

- VPC dedicada 10.40.0.0/16 e duas subnets publicas em AZs distintas; verificar sobreposicao antes de conectar outras redes.
- Sem NAT Gateway para simplificar o laboratorio. Workers com IPv4 publico para saida; sem SSH habilitado pelo codigo. Isso nao publica automaticamente os servicos Kubernetes. Ha cobranca de IPv4 publico.
- EKS com endpoint privado e publico restrito aos CIDRs informados.
- Dois workers t3.medium iniciais e discos de 20 GiB. Capacidade deve ser medida com os servicos reais. Min/max nao implementam autoscaling: Cluster Autoscaler/Karpenter nao estao instalados.
- Roles IAM existentes fornecidas por variavel. Nao cria roles, policies ou assume que LabRole tenha todas as permissoes/trusts necessarios.
- Driver EBS CSI opcional e desabilitado ate validar IAM. RabbitMQ com PVC depende dele funcionando.
- VPC CNI gerenciado com NetworkPolicy habilitada em modo standard. Validar agente e testes de acesso permitido/negado no EKS; o modo standard pode permitir trafego brevemente no inicio de um Pod. Nao alterar para strict sem politicas para os outros workloads. O addon adota a instalacao inicial self-managed do cluster novo com resolve_conflicts_on_create=OVERWRITE; nao reutilizar isso para adotar cluster existente sem revisar sua configuracao.
- Nao implanta aplicacoes, bancos, gateway, observabilidade ou RabbitMQ.

## Antes de provisionar

Confirmar regiao/AZs permitidas, versao EKS em suporte standard e tipo de instancia disponivel. Validar roles de cluster e nodes (trust e policies), permissoes iam:PassRole e quotas. A role que criar o cluster recebe acesso administrativo inicial; colegas/pipelines precisam de acesso proprio posteriormente.

Para EBS: confirmar permissoes do driver via IRSA com OIDC existente (nao criado aqui) ou role dos nodes com permissoes verificadas. Nao basta instalar o addon. Se usar credenciais da role do node, revisar acesso do controller ao IMDS; nao presumir que o lab permita alterar IAM.

Nao colocar credenciais AWS no Git. Usar sessao temporaria do Academy na maquina, incluindo AWS_SESSION_TOKEN. Nao enviar chaves no chat.

## Preparacao e plano

```powershell
cd infra/cluster
Copy-Item academy.tfvars.example academy.tfvars
# Preencher valores reais em academy.tfvars.
terraform init
terraform fmt -check
terraform validate
terraform plan -var-file=academy.tfvars -out=cluster.tfplan
```

O exemplo contem placeholders e nao esta pronto para apply. O plan exige sessao AWS valida. Revisar custos e eventuais substituicoes antes de aplicar:

```powershell
terraform apply cluster.tfplan
terraform output configure_kubectl
```

Executar o comando de kubeconfig mostrado; confirmar contexto, nodes Ready e driver EBS antes de seguir o README de `k8s/`. EKS, nodes, discos e IPs geram custos. Nao assumir que encerrar a sessao do lab apaga todos os recursos.

O acesso usado nesta validacao fica em .local/kubeconfig, sem alterar o contexto global do operador. Na raiz do repositorio, para usar o mesmo contexto no terminal atual:

```powershell
$env:KUBECONFIG = (Resolve-Path '.local/kubeconfig').Path
kubectl config current-context
kubectl get nodes
```

Para gerar esse arquivo novamente, usar aws eks update-kubeconfig com --name gearflow-fase4 --region us-east-1 --profile academy --kubeconfig .local/kubeconfig --alias gearflow-fase4.

## Teste de armazenamento

Os manifestos em tests/ criam namespace exclusivo, StorageClass gp3 criptografado com reclaimPolicy Delete, PVC de 1 GiB e Jobs de escrita/leitura. Confirmar o contexto do cluster antes de executar. Esse teste tambem consome creditos temporariamente.

```powershell
# Na raiz do repositorio, com kubeconfig apontando ao gearflow-fase4:
kubectl apply -f infra/cluster/tests/ebs-write.yaml
kubectl -n gearflow-infra-validation wait --for=condition=complete job/ebs-write --timeout=300s
kubectl -n gearflow-infra-validation logs job/ebs-write
kubectl -n gearflow-infra-validation delete job ebs-write --wait=true
kubectl apply -f infra/cluster/tests/ebs-read.yaml
kubectl -n gearflow-infra-validation wait --for=condition=complete job/ebs-read --timeout=300s
kubectl -n gearflow-infra-validation logs job/ebs-read
```

Esperado: WRITE_OK e READ_OK em Pods diferentes, com o mesmo PVC. Antes da limpeza, registrar o volumeHandle do PV para confirmar depois que o EBS foi excluido. Remover somente o namespace gearflow-infra-validation e a StorageClass gearflow-validation-gp3 criados pelo teste. A policy Delete deste teste e diferente da policy Retain do RabbitMQ; nao reutilizar estes manifestos para dados de negocio.

## State e pipelines

Ao atualizar o provider/lock file, registrar os checksums das duas plataformas usadas pelo projeto antes de commitar:

```powershell
terraform '-chdir=infra/cluster' providers lock -platform=linux_amd64 -platform=windows_amd64
```

O CI e o CD usam init -lockfile=readonly para validar o lock versionado sem corrigi-lo silenciosamente no runner. O hash h1 de Windows nao valida o pacote extraido de Linux; os dois precisam estar registrados. A versao do provider continua fixada no lock file, sem upgrade automatico.

State migrado para HCP Terraform: organizacao gearflowfiapmurilo, workspace gearflow-fase4-cluster, Execution Mode local. O arquivo backend.tf conecta todos os operadores ao mesmo state remoto com locking. Local significa que plan/apply executam na maquina ou runner; o HCP armazena o state. Nao criar outro workspace/state para o mesmo cluster.

Na migracao, o workspace estava vazio. Foi preservado backup em .local/state-backups e comparado o conteudo completo de resources e outputs antes/depois: identico, com 12 instancias gerenciadas. O HCP iniciou nova linhagem/serial; os recursos AWS nao foram alterados. O Terraform validate passou com o novo backend. Backups locais nao sao mais a fonte de verdade; nao restaurar por cima do workspace sem revisao.

Novos operadores precisam de acesso ao workspace, terraform login e terraform init neste diretorio. A autenticacao HCP e separada do perfil AWS Academy. No GitHub, os Secrets TF_API_TOKEN e TF_CLUSTER_CONFIG_JSON fornecem acesso ao state e parametros do cluster, restritos ao repositorio de infraestrutura. Nao enviar esses valores pelo chat nem coloca-los no backend.tf. O coordenador GearFlow CD e o workflow reutilizavel Cluster CD estao preparados; falta a publicacao/primeira execucao remota. Detalhes em ../../docs/hcp-state.md e ../../docs/infra-cd.md.

O CD do RabbitMQ usa kubectl e nao acessa o state; veja ../../docs/cd.md. Nunca colocar state/plans em Git.

`cluster-ci.yml` apenas fmt/init/validate, sem credenciais AWS e sem apply. Mudancas em `infra/cluster/**` disparam esse CI. O CI do broker e independente. Futuro CD de cluster deve usar plan revisado; CD do broker e dos servicos aponta ao cluster existente.

Credenciais Academy expiram. A estrategia de autenticacao do CD e acesso de rede ao endpoint restrito precisam ser definidas; runners publicos do GitHub nao estao automaticamente autorizados pelos CIDRs.

## Limpeza

Antes de destruir, remover recursos Kubernetes que criaram infraestrutura AWS e revisar volumes EBS retidos. PVCs do RabbitMQ usam Retain; destruir o cluster nao garante remover esses volumes. Planejar destruicao com o grupo, pois todos os servicos ficam indisponiveis. Nao ha destroy automatico.

Na raiz do repositorio, o script abaixo usa o perfil academy e o state deste diretorio. Sem opcoes, apenas verifica o destino e gera uma previa, sem excluir recursos:

```powershell
pwsh -File scripts/Destroy-Cluster.ps1
```

Para excluir de fato, depois de revisar a previa e combinar a parada dos servicos:

```powershell
pwsh -File scripts/Destroy-Cluster.ps1 -Execute -ConfirmClusterName gearflow-fase4
```

O script valida conta, nome/regiao, recursos esperados no state e consulta o cluster por kubeconfig isolado. Bloqueia se encontrar PVCs, PVs, Services LoadBalancer ou Ingresses; nao apaga dados de aplicacoes automaticamente. Sem acesso Kubernetes, tambem bloqueia. Se o cluster nao existir (por exemplo, criacao parcial), ainda pode planejar/remover recursos remanescentes do state.

O plano de destruicao fica em .local/destroy/, ignorado pelo Git; o script gera um novo a cada execucao. Ele nao exclui roles preexistentes, credenciais, state local, nem recursos externos ao state. Volumes retidos cujo PVC/PV ja foi removido precisam ser identificados/revisados diretamente na AWS; nao ha garantia de zerar a conta inteira. Preserve o state se uma exclusao falhar e repita somente apos diagnosticar.
