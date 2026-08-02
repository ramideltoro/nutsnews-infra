#!/usr/bin/env node

import { appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

export const DEFAULT_MAX_ATTEMPTS = 4;
export const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;

const RETRYABLE_HTTP_STATUSES = new Set([408, 425, 429]);
const MIGRATION_WORKFLOW =
  "https://github.com/ramideltoro/nutsnews/actions/workflows/production-supabase-migration.yml";

function defaultSleep(milliseconds) {
  return new Promise((resolveSleep) => setTimeout(resolveSleep, milliseconds));
}

export function isRetryableStatus(status) {
  return RETRYABLE_HTTP_STATUSES.has(status) || status >= 500;
}

export async function fetchWithRetry(
  label,
  url,
  options = {},
  {
    fetchImpl = globalThis.fetch,
    sleepImpl = defaultSleep,
    maxAttempts = DEFAULT_MAX_ATTEMPTS,
    requestTimeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
  } = {},
) {
  if (!Number.isInteger(maxAttempts) || maxAttempts < 1) {
    throw new Error(`${label} retry policy requires at least one attempt.`);
  }

  let lastFailure = "unknown failure";

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      const response = await fetchImpl(url, {
        ...options,
        signal: AbortSignal.timeout(requestTimeoutMs),
      });

      if (response.ok || !isRetryableStatus(response.status)) {
        return response;
      }

      lastFailure = `HTTP ${response.status}`;
      await response.body?.cancel?.();
    } catch (error) {
      lastFailure = error instanceof Error ? error.name : "request error";
    }

    if (attempt < maxAttempts) {
      const delayMs = attempt * 5_000;
      console.warn(
        `${label} attempt ${attempt}/${maxAttempts} failed with ${lastFailure}; retrying in ${delayMs / 1_000}s.`,
      );
      await sleepImpl(delayMs);
    }
  }

  throw new Error(
    `${label} remained unavailable after ${maxAttempts} attempts (${lastFailure}). Retry this promotion when the upstream service recovers.`,
  );
}

export async function verifyProductionSchemaContract({
  sourceCommit,
  migrationHead,
  schemaVersion,
  productionOrigin = "https://www.nutsnews.com",
  fetchImpl = globalThis.fetch,
  sleepImpl = defaultSleep,
  maxAttempts = DEFAULT_MAX_ATTEMPTS,
  requestTimeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
}) {
  function migrationRequired(message) {
    throw new Error(
      `${message} Run ${MIGRATION_WORKFLOW} for source ${sourceCommit} and migration head ${migrationHead}, then rerun this promotion.`,
    );
  }

  if (
    !/^[0-9a-f]{40}$/.test(sourceCommit) ||
    !/^[0-9]{14}$/.test(migrationHead) ||
    !/^[0-9]{14}$/.test(schemaVersion)
  ) {
    throw new Error("Production schema verification received invalid release identity.");
  }

  const retryOptions = {
    fetchImpl,
    sleepImpl,
    maxAttempts,
    requestTimeoutMs,
  };
  const configUrl = new URL("/api/runtime-config", productionOrigin);
  const configResponse = await fetchWithRetry(
    "Production runtime config",
    configUrl,
    { headers: { "Cache-Control": "no-cache" } },
    retryOptions,
  );
  if (!configResponse.ok) {
    throw new Error(`Production runtime config returned HTTP ${configResponse.status}.`);
  }
  const config = await configResponse.json();
  if (
    config?.runtimeEnv !== "production" ||
    config?.sideEffectsMode !== "live" ||
    typeof config?.supabaseUrl !== "string" ||
    typeof config?.supabaseAnonKey !== "string"
  ) {
    throw new Error("Production runtime config did not expose the expected Supabase identity.");
  }

  const supabaseUrl = new URL(config.supabaseUrl);
  const refMatch = supabaseUrl.hostname.match(/^([a-z0-9]{20})\.supabase\.co$/);
  if (!refMatch) {
    throw new Error("Production Supabase URL does not identify an approved Supabase project ref.");
  }
  const supabaseProjectRef = refMatch[1];

  const rpcUrl = new URL("/rest/v1/rpc/nutsnews_migration_schema_contract", supabaseUrl);
  const rpcResponse = await fetchWithRetry(
    "Production Supabase schema contract RPC",
    rpcUrl,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        apikey: config.supabaseAnonKey,
        Authorization: `Bearer ${config.supabaseAnonKey}`,
        "Content-Type": "application/json",
      },
      body: "{}",
    },
    retryOptions,
  );
  if (!rpcResponse.ok) {
    migrationRequired(`Production Supabase schema contract RPC returned HTTP ${rpcResponse.status}.`);
  }
  const payload = await rpcResponse.json();
  const row = Array.isArray(payload) ? payload[0] : payload;
  if (!row || typeof row !== "object") {
    migrationRequired("Production Supabase schema contract RPC returned no contract row.");
  }
  if (
    row.migration_head !== migrationHead ||
    row.legacy_schema_version !== schemaVersion ||
    !/^[a-f0-9]{32}$/.test(row.expected_schema_fingerprint ?? "") ||
    row.expected_schema_fingerprint !== row.actual_schema_fingerprint
  ) {
    migrationRequired(
      `Production Supabase is not compatible with this release: expected migration_head=${migrationHead} and schema_version=${schemaVersion}.`,
    );
  }

  return { supabaseProjectRef };
}

async function main() {
  const sourceCommit = process.env.SOURCE_COMMIT ?? "";
  const migrationHead = process.env.MIGRATION_HEAD ?? "";
  const schemaVersion = process.env.SCHEMA_VERSION ?? "";
  const outputPath = process.env.GITHUB_OUTPUT ?? "";

  if (!outputPath) {
    throw new Error("Production schema verification requires GITHUB_OUTPUT.");
  }

  const { supabaseProjectRef } = await verifyProductionSchemaContract({
    sourceCommit,
    migrationHead,
    schemaVersion,
  });
  appendFileSync(outputPath, `supabase_project_ref=${supabaseProjectRef}\n`, "utf8");
  console.log(`Verified production Supabase schema contract for ${sourceCommit}.`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : "Production schema verification failed.");
    process.exitCode = 1;
  });
}
