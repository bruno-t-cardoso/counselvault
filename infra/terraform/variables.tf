variable "region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment, used in resource names."
  type        = string

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging or prod."
  }
}

variable "allowed_origins" {
  description = "Web origins allowed to upload to and download from the bucket with presigned URLs."
  type        = list(string)
}

variable "max_receive_count" {
  description = "How many times the worker may fail on an upload event before it moves to the dead-letter queue."
  type        = number
  default     = 5
}
