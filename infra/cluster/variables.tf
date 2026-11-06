variable "aws_region" {
  type    = string
  default = "us-east-1"
}
variable "cluster_name" {
  type    = string
  default = "gearflow-fase4"
}
variable "kubernetes_version" {
  type        = string
  description = "Versao EKS com suporte standard disponivel na regiao, confirmada antes do plan."
}
variable "cluster_role_arn" {
  type        = string
  description = "Role existente permitida no Academy, com trust eks.amazonaws.com e permissoes de cluster."
}
variable "node_role_arn" {
  type        = string
  description = "Role existente com trust ec2.amazonaws.com e permissoes EKS worker/ECR/CNI."
}
variable "admin_cidrs" {
  type        = list(string)
  description = "IPs publicos autorizados a acessar a API Kubernetes (ex.: IP/32)."
  validation {
    condition     = length(var.admin_cidrs) > 0 && alltrue([for cidr in var.admin_cidrs : can(cidrnetmask(cidr)) && cidr != "0.0.0.0/0"])
    error_message = "Informe CIDRs IPv4 restritos; nao use 0.0.0.0/0."
  }
}
variable "availability_zones" {
  type        = list(string)
  description = "Duas AZs distintas permitidas na regiao."
  validation {
    condition     = length(var.availability_zones) == 2 && length(distinct(var.availability_zones)) == 2
    error_message = "Informe exatamente duas AZs distintas."
  }
}
variable "node_instance_type" {
  type    = string
  default = "t3.medium"
}
variable "enable_ebs_csi" {
  type        = bool
  default     = false
  description = "Ativar somente apos verificar permissoes IAM do driver."
}
variable "ebs_csi_role_arn" {
  type        = string
  default     = null
  description = "Role IRSA existente, com trust OIDC do cluster; null usa a cadeia de credenciais do node, que deve ter permissoes EBS."
}
variable "ebs_csi_version" {
  type        = string
  default     = null
  description = "Versao do addon compativel com o Kubernetes escolhido."
}
