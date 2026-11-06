# Plano revisado em 04/11/2026 — aplicado e validado

Resultado autorizado e aplicado: **12 recursos criados, 0 alterados, 0 destruidos**. Dois nodes Ready; addons VPC CNI e EBS CSI ACTIVE; teste de escrita/leitura em Pods diferentes passou. EBS temporario de 1 GiB excluido e confirmado pela AWS. Detalhes em ../../docs/cluster-validation.md.

## Escopo verificado

- Perfil academy com sessao valida; regiao us-east-1.
- Antes do apply, nenhum cluster EKS e nenhuma instancia pending/running retornados nas consultas. Depois do apply, gearflow-fase4 e seus dois workers estao ativos.
- VPC existente 172.31.0.0/16; nova rede 10.40.0.0/16 sem sobreposicao com ela.
- Cluster gearflow-fase4, EKS 1.35 em STANDARD_SUPPORT conforme API.
- Duas subnets publicas, Internet Gateway e rotas, sem NAT Gateway.
- Dois workers On-Demand t3.medium; root gp3 criptografado de 20 GiB por worker.
- Roles existentes LabEksClusterRole para control plane e LabRole para workers; nenhuma role nova.
- IMDSv2 obrigatorio com hop limit 2; EBS CSI usa credenciais dos nodes, sem IRSA.
- Endpoint privado e publico restrito ao IPv4 atual do operador (/32), revalidado sem alteracao.
- VPC CNI v1.22.4-eksbuild.10, default compativel consultado pela API; schema inclui enableNetworkPolicy. Plano habilita NetworkPolicy em modo standard antes dos workers.
- EBS CSI v1.67.2-eksbuild.1 conforme configuracao local.

Os 12 recursos Terraform incluem node group e launch template. Instancias e discos gerenciados nao aparecem individualmente na contagem. O plano substitui o anterior de 11 recursos.

## Validacao executada depois do apply

Nodes Ready e addons ativos confirmados; agente com --enable-network-policy=true. PVC gp3 temporario de 1 GiB ficou Bound, primeiro Job escreveu WRITE_OK e segundo leu READ_OK depois da remocao do primeiro. Namespace/StorageClass do teste removidos e exclusao do EBS confirmada pelo waiter da AWS.

RabbitMQ e seu PVC de 10 GiB ficam para a etapa 3 e nao serao implantados nesta etapa. Teste de rede das aplicacoes depende dessa implantacao posterior.

## Custos e limites

EKS, EC2, discos e IPv4 publicos consomem creditos, inclusive o EBS temporario de teste. O fim da sessao Academy nao garante o encerramento dos custos. Nao ha destroy automatico. Revisar quotas novamente se o ambiente mudar antes do apply.

Permissoes EBS da LabRole foram simuladas anteriormente; nao garantem que todas as chamadas do apply ou o driver funcionem em runtime. Pods que alcancem IMDS podem obter permissoes amplas do node. NetworkPolicy do broker sera aplicada na etapa 3; nao isola IAM dos demais workloads.

State local, um unico operador. Guardar com seguranca e migrar para backend remoto com locking antes de automatizar Terraform ou permitir varios operadores.

## Arquivos locais e verificacoes

academy.tfvars.json e cluster.tfplan permanecem ignorados pelo Git. O plano completo esta em arquivo local; seu log fica em .local/cluster-plan.log, tambem ignorado. Nao versionar esses arquivos nem state/credenciais.

Terraform fmt/validate, plan e apply passaram. A primeira tentativa de apply falhou por DNS sem criar os recursos; o plano foi regenerado e a segunda concluiu. Alteracoes futuras de codigo, IP, sessao ou estado da conta exigem novo plano. Nao reutilizar o plano ja aplicado. Previa do script Destroy-Cluster.ps1 validada com 12 exclusoes, sem executa-las.
