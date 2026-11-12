output "bucket_name" {
  description = "Set as S3_BUCKET for the application."
  value       = aws_s3_bucket.documents.id
}

output "uploads_queue_name" {
  description = "Set as UPLOAD_EVENTS_QUEUE for the application."
  value       = aws_sqs_queue.uploads.name
}

output "dead_letter_queue_name" {
  description = "Upload events the worker failed on repeatedly end up here."
  value       = aws_sqs_queue.uploads_dead_letter.name
}

output "kms_key_arn" {
  value = aws_kms_key.documents.arn
}

output "app_policy_arn" {
  description = "Attach to the role the API and worker run as."
  value       = aws_iam_policy.app.arn
}
