# Cluster compartilhado GearFlow — AWS Academy

Plano inicial preparado, ainda nao aplicado: [PLAN-REVIEW.md](PLAN-REVIEW.md). A configuracao local de teste seleciona LabRole nos nodes e driver EBS via IMDSv2, sem role IRSA. O launch template configura hop limit 2; Pods com acesso ao IMDS podem obter permissoes da role do node.

O plano salvo anteriormente ficou desatualizado: a configuracao agora inclui o addon `vpc-cni` com `enableNetworkPolicy=true`, antes dos workers. Gerar e revisar novo plano antes de qualquer apply. A versao default compativel do addon e consultada pela API durante o plan e fica registrada no plano; confirmar suporte a NetworkPolicy. Nenhum plano novo/apply foi executado nesta etapa.

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

## State e pipelines

Inicialmente state local, ignorado no Git: um unico operador deve executar Terraform e guardar o state com seguranca. Nao executar em maquinas diferentes com states independentes. Antes de CD/apply no GitHub, configurar backend remoto compartilhado com locking (S3 ou alternativa permitida pelo Academy) e migrar o state. Nunca colocar state/plans em Git.

`cluster-ci.yml` apenas fmt/init/validate, sem credenciais AWS e sem apply. Mudancas em `infra/cluster/**` disparam esse CI. O CI do broker e independente. Futuro CD de cluster deve usar plan revisado; CD do broker e dos servicos aponta ao cluster existente.

Credenciais Academy expiram. A estrategia de autenticacao do CD e acesso de rede ao endpoint restrito precisam ser definidas; runners publicos do GitHub nao estao automaticamente autorizados pelos CIDRs.

## Limpeza

Antes de destruir, remover recursos Kubernetes que criaram infraestrutura AWS e revisar volumes EBS retidos. PVCs do RabbitMQ usam Retain; destruir o cluster nao garante remover esses volumes. Planejar destruicao com o grupo, pois todos os servicos ficam indisponiveis. Nao ha destroy automatico.
