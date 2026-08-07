job "web" {
  datacenters = ["dc1"]
}

variable "image" {
  type = string
}

resource "docker_container" "web" {
  image = var.image
}
