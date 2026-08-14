#!/usr/bin/env python3
"""Generate OpenChoreo workload.yaml descriptors from Choreo V2 .choreo/component.yaml files.

Scans one or more repo checkouts for V2 component descriptors and emits an OC
workload descriptor (openchoreo.dev/v1alpha1) at each app path root — the file
the OC build pipeline reads to (re)generate the Workload CR. Landing this file
in the repo is the durable fix for the F26/F86 endpoint-strip on source builds.

Field mapping (V2 -> OC):
  endpoints[].name                    -> endpoints[].name
  endpoints[].displayName             -> endpoints[].displayName
  endpoints[].service.port  (or .port)-> endpoints[].port
  endpoints[].service.basePath (or .context) -> endpoints[].basePath
  endpoints[].type  REST->HTTP, WS->Websocket, GRPC->gRPC (others pass through)
  endpoints[].networkVisibilities  Public->external, Organization->namespace,
                                   Project->project
  endpoints[].schemaFilePath          -> endpoints[].schemaFile (same base dir)

Not carried (reported as warnings instead):
  dependencies.connectionReferences  — need V2 extraction data to resolve the
    target component/endpoint; map manually into dependencies.endpoints[].
  container command/args             — OC descriptor schema has no such fields.

Usage:
  component_yaml_to_workload.py REPO_DIR [REPO_DIR ...]        # dry run
  component_yaml_to_workload.py --write REPO_DIR [...]         # write into repos
  component_yaml_to_workload.py --write --force REPO_DIR [...] # overwrite existing
  component_yaml_to_workload.py --check REPO_DIR [...]         # drift check (CI): exit 1 if
                                                               # any workload.yaml is missing
                                                               # or differs from what the
                                                               # descriptor would generate
  component_yaml_to_workload.py --enrich FILE --write REPO_DIR # merge an extraction-derived
                                                               # enrichment artifact (YAML,
                                                               # keyed by app path)

Enrichment artifact format (keys are app paths relative to the repo root; '.' for root):
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
Only `configurations` and `dependencies` are merged (whole-key replace); endpoints always come
from the V2 descriptor. Secrets must NOT be placed in the artifact — they stay cluster-side.

Legacy .choreo/endpoints.yaml descriptors are also handled.
"""

import argparse
import sys
from pathlib import Path

import yaml

TYPE_MAP = {
    "REST": "HTTP",
    "HTTP": "HTTP",
    "GRAPHQL": "GraphQL",
    "GRPC": "gRPC",
    "TCP": "TCP",
    "UDP": "UDP",
    "WS": "Websocket",
    "WEBSOCKET": "Websocket",
}

VISIBILITY_MAP = {
    "PUBLIC": "external",
    "ORGANIZATION": "namespace",
    "PROJECT": "project",
}

# Endpoint types the OC `service` CT will not route externally (F29).
NO_EXTERNAL_ROUTE_TYPES = {"gRPC", "TCP", "UDP"}

DESCRIPTOR_NAMES = ("component.yaml", "component.yml", "endpoints.yaml", "endpoints.yml")

HEADER = """\
# OpenChoreo Workload Descriptor
# Generated from .choreo/{src} by scripts/component_yaml_to_workload.py
# (Choreo V2 -> OpenChoreo migration). Review before committing.
"""


def k8s_name(raw: str) -> str:
    out = "".join(c if c.isalnum() else "-" for c in raw.lower()).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out[:63] or "workload"


def convert_endpoint(ep: dict, app_dir: Path, warnings: list) -> dict:
    name = ep.get("name") or "endpoint"
    svc = ep.get("service") or {}
    port = svc.get("port", ep.get("port"))
    base_path = svc.get("basePath") or ep.get("context") or ""

    v2_type = str(ep.get("type", "REST")).upper()
    oc_type = TYPE_MAP.get(v2_type)
    if oc_type is None:
        warnings.append(f"endpoint '{name}': unknown V2 type {ep.get('type')!r}; kept verbatim")
        oc_type = ep.get("type")

    vis_raw = ep.get("networkVisibilities") or ep.get("networkVisibility") or []
    if isinstance(vis_raw, str):
        vis_raw = [vis_raw]
    visibility = []
    for v in vis_raw:
        mapped = VISIBILITY_MAP.get(str(v).upper())
        if mapped is None:
            warnings.append(f"endpoint '{name}': unknown visibility {v!r}; skipped")
        elif mapped not in visibility:
            visibility.append(mapped)

    if oc_type in NO_EXTERNAL_ROUTE_TYPES and "external" in visibility:
        warnings.append(
            f"endpoint '{name}': type {oc_type} gets no external Route from the "
            f"service CT (F29) despite external visibility"
        )

    out = {"name": name}
    if ep.get("displayName"):
        out["displayName"] = ep["displayName"]
    if port is None:
        warnings.append(f"endpoint '{name}': no port declared")
    else:
        out["port"] = int(port)
    out["type"] = oc_type
    if base_path:
        out["basePath"] = base_path
    if visibility:
        out["visibility"] = visibility

    schema_path = ep.get("schemaFilePath")
    if schema_path:
        out["schemaFile"] = schema_path
        if not (app_dir / schema_path).is_file():
            warnings.append(
                f"endpoint '{name}': schemaFile '{schema_path}' not found under {app_dir}"
            )
    return out


