# Etapa 2 — EKS provisionado e validado

## Resultado

Apply do plano autorizado: **12 recursos criados, 0 alterados, 0 destruidos**, usando Terraform local e perfil AWS academy em us-east-1. Cluster gearflow-fase4, Kubernetes 1.35, dois workers t3.medium On-Demand com root gp3 criptografado de 20 GiB por node. State local preservado e ignorado pelo Git.

A primeira tentativa falhou por resolucao de DNS sem criar os recursos; conta/state foram conferidos e o plano regenerado com o mesmo escopo. A segunda tentativa concluiu. A criacao do control plane levou 17m56s; node group 1m59s. Nenhuma role nova foi criada.

## Evidencias

- Dois nodes Ready, versao v1.35.9-eks-6e7947a.
- VPC CNI v1.22.4-eksbuild.10 e aws-ebs-csi-driver v1.67.2-eksbuild.1: ACTIVE.
- Pods aws-node, CoreDNS, kube-proxy e EBS CSI Running/Ready.
- Agente aws-eks-nodeagent presente com --enable-network-policy=true.
- Namespace exclusivo gearflow-infra-validation; PVC gp3 criptografado de 1 GiB com reclaimPolicy Delete ficou Bound.
- Job ebs-write retornou WRITE_OK; depois de excluir esse Job/Pod, Job ebs-read montou o mesmo PVC e retornou READ_OK. Isso valida provisionamento, montagem e leitura/escrita reais com as permissoes do laboratorio.
- Namespace e StorageClass de teste removidos; aws ec2 wait volume-deleted confirmou exclusao do EBS temporario. ID de controle fica somente em .local/ebs-validation-volume.txt.

## Destruicao

scripts/Destroy-Cluster.ps1 passou no parse PowerShell, no bloqueio de nome de cluster incorreto antes de chamadas AWS e na previa real. A previa verificou conta/state, consultou os workloads do cluster e mostrou exatamente 12 recursos a excluir, sem executar a exclusao.

Por padrao o script apenas gera a previa. Execucao exige -Execute -ConfirmClusterName gearflow-fase4. Bloqueia se houver PVC, PV, Service LoadBalancer ou Ingress, para evitar descartar dados ou deixar infraestrutura dinamica sem revisao. Nao remove automaticamente volumes retidos sem PVC/PV nem recursos externos ao state. A destruicao real ainda nao foi testada, pois o ambiente permanece ativo para a etapa 3.

## Acesso e limites

Kubeconfig em .local/kubeconfig, contexto gearflow-fase4, usando perfil academy. Nao foi alterado o contexto global do operador. Configuracoes locais, kubeconfig, logs, planos e state permanecem fora do Git.

RabbitMQ, seu PVC de 10 GiB e os microsservicos nao foram implantados nesta etapa. NetworkPolicy esta habilitada no addon, mas os testes de bloqueio/permitir acesso ao broker dependem da etapa 3. CI/CD remoto e backend compartilhado continuam pendentes. O cluster usa LabRole nos nodes para o EBS CSI, adaptacao de laboratorio com permissoes amplas para Pods que alcancem IMDS.

EKS, dois workers, discos root e IPv4 publicos continuam consumindo creditos ate a remocao; encerrar a sessao do laboratorio nao garante encerrar todos os custos.
