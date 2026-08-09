#!/usr/bin/env python3
"""Operational entrypoint for the whole project.

Python rather than make or shell so the same commands work on Windows, macOS,
Linux, and CI runners without a second implementation drifting out of sync.

    python manage.py bootstrap                 # one-time: terraform state backend
    python manage.py up --env dev              # create/update the environment
    python manage.py secrets --env dev         # write secret values
    python manage.py migrate                   # apply supabase migrations
    python manage.py seed --email a@b.com      # create the first admin
    python manage.py deploy --env dev          # build, push, blue/green release
    python manage.py status --env dev
    python manage.py logs --env dev --service api
    python manage.py destroy --env dev         # tear the environment down
    python manage.py local                     # docker compose up
    python manage.py test
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
TF_ROOT = ROOT / "infra" / "terraform"
PROJECT = "agentic-rag"
REGION = "us-east-1"
ENVIRONMENTS = ("dev", "prod")

SECRET_KEYS = [
    "OPENAI_API_KEY",
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_JWT_SECRET",
    "SUPABASE_DB_URL",
]


# --------------------------------------------------------------- utilities
class Failure(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"\n  error: {message}\n")


def say(message: str) -> None:
    print(f"==> {message}", flush=True)


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> str:
    printable = " ".join(command)
    if not capture:
        print(f"    $ {printable}", flush=True)
    result = subprocess.run(
        command,
        cwd=str(cwd or ROOT),
        text=True,
        capture_output=capture,
        env={**os.environ, **(env or {})},
    )
    if check and result.returncode != 0:
        if capture and result.stderr:
            print(result.stderr, file=sys.stderr)
        raise Failure(f"command failed ({result.returncode}): {printable}")
    return (result.stdout or "").strip() if capture else ""


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise Failure(f"'{name}' is not on PATH. Install it and try again.")


def aws(args: list[str]) -> str:
    require_tool("aws")
    return run(["aws", *args, "--region", REGION], capture=True)


def account_id() -> str:
    try:
        return aws(["sts", "get-caller-identity", "--query", "Account", "--output", "text"])
    except Failure:
        raise Failure(
            "No usable AWS credentials. Sign in first — in this session you can run\n"
            "  ! aws sso login    (or configure a profile with `aws configure`)"
        ) from None


def check_env(value: str) -> str:
    if value not in ENVIRONMENTS:
        raise Failure(f"--env must be one of {', '.join(ENVIRONMENTS)}")
    return value


def env_dir(environment: str) -> Path:
    return TF_ROOT / "envs" / environment


def ecr_repo(environment: str, service: str) -> str:
    return f"{account_id()}.dkr.ecr.{REGION}.amazonaws.com/{PROJECT}-{environment}-{service}"


def git_tag() -> str:
    try:
        sha = run(["git", "rev-parse", "--short", "HEAD"], capture=True, check=False)
    except Exception:
        sha = ""
    return sha or time.strftime("%Y%m%d-%H%M%S")


def load_dotenv() -> dict[str, str]:
    path = ROOT / ".env"
    if not path.exists():
        raise Failure(
            "No .env file. Copy .env.example to .env and fill in the values first."
        )
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


# --------------------------------------------------------------- terraform
def tf(environment: str, args: list[str], *, capture: bool = False) -> str:
    require_tool("terraform")
    return run(["terraform", *args], cwd=env_dir(environment), capture=capture)


def tf_init(environment: str) -> None:
    bucket = f"{PROJECT}-tfstate-{account_id()}"
    say(f"terraform init ({environment}) using s3://{bucket}")
    tf(
        environment,
        [
            "init",
            "-reconfigure",
            "-input=false",
            f"-backend-config=bucket={bucket}",
            f"-backend-config=key={environment}/terraform.tfstate",
            f"-backend-config=region={REGION}",
            f"-backend-config=dynamodb_table={PROJECT}-tfstate-lock",
            "-backend-config=encrypt=true",
        ],
    )


def tf_outputs(environment: str) -> dict:
    raw = tf(environment, ["output", "-json"], capture=True)
    if not raw:
        raise Failure(
            f"No terraform outputs for '{environment}'. Run `python manage.py up "
            f"--env {environment}` first."
        )
    return {key: value["value"] for key, value in json.loads(raw).items()}


def image_vars(environment: str, tag: str) -> list[str]:
    return [
        f"-var=api_image={ecr_repo(environment, 'api')}:{tag}",
        f"-var=ui_image={ecr_repo(environment, 'ui')}:{tag}",
    ]


def extra_vars(args: argparse.Namespace) -> list[str]:
    values: list[str] = []
    if getattr(args, "alert_email", None):
        values.append(f"-var=alert_email={args.alert_email}")
    if getattr(args, "github_repo", None):
        values.append(f"-var=github_repository={args.github_repo}")
    return values


# ------------------------------------------------------------------ docker
def ecr_login() -> None:
    require_tool("docker")
    say("authenticating docker against ECR")
    password = aws(["ecr", "get-login-password"])
    result = subprocess.run(
        ["docker", "login", "--username", "AWS", "--password-stdin",
         f"{account_id()}.dkr.ecr.{REGION}.amazonaws.com"],
        input=password,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise Failure(f"docker login failed: {result.stderr.strip()}")


def build_and_push(environment: str, tag: str) -> None:
    ecr_login()
    for service, context in (("api", "backend"), ("ui", "frontend")):
        repository = ecr_repo(environment, service)
        say(f"building {service}:{tag}")
        run([
            "docker", "build",
            "--platform", "linux/amd64",  # Fargate is x86_64; an arm64 image will not start
            "-t", f"{repository}:{tag}",
            "-t", f"{repository}:latest",
            str(ROOT / context),
        ])
        say(f"pushing {service}:{tag}")
        run(["docker", "push", f"{repository}:{tag}"])


# ---------------------------------------------------------------- commands
def cmd_bootstrap(args: argparse.Namespace) -> None:
    require_tool("terraform")
    directory = TF_ROOT / "bootstrap"
    say("creating the terraform state bucket and lock table")
    run(["terraform", "init", "-input=false"], cwd=directory)
    run(["terraform", "apply", "-input=false", "-auto-approve"], cwd=directory)
    say("done. Run `python manage.py up --env dev` next.")


def cmd_up(args: argparse.Namespace) -> None:
    environment = check_env(args.env)
    tag = args.tag or git_tag()
    tf_init(environment)

    # The task definition needs an image, and the image needs a registry, so the
    # apply is split: registries first, then push, then everything else.
    say("phase 1/3 — creating the container registries")
    tf(environment, [
        "apply", "-input=false", "-auto-approve",
        "-target=module.stack.module.api.aws_ecr_repository.this",
        "-target=module.stack.module.ui.aws_ecr_repository.this",
        "-var=api_image=placeholder", "-var=ui_image=placeholder",
        *extra_vars(args),
    ])

    say("phase 2/3 — building and pushing images")
    build_and_push(environment, tag)

    say("phase 3/3 — applying the full environment")
    plan = env_dir(environment) / "tfplan"
    tf(environment, ["plan", "-input=false", "-out=tfplan", *image_vars(environment, tag),
                     *extra_vars(args)])
    tf(environment, ["apply", "-input=false", "tfplan"])
    plan.unlink(missing_ok=True)

    outputs = tf_outputs(environment)
    print()
    say(f"environment '{environment}' is up")
    print(f"    UI            {outputs.get('ui_url')}")
    print(f"    API           {outputs.get('api_url')}")
    print(f"    Dashboard     {outputs.get('dashboard')}")
    print(f"    Deploy role   {outputs.get('github_deploy_role_arn')}")
    print()
    print("    Next: python manage.py secrets --env " + environment)
    print("          python manage.py migrate")


def cmd_secrets(args: argparse.Namespace) -> None:
    """Write secret values straight to Secrets Manager.

    Values are typed at the prompt or read from .env and passed to the AWS CLI.
    They never enter Terraform state, a variable file, or the shell history.
    """
    environment = check_env(args.env)
    from_env = load_dotenv() if args.from_env_file else {}

    for key in SECRET_KEYS:
        name = f"{PROJECT}-{environment}/{key}"
        value = from_env.get(key, "")
        if not value:
            value = getpass.getpass(f"    {key} (leave blank to skip): ").strip()
        if not value:
            print(f"    skipped {key}")
            continue
        aws(["secretsmanager", "put-secret-value",
             "--secret-id", name, "--secret-string", value])
        print(f"    set {key}")

    say("secrets written. Redeploy so the tasks pick them up:")
    print(f"    python manage.py deploy --env {environment}")


def cmd_deploy(args: argparse.Namespace) -> None:
    environment = check_env(args.env)
    tag = args.tag or git_tag()
    tf_init(environment)
    outputs = tf_outputs(environment)
    services = outputs["services"]

    if not args.skip_build:
        build_and_push(environment, tag)

    for name in ("api", "ui"):
        service = services[name]
        image = f"{ecr_repo(environment, name)}:{tag}"
        say(f"releasing {name} -> {tag}")
        task_definition_arn = register_task_definition(service["task_family"], name, image)
        deployment_id = start_deployment(service, task_definition_arn)
        gate_and_finish(
            deployment_id,
            name=name,
            test_url=outputs["api_test_url"] if name == "api" else outputs["ui_test_url"],
            health_path="/health/ready" if name == "api" else "/_stcore/health",
        )

    print()
    say("deployed")
    print(f"    UI   {outputs['ui_url']}")
    print(f"    API  {outputs['api_url']}")


def register_task_definition(family: str, container: str, image: str) -> str:
    """Copy the live task definition, swap the image, register a new revision."""
    current = json.loads(
        aws(["ecs", "describe-task-definition", "--task-definition", family])
    )["taskDefinition"]

    for key in ("taskDefinitionArn", "revision", "status", "requiresAttributes",
                "compatibilities", "registeredAt", "registeredBy", "deregisteredAt"):
        current.pop(key, None)

    matched = False
    for definition in current["containerDefinitions"]:
        if definition["name"] == container:
            definition["image"] = image
            matched = True
    if not matched:
        raise Failure(f"no container named '{container}' in task family '{family}'")

    payload = json.dumps(current)
    registered = json.loads(
        aws(["ecs", "register-task-definition", "--cli-input-json", payload])
    )
    return registered["taskDefinition"]["taskDefinitionArn"]


def start_deployment(service: dict, task_definition_arn: str) -> str:
    appspec = json.dumps({
        "version": 0.0,
        "Resources": [{
            "TargetService": {
                "Type": "AWS::ECS::Service",
                "Properties": {
                    "TaskDefinition": task_definition_arn,
                    "LoadBalancerInfo": {
                        "ContainerName": service["container_name"],
                        "ContainerPort": service["container_port"],
                    },
                },
            }
        }],
    })
    revision = json.dumps({
        "revisionType": "AppSpecContent",
        "appSpecContent": {"content": appspec},
    })
    result = json.loads(aws([
        "deploy", "create-deployment",
        "--application-name", service["codedeploy_app"],
        "--deployment-group-name", service["codedeploy_group"],
        "--revision", revision,
        "--output", "json",
    ]))
    return result["deploymentId"]


def deployment_status(deployment_id: str) -> str:
    info = json.loads(aws([
        "deploy", "get-deployment", "--deployment-id", deployment_id, "--output", "json"
    ]))
    return info["deploymentInfo"]["status"]


def gate_and_finish(deployment_id: str, *, name: str, test_url: str, health_path: str) -> None:
    """Wait for green to be healthy, smoke test it, then shift traffic.

    The deployment group is configured to stop at Ready, so no production
    traffic has moved at this point. If the smoke test fails we stop the
    deployment and blue keeps serving.
    """
    say(f"waiting for the replacement {name} tasks (deployment {deployment_id})")
    deadline = time.time() + 900
    status = ""
    while time.time() < deadline:
        status = deployment_status(deployment_id)
        if status in ("Ready", "Succeeded", "Failed", "Stopped"):
            break
        time.sleep(10)

    if status in ("Failed", "Stopped"):
        raise Failure(f"{name} deployment {status.lower()}. Check CodeDeploy in the console.")

    if status == "Succeeded":
        say(f"{name} deployed (no approval gate configured)")
        return

    say(f"smoke testing {name} on the test listener — no live traffic yet")
    if not smoke_test(f"{test_url}{health_path}"):
        say(f"smoke test FAILED — stopping the {name} deployment, blue keeps serving")
        aws(["deploy", "stop-deployment", "--deployment-id", deployment_id,
             "--auto-rollback-enabled"])
        raise Failure(f"{name} failed its smoke test. Nothing was released.")

    say(f"smoke test passed — shifting traffic to the new {name}")
    aws(["deploy", "continue-deployment", "--deployment-id", deployment_id,
         "--deployment-wait-type", "READY_WAIT"])

    deadline = time.time() + 1800
    while time.time() < deadline:
        status = deployment_status(deployment_id)
        if status in ("Succeeded", "Failed", "Stopped"):
            break
        time.sleep(10)
    if status != "Succeeded":
        raise Failure(f"{name} deployment ended as {status}.")
    say(f"{name} is live")


def smoke_test(url: str, attempts: int = 20, delay: int = 10) -> bool:
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
                if response.status == 200:
                    print(f"    {url} -> 200")
                    return True
                print(f"    attempt {attempt}: {response.status}")
        except (urllib.error.URLError, OSError) as exc:
            print(f"    attempt {attempt}: {exc}")
        time.sleep(delay)
    return False


def cmd_status(args: argparse.Namespace) -> None:
    environment = check_env(args.env)
    tf_init(environment)
    outputs = tf_outputs(environment)

    print(f"\n  {environment}")
    print(f"    UI         {outputs['ui_url']}")
    print(f"    API        {outputs['api_url']}")
    print(f"    Dashboard  {outputs['dashboard']}\n")

    for name, service in outputs["services"].items():
        described = json.loads(aws([
            "ecs", "describe-services",
            "--cluster", outputs["cluster_name"],
            "--services", service["service_name"],
            "--output", "json",
        ]))["services"]
        if not described:
            print(f"    {name:4} not found")
            continue
        info = described[0]
        print(f"    {name:4} {info['status']:8} running={info['runningCount']} "
              f"desired={info['desiredCount']} pending={info['pendingCount']}")

    alarms = json.loads(aws([
        "cloudwatch", "describe-alarms", "--state-value", "ALARM", "--output", "json"
    ]))["MetricAlarms"]
    relevant = [a for a in alarms if a["AlarmName"].startswith(f"{PROJECT}-{environment}")]
    print()
    if relevant:
        for alarm in relevant:
            print(f"    ALARM  {alarm['AlarmName']}")
    else:
        print("    no alarms firing")
    print()


def cmd_logs(args: argparse.Namespace) -> None:
    environment = check_env(args.env)
    group = f"/ecs/{PROJECT}-{environment}/{args.service}"
    say(f"tailing {group} (ctrl-c to stop)")
    require_tool("aws")
    subprocess.run(
        ["aws", "logs", "tail", group, "--follow", "--format", "short",
         "--since", args.since, "--region", REGION],
        cwd=str(ROOT),
    )


def cmd_destroy(args: argparse.Namespace) -> None:
    environment = check_env(args.env)
    tf_init(environment)

    say(f"planning the destruction of '{environment}'")
    tf(environment, ["plan", "-destroy", "-input=false",
                     *image_vars(environment, "destroy"), *extra_vars(args)])

    print()
    print("  This permanently deletes the VPC, load balancer, ECS services,")
    print("  container images, CloudWatch logs, and Secrets Manager entries")
    print(f"  for '{environment}'. Documents in Supabase are NOT affected.")
    print()

    if environment == "prod" and args.confirm != "DESTROY-PROD":
        raise Failure(
            "Destroying prod requires --confirm DESTROY-PROD in addition to typing the name."
        )

    if not args.yes:
        typed = input(f"  Type the environment name to confirm ({environment}): ").strip()
        if typed != environment:
            raise Failure("name did not match. Nothing was destroyed.")

    tf(environment, ["destroy", "-input=false", "-auto-approve",
                     *image_vars(environment, "destroy"), *extra_vars(args)])
    say(f"'{environment}' destroyed. The terraform state bucket is intentionally left in place.")


def cmd_migrate(args: argparse.Namespace) -> None:
    """Apply supabase/migrations in order, once each."""
    import asyncio

    try:
        import asyncpg
    except ImportError:
        raise Failure(
            "asyncpg is missing. Install it:\n"
            "    pip install -r backend/requirements.txt"
        ) from None

    url = os.getenv("SUPABASE_DB_URL") or load_dotenv().get("SUPABASE_DB_URL", "")
    if not url:
        raise Failure("SUPABASE_DB_URL is not set (env or .env).")

    files = sorted((ROOT / "supabase" / "migrations").glob("*.sql"))
    if not files:
        raise Failure("no migration files found")

    async def apply() -> None:
        conn = await asyncpg.connect(url, statement_cache_size=0)
        try:
            await conn.execute(
                """
                create table if not exists public.schema_migrations (
                    filename text primary key,
                    applied_at timestamptz not null default now()
                )
                """
            )
            applied = {
                r["filename"]
                for r in await conn.fetch("select filename from schema_migrations")
            }
            for path in files:
                if path.name in applied:
                    print(f"    skip  {path.name}")
                    continue
                print(f"    apply {path.name}")
                async with conn.transaction():
                    await conn.execute(path.read_text(encoding="utf-8"))
                    await conn.execute(
                        "insert into schema_migrations (filename) values ($1)", path.name
                    )
        finally:
            await conn.close()

    say("applying migrations")
    asyncio.run(apply())
    say("schema is up to date")


def cmd_seed(args: argparse.Namespace) -> None:
    """Create the first administrator and a starter department."""
    import asyncio
    import json as _json
    import urllib.request as _request

    values = load_dotenv()
    url = values.get("SUPABASE_URL", "").rstrip("/")
    service_key = values.get("SUPABASE_SERVICE_ROLE_KEY", "")
    db_url = values.get("SUPABASE_DB_URL", "")
    if not (url and service_key and db_url):
        raise Failure("SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and SUPABASE_DB_URL must be set in .env")

    password = args.password or getpass.getpass("    password for the admin account: ")
    if len(password) < 10:
        raise Failure("password must be at least 10 characters")

    request = _request.Request(
        f"{url}/auth/v1/admin/users",
        method="POST",
        data=_json.dumps({
            "email": args.email,
            "password": password,
            "email_confirm": True,
            "user_metadata": {"full_name": args.name or "Administrator"},
        }).encode(),
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with _request.urlopen(request, timeout=30) as response:  # noqa: S310
            created = _json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:300]
        if exc.code in (409, 422):
            raise Failure(f"an account already exists for {args.email}") from None
        raise Failure(f"could not create the account: {detail}") from None

    user_id = created["id"]

    import asyncpg

    async def finish() -> None:
        conn = await asyncpg.connect(db_url, statement_cache_size=0)
        try:
            await conn.execute(
                """
                insert into profiles (id, email, full_name, role)
                values ($1::uuid, $2, $3, 'admin')
                on conflict (id) do update set role = 'admin'
                """,
                user_id, args.email, args.name or "Administrator",
            )
            await conn.execute(
                """
                insert into departments (name, slug, description)
                values ('General', 'general', 'Starter department')
                on conflict (slug) do nothing
                """
            )
        finally:
            await conn.close()

    asyncio.run(finish())
    say(f"administrator created: {args.email}")
    print("    Sign in through the Streamlit UI and create departments and users there.")


def cmd_local(args: argparse.Namespace) -> None:
    require_tool("docker")
    if not (ROOT / ".env").exists():
        raise Failure("copy .env.example to .env and fill it in first")
    run(["docker", "compose", "up", "--build", *(["-d"] if args.detach else [])])
    if args.detach:
        print("    UI   http://localhost:8501")
        print("    API  http://localhost:8000/docs")


def cmd_test(args: argparse.Namespace) -> None:
    backend = ROOT / "backend"
    python = sys.executable
    run([python, "-m", "pytest", "tests/unit", "-q"], cwd=backend)
    run([python, "-m", "ruff", "check", "app", "tests"], cwd=backend)


# -------------------------------------------------------------------- main
def main() -> None:
    parser = argparse.ArgumentParser(
        prog="manage.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def with_env(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--env", required=True, choices=ENVIRONMENTS)
        return p

    sub.add_parser("bootstrap", help="create the terraform state backend").set_defaults(
        func=cmd_bootstrap
    )

    up = with_env(sub.add_parser("up", help="create or update an environment"))
    up.add_argument("--tag", help="image tag (defaults to the git sha)")
    up.add_argument("--alert-email", help="where CloudWatch alarms are sent")
    up.add_argument("--github-repo", help="owner/repo allowed to assume the deploy role")
    up.set_defaults(func=cmd_up)

    secrets = with_env(sub.add_parser("secrets", help="write secret values"))
    secrets.add_argument(
        "--from-env-file",
        action="store_true",
        help="read values from .env instead of prompting",
    )
    secrets.set_defaults(func=cmd_secrets)

    deploy = with_env(sub.add_parser("deploy", help="build, push, blue/green release"))
    deploy.add_argument("--tag", help="image tag (defaults to the git sha)")
    deploy.add_argument("--skip-build", action="store_true", help="release an existing tag")
    deploy.set_defaults(func=cmd_deploy)

    with_env(sub.add_parser("status", help="services and alarms")).set_defaults(
        func=cmd_status
    )

    logs = with_env(sub.add_parser("logs", help="tail CloudWatch logs"))
    logs.add_argument("--service", default="api", choices=("api", "ui"))
    logs.add_argument("--since", default="10m")
    logs.set_defaults(func=cmd_logs)

    destroy = with_env(sub.add_parser("destroy", help="tear an environment down"))
    destroy.add_argument("--confirm", default="", help="DESTROY-PROD, required for prod")
    destroy.add_argument("--yes", action="store_true", help="skip the typed confirmation")
    destroy.set_defaults(func=cmd_destroy)

    sub.add_parser("migrate", help="apply supabase migrations").set_defaults(func=cmd_migrate)

    seed = sub.add_parser("seed", help="create the first administrator")
    seed.add_argument("--email", required=True)
    seed.add_argument("--password", help="prompted for if omitted")
    seed.add_argument("--name", help="full name")
    seed.set_defaults(func=cmd_seed)

    local = sub.add_parser("local", help="run the stack with docker compose")
    local.add_argument("--detach", action="store_true")
    local.set_defaults(func=cmd_local)

    sub.add_parser("test", help="unit tests and lint").set_defaults(func=cmd_test)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  interrupted")
        sys.exit(130)
