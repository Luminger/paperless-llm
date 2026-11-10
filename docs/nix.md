# Nix & NixOS

The container image (`deploy/Containerfile`) remains the default artifact —
everything below is for people who would rather run paperless-llm as a native
service. This flake ships a `packages.<system>.paperless-llm` (backend from
`backend/uv.lock` via [uv2nix](https://github.com/pyproject-nix/uv2nix), frontend
built with `npm run build`) and a `nixosModules.default` service module.

## Run it without installing

```bash
nix run github:Luminger/paperless-llm -- serve --host 0.0.0.0 --port 8100
```

Configuration is the same as any other deployment: `PLLM_*` environment
variables, a `paperless-llm.toml`, or the Settings UI. At minimum point it at
paperless-ngx and an OpenAI-compatible endpoint:

```bash
PLLM_PAPERLESS__BASE_URL=http://paperless.example:8000 \
PLLM_PAPERLESS__TOKEN=… \
PLLM_LLM__AGENT__BASE_URL=http://llm.example:8001/v1 \
PLLM_LLM__AGENT__MODEL=qwen3.6-27b \
  nix run github:Luminger/paperless-llm -- serve
```

Add the flake as an input to get the package and the module:

```nix
{
  inputs.paperless-llm.url = "github:Luminger/paperless-llm";
}
```

## NixOS module

```nix
{ inputs, ... }:

{
  imports = [ inputs.paperless-llm.nixosModules.default ];

  services.paperless-llm = {
    enable = true;
    # listenAddress defaults to 127.0.0.1 and port to 8100; put a reverse
    # proxy (TLS) in front of it — the app has no TLS of its own and the
    # module opens no firewall ports.
    paperless = {
      baseUrl = "http://127.0.0.1:8000";
      tokenFile = "/run/secrets/paperless-llm-token";
    };
    llm.agent = {
      baseUrl = "http://127.0.0.1:8001/v1";
      model = "qwen3.6-27b";
      maxConcurrent = 1;
    };
    auth.sessionSecretFile = "/run/secrets/paperless-llm-session";
    webhook = {
      secretFile = "/run/secrets/paperless-llm-webhook";
      publicUrl = "https://paperless-llm.example";
    };
  };
}
```

Secrets are always **file paths** (`tokenFile`, `sessionSecretFile`,
`secretFile`, `apiKeyFile`), never literals, so any secret manager
(sops-nix, agenix, clan vars, …) can supply them. The start script reads each
file into the environment variable the app expects, so no secret reaches the
nix store or a unit file. `extraEnvironment` and `environmentFiles` pass
through anything the typed options do not cover — note that env-provided keys
become read-only in the Settings UI.

The unit runs as a dedicated `paperless-llm` system user with `ProtectSystem=strict`,
`ProtectHome`, `PrivateTmp`, `NoNewPrivileges` and `UMask=0077`; `dataDir`
(default `/var/lib/paperless-llm`) is the only writable path and holds the
SQLite database plus the OCR render cache. Database migrations run at startup.

## Why the package sets `PLLM_ROOT_DIR` and `PLLM_FRONTEND_DIST`

The app needs two things that are *not* importable Python modules: the alembic
tree (`alembic.ini` + `alembic/`, upgraded at startup) and the built frontend
(`frontend/dist`, served as the SPA). In a source checkout and in the container
image both live next to the `app` package, so they were resolved relative to
`backend/app/…`. Nix installs `app` into `site-packages`-adjacent paths and the
frontend into its own store path, so the package pins them instead:

| Variable | Points at | Default |
| --- | --- | --- |
| `PLLM_ROOT_DIR` | directory containing `alembic.ini` and `alembic/` | the `backend/` directory next to the `app` package |
| `PLLM_FRONTEND_DIST` | directory containing the built `index.html` + `assets/` | `<root_dir>/../frontend/dist` |

The wrapper the flake builds sets both, so a nix install never depends on the
site-packages layout.

Both are ordinary settings and can also be set by hand for non-nix installs:

```bash
PLLM_ROOT_DIR=/opt/paperless-llm \
PLLM_FRONTEND_DIST=/opt/paperless-llm/frontend/dist \
  paperless-llm serve
```

## `withTesseract`

The package argument `withTesseract` (default `true`) puts `tesseract` plus the
`osd` traineddata on the wrapper `PATH` and sets `TESSDATA_PREFIX`. It is used
for **orientation detection only** — paperless-llm shells out to
`tesseract … --psm 0` to detect flipped or sideways scans before a vision model
reads them. Without it the app logs `OCR auto-rotate disabled` and keeps
working, so `withTesseract = false` is a legitimate, smaller build:

```nix
inputs.paperless-llm.packages.x86_64-linux.paperless-llm.override {
  withTesseract = false;
}
```

## Checks and development

```bash
nix flake check          # needs a Linux builder for `paperless-llm-cli`
nix build .#paperless-llm
nix develop              # uv, python 3.13, node 24, ruff — see docs/development.md
```

`nix flake check` runs three checks: `paperless-llm` (package + module
instantiation), `paperless-llm-version` (`backend/pyproject.toml` version must
match `frontend/package.json` and the derivation) and, on Linux,
`paperless-llm-cli` (runs `paperless-llm --help` against the built package).
