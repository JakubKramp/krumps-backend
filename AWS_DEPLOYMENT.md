# AWS Deployment Runbook — ECS Fargate

Step-by-step guide to deploying this FastAPI cookbook app on AWS using ECS Fargate,
via the AWS Console. Each step includes the reasoning behind it.

Work **top to bottom** — the setup is built bottom-up because every layer's outputs
(subnet IDs, endpoints, ARNs) are inputs to the next.

---

## Architecture overview

```
                    Internet
                       │
                 ┌─────▼─────┐   HTTPS (ACM cert)
                 │    ALB     │   Route 53 → ALB
                 └─────┬─────┘
        ┌──────────────┘  (public subnets)
        │
  ┌─────▼──────────────── private subnets ─────────────────┐
  │                                                         │
  │  ECS Service: api            ECS Service: celery-worker │
  │   (uvicorn, 2 tasks,          (celery worker, 1+ tasks) │
  │    behind ALB)                                          │
  │                              ECS Service: celery-beat   │
  │                               (desiredCount = 1)        │
  │       │            │                  │                 │
  │       ▼            ▼                  ▼                 │
  │   ┌────────┐        ┌───────────┐                       │
  │   │  RDS   │        │ElastiCache│                        │
  │   │Postgres│        │  Redis    │                        │
  │   └────────┘        └───────────┘                       │
  └─────────────────────────────────────────────────────────┘
        │
        ▼
     S3 bucket  (dish images — via S3FileUploader)
```

**Service mapping (from `docker-compose-prod.yml`):**

| Compose service | AWS |
|---|---|
| `api` (uvicorn) | ECS service behind an ALB |
| `celery-worker` | ECS service (scalable) |
| `celery-beat` | ECS service, **exactly 1 task** |
| `postgres` | RDS for PostgreSQL |
| `redis` | ElastiCache for Redis |
| image storage | S3 (via `S3FileUploader`) |

All three ECS services run the **same Docker image**; they differ only in the container
command (mirroring the `command:` overrides in your compose file).

---

## Cost note (before you start)

This design is **not free-tier friendly** — Fargate and the ALB are excluded from the free tier.
Free-tier eligible pieces (first 12 months): a single `db.t4g.micro` RDS and a small ElastiCache
node. The ALB (~$16–18/mo) and Fargate tasks are the main fixed costs. The ACM TLS certificate is
free. If minimizing cost matters more than managed infra, consider the single-EC2 + Cloudflare
alternative instead.

---

## Phase 0 — Decisions & code checkpoints

- **Pick one region** (e.g. `eu-west-1`) and create everything in it.
  *Why:* almost nothing spans regions; co-locating keeps traffic between services fast and free.
- **Playwright is disabled in prod** — set `PLAYWRIGHT_ENABLED=false`.
  *Why:* the `Dockerfile` never runs `playwright install`, so DRI scraping would crash with no
  browser binary. To run it in prod, add the browser install to the image first (separate task).
- **Migrations run automatically** — `entrypoint.sh` runs `alembic upgrade head` on every
  container start, so no separate migration step is needed. (Every task runs it; Alembic's lock
  makes concurrent starts safe.)
- **Drop `--reload`** in the api container command (Phase 10).
  *Why:* it's a dev-only flag that watches the filesystem and wastes resources in prod.
- **Health endpoint exists:** `GET /health-check` returns 200 — used by the ALB (Phase 11).

**Names used in this guide:** cluster `cookbook`, ECR repo `cookbook-api`, task defs
`cookbook-api` / `cookbook-worker` / `cookbook-beat`.

---

## Phase 1 — Network (VPC)

1. **VPC console → Create VPC → "VPC and more"** (the wizard).
2. Set: name `cookbook`, **2 Availability Zones**, **2 public subnets**, **2 private subnets**,
   **1 NAT gateway**, **0 VPC endpoints**.
3. Create. Note the **VPC ID** and the four **subnet IDs**.

**Why:**
- *Custom VPC (not default):* the default VPC has only public subnets; you need private subnets so
  the DB, cache, and tasks have no route from the internet.
- *2 AZs:* high availability — the ALB and ECS spread across AZs; RDS Multi-AZ needs a second AZ.
- *Public vs private:* public subnets route to the internet gateway (the ALB must be reachable);
  private subnets don't (tasks/DB/cache must not be). This is the core security boundary.
- *NAT gateway:* private tasks still need outbound internet (nutrition API, image pulls). NAT allows
  outbound while blocking inbound. One NAT (vs per-AZ) trades a little HA for lower cost.

