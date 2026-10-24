# AWS deployment procedure — NOT yet executed

**Purpose:** execute an actual Quipu Vision OpenCV 5 image workload in AWS Lambda. The existing Linux `vision` target only proves local Docker operation. **Do not claim the AWS requirement is met until the Lambda request returns a completed A2A result.**

## Preconditions

- Use an AWS account that you are authorized and eligible to operate. Comply with the provider's account, payment and age requirements; if applicable, involve an authorized parent or guardian. Never post credentials, bills or account numbers in screenshots.
- AWS services can generate costs. Review your account's billing controls and budgets before deployment.
- AWS CLI v2, Docker Desktop Linux containers + Buildx. AWS role/user authorized for ECR, Lambda, IAM role creation and CloudFormation deployment.
- Root folder: `C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent`.
- The Dockerfile includes `vision_lambda` and Lambda Web Adapter. The CloudFormation template should be in `Quipu-Vision\integrations\aws\vision-lambda.yaml` or (if synchronized) `infra\aws\vision-lambda.yaml`.

## 1. Check the authenticated account (don't share its output)

```powershell
aws --version
aws sts get-caller-identity
$Region = 'us-east-1'  # Change to YOUR chosen supported region
$env:AWS_DEFAULT_REGION = $Region
```

## 2. Build the Lambda image and push it to Amazon ECR

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
$AccountId = (aws sts get-caller-identity --query Account --output text).Trim()
$Repo = 'quipu-vision-competition'
$Tag = 'submission-20261024'
$Registry = "$AccountId.dkr.ecr.$Region.amazonaws.com"
$ImageUri = "$Registry/${Repo}:$Tag"

# One-time repository creation; if it already exists, skip this call.
aws ecr create-repository --region $Region --repository-name $Repo --image-scanning-configuration scanOnPush=true

aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $Registry

docker buildx build --platform linux/amd64 --provenance=false --sbom=false --target vision_lambda --tag $ImageUri --load .
docker push $ImageUri
```

**Make sure you push the final intended source revision.** Lambda container images must reside in ECR in the same Region as the function. Avoid printing authorization tokens into logs.

## 3. Validate and deploy the CloudFormation stack

```powershell
$Template = '.\Quipu-Vision\integrations\aws\vision-lambda.yaml'
if (-not (Test-Path $Template)) { $Template = '.\infra\aws\vision-lambda.yaml' }
if (-not (Test-Path $Template)) { throw 'CloudFormation template missing' }
aws cloudformation validate-template --region $Region --template-body "file://$Template"

aws cloudformation deploy --region $Region `
  --template-file $Template `
  --stack-name quipu-vision-competition `
  --capabilities CAPABILITY_IAM `
  --parameter-overrides "VisionImageUri=$ImageUri"

aws cloudformation describe-stacks --region $Region `
  --stack-name quipu-vision-competition --query 'Stacks[0].StackStatus' --output text
```

The stack should report a successful deployment status before performing any requests. Verify that the function URL's authentication is **AWS_IAM**. That URL is NOT a publicly accessible link for judges.

## 4. Signed image-bearing A2A request (real proof)

Get the Function URL from CloudFormation output locally (do not paste credentials into chat):

```powershell
$FunctionUrl = (aws cloudformation describe-stacks --region $Region `
  --stack-name quipu-vision-competition `
  --query "Stacks[0].Outputs[?OutputKey=='VisionFunctionUrl'].OutputValue | [0]" `
  --output text).Trim()

python -m pip install boto3
$Images = '.\Quipu-Vision\examples\evaluation\images'
python .\quipu-vision-cierre-2026\scripts\aws_signed_probe.py --url $FunctionUrl --region $Region `
  --reference "$Images\base.png" --observed "$Images\object_a.png" `
  --save-json .\Quipu-Vision\outputs\aws-inspect-result.json

python .\quipu-vision-cierre-2026\scripts\aws_signed_probe.py --url $FunctionUrl --region $Region `
  --reference "$Images\base.png" --observed "$Images\object_a.png" `
  --followup "$Images\object_a_clone.png" `
  --save-json .\Quipu-Vision\outputs\aws-verify-result.json
```

**Note:** the probe path assumes you extracted the closeout-kit folder into `AegisForge_agent/`. If extracted elsewhere, use the actual path. These requests use the normal AWS credential provider chain and are signed with SigV4. `AWS_IAM` Function URL calls now require `lambda:InvokeFunctionUrl` **and** `lambda:InvokeFunction` for the caller; identity-based permissions and, for cross-account callers, resource-based permissions may both be needed. Never change an IAM URL to public access just to make browser testing easier.

The probe only prints `A2A VISION PROCESSING VERIFIED` if it sees a completed Quipu Vision artifact; otherwise it fails. If you get HTTP 403 inspect authorized IAM invocation permissions. If HTTP 502/503/504 inspect the Lambda Web Adapter startup and CloudWatch logs. Do not represent a successful stack creation or `/health` response as proof of OpenCV processing.

## 5. Evidence and judge access

Keep: Region, stack status, redacted function configuration, image digest, sanitized `aws-inspect-result.json` and `aws-verify-result.json`, CloudWatch request ID and relevant log line, end-to-end timing, exact Git SHA and image URI tag/digest (redact account number). **Do not include images without permission or secret values.**

For judges, use an explicitly authorized endpoint or a confirmed live screen-share. A localhost URL is not judge-accessible, and an IAM-protected URL without access credentials is not judge-accessible.

## 6. Resource cleanup (after confirming you no longer need the demo)

Cloud resources may incur charges. After the competition/demo and when no longer required, review resources and delete deliberately through AWS Console or CLI; do not delete an active judging endpoint prematurely. Example CloudFormation stack deletion, **only when explicitly intended**:

```powershell
# Optional, destructive; do not run while judging/demo access is needed.
# aws cloudformation delete-stack --region $Region --stack-name quipu-vision-competition
```

Official docs: https://docs.aws.amazon.com/lambda/latest/dg/images-create.html and https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html
