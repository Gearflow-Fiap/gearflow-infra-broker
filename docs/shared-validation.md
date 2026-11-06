# Preparação do ambiente compartilhado — 04/11/2026

Status: configuração preparada e validada localmente; nenhum apply/deploy AWS nesta etapa.

## Imagem

Compose e StatefulSet usam `rabbitmq:4.2.9-management@sha256:4e1c1636957aa13c2dbeb8c92606891ac0c396999bb4d7c3a7e3ba38430df7fa`.

Digest do índice OCI e suporte linux/amd64 conferidos no registro com `docker buildx imagetools inspect`; imagem baixada para validação. A referência vem da [imagem oficial Docker](https://github.com/docker-library/official-images/blob/master/library/rabbitmq). Atualizações exigem mudar a referência nos dois arquivos e repetir os testes.

## Resultados locais

- Parse PowerShell e renderização `kubectl kustomize k8s`: passaram.
- Terraform `fmt -check` e `validate`: passaram com o provider travado no lock file. Não foi gerado plano novo nem consultada a conta AWS nesta etapa.
- Compose com nome de projeto, credenciais e portas exclusivos para o teste: startup repetido, roteamento, leitura, persistência após reinício e DLQ passaram. O volume e o projeto descartáveis foram removidos ao finalizar; o Compose de desenvolvimento não foi reiniciado.
- `Test-SharedBroker.ps1`: quatro usuários criados sem tags, ACL conferida na API, acesso à API administrativa negado aos usuários de serviço, execução repetida sem trocar senhas, handshake TLS com CA/hostname corretos, rejeição de CA/hostname incorretos e reinício com a configuração exata do Kubernetes passaram. Listener 5672 ausente e painel em 127.0.0.1. Container e volume exclusivos removidos ao finalizar.
- Arquivos locais de TLS, credenciais, tfvars, state e plano continuam ignorados pelo Git.

## Limites e próxima etapa

Ainda não foram validados: cliente AMQP real e suas negações de operações, Secret/PVC no EKS, persistência em EBS, enforcement da NetworkPolicy e integração dos microsserviços. Os testes de mensagens existentes usam HTTP de gerenciamento. A verificação de ACL confirma regras cadastradas e convenções de nomes, sem substituir testes de autorização AMQP.

O CI foi atualizado para executar renderização Kustomize e o teste compartilhado; a execução no GitHub depende de publicar as alterações e não foi confirmada aqui.

O plano Terraform salvo de 11 recursos está desatualizado. O código agora adiciona VPC CNI gerenciado com NetworkPolicy habilitada. Revalidar sessão Academy, IP, versão/schema do addon e recursos da conta, gerar e revisar novo plano antes de pedir autorização para provisionar. O laboratório continua com um nó RabbitMQ, sem alta disponibilidade e sem CD.
