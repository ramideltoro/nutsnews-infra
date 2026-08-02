#!/usr/bin/env node

import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

export const APPROVED_BACKEND_API_URL = "https://backend.nutsnews.com/api/app/db";
export const BACKEND_MIGRATION_WORKFLOW =
  "https://github.com/ramideltoro/nutsnews-backend/actions/workflows/backend-postgres-migration.yml";
export const DEFAULT_REQUEST_TIMEOUT_MS = 15_000;

function migrationRequired(message, migrationHead) {
  throw new Error(
    `${message} Run ${BACKEND_MIGRATION_WORKFLOW} for migration head ${migrationHead}, then retry the protected release.`,
  );
}

export function parseProtectedAppEnvironment(rawAppEnvironment) {
  let values;
  try {
    values = JSON.parse(rawAppEnvironment);
  } catch {
    throw new Error("Protected application environment must be valid JSON.");
  }

  if (!values || Array.isArray(values) || typeof values !== "object") {
    throw new Error("Protected application environment must be a JSON object.");
  }

  const providerMode = String(values.NUTSNEWS_DATABASE_PROVIDER_MODE ?? "").trim();
  const baseUrl = String(values.NUTSNEWS_BACKEND_API_URL ?? "").trim();
  const token = String(values.NUTSNEWS_BACKEND_API_TOKEN ?? "").trim();

  if (providerMode !== "backend_postgres_primary") {
    throw new Error(
      "Protected production ownership must remain backend_postgres_primary for an application release.",
    );
  }
  if (baseUrl !== APPROVED_BACKEND_API_URL) {
    throw new Error("Protected production backend API URL is not the approved exact endpoint.");
  }
  if (!token) {
    throw new Error("Protected production backend API token is missing.");
  }

  return { baseUrl, providerMode, token };
}

export async function verifyBackendReleaseSchemaContract({
  rawAppEnvironment,
  migrationHead,
  schemaVersion,
  fetchImpl = globalThis.fetch,
  requestTimeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
}) {
  if (!/^[0-9]{14}$/.test(migrationHead) || !/^[0-9]{14}$/.test(schemaVersion)) {
    throw new Error("Backend schema verification received an invalid release identity.");
  }

  const { baseUrl, providerMode, token } = parseProtectedAppEnvironment(rawAppEnvironment);
  const endpoint = new URL(`${baseUrl}/load-readiness-schema-contract`);

  let response;
  try {
    response = await fetchImpl(endpoint, {
      method: "POST",
      redirect: "error",
      headers: {
        accept: "application/json",
        authorization: `Bearer ${token}`,
        "content-type": "application/json",
        "x-nutsnews-db-client": "protected-release-preflight",
      },
      body: JSON.stringify({ providerMode }),
      signal: AbortSignal.timeout(requestTimeoutMs),
    });
  } catch (error) {
    const failureClass = error instanceof Error && error.name === "TimeoutError" ? "timed out" : "failed";
    throw new Error(`Active backend schema contract request ${failureClass}.`);
  }

  if (!response.ok) {
    throw new Error(`Active backend schema contract returned HTTP ${response.status}.`);
  }
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().startsWith("application/json")) {
    throw new Error("Active backend schema contract did not return JSON.");
  }

  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error("Active backend schema contract returned invalid JSON.");
  }
  const row = Array.isArray(payload) ? payload[0] : payload;
  if (!row || typeof row !== "object") {
    migrationRequired("Active backend schema contract returned no contract row.", migrationHead);
  }

  const expectedFingerprint = row.expected_schema_fingerprint ?? "";
  const actualFingerprint = row.actual_schema_fingerprint ?? "";
  if (
    row.migration_head !== migrationHead ||
    row.legacy_schema_version !== schemaVersion ||
    !/^[a-f0-9]{32}$/.test(expectedFingerprint) ||
    !/^[a-f0-9]{32}$/.test(actualFingerprint) ||
    expectedFingerprint !== actualFingerprint
  ) {
    migrationRequired(
      `Active backend PostgreSQL is not compatible with this release: expected migration_head=${migrationHead} and schema_version=${schemaVersion}.`,
      migrationHead,
    );
  }

  return {
    providerMode,
    migrationHead: row.migration_head,
    schemaVersion: row.legacy_schema_version,
  };
}

async function main() {
  const result = await verifyBackendReleaseSchemaContract({
    rawAppEnvironment: process.env.NUTSNEWS_APP_ENVS_JSON ?? "",
    migrationHead: process.env.RELEASE_MIGRATION_HEAD ?? "",
    schemaVersion: process.env.RELEASE_SCHEMA_VERSION ?? "",
  });
  console.log(
    `Verified active backend PostgreSQL schema contract for ${result.providerMode} at migration head ${result.migrationHead}.`,
  );
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : "Active backend schema verification failed.");
    process.exitCode = 1;
  });
}
