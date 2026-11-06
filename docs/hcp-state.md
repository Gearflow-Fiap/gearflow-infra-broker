# State compartilhado no HCP Terraform

- Organizacao: gearflowfiapmurilo.
- Workspace: gearflow-fase4-cluster.
- Workflow: CLI-driven; Execution Mode: local.
- Conexao declarada em infra/cluster/backend.tf com cloud block, sem tokens no codigo.

Workspace: https://app.terraform.io/app/gearflowfiapmurilo/workspaces/gearflow-fase4-cluster

## Migracao concluida

O workspace foi confirmado vazio, em modo local, sem run em andamento e desbloqueado. A autenticacao local existente do Terraform foi utilizada sem exibir o token. Backup do state original preservado em .local/state-backups antes de confirmar a migracao pelo terraform init.

O state remoto possui os mesmos 12 recursos gerenciados. Todos os atributos de resources (incluindo dados e IDs) e os outputs foram comparados integralmente ao backup e permaneceram identicos. O HCP iniciou a linhagem/serial do novo snapshot; essa metadata difere do antigo arquivo local. Terraform validate passou e workspace show retornou gearflow-fase4-cluster. Nenhum plan/apply ou mudanca de recurso AWS foi executado nessa migracao.

Nao executar novamente uma migracao automatica por cima deste state. Arquivos antigos de plan foram gerados com o backend anterior: gerar um plano novo antes de aplicar. Nao usar snapshots locais antigos como se fossem o state corrente.

## Operacao local

```powershell
terraform login
$env:AWS_PROFILE='academy'
terraform '-chdir=infra/cluster' init
terraform '-chdir=infra/cluster' plan '-var-file=academy.tfvars.json' '-out=cluster.tfplan'
```

O token HCP autoriza acesso ao state; as credenciais AWS autorizam consultar/alterar a infraestrutura. Uma autenticacao nao substitui a outra. Compartilhar o workspace com os integrantes autorizados e usar locking; nao criar states independentes para o mesmo cluster.

## GitHub — proxima configuracao

Criar um token HCP para o CI na conta autorizada a ler/escrever e bloquear o workspace. Cadastrar em Organization Settings > Secrets and variables > Actions, com nome **TF_API_TOKEN**, liberando somente gearflow-infra-broker. O valor e o token completo, sem aspas; nao enviar no chat. O workflow podera mapea-lo para TF_TOKEN_app_terraform_io ou usa-lo no hashicorp/setup-terraform.

Os tres secrets AWS existentes continuam sendo necessarios e expirando conforme a sessao Academy. Variables da AWS e nome de cluster nao substituem o token HCP. O workspace em modo local nao injeta suas variables nos processos do GitHub; os inputs Terraform tambem precisam ser fornecidos pelo pipeline.

Cluster CI continua com init -backend=false: valida sintaxe/provider em PRs sem autenticar no HCP ou acessar o state. O pipeline reutilizavel Cluster CD foi preparado com autenticacao HCP e e chamado pelo coordenador GearFlow CD, junto do broker. O usuario informou o cadastro de TF_API_TOKEN e TF_CLUSTER_CONFIG_JSON. Veja infra-cd.md para plan, aprovacao manual e limitacoes. Nao colocar state, plans ou tokens como artefatos de PR publico.
