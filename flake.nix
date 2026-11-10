{
  description = "paperless-llm — local-LLM metadata pipeline and taxonomy governance for paperless-ngx";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";

    # backend/uv.lock -> nix package set (pyproject.nix + uv2nix), and the
    # PEP-517 build systems those packages are built with.
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
    }:
    {
      # System-independent, like every NixOS module: consumers import it and
      # the per-system package default is resolved inside the module.
      nixosModules = {
        default = import ./nix/module.nix self;
        paperless-llm = self.nixosModules.default;
      };
    }
    // flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python313;

        paperless-llm = pkgs.callPackage ./nix/package.nix {
          inherit python pyproject-nix uv2nix pyproject-build-systems;
        };

        # The release version lives in backend/pyproject.toml; the frontend
        # package.json carries the same one. `paperless-llm-version` below
        # pins them together so the package metadata cannot drift.
        pyprojectVersion = (pkgs.lib.importTOML ./backend/pyproject.toml).project.version;
        frontendVersion = (pkgs.lib.importJSON ./frontend/package.json).version;

        # A minimal `services.paperless-llm` config, evaluated for
        # x86_64-linux so it is exercised from any host. Evaluation only —
        # nothing is built.
        moduleEval = nixpkgs.lib.nixosSystem {
          system = "x86_64-linux";
          modules = [
            self.nixosModules.default
            {
              system.stateVersion = "25.11";
              services.paperless-llm = {
                enable = true;
                paperless = {
                  baseUrl = "http://paperless.example:8000";
                  tokenFile = "/run/secrets/paperless-llm-token";
                };
                llm.agent = {
                  baseUrl = "http://127.0.0.1:8001/v1";
                  model = "qwen3.6-27b";
                };
                auth.sessionSecretFile = "/run/secrets/paperless-llm-session";
                webhook.publicUrl = "http://paperless-llm.example:8100";
              };
            }
          ];
        };
      in
      {
        packages = {
          inherit paperless-llm;
          default = paperless-llm;
        };

        checks = {
          # Instantiation smoke test: the package instantiates and the module
          # produces a system closure for a minimal config. Cheap enough to
          # run anywhere; the build-level smoke test is `paperless-llm-cli`
          # (Linux only, it needs the package built).
          paperless-llm = pkgs.runCommand "paperless-llm-eval-check" { } ''
            mkdir -p $out
            echo "${paperless-llm.drvPath}" > $out/package.drv-path
            echo "${moduleEval.config.system.build.toplevel.drvPath}" > $out/nixos-system.drv-path
            echo "${moduleEval.config.systemd.services.paperless-llm.serviceConfig.ExecStart}" \
              > $out/exec-start
          '';

          # version consistency: backend/pyproject.toml is authoritative,
          # frontend/package.json and the derivation must agree with it.
          paperless-llm-version = pkgs.runCommand "paperless-llm-version-check" { } ''
            fail() { echo "$1" >&2; exit 1; }

            test "${paperless-llm.version}" = "${pyprojectVersion}" \
              || fail "package version ${paperless-llm.version} != pyproject.toml ${pyprojectVersion}"
            test "${frontendVersion}" = "${pyprojectVersion}" \
              || fail "frontend/package.json ${frontendVersion} != pyproject.toml ${pyprojectVersion}"
            grep -qF '"version": "${pyprojectVersion}"' ${./frontend/package.json} \
              || fail "frontend/package.json does not declare version ${pyprojectVersion}"

            mkdir -p $out
            echo "${pyprojectVersion}" > $out/version
          '';
        }
        // pkgs.lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
          # Build-level smoke test: the wrapper must start the app, and the
          # app must find its alembic tree through PLLM_ROOT_DIR. Needs a
          # real build, so it is Linux-only (the deployment target).
          paperless-llm-cli = pkgs.runCommand "paperless-llm-cli-check" { } ''
            export HOME=$(mktemp -d)
            ${paperless-llm}/bin/paperless-llm --help > /dev/null
            ${paperless-llm}/bin/paperless-llm serve --help > /dev/null
            ${paperless-llm}/bin/paperless-llm seed --help > /dev/null
            test -f ${paperless-llm}/share/paperless-llm/alembic.ini
            test -d ${paperless-llm}/share/paperless-llm/alembic
            mkdir -p $out
            echo "ok" > $out/result
          '';
        };

        devShells.default = pkgs.mkShell {
          packages = [
            # Backend — the package itself is managed by uv; nix only
            # provides the interpreter and uv.
            python
            pkgs.uv

            # Frontend
            pkgs.nodejs_24

            # Test corpus generation & OCR-adjacent tooling used by scripts
            pkgs.ruff

            # podman is provided by the host system (NixOS module), not
            # the dev shell. compose is invoked as `podman compose`.
          ];

          env = {
            # Pin uv to the nix-provided interpreter so venvs are
            # reproducible and don't download standalone CPython builds
            # that would need nix-ld patching.
            UV_PYTHON = python.interpreter;
            UV_PYTHON_DOWNLOADS = "never";

            # manylinux wheels (pydantic-core, pymupdf, ...) resolve their
            # native deps through the loader; give them the usual suspects.
            LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
              pkgs.stdenv.cc.cc.lib
              pkgs.zlib
            ];
          };

          shellHook = ''
            echo "paperless-llm dev shell — python $(${python.interpreter} --version 2>&1 | cut -d' ' -f2), uv $(uv --version | cut -d' ' -f2), node $(node --version)"
          '';
        };
      }
    );
}
