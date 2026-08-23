data "aws_iam_role" "terraform_bootstrap" {
  name = "campaign-terraform-bootstrap"
}

data "aws_iam_policy_document" "terraform_bootstrap_route53" {
  statement {
    sid = "DiscoverHostedZoneByName"
    actions = [
      "route53:ListHostedZones",
      "route53:ListHostedZonesByName",
    ]
    resources = ["*"]
  }

  statement {
    sid = "ReadFursaClickHostedZone"
    actions = [
      "route53:GetHostedZone",
      "route53:ListResourceRecordSets",
      "route53:ListTagsForResource",
    ]
    resources = ["arn:aws:route53:::hostedzone/Z0068791177VM3T59WYDS"]
  }

  statement {
    sid       = "ChangeCampaignDnsRecords"
    actions   = ["route53:ChangeResourceRecordSets"]
    resources = ["arn:aws:route53:::hostedzone/Z0068791177VM3T59WYDS"]

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "route53:ChangeResourceRecordSetsNormalizedRecordNames"
      values = [
        "campaign-argocd.adan.fursa.click",
        "campaign-dev.adan.fursa.click",
        "campaign-grafana.adan.fursa.click",
        "campaign-prod.adan.fursa.click",
        "campaign-prometheus.adan.fursa.click",
      ]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "route53:ChangeResourceRecordSetsRecordTypes"
      values   = ["A"]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "route53:ChangeResourceRecordSetsActions"
      values   = ["CREATE", "UPSERT", "DELETE"]
    }
  }

  statement {
    sid       = "ReadRoute53ChangeStatus"
    actions   = ["route53:GetChange"]
    resources = ["arn:aws:route53:::change/*"]
  }
}

resource "aws_iam_role_policy" "terraform_bootstrap_route53" {
  name   = "campaign-cluster-route53-dns"
  role   = data.aws_iam_role.terraform_bootstrap.name
  policy = data.aws_iam_policy_document.terraform_bootstrap_route53.json
}
