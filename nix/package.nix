# paperless-llm — Python application (uv.lock → nix via uv2nix) + built
# frontend + a wrapper that pins the two runtime roots.
#
# backend/app/config.py resolves the install layout through settings:
#   PLLM_ROOT_DIR       -> directory holding alembic.ini + alembic/
#   PLLM_FRONTEND_DIST  -> the built SPA (index.html + assets/)
# Nix installs both into this derivation's own layout and exports them from
# the wrapper, so nothing depends on where site-packages ends up or on the
# container's /app/backend + /app/frontend/dist tree.
{
  lib,
  pkgs,
  python,
  pyproject-nix,
  uv2nix,
  pyproject-build-systems,
  # Orientation detection only (see osdTraineddata below). Off = the app
  # logs "OCR auto-rotate disabled" and works without it.
  withTesseract ? true,
}:

let
  inherit (lib) optionalString;

  backendSrc = ../backend;

  version = (lib.importTOML (backendSrc + "/pyproject.toml")).project.version;

  # The application code, the alembic tree and the lock file — nothing else
  # (tests/fixtures/corpus stays out of the store copy).
  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      (backendSrc + "/app")
      (backendSrc + "/alembic")
      (backendSrc + "/alembic.ini")
      (backendSrc + "/pyproject.toml")
      (backendSrc + "/uv.lock")
    ];
  };

  # uv.lock is the single source of Python dependencies: uv2nix renders it
  # into a package set, wheels preferred so no compiler ends up in the
  # build inputs.
  workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = backendSrc; };

  pythonSet =
    (pkgs.callPackage pyproject-nix.build.packages { inherit python; }).overrideScope
      (
        lib.composeManyExtensions [
          pyproject-build-systems.overlays.wheel
          # Parentheses are load-bearing: list elements are not function
          # applications, so without them this entry is two overlays (the
          # unapplied mkPyprojectOverlay plus its attrset, neither of which is
          # an overlay) and the composed extension recurses into itself.
          (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
        ]
      );

  # Runtime dependencies only (uv.lock's default group). The app itself is
  # installed from source below — it is not a lock entry.
  venv = pythonSet.mkVirtualEnv "paperless-llm-venv" workspace.deps.default;

  # `npm run build` (tsc -b && vite build) → dist/. The lock file is
  # authoritative; refresh the hash after a dependency bump with:
  #   nix shell nixpkgs#prefetch-npm-deps -c \
  #     prefetch-npm-deps frontend/package-lock.json
  frontend = pkgs.buildNpmPackage {
    pname = "paperless-llm-frontend";
    inherit version;
    src = ../frontend;
    nodejs = pkgs.nodejs_24;
    npmDepsHash = "sha256-n7LuFP5AdxlP7YcKAB6hpJL5D1TuudvLm0uBYnkCmyE=";
    npmFlags = [
      "--no-audit"
      "--no-fund"
    ];
    installPhase = ''
      runHook preInstall
      mkdir -p $out/dist
      cp -r dist/. $out/dist/
      runHook postInstall
    '';
  };

  # tesseract is only used for orientation detection: the app shells out to
  # `tesseract stdin stdout --psm 0`, which needs the `osd` traineddata.
  # nixpkgs' tesseract ships the engine without any language data, so the
  # one file OSD needs comes from the upstream tessdata_fast repo (the same
  # data Debian's tesseract-ocr-osd package provides).
  osdTraineddata = pkgs.fetchurl {
    url = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/osd.traineddata";
    sha256 = "1zq0dfliavglmix7zzrqdxz1w01rm1f1x1352bqn8xf4zivdbxcw";
  };

  tessdata = pkgs.runCommand "paperless-llm-tessdata-osd" { } ''
    mkdir -p $out/share/tessdata
    cp ${osdTraineddata} $out/share/tessdata/osd.traineddata
  '';
in
pkgs.stdenvNoCC.mkDerivation {
  pname = "paperless-llm";
  inherit version src;

  dontConfigure = true;
  dontBuild = true;
  dontUnpack = true;

  nativeBuildInputs = [ pkgs.makeWrapper ];

  installPhase = ''
    runHook preInstall

    # PLLM_ROOT_DIR: the alembic tree the app upgrades at startup.
    root=$out/share/paperless-llm
    mkdir -p $root
    cp -r $src/backend/alembic $root/alembic
    install -m644 $src/backend/alembic.ini $root/alembic.ini

    # Application code on PYTHONPATH. A dist-info directory keeps
    # importlib.metadata.version("paperless-llm") working (the /api/meta
    # endpoint otherwise reports "dev"); the project is not a uv.lock
    # package, so there is no wheel build whose metadata could be reused.
    site=$out/lib/paperless-llm
    mkdir -p $site
    cp -r $src/backend/app $site/app
    mkdir -p $site/paperless_llm-${version}.dist-info
    cat > $site/paperless_llm-${version}.dist-info/METADATA <<EOF
    Metadata-Version: 2.1
    Name: paperless-llm
    Version: ${version}
    Summary: Local-LLM metadata pipeline and taxonomy governance for paperless-ngx
    License: AGPL-3.0-or-later
    EOF

    mkdir -p $out/bin
    makeWrapper ${venv}/bin/python $out/bin/paperless-llm \
      --add-flags "-m app.cli" \
      --prefix PYTHONPATH : "$site" \
      --set-default PLLM_ROOT_DIR "$root" \
      --set-default PLLM_FRONTEND_DIST "${frontend}/dist" \
      ${optionalString withTesseract ''
        --prefix PATH : ${lib.makeBinPath [ pkgs.tesseract ]} \
        --set-default TESSDATA_PREFIX "${tessdata}/share/tessdata"
      ''}

    runHook postInstall
  '';

  meta = {
    description = "Local-LLM metadata pipeline and taxonomy governance for paperless-ngx";
    homepage = "https://github.com/Luminger/paperless-llm";
    license = lib.licenses.agpl3Plus;
    mainProgram = "paperless-llm";
    platforms = lib.platforms.unix;
  };
}
