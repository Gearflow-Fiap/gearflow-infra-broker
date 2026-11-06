resource "aws_vpc" "this" {
  cidr_block           = "10.40.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = var.cluster_name }
}
resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
}
resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.this.id
  availability_zone       = var.availability_zones[count.index]
  cidr_block              = cidrsubnet(aws_vpc.this.cidr_block, 8, count.index)
  map_public_ip_on_launch = true
  tags = {
    Name                     = "${var.cluster_name}-public-${count.index + 1}"
    "kubernetes.io/role/elb" = "1"
  }
}
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
}
resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}
resource "aws_eks_cluster" "this" {
  name     = var.cluster_name
  role_arn = var.cluster_role_arn
  version  = var.kubernetes_version
  access_config {
    authentication_mode                         = "API_AND_CONFIG_MAP"
    bootstrap_cluster_creator_admin_permissions = true
  }
  vpc_config {
    subnet_ids              = aws_subnet.public[*].id
    endpoint_private_access = true
    endpoint_public_access  = true
    public_access_cidrs     = var.admin_cidrs
  }
  depends_on = [aws_route_table_association.public]
}
resource "aws_launch_template" "workers" {
  name_prefix = "${var.cluster_name}-workers-"
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }
  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = 20
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }
}
data "aws_eks_addon_version" "vpc_cni" {
  addon_name         = "vpc-cni"
  kubernetes_version = var.kubernetes_version
  most_recent        = false
}
resource "aws_eks_addon" "vpc_cni" {
  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = "vpc-cni"
  addon_version               = data.aws_eks_addon_version.vpc_cni.version
  resolve_conflicts_on_create = "OVERWRITE"
  configuration_values = jsonencode({
    enableNetworkPolicy = "true"
  })
}
resource "aws_eks_node_group" "this" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "shared-workers"
  node_role_arn   = var.node_role_arn
  subnet_ids      = aws_subnet.public[*].id
  instance_types  = [var.node_instance_type]
  ami_type        = "AL2023_x86_64_STANDARD"
  capacity_type   = "ON_DEMAND"
  version         = var.kubernetes_version
  launch_template {
    id      = aws_launch_template.workers.id
    version = tostring(aws_launch_template.workers.latest_version)
  }
  scaling_config {
    desired_size = 2
    min_size     = 1
    max_size     = 3
  }
  update_config {
    max_unavailable = 1
  }
  depends_on = [aws_eks_addon.vpc_cni]
}
resource "aws_eks_addon" "ebs" {
  count                    = var.enable_ebs_csi ? 1 : 0
  cluster_name             = aws_eks_cluster.this.name
  addon_name               = "aws-ebs-csi-driver"
  addon_version            = var.ebs_csi_version
  service_account_role_arn = var.ebs_csi_role_arn
  depends_on               = [aws_eks_node_group.this]
}
