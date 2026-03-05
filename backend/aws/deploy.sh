#!/usr/bin/env bash
# Bechtel Backend — Deploy to AWS ECS Fargate
# Usage: ./deploy.sh [--tag TAG]
#
# This script:
#   1. Zips the backend source code
#   2. Uploads it to S3
#   3. Triggers a CodeBuild build (builds Docker image & pushes to ECR)
#   4. Waits for the build to complete
#   5. Registers a new task definition
#   6. Creates or updates the ECS service

set -euo pipefail

# Configuration
AWS_REGION="us-east-1"
AWS_ACCOUNT_ID="736744502425"
ECR_REPO="bechtel-backend"
S3_BUCKET="pbichat-codebuild-source-736744502425"
S3_KEY="bechtel-source.zip"
CODEBUILD_PROJECT="bechtel-backend-build"
ECS_CLUSTER="pbichat"
ECS_SERVICE="bechtel-backend"
TASK_FAMILY="bechtel-backend"
IMAGE_TAG="${1:-latest}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"

echo "==> Packaging backend source..."
cd "$BACKEND_DIR"
python3 -c "
import zipfile, os
with zipfile.ZipFile('/tmp/bechtel-backend-source.zip', 'w', zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk('.'):
        dirs[:] = [d for d in dirs if d not in ('__pycache__', '.git', 'aws', 'venv', '.claude')]
        for f in files:
            if f in ('.env',) or f.endswith('.pyc'):
                continue
            zf.write(os.path.join(root, f))
"

echo "==> Uploading to S3..."
aws s3 cp /tmp/bechtel-backend-source.zip "s3://$S3_BUCKET/$S3_KEY" --region "$AWS_REGION"

echo "==> Starting CodeBuild..."
BUILD_ID=$(aws codebuild start-build \
  --project-name "$CODEBUILD_PROJECT" \
  --environment-variables-override "name=IMAGE_TAG,value=$IMAGE_TAG,type=PLAINTEXT" \
  --region "$AWS_REGION" \
  --query 'build.id' --output text)

echo "    Build ID: $BUILD_ID"
echo "==> Waiting for build to complete..."

while true; do
  STATUS=$(aws codebuild batch-get-builds --ids "$BUILD_ID" \
    --region "$AWS_REGION" --query 'builds[0].buildStatus' --output text)
  PHASE=$(aws codebuild batch-get-builds --ids "$BUILD_ID" \
    --region "$AWS_REGION" --query 'builds[0].currentPhase' --output text)
  echo "    Phase: $PHASE | Status: $STATUS"
  if [ "$STATUS" = "SUCCEEDED" ]; then
    break
  elif [ "$STATUS" = "FAILED" ] || [ "$STATUS" = "FAULT" ] || [ "$STATUS" = "STOPPED" ]; then
    echo "ERROR: Build $STATUS"
    exit 1
  fi
  sleep 10
done

echo "==> Registering task definition..."
TASK_DEF_ARN=$(aws ecs register-task-definition \
  --cli-input-json file://"$SCRIPT_DIR/task-definition.json" \
  --region "$AWS_REGION" \
  --query 'taskDefinition.taskDefinitionArn' --output text)
echo "    Task definition: $TASK_DEF_ARN"

echo "==> Checking if ECS service exists..."
SERVICE_STATUS=$(aws ecs describe-services --cluster "$ECS_CLUSTER" --services "$ECS_SERVICE" \
  --region "$AWS_REGION" --query 'services[0].status' --output text 2>/dev/null || echo "MISSING")

if [ "$SERVICE_STATUS" = "ACTIVE" ]; then
  echo "==> Updating existing ECS service..."
  aws ecs update-service \
    --cluster "$ECS_CLUSTER" \
    --service "$ECS_SERVICE" \
    --task-definition "$TASK_DEF_ARN" \
    --force-new-deployment \
    --region "$AWS_REGION" \
    --query 'service.{status:status,desired:desiredCount}' --output json
else
  echo "==> Creating new ECS service..."
  aws ecs create-service \
    --cluster "$ECS_CLUSTER" \
    --service-name "$ECS_SERVICE" \
    --task-definition "$TASK_DEF_ARN" \
    --desired-count 1 \
    --launch-type FARGATE \
    --network-configuration '{
      "awsvpcConfiguration": {
        "subnets": ["subnet-06628d9f62867aa31","subnet-0133cb44b8044ca7c","subnet-06afce4d6b18aafa1"],
        "securityGroups": ["sg-0649a2dbb29277d21"],
        "assignPublicIp": "ENABLED"
      }
    }' \
    --load-balancers '[{
      "targetGroupArn": "arn:aws:elasticloadbalancing:us-east-1:736744502425:targetgroup/bechtel-backend-tg/a875f8d99270804f",
      "containerName": "bechtel-backend",
      "containerPort": 8000
    }]' \
    --region "$AWS_REGION" \
    --query 'service.{status:status,desired:desiredCount}' --output json
fi

echo "==> Waiting for service to stabilize..."
aws ecs wait services-stable --cluster "$ECS_CLUSTER" --services "$ECS_SERVICE" --region "$AWS_REGION"

echo "==> Deployment complete!"
echo "    Endpoint: https://bechtel.gnosi.io/health"
curl -s https://bechtel.gnosi.io/health
echo ""