---

## Phase 2 — Security groups (EC2 console → Security Groups)

Create **three** SGs in the `cookbook` VPC, **empty first**, then add the cross-referencing rules in
this order (each references the previous one, so it must already exist):

1. `alb-sg` — inbound **443 from `0.0.0.0/0`** (add 80 too if you want an HTTP→HTTPS redirect).
2. `app-sg` — inbound **8000 from `alb-sg`**. Used by both `api` and `celery-worker`.
3. `data-sg` — inbound **5432 + 6379 from `app-sg`**. Used by both RDS and ElastiCache.

Leave default "all" outbound on each. When creating RDS/ElastiCache/ALB/ECS later, **select these
existing SGs** rather than letting the wizard auto-create one.

**Why:**
- *SGs are mandatory* — every network interface must have one; if you don't specify, AWS uses the
  VPC default SG. So the only question is how they're configured.
- *Reference SG-by-SG, not by IP:* Fargate task IPs change on every deploy. "Allow from `app-sg`"
  means "any task wearing that SG," which survives redeploys; a hardcoded CIDR would break.
- *api only from `alb-sg`:* the app should be reachable only through the load balancer.
- *data-sg only from `app-sg`:* least privilege — the DB/cache accept connections only from the app,
  never the internet. This is the one boundary you must not merge away.

> Collapsed from a stricter 5-SG layout: `worker-sg` merged into `app-sg` (the worker has no inbound
> listener), and RDS + Redis share one `data-sg` (both accept from the same source).

---

## Phase 3 — S3 bucket

Already created for `S3FileUploader`. Confirm it's in this region; note the name for
`AWS_BUCKET_NAME`. Keep **Block Public Access ON**.

**Why:** dish images are user content — a public bucket is world-readable. `get_url`'s presigned
URLs grant time-limited access to individual objects (that's what `FILE_URL_EXPIRATION_SECONDS` is
for).

---

## Phase 4 — RDS PostgreSQL

1. **RDS console → Create database → Standard → PostgreSQL.**
   - **Check the region offers PostgreSQL 18** to match the `postgres:18.2` pin in compose; if only
     ≤17 is available, use that or bump the version deliberately.
2. Template **Dev/Test** (or Production for Multi-AZ). Instance **`db.t4g.micro`** (free-tier
   eligible for 12 months). Set master username/password — **save the password** for Phase 6.
3. **Connectivity:** VPC `cookbook`, **no public access**, **private** subnets (make a DB subnet
   group from them if prompted), security group **`data-sg`**.
4. Set **initial database name** (e.g. `cookbook`).
5. Create. Copy the **endpoint** → this is `POSTGRES_HOST`.

**Why:**
- *Managed RDS, not a Postgres container:* **Fargate has no persistent disk** — a task's local
  storage is wiped on restart, so a Postgres container would lose all data on any redeploy. RDS also
  gives backups, patching, and failover.
- *RDS over Aurora:* Aurora isn't free-tier eligible and solves scaling/HA problems this app doesn't
  have; the code (`asyncpg`/SQLAlchemy/Alembic) is identical on both. Revisit Aurora only if you hit
  heavy read concurrency or need faster failover.
- *Private + no public access:* an internet-reachable database is a catastrophic misconfiguration.
- *`POSTGRES_HOST`:* `settings.py` builds `DATABASE_URL` from the `POSTGRES_*` parts, so you set
  those, not a full URL.

---

## Phase 5 — ElastiCache Redis
sudo curl -fSL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 -o /usr/local/lib/docker/cli-plugins/docker-compose   
1. **ElastiCache console → Redis OSS → Create.**
2. Cluster mode disabled, small node (e.g. `cache.t4g.micro`), 0–1 replicas.
3. Subnet group from the **private** subnets, security group **`data-sg`**.
4. Create. Copy the **primary endpoint** → `CELERY_BROKER_URL = redis://<endpoint>:6379/0`.

**Why:** the Celery broker must be a single shared endpoint that both the worker and beat connect
to. A Redis container on Fargate would be ephemeral and per-task — beat would publish to one Redis
and the worker read from another, so no jobs would ever run.

---

## Phase 6 — Secrets (Secrets Manager)

Store the sensitive values (task def injects them at runtime). One secret each,
**Store a new secret → Other type → plaintext**:

- `cookbook/SECRET_KEY`
- `cookbook/POSTGRES_PASSWORD`
- `cookbook/NUTRITION_APIKEY`

