# Plano preparado em 03/11/2026 — nao aplicado

> **DESATUALIZADO em 04/11/2026. Nao aplicar o `cluster.tfplan` antigo.** A configuracao agora inclui addon VPC CNI gerenciado com NetworkPolicy habilitada, antes dos workers. Gerar e revisar novo plano, revalidando sessao Academy, IP e ambiente. O resultado de 11 recursos abaixo e apenas historico, nao corresponde mais ao codigo atual.

Resultado: **11 recursos a criar, 0 a alterar, 0 a destruir**.

## Configuracao local selecionada

- Regiao us-east-1; EKS 1.35, retornado em suporte standard pela API.
- Nome gearflow-fase4; rede 10.40.0.0/16 sem sobreposicao com a VPC retornada na consulta.
- Duas subnets publicas em us-east-1a/us-east-1b, Internet Gateway e roteamento.
- Role preexistente LabEksClusterRole para o control plane; LabRole para workers.
- Dois workers On-Demand t3.medium, discos gp3 criptografados de 20 GiB cada.
- Launch template com IMDSv2 obrigatorio e hop limit 2 para permitir acesso do controller EBS as credenciais dos nodes.
- Addon aws-ebs-csi-driver v1.67.2-eksbuild.1, retornado como default compativel com 1.35.
- Endpoint privado e endpoint publico restrito ao IPv4 atual do operador (/32).

Os 11 recursos Terraform incluem node group e launch template; instancias e discos gerenciados pelo EKS nao aparecem como recursos Terraform separados. O disco de 10 GiB do RabbitMQ sera provisionado posteriormente pelo PVC, nao por este plano.

## Evidencias e limites

Simulacao IAM retornou allowed para operacoes EBS consultadas na LabRole, operacoes de rede selecionadas, acesso ECR e PassRole para EC2. Isso nao garante permissoes efetivas de todas as chamadas durante apply nem funcionamento do driver.

Nenhuma instancia running/pending foi retornada na consulta inicial da regiao. Isso e uma fotografia, nao reserva de quota; revisar antes do apply e considerar capacidade temporaria em upgrades.

Usar a role do node disponibiliza permissoes amplas a Pods que alcancem IMDS. E uma adaptacao para laboratorio, nao isolamento por workload. Nao configurar LabRole como service_account_role_arn: nao foi validada como role IRSA.

## Arquivos locais

`academy.tfvars.json` e `cluster.tfplan` estao ignorados pelo Git. O JSON contem os ARNs reais e o CIDR do operador; nao contem chaves AWS. Perfil AWS usado: academy. State ainda local, com um unico operador; migrar para backend compartilhado antes do CD.

## Proxima etapa

Revisar este plano antes de provisionar: EKS, EC2, EBS e IPv4 publico consomem creditos. Apos criacao, verificar nodes Ready, addon ativo, PVC Bound e leitura/escrita num Pod de teste. Nao confundir terraform plan bem-sucedido com deploy validado.

Se a sessao expirar, atualizar o perfil academy. Se configuracao, IP ou ambiente mudarem, gerar novo plano. Nenhum apply foi executado nesta etapa.
