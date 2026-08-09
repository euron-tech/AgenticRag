---
name: infra-ops
description: Provision, deploy, inspect, and destroy the AWS infrastructure for this project — Terraform in us-east-1, ECR image builds, CodeDeploy ECS blue/green releases, CloudWatch log and alarm inspection, and the one-command environment destroyer. Use whenever the task involves terraform, ECS, ECR, ALB, CodeDeploy, Secrets Manager, CloudWatch, GitHub Actions deploy workflows, or tearing an environment down.
---

# Infrastructure operations

Authoritative runbook for anything touching AWS in this project. Read this before running a
Terraform or AWS command, and before editing anything under `infra/` or `.github/workflows/`.

## Ground rules

- **Region is `us-east-1`.** Always. Check `AWS_REGION` before any CLI call.
- **Environment is always explicit.** Every command takes `ENV=dev` or `ENV=prod`. Never infer it
  from the current branch or the last command run.
- **Nothing is created by hand.** If it is not in Terraform, it will not be destroyed by the
  destroyer, and it will drift. Console clicks and one-off `aws ... create-*` calls are not allowed.
- **Read the plan.** `terraform plan` output gets read before every apply. Destroy plans get read
  twice.

## Preconditions

Before any AWS work, confirm in this order:

1. `aws sts get-caller-identity` returns the expected account. If credentials are missing or
   expired, ask the owner to run `aws login` themselves — do not attempt to authenticate for them.
2. `infra/terraform/bootstrap` has been applied (state bucket + DynamoDB lock table exist).
3. The environment's secrets exist in Secrets Manager. A deploy against missing secrets fails at
   task start with a confusing error — check first.

## Standard flows

### First-time setup for an environment
```
make bootstrap ENV=dev     # state bucket + lock table, run once per account
make secrets ENV=dev       # create/update Secrets Manager entries from prompts, never from files
make up ENV=dev            # terraform apply
```

### Deploying a change
```
make deploy ENV=dev
```
This builds both images, tags them with the commit SHA, pushes to ECR, renders the task
definitions, and triggers a CodeDeploy blue/green release. Smoke tests run against the **test
listener** before any production traffic shifts. Never tag or deploy `latest`.

### Inspecting a running environment
```
make logs ENV=dev SVC=api       # tail CloudWatch logs
make status ENV=dev             # service, task, target group health
make alarms ENV=dev             # current alarm states
```

### Destroying an environment
```
make destroy ENV=dev
make destroy ENV=prod CONFIRM=DESTROY-PROD
```
The destroyer prints the full resource list and requires the environment name to be typed back.
Prod additionally requires the confirmation string. **Never run a destroy without the owner
explicitly asking for it in the current conversation** — prior approval of a dev teardown is not
approval of a prod teardown.

Order matters on teardown: ECR images are force-deleted, CloudWatch log groups removed, ECS
services drained before the ALB goes. If Terraform hangs on the ALB, check for a lingering ENI
from a task that did not drain.

## Blue/green specifics

- Two target groups per service, plus a test listener on a separate port.
- CodeDeploy shifts traffic only after the smoke test passes against the test listener.
- CloudWatch alarms are wired as CodeDeploy rollback triggers. A deploy that trips an alarm during
  the bake window rolls back automatically — do not add a manual rollback path that competes with it.
- If a deployment is stuck `InProgress`, check the target group health first. The usual cause is a
  failing `/health` check, and the usual cause of that is a missing or wrong secret.

## Cost discipline

Dev deliberately runs without a NAT gateway — tasks sit in public subnets with public IPs. Do not
"fix" this by adding a NAT to dev; it is the single largest idle cost in a small environment and
dev has no requirement for it. Prod uses private subnets with NAT.

Keep ECR lifecycle policies in place (keep last 10 images). Untagged image sprawl is the second
most common surprise on the bill.

## When something is wrong

- Task starts then dies immediately → CloudWatch logs for the task, almost always config or secrets.
- ALB returns 503 → no healthy targets; check `/health` and the security group between ALB and task.
- Terraform state lock stuck → verify no other apply is running before force-unlocking, and say so.
- GitHub Actions cannot assume the role → the OIDC trust policy's `sub` condition does not match the
  branch or environment being deployed.