Copy each **secret ARN**.

**Why:** task-definition env vars are visible to anyone with ECS read access and leak into CI logs /
version control. `SECRET_KEY` signs JWTs (leak = forge logins), `POSTGRES_PASSWORD` is DB access,
`NUTRITION_APIKEY` is a billable third-party credential — these stay in Secrets Manager. Everything
else is non-sensitive config and goes as plain env vars.

---

## Phase 7 — IAM roles (IAM console → Roles, trusted by `ecs-tasks.amazonaws.com`)

1. **`cookbookTaskExecutionRole`** — attach managed **`AmazonECSTaskExecutionRolePolicy`**, plus an
   inline policy granting `secretsmanager:GetSecretValue` on the three secret ARNs.
2. **`cookbookTaskRole`** — inline policy with `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject` on
   `arn:aws:s3:::<bucket>/*`.

**Why:**
- *Two roles, split deliberately:* the **execution role** is used by the ECS agent to set the task
  up **before your code runs** (pull image, fetch secrets, write logs); the **task role** is assumed
  by your **application code** at runtime. Least privilege on both sides.
- *Task role → S3:* this makes `boto3.client("s3")` in `S3FileUploader` work with **no static keys**
  — boto3's default credential chain picks up the task role automatically.
- *Execution role → secrets:* the agent, not your code, reads and injects the secrets.

---

## Phase 8 — ECR image

Your CI already builds and pushes to ECR on `master`. Confirm at least one image tag exists in the
`cookbook-api` repo (**ECR console → Repositories**). If not, run the pipeline once.

**Why:** a service can't start a task without an image — the task def points at a specific repo/tag.
ECR is private, in-region (fast pulls, no egress), and authenticates via IAM.

---

## Phase 9 — ECS cluster

**ECS console → Clusters → Create cluster.** Name `cookbook`, infrastructure
**AWS Fargate (serverless)**. Create.

**Why:** the cluster is the logical namespace that owns services and tasks. With Fargate there's no
EC2 fleet under it — it's essentially free, but services must live in one.

---

## Phase 10 — Task definitions (create three, Fargate)

**Shared settings for all three:**
- Image URI: your ECR image (`...dkr.ecr.<region>.amazonaws.com/cookbook-api:latest`)
- Task execution role: `cookbookTaskExecutionRole`; Task role: `cookbookTaskRole`
- **Log configuration:** enable `awslogs` (creates a CloudWatch log group)
- **Environment variables** (plain) and **secrets** (from Secrets Manager) — see the reference table
  at the end of this doc.

**Per-task differences:**

| Task def | CPU / Mem | Container command | Port |
|---|---|---|---|
| `cookbook-api` | 1 vCPU / 2 GB | `uvicorn app.main:app --host 0.0.0.0 --port 8000` | 8000 |
| `cookbook-worker` | 0.5 vCPU / 1 GB | `celery -A app.celery_app worker --loglevel=info` | — |
| `cookbook-beat` | 0.25 vCPU / 0.5 GB | `celery -A app.celery_app beat --loglevel=info` | — |

**Why:**
- *Three defs:* they run different processes with different resource needs; the task def is the
  immutable "recipe" (image + resources + env + roles).
- *Different sizes:* the api serves concurrent HTTP (most resources), the worker does background
  jobs (medium), beat just ticks a schedule (tiny). Right-sizing is the main cost lever.
- *Drop `--reload`:* prod stability/perf.
- *`awslogs`:* Fargate has no host to SSH into — CloudWatch is the only window into container output.

---

## Phase 11 — Certificate + Load Balancer

1. **ACM console → Request certificate** for your domain (DNS validation; add the CNAME to your
   DNS). Wait for **Issued**.
2. **EC2 console → Load Balancers → Create → Application Load Balancer.**
   - Internet-facing, **public** subnets (both AZs), security group **`alb-sg`**.
   - Listener **HTTPS:443** → select the ACM cert.
   - Create a **target group**: type **IP**, protocol HTTP, port **8000**, VPC `cookbook`, health
     check path **`/health-check`**. Point the 443 listener at it.
   - (Optional: HTTP:80 listener redirecting to 443.)

**Why:**
- *ACM cert:* free TLS terminated at the ALB, so the app never handles certificates.
- *The ALB:* Fargate task IPs are ephemeral and plural, so you need one stable front door. It gives
  TLS termination, health checks, traffic spreading across tasks/AZs, and auto-registers/drains
  tasks during deploys — which is what makes rolling deploys zero-downtime.
