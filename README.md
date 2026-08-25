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
2. Allow workflows to open pull requests (off by default): repo **Settings → Actions →
   General → Workflow permissions** → select *Read and write permissions* and tick
   *Allow GitHub Actions to create and approve pull requests*. Equivalent CLI:
   `gh api -X PUT repos/OWNER/REPO/actions/permissions/workflow -f default_workflow_permissions=write -F can_approve_pull_request_reviews=true`
3. Actions tab → *Generate OpenChoreo workload descriptors* → **Run workflow**.
4. Review and merge the PR it opens (branch `choreo/workload-descriptors`).

Optionally also add `examples/workload-descriptor-drift-check.yml` — it fails any PR where
`workload.yaml` has drifted from `.choreo/component.yaml` during the transition window.

> **Note:** the auto-generated "Installation" snippet on the Marketplace page is a single *step*,
> not a complete workflow — pasting it into an empty file fails with "A sequence was not
> expected". Use the complete example below (or the files in `examples/`) instead; the
> Marketplace snippet is only for adding this action to a workflow you already have.
>
> ```yaml
> name: Generate OpenChoreo workload descriptors
> on:
>   workflow_dispatch:
> permissions:
>   contents: write
>   pull-requests: write
> jobs:
>   generate:
>     runs-on: ubuntu-latest
>     steps:
>       - uses: actions/checkout@v4
>       - uses: isuruRuhu/openchoreo-workload-descriptor-action@v1
> ```

## Action repo visibility (verified behavior)

- **Public action repo**: usable from any repository anywhere (Marketplace listing optional —
  it adds discoverability only, and can be delisted at any time without breaking consumers).
- **Private action repo**: usable only from *private* repositories of the same owner/org, and
  only with Settings → Actions → Access set to share; **public repos cannot resolve it**
  (`Unable to resolve action ... not found`), and Marketplace publishing is not possible.
- Never delete or privatize the action repo once external consumers reference it — that breaks
  their workflows at the next run. To retire it, archive the repo (workflows keep working) and
  add a deprecation notice.

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
| `enrichment-file` | – | path to a migration-provided YAML artifact keyed by app path; merges `name`, `endpoints`, `configurations` (non-secret env) and `dependencies`. For components with no `.choreo/component.yaml` the artifact is the endpoint source |
| `pr-branch` | `choreo/workload-descriptors` | branch used for the PR |
| `pr-title` | `Add OpenChoreo workload.yaml descriptors` | commit + PR title |
| `token` | `github.token` | token for pushing the branch and opening the PR |

## Enrichment artifact

Your migration engineer may hand you a small YAML file with per-component settings recovered
from Choreo V2 (endpoint definitions, non-secret environment variables, service-to-service
connections). Commit it anywhere in the repo and pass its path as `enrichment-file`; the
generated descriptors absorb it. Do not place secrets in this file.

Keys are app paths relative to the repo root (`.` for the root). Mergeable keys per entry:
`name` (descriptor `metadata.name`), `endpoints`, `configurations`, `dependencies`.

**Components created in the Choreo console without a `.choreo/component.yaml`** keep their
endpoint configuration only in Choreo's own store, so there is nothing in the repo to convert.
For those, the artifact *is* the source: an entry whose app path has no descriptor still
produces a `workload.yaml` (the directory must exist). Where both exist, artifact `endpoints`
override the descriptor's. The migration engineer generates the artifact from the Choreo V2
extraction on their side — your CI never needs Choreo credentials.

```yaml
# enrichment.yaml — keys are app paths relative to the repo root
apps/inventory/backend:                # no .choreo/component.yaml here — artifact is the source
  name: inventory-service
  endpoints:
    - name: endpoint-9090
      displayName: Endpoint 9090
      port: 9090
      type: HTTP
      basePath: /
      visibility: [external]
inventory-service:                     # has a .choreo/component.yaml — artifact enriches it
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
