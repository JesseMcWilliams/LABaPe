terraform {
  required_providers {
    hyperv = {
      source  = "taliesins/hyperv"
      version = "~> 1.2"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }
}
