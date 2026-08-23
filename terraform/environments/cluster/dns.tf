data "aws_route53_zone" "fursa_click" {
  name         = "fursa.click."
  private_zone = false
}

locals {
  public_dns_names = toset([
    "campaign-dev.adan.fursa.click",
    "campaign-prod.adan.fursa.click",
    "campaign-grafana.adan.fursa.click",
    "campaign-argocd.adan.fursa.click",
    "campaign-prometheus.adan.fursa.click",
  ])
}

resource "aws_route53_record" "public" {
  for_each = local.public_dns_names

  zone_id = data.aws_route53_zone.fursa_click.zone_id
  name    = each.value
  type    = "A"

  alias {
    name                   = module.cluster.alb_dns_name
    zone_id                = module.cluster.alb_zone_id
    evaluate_target_health = true
  }
}
