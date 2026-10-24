variable "region" {
  description = "OCI region (your home region: Always Free resources only exist there)."
  type        = string
}

variable "tenancy_ocid" {
  type = string
}

variable "user_ocid" {
  type = string
}

variable "fingerprint" {
  type = string
}

variable "private_key_path" {
  type = string
}

variable "compartment_ocid" {
  description = "Compartment for all resources (the tenancy root works)."
  type        = string
}

variable "ssh_public_key" {
  description = "Public key authorised for the ubuntu user."
  type        = string
}

variable "ssh_allowed_cidr" {
  description = "Source range allowed to SSH, e.g. your IP as 203.0.113.7/32. No default on purpose."
  type        = string

  validation {
    condition     = var.ssh_allowed_cidr != "0.0.0.0/0"
    error_message = "Do not open SSH to the whole internet; use your own IP range."
  }
}

variable "domain" {
  description = "Public host name pointing at the instance (e.g. finrag.duckdns.org); Caddy gets a Let's Encrypt certificate for it."
  type        = string
}

variable "repo_url" {
  type    = string
  default = "https://github.com/vasusadariya/finrag.git"
}

variable "repo_ref" {
  description = "Branch or tag to deploy."
  type        = string
  default     = "main"
}

variable "gemini_api_key" {
  type      = string
  sensitive = true
}

variable "demo_api_key" {
  description = "Shared secret the web proxy sends to the API (protects the free LLM quota)."
  type        = string
  sensitive   = true
}

variable "postgres_password" {
  type      = string
  sensitive = true
}

variable "ro_password" {
  description = "Password of the read-only database role used for model-written SQL."
  type        = string
  sensitive   = true
}

variable "edgar_user_agent" {
  description = "Name and email, required by SEC EDGAR."
  type        = string
}

variable "alert_email" {
  description = "Receives the budget alert."
  type        = string
}

variable "ocpus" {
  type    = number
  default = 2
}

variable "memory_gb" {
  description = "Always Free allows up to 24 GB across A1 instances; the embedder needs about 3."
  type        = number
  default     = 12
}
