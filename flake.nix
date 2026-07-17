{
  description = "paperless-llm — local-LLM metadata pipeline and taxonomy governance for paperless-ngx";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python313;
      in
      {
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
