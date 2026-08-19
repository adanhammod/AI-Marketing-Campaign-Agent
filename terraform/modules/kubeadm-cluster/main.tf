variable "name" {
  type = string
}
variable "vpc_id" {
  type = string
}
variable "subnet_ids" {
  type = list(string)
}
variable "k8s_api_allowed_cidr" {
  type = string
}
variable "dev_alb_allowed_cidr" {
  type = string
}
variable "control_plane_instance_type" {
  type    = string
  default = "t3.medium"
}
variable "worker_instance_type" {
  type    = string
  default = "m7i.xlarge"
}
variable "aws_region" {
  type = string
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}
resource "aws_security_group" "nodes" {
  name   = "${var.name}-nodes"
  vpc_id = var.vpc_id
  ingress {
    from_port   = 6443
    to_port     = 6443
    protocol    = "tcp"
    cidr_blocks = [var.k8s_api_allowed_cidr]
  }
  ingress {
    from_port = 0
    to_port   = 0
    protocol  = "-1"
    self      = true
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
resource "aws_security_group" "alb" {
  name   = "${var.name}-alb"
  vpc_id = var.vpc_id
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = [var.dev_alb_allowed_cidr]
  }
  egress {
    from_port       = 30080
    to_port         = 30080
    protocol        = "tcp"
    security_groups = [aws_security_group.nodes.id]
  }
}
resource "aws_iam_role" "control_plane" {
  name               = "${var.name}-control-plane"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role" "worker" {
  name               = "${var.name}-worker"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy_attachment" "control_plane_ssm" {
  role       = aws_iam_role.control_plane.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_role_policy_attachment" "worker_ssm" {
  role       = aws_iam_role.worker.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_role_policy_attachment" "worker_ecr" {
  role       = aws_iam_role.worker.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}
resource "aws_ssm_parameter" "join_command" {
  name  = "/${var.name}/kubeadm/join-command"
  type  = "SecureString"
  value = "PENDING_CONTROL_PLANE_INITIALIZATION"

  lifecycle {
    ignore_changes = [value]
  }
}
resource "aws_iam_role_policy" "control_plane_join_command" {
  name = "${var.name}-control-plane-kubeadm-join"
  role = aws_iam_role.control_plane.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameter", "ssm:PutParameter"]
      Resource = aws_ssm_parameter.join_command.arn
    }]
  })
}
resource "aws_iam_role_policy" "worker_join_command" {
  name = "${var.name}-worker-kubeadm-join"
  role = aws_iam_role.worker.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameter"]
      Resource = aws_ssm_parameter.join_command.arn
    }]
  })
}
resource "aws_iam_instance_profile" "control_plane" {
  name = "${var.name}-control-plane"
  role = aws_iam_role.control_plane.name
}
resource "aws_iam_instance_profile" "worker" {
  name = "${var.name}-worker"
  role = aws_iam_role.worker.name
}
locals {
  template_vars = {
    aws_region          = var.aws_region
    join_parameter_name = aws_ssm_parameter.join_command.name
  }
  control_plane_user_data = templatefile("${path.module}/templates/control-plane-user-data.sh.tftpl", local.template_vars)
  worker_user_data        = templatefile("${path.module}/templates/worker-user-data.sh.tftpl", local.template_vars)
}
resource "aws_instance" "control" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.control_plane_instance_type
  subnet_id                   = var.subnet_ids[0]
  vpc_security_group_ids      = [aws_security_group.nodes.id]
  iam_instance_profile        = aws_iam_instance_profile.control_plane.name
  user_data                   = local.control_plane_user_data
  user_data_replace_on_change = true
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }
  root_block_device {
    volume_size = 40
    volume_type = "gp3"
    encrypted   = true
  }
  tags = {
    Name = "${var.name}-control-plane"
  }
}
resource "aws_launch_template" "worker" {
  name_prefix            = "${var.name}-worker-"
  image_id               = data.aws_ami.ubuntu.id
  instance_type          = var.worker_instance_type
  vpc_security_group_ids = [aws_security_group.nodes.id]
  user_data              = base64encode(local.worker_user_data)
  iam_instance_profile { name = aws_iam_instance_profile.worker.name }
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }
  block_device_mappings {
    device_name = "/dev/sda1"
    ebs {
      volume_size           = 100
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }
  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${var.name}-worker" }
  }
}
resource "aws_autoscaling_group" "worker" {
  name                = "${var.name}-worker"
  min_size            = 1
  max_size            = 1
  desired_capacity    = 1
  vpc_zone_identifier = var.subnet_ids
  target_group_arns   = [aws_lb_target_group.dev.arn]
  launch_template {
    id      = aws_launch_template.worker.id
    version = "$Latest"
  }
  depends_on = [aws_instance.control, aws_iam_role_policy.worker_join_command]
}
resource "aws_lb" "dev" {
  name               = substr("${var.name}-dev", 0, 32)
  load_balancer_type = "application"
  subnets            = var.subnet_ids
  security_groups    = [aws_security_group.alb.id]
}
resource "aws_lb_target_group" "dev" {
  name     = substr("${var.name}-dev", 0, 32)
  port     = 30080
  protocol = "HTTP"
  vpc_id   = var.vpc_id
  health_check {
    path    = "/healthz"
    matcher = "200"
  }
}
resource "aws_lb_listener" "dev" {
  load_balancer_arn = aws_lb.dev.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.dev.arn
  }
}
output "alb_dns_name" {
  value = aws_lb.dev.dns_name
}
output "control_plane_id" {
  value = aws_instance.control.id
}
output "worker_autoscaling_group_name" {
  value = aws_autoscaling_group.worker.name
}
output "worker_role_name" {
  value = aws_iam_role.worker.name
}
output "worker_role_arn" {
  value = aws_iam_role.worker.arn
}
