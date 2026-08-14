# OpenChoreo Workload Descriptor Generator (GitHub Action)

Self-service migration step for Choreo V2 customers moving to OpenChoreo: converts every
`.choreo/component.yaml` in your repository into an OpenChoreo `workload.yaml` descriptor and
opens a pull request for you to review — your repo, your merge. No external system ever needs
write access to your repository.

Why you need this: OpenChoreo source builds regenerate the component's Workload from the repo's
`workload.yaml` on every build. Without it, endpoints, visibility, and API schemas are lost on
the first rebuild after migration. With it, your services keep their routes and (for endpoints
with an OpenAPI file) appear automatically in the OpenChoreo API catalog.

## Quick start

1. Copy `examples/generate-workload-descriptors.yml` into `.github/workflows/` on your default
   branch.
2. Actions tab → *Generate OpenChoreo workload descriptors* → **Run workflow**.
3. Review and merge the PR it opens (branch `choreo/workload-descriptors`).

Optionally also add `examples/workload-descriptor-drift-check.yml` — it fails any PR where
`workload.yaml` has drifted from `.choreo/component.yaml` during the transition window.

## What gets converted

| V2 `component.yaml` | OC `workload.yaml` |
|---|---|
| `endpoints[].name` / `displayName` / `service.port` / `service.basePath` | same fields |
| `type: REST / GraphQL / GRPC / TCP / UDP / WS` | `HTTP / GraphQL / gRPC / TCP / UDP / Websocket` |
| `networkVisibilities: Public / Organization / Project` | `visibility: external / namespace / project` |
| `schemaFilePath` | `schemaFile` (drives the API catalog entry) |

Legacy `.choreo/endpoints.yaml` descriptors are handled too. One `workload.yaml` is written per
app path (monorepos supported).

## What is NOT converted (by design)

- **Secrets** — never written to the repo; they are configured on the OpenChoreo side
  (SecretReference).
- **`dependencies.connectionReferences`** — V2 Connections require migration-side data to
  resolve; they arrive via the enrichment artifact (below) or are configured cluster-side.
- **Container `command`/`args`** — the OC descriptor schema has no such fields; handled
  cluster-side by the migration tooling.

## Inputs

| Input | Default | Purpose |
|---|---|---|
| `mode` | `generate` | `generate` opens a PR; `check` fails on drift (CI gate) |
| `enrichment-file` | – | path to a migration-provided YAML artifact merging `configurations` (non-secret env) and `dependencies` per app path |
| `pr-branch` | `choreo/workload-descriptors` | branch used for the PR |
| `pr-title` | `Add OpenChoreo workload.yaml descriptors` | commit + PR title |
| `token` | `github.token` | token for pushing the branch and opening the PR |

## Enrichment artifact

Your migration engineer may hand you a small YAML file with per-component settings recovered
from Choreo V2 (non-secret environment variables, service-to-service connections). Commit it
anywhere in the repo and pass its path as `enrichment-file`; the generated descriptors absorb
it. Do not place secrets in this file.

```yaml
# enrichment.yaml — keys are app paths relative to the repo root
inventory-service:
  configurations:
    env:
      - name: LOG_LEVEL
        value: info
  dependencies:
    endpoints:
      - component: metadata-service
        name: metadata-api
        visibility: project
        envBindings:
          address: METADATA_SVC_URL
```

## After merging

Trigger a build for each component in the OpenChoreo console (Build Now). The build reads the
merged `workload.yaml`, and the component's endpoints/schemas take effect from that build onward.
