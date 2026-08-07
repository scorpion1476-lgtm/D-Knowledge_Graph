terraform {
  required_version = ">= 1.0"
}

provider "aws" {
  region = "us-east-1"
}

variable "bucket_name" {
  type = string
}

resource "aws_s3_bucket" "data" {
  bucket = var.bucket_name
}

resource "aws_s3_bucket_policy" "data_policy" {
  bucket = aws_s3_bucket.data.id
}

data "aws_caller_identity" "current" {}

module "network" {
  source = "./modules/network"
  cidr   = var.bucket_name
}

output "bucket_arn" {
  value = aws_s3_bucket.data.arn
}
