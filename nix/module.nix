# NixOS module for paperless-llm.
#
# Secrets are file paths (`*File`), never literals: consumers bring their own
# secret management (sops-nix, clan vars, agenix, …). The start script reads
# each file into the environment variable the app expects at exec time, so no
# secret is ever written into the world-readable nix store or a unit file.
self:
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib)
    mkEnableOption
    mkIf
    mkOption
    optionalAttrs
    types
    ;

  cfg = config.services.paperless-llm;

  user = "paperless-llm";
  group = "paperless-llm";
  defaultDataDir = "/var/lib/paperless-llm";

  # StateDirectory only covers /var/lib/<name>; any other dataDir needs a
  # tmpfiles rule plus an explicit ReadWritePaths entry.
  usesStateDirectory = cfg.dataDir == defaultDataDir;

  databaseUrl =
    if cfg.databaseUrl != null then
      cfg.databaseUrl
    else
      "sqlite+aiosqlite:///${cfg.dataDir}/paperless_llm.sqlite3";

  # ---------------------------------------------------------------- secrets
  # Environment variable -> file. Read by startScript (never put in the store).
  secretFiles = lib.filterAttrs (_: file: file != null) {
    PLLM_PAPERLESS__TOKEN = cfg.paperless.tokenFile;
    PLLM_AUTH__SESSION_SECRET = cfg.auth.sessionSecretFile;
    PLLM_WEBHOOK__SECRET = cfg.webhook.secretFile;
    PLLM_LLM__AGENT__API_KEY = cfg.llm.agent.apiKeyFile;
    PLLM_LLM__OCR__API_KEY = cfg.llm.ocr.apiKeyFile;
    PLLM_LLM__EMBEDDINGS__API_KEY = cfg.llm.embeddings.apiKeyFile;
    PLLM_LLM__RERANKER__API_KEY = cfg.llm.reranker.apiKeyFile;
  };

  startScript = pkgs.writeShellScript "paperless-llm-start" ''
    set -eu
    ${lib.concatStringsSep "\n" (
      lib.mapAttrsToList (variable: file: ''
        ${variable}="$(cat ${lib.escapeShellArg (toString file)})"
        export ${variable}
      '') secretFiles
    )}
    exec ${lib.getExe cfg.package} serve \
      --host ${lib.escapeShellArg cfg.listenAddress} \
      --port ${toString cfg.port}
  '';

  # ------------------------------------------------------------- environment
  # Only keys that are actually set: an unset option must let the app's own
  # default apply (and stay editable in its Settings UI).
  environment = lib.filterAttrs (_: value: value != null) {
    PLLM_DATA_DIR = cfg.dataDir;
    PLLM_DATABASE_URL = databaseUrl;

    PLLM_PAPERLESS__BASE_URL = cfg.paperless.baseUrl;
    PLLM_PAPERLESS__EXTERNAL_URL = cfg.paperless.externalUrl;
    PLLM_PAPERLESS__VERIFY_TLS = cfg.paperless.verifyTls;

    PLLM_LLM__AGENT__BASE_URL = cfg.llm.agent.baseUrl;
    PLLM_LLM__AGENT__MODEL = cfg.llm.agent.model;
    PLLM_LLM__AGENT__MAX_CONCURRENT = cfg.llm.agent.maxConcurrent;
    PLLM_LLM__AGENT__MAX_INPUT_TOKENS = cfg.llm.agent.maxInputTokens;
    PLLM_LLM__AGENT__TIMEOUT_SECONDS = cfg.llm.agent.timeoutSeconds;
    PLLM_LLM__AGENT__SUPPORTS_STREAMING = cfg.llm.agent.supportsStreaming;

    PLLM_LLM__OCR__BASE_URL = cfg.llm.ocr.baseUrl;
    PLLM_LLM__OCR__MODEL = cfg.llm.ocr.model;
    PLLM_LLM__OCR__MAX_CONCURRENT = cfg.llm.ocr.maxConcurrent;
    PLLM_LLM__OCR__TIMEOUT_SECONDS = cfg.llm.ocr.timeoutSeconds;

    PLLM_LLM__EMBEDDINGS__BASE_URL = cfg.llm.embeddings.baseUrl;
    PLLM_LLM__EMBEDDINGS__MODEL = cfg.llm.embeddings.model;
    PLLM_LLM__EMBEDDINGS__MAX_CONCURRENT = cfg.llm.embeddings.maxConcurrent;

    PLLM_LLM__RERANKER__BASE_URL = cfg.llm.reranker.baseUrl;
    PLLM_LLM__RERANKER__MODEL = cfg.llm.reranker.model;

    PLLM_AUTH__COOKIE_SECURE = cfg.auth.cookieSecure;
    PLLM_AUTH__TRUST_PROXY_HEADERS = cfg.auth.trustProxyHeaders;
    PLLM_AUTH__SESSION_HOURS = cfg.auth.sessionHours;
    PLLM_AUTH__LOGIN_BACKOFF_AFTER = cfg.auth.backoffAfter;
    PLLM_AUTH__LOGIN_BACKOFF_CAP_SECONDS = cfg.auth.backoffCapSeconds;

    PLLM_WEBHOOK__PUBLIC_URL = cfg.webhook.publicUrl;
    PLLM_WEBHOOK__APPLY_POLICY = cfg.webhook.applyPolicy;
  }
  // cfg.extraEnvironment;

  # `systemd.services.*.environment` is an attrset of strings (nixpkgs does
  # the Environment= escaping); booleans and numbers render as PLLM wants
  # them ("true"/"false", decimal).
  environmentValues = lib.mapAttrs (
    _: value: if lib.isBool value then lib.boolToString value else toString value
  ) environment;

  # ---------------------------------------------------------------- profiles
  mkEndpointOptions =
    extra:
    {
      baseUrl = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "OpenAI-compatible endpoint; null keeps the app default.";
      };
      model = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Model name as the server knows it; null keeps the app default.";
      };
      apiKeyFile = mkOption {
        type = types.nullOr types.path;
        default = null;
        example = "/run/secrets/paperless-llm-api-key";
        description = "File whose content is sent as the bearer token.";
      };
    }
    // extra;