def convert_descriptor(desc: dict, app_dir: Path, src_name: str, enrichment: dict | None = None):
    """Returns (workload_dict, warnings)."""
    warnings = []
    endpoints = desc.get("endpoints") or []
    if not isinstance(endpoints, list):
        warnings.append(f"'endpoints' is not a list in {src_name}; ignored")
        endpoints = []

    workload = {
        "apiVersion": "openchoreo.dev/v1alpha1",
        "metadata": {"name": k8s_name(app_dir.name)},
    }
    if endpoints:
        workload["endpoints"] = [convert_endpoint(e, app_dir, warnings) for e in endpoints]
    else:
        warnings.append("no endpoints declared in V2 descriptor (fine for tasks/workers)")

    deps = desc.get("dependencies") or {}
    conn_refs = deps.get("connectionReferences") or []
    if conn_refs:
        names = ", ".join(str(c.get("name", "?")) for c in conn_refs)
        warnings.append(
            f"V2 connectionReferences present ({names}) — NOT converted; resolve "
            f"targets from extraction data and add dependencies.endpoints[] manually"
        )

    if desc.get("configurations") or desc.get("configuration"):
        warnings.append("V2 'configuration(s)' block present — review manually")

    if enrichment:
        for key in ("configurations", "dependencies"):
            if key in enrichment:
                workload[key] = enrichment[key]
        unknown = set(enrichment) - {"configurations", "dependencies"}
        if unknown:
            warnings.append(f"enrichment keys ignored (not mergeable): {sorted(unknown)}")

    return workload, warnings


def find_descriptors(repo_dir: Path):
    for choreo_dir in sorted(repo_dir.rglob(".choreo")):
        if not choreo_dir.is_dir() or ".git" in choreo_dir.parts:
            continue
        for candidate in DESCRIPTOR_NAMES:
            f = choreo_dir / candidate
            if f.is_file():
                yield f
                break


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repos", nargs="+", type=Path, help="repo checkout directories")
    ap.add_argument("--write", action="store_true", help="write workload.yaml into the repo tree")
    ap.add_argument("--force", action="store_true", help="overwrite an existing workload.yaml")
    ap.add_argument("--check", action="store_true",
                    help="drift check: exit 1 if any workload.yaml is missing or differs")
    ap.add_argument("--enrich", type=Path, default=None,
                    help="YAML enrichment artifact keyed by app path (configurations/dependencies)")
    args = ap.parse_args()

    if args.check and (args.write or args.force):
        ap.error("--check cannot be combined with --write/--force")

    enrichment_map = {}
    if args.enrich:
        enrichment_map = yaml.safe_load(args.enrich.read_text()) or {}
        if not isinstance(enrichment_map, dict):
            ap.error(f"enrichment file {args.enrich} must be a YAML mapping keyed by app path")

    exit_code = 0
    generated = 0
    drift = 0
    for repo in args.repos:
        if not repo.is_dir():
            print(f"ERROR: {repo} is not a directory", file=sys.stderr)
            exit_code = 1
            continue
        found = False
        for desc_file in find_descriptors(repo):
            found = True
            app_dir = desc_file.parent.parent
            rel = app_dir.relative_to(repo)
            label = f"{repo.name}/{rel}" if str(rel) != "." else repo.name
            try:
                desc = yaml.safe_load(desc_file.read_text()) or {}
            except yaml.YAMLError as e:
                print(f"== {label}\n  ERROR: cannot parse {desc_file.name}: {e}")
                exit_code = 1
                continue

            enrichment = enrichment_map.get(str(rel))
            workload, warnings = convert_descriptor(desc, app_dir, desc_file.name, enrichment)
            target = app_dir / "workload.yaml"
            body = HEADER.format(src=desc_file.name) + yaml.safe_dump(
                workload, sort_keys=False, default_flow_style=False
            )

            print(f"== {label}  (schemaVersion {desc.get('schemaVersion', '?')})")
            n_eps = len(workload.get("endpoints", []))
            mode = "(check)" if args.check else (target if args.write else "(dry run)")
            print(f"  endpoints: {n_eps}  ->  {mode}")
            for w in warnings:
                print(f"  WARN: {w}")

            if args.check:
                if not target.exists():
                    print("  DRIFT: workload.yaml missing")
                    drift += 1
                elif target.read_text() != body:
                    print("  DRIFT: workload.yaml differs from generated content")
                    drift += 1
                else:
                    print("  in sync")
                continue

            if args.write:
                if target.exists() and not args.force:
                    existing = target.read_text()
                    if existing == body:
                        print("  unchanged (already up to date)")
                    else:
                        print("  SKIP: workload.yaml exists and differs (use --force)")
                        exit_code = 1
                    continue
                target.write_text(body)
                generated += 1
            else:
                print("  --- generated content ---")
                for line in body.splitlines():
                    print(f"  | {line}")
        if not found:
            print(f"== {repo.name}\n  WARN: no .choreo descriptors found")

    if args.check:
        print(f"\n{drift} file(s) out of sync")
        return 1 if drift else exit_code
    if args.write:
        print(f"\n{generated} workload.yaml file(s) written")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