- *Target group type IP:* Fargate tasks register by IP, not as EC2 instances.
- *Health check `/health-check`:* a cheap endpoint that's 200 only when the app is truly ready. The
  ALB uses it to route away from sick tasks and to gate rolling deploys.

---

## Phase 12 — ECS services (cluster `cookbook` → Create service, Fargate, Rolling update)

All use **private subnets** and **Public IP OFF**.

| Service | Task def | Desired | SG | Load balancer |
|---|---|---|---|---|
| `api` | `cookbook-api` | 2 | `app-sg` | **Yes** — attach the Phase 11 target group |
| `celery-worker` | `cookbook-worker` | 1+ | `app-sg` | No |
| `celery-beat` | `cookbook-beat` | **1** | `app-sg` | No |

**Why:**
- *Service, not a bare task:* it maintains `desiredCount`, replaces failed tasks, does rolling
  deploys, and keeps the ALB target group in sync.
- *api desiredCount 2:* survive one task/AZ loss and enable zero-downtime deploys.
- *beat = 1, no autoscaling:* **two schedulers would double-fire** your daily `fridge/tasks.py`
  expiry job. This is the sharpest app-specific constraint.
- *worker scales freely:* stateless queue consumption — more workers = more throughput.

---

## Phase 13 — DNS

Add an **A / ALIAS record** for your domain → the ALB's DNS name (Route 53, or a CNAME at your
existing provider).

**Why:** maps your human-readable name to the ALB's AWS-managed DNS name; ALIAS works at the apex
and is free to resolve.

---

## Phase 14 — Verify

1. ECS → `api` service → **Tasks** reach **Running** and the target group shows **healthy**
   (proves image pull, networking, DB/Redis connectivity, and that the entrypoint's migration ran).
2. `https://<yourdomain>/health-check` → **200** (proves DNS → ALB → cert → target group → task).
3. Upload a dish image → object lands in S3 and its presigned URL loads (exercises the **task role**).
4. CloudWatch logs → `celery-worker` connected to Redis, `celery-beat` registered the schedule.

---

## Phase 15 — Point CI at ECS

Replace the SSH-deploy step in `.github/workflows/ci.yml` with, per service:

```sh
aws ecs update-service --cluster cookbook --service api            --force-new-deployment
aws ecs update-service --cluster cookbook --service celery-worker  --force-new-deployment
aws ecs update-service --cluster cookbook --service celery-beat    --force-new-deployment
```

- No separate migration step — the entrypoint runs `alembic upgrade head` on boot.
- Add **`ecs:UpdateService`** to the OIDC role CI already uses to push to ECR.

**Why:** `update-service --force-new-deployment` tells ECS to re-pull `:latest` and roll tasks —
the deploy mechanism that replaces the old SSH `docker compose up`.

---

## Environment variable reference

Set on all three task definitions. Sensitive values come from Secrets Manager; the rest are plain
env vars.

| Variable | Source | Value |
|---|---|---|
| `POSTGRES_USER` | env | RDS master username |
| `POSTGRES_HOST` | env | RDS endpoint |
| `POSTGRES_DATABASE` | env | e.g. `cookbook` |
| `POSTGRES_PASSWORD` | **secret** | `cookbook/POSTGRES_PASSWORD` |
| `CELERY_BROKER_URL` | env | `redis://<elasticache-endpoint>:6379/0` |
| `CLOUD_PROVIDER` | env | `aws` |
| `AWS_BUCKET_NAME` | env | your S3 bucket |
| `AWS_REGION` | env | your region |
| `SECRET_KEY` | **secret** | `cookbook/SECRET_KEY` |
| `ALGORITHM` | env | e.g. `HS256` |
| `NUTRITION_API_URL` | env | your value |
| `NUTRITION_APIKEY` | **secret** | `cookbook/NUTRITION_APIKEY` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | env | e.g. `60` |
| `APP_LOCATION` | env | your value |
| `CORS_ALLOWED_ORIGINS` | env | your value |
| `PLAYWRIGHT_ENABLED` | env | `false` (see Phase 0) |
| `FILE_MAX_UPLOAD_SIZE` | env | your value |
| `ALLOWED_EXTENSIONS` | env | your value |
| `FILE_URL_EXPIRATION_SECONDS` | env | e.g. `3600` |

> With `CLOUD_PROVIDER=aws`, the settings validator requires `AWS_BUCKET_NAME` and `AWS_REGION`.
> AWS credentials are **not** set as env vars — `boto3` picks them up from the task role.
