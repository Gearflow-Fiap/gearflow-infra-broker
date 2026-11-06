output "cluster_name" {
  value = aws_eks_cluster.this.name
}
output "region" {
  value = var.aws_region
}
output "vpc_id" {
  value = aws_vpc.this.id
}
output "oidc_issuer" {
  value = aws_eks_cluster.this.identity[0].oidc[0].issuer
}
output "configure_kubectl" {
  value = "aws eks update-kubeconfig --region ${var.aws_region} --name ${aws_eks_cluster.this.name}"
}