in
{
  options.services.paperless-llm = {
    enable = mkEnableOption "paperless-llm, a local-LLM metadata assistant for paperless-ngx";

    package = mkOption {
      type = types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.paperless-llm;
      defaultText = lib.literalExpression "paperless-llm.packages.\${system}.paperless-llm";
      description = "The paperless-llm package to run.";
    };

    listenAddress = mkOption {
      type = types.str;
      default = "127.0.0.1";
      description = ''
        Address the API server binds. Loopback by default: the app has no TLS
        of its own, so put a reverse proxy in front of it for anything else.
      '';
    };

    port = mkOption {
      type = types.port;
      default = 8100;
      description = "Port the API server listens on.";
    };

    dataDir = mkOption {
      type = types.path;
      default = defaultDataDir;
      description = ''
        Directory for the SQLite database and the OCR render cache. Created
        and owned by the service user. Migrations run at startup, so this is
        the only path the service needs write access to (unless
        {option}`databaseUrl` points elsewhere).
      '';
    };

    databaseUrl = mkOption {
      type = types.nullOr types.str;
      default = null;
      defaultText = lib.literalExpression ''"sqlite+aiosqlite:///''${dataDir}/paperless_llm.sqlite3"'';
      example = "postgresql+asyncpg://paperless-llm@localhost/paperless_llm";
      description = ''
        SQLAlchemy URL. PostgreSQL needs the package's `postgres` extra, which
        the default package does not enable.
      '';
    };

    paperless = {
      baseUrl = mkOption {
        type = types.nullOr types.str;
        default = null;
        example = "http://paperless-ngx:8000";
        description = "paperless-ngx API base URL.";
      };

      externalUrl = mkOption {
        type = types.nullOr types.str;
        default = null;
        example = "https://paperless.example.com";
        description = "URL users reach paperless-ngx at (deep links); falls back to the API URL.";
      };

      tokenFile = mkOption {
        type = types.nullOr types.path;
        default = null;
        description = "File with a paperless-ngx API token (background credentials).";
      };

      verifyTls = mkOption {
        type = types.nullOr types.bool;
        default = null;
        description = "Verify the paperless-ngx TLS certificate; null keeps the app default (on).";
      };
    };

    llm = {
      agent = mkOption {
        type = types.submodule {
          options = mkEndpointOptions {
            maxConcurrent = mkOption {
              type = types.nullOr types.ints.positive;
              default = null;
              description = "App-level cap on concurrent requests to this endpoint.";
            };
            maxInputTokens = mkOption {
              type = types.nullOr types.ints.positive;
              default = null;
              description = "Clamp for tool results (document text); keep far below the server context.";
            };
            timeoutSeconds = mkOption {
              type = types.nullOr types.numbers.positive;
              default = null;
              description = "Wall-clock cap per LLM call.";
            };
            supportsStreaming = mkOption {
              type = types.nullOr types.bool;
              default = null;
              description = "Token-level streaming; off for servers with buggy streaming tool-call parsers.";
            };
          };
        };
        default = { };
        description = "Tool-calling chat model used by the agent.";
      };

      ocr = mkOption {
        type = types.submodule {
          options = mkEndpointOptions {
            maxConcurrent = mkOption {
              type = types.nullOr types.ints.positive;
              default = null;
              description = "Endpoint admission for OCR calls; null follows the agent profile.";
            };
            timeoutSeconds = mkOption {
              type = types.nullOr types.numbers.positive;
              default = null;
              description = "Wall-clock cap per OCR call.";
            };
          };
        };
        default = { };
        description = "Vision model for the OCR gate (falls back to the agent endpoint).";
      };

      embeddings = mkOption {
        type = types.submodule {
          options = mkEndpointOptions {
            maxConcurrent = mkOption {
              type = types.nullOr types.ints.positive;
              default = null;
              description = "App-level cap on concurrent embedding requests.";
            };
          };
        };
        default = { };
        description = "Embedding model; configuring it enables the RAG features.";
      };

      reranker = mkOption {
        type = types.submodule {
          options = mkEndpointOptions { };
        };
        default = { };
        description = "Optional Cohere-compatible rerank endpoint.";
      };
    };

    auth = {
      cookieSecure = mkOption {
        type = types.nullOr types.bool;
        default = null;
        description = "Set the session cookie's Secure flag (TLS deployments).";
      };

      sessionSecretFile = mkOption {
        type = types.nullOr types.path;
        default = null;
        description = ''
          File with the cookie signing secret. Empty: the app generates one
          and persists it in its database on first start.
        '';
      };

      trustProxyHeaders = mkOption {
        type = types.nullOr types.bool;
        default = null;
        description = ''
          Take the client IP from the first X-Forwarded-For entry. Enable only
          behind a reverse proxy you control — it is trusted blindly and gates
          the login throttle.
        '';
      };

      sessionHours = mkOption {
        type = types.nullOr types.ints.positive;
        default = null;
        description = "Signed session cookie lifetime.";
      };

      backoffAfter = mkOption {
        type = types.nullOr types.ints.positive;
        default = null;
        description = "Free failed logins per (user, IP) before the exponential backoff kicks in.";
      };

      backoffCapSeconds = mkOption {
        type = types.nullOr types.ints.positive;
        default = null;
        description = "Upper bound of the login backoff.";
      };
    };

    webhook = {
      secretFile = mkOption {
        type = types.nullOr types.path;
        default = null;
        description = ''
          File with the shared secret expected in the X-PLLM-Token header of
          webhook ingress. Unset disables the webhook endpoint.
        '';
      };

      publicUrl = mkOption {
        type = types.nullOr types.str;
        default = null;
        example = "https://paperless-llm.example.com";
        description = "Base URL paperless-ngx posts workflows to (used by the one-click setup).";
      };

      applyPolicy = mkOption {
        type = types.nullOr (
          types.enum [
            "review"
            "auto"
          ]
        );
        default = null;
        description = "Default policy for sessions created through webhook ingress.";
      };
    };

    environmentFiles = mkOption {
      type = types.listOf types.path;
      default = [ ];
      example = [ "/run/secrets/paperless-llm.env" ];
      description = ''
        Extra systemd environment files (PLLM_… on one KEY=value per line).
        Useful for settings the typed options above do not cover — they are
        applied as the app's env layer, so they win over the UI and the config
        file.
      '';
    };

    extraEnvironment = mkOption {
      type = types.attrsOf types.str;
      default = { };
      example = {
        PLLM_QUEUE__BATCH_CONCURRENCY = "1";
      };
      description = "Raw PLLM_* variables merged last, overriding the typed options above.";
    };
  };

  config = mkIf cfg.enable {
    users.users.${user} = {
      isSystemUser = true;
      inherit group;
      home = cfg.dataDir;
      description = "paperless-llm service user";
    };
    users.groups.${group} = { };

    systemd.services.paperless-llm = {
      description = "paperless-llm — local-LLM metadata assistant for paperless-ngx";
      wantedBy = [ "multi-user.target" ];
      wants = [ "network-online.target" ];
      after = [ "network-online.target" ];

      environment = environmentValues;

      serviceConfig = {
        Type = "simple";
        User = user;
        Group = group;
        # Alembic upgrades the schema at startup; the app then serves.
        ExecStart = startScript;
        Restart = "on-failure";
        RestartSec = 5;
        EnvironmentFile = cfg.environmentFiles;

        # Everything the app writes lives under dataDir (DB, renders, caches,
        # generated session secret). 0077 keeps that private.
        UMask = "0077";
        ReadWritePaths = lib.optionals (!usesStateDirectory) [ cfg.dataDir ];

        # Hardening. No ports are opened here on purpose: the service binds
        # loopback and a reverse proxy owns ingress.
        NoNewPrivileges = true;
        PrivateTmp = true;
        PrivateDevices = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        ProtectClock = true;
        ProtectHostname = true;
        LockPersonality = true;
        RestrictAddressFamilies = [
          "AF_INET"
          "AF_INET6"
          "AF_UNIX"
        ];
        RestrictNamespaces = true;
        RestrictRealtime = true;
        RestrictSUIDSGID = true;
        SystemCallArchitectures = "native";
        SystemCallFilter = [
          "@system-service"
          "~@privileged"
        ];
      }
      // optionalAttrs usesStateDirectory {
        StateDirectory = user;
        StateDirectoryMode = "0700";
      };
    };

    systemd.tmpfiles.settings = optionalAttrs (!usesStateDirectory) {
      "10-paperless-llm"."${cfg.dataDir}".d = {
        inherit user group;
        mode = "0700";
      };
    };
  };
}
