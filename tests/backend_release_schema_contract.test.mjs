import assert from "node:assert/strict";
import test from "node:test";

import {
  APPROVED_BACKEND_API_URL,
  verifyBackendReleaseSchemaContract,
} from "../scripts/verify_backend_release_schema_contract.mjs";

const migrationHead = "20260802040522";
const schemaVersion = "20260712170000";
const fingerprint = "a".repeat(32);
const token = "protected-backend-token-fixture";

function appEnvironment(overrides = {}) {
  return JSON.stringify({
    NUTSNEWS_DATABASE_PROVIDER_MODE: "backend_postgres_primary",
    NUTSNEWS_BACKEND_API_URL: APPROVED_BACKEND_API_URL,
    NUTSNEWS_BACKEND_API_TOKEN: token,
    ...overrides,
  });
}

function response(payload, { status = 200, contentType = "application/json; charset=utf-8" } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ "content-type": contentType }),
    json: async () => payload,
  };
}

function schemaResponse(overrides = {}) {
  return response([
    {
      migration_head: migrationHead,
      legacy_schema_version: schemaVersion,
      expected_schema_fingerprint: fingerprint,
      actual_schema_fingerprint: fingerprint,
      ...overrides,
    },
  ]);
}

test("verifies the exact active backend contract without exposing the token", async () => {
  let request;
  const result = await verifyBackendReleaseSchemaContract({
    rawAppEnvironment: appEnvironment(),
    migrationHead,
    schemaVersion,
    fetchImpl: async (url, options) => {
      request = { url: String(url), options };
      return schemaResponse();
    },
  });

  assert.deepEqual(result, {
    providerMode: "backend_postgres_primary",
    migrationHead,
    schemaVersion,
  });
  assert.equal(request.url, `${APPROVED_BACKEND_API_URL}/load-readiness-schema-contract`);
  assert.equal(request.options.redirect, "error");
  assert.deepEqual(JSON.parse(request.options.body), { providerMode: "backend_postgres_primary" });
  assert.equal(request.options.headers.authorization, `Bearer ${token}`);
});

test("rejects any backend URL other than the exact protected endpoint", async () => {
  await assert.rejects(
    verifyBackendReleaseSchemaContract({
      rawAppEnvironment: appEnvironment({
        NUTSNEWS_BACKEND_API_URL: `${APPROVED_BACKEND_API_URL}/`,
      }),
      migrationHead,
      schemaVersion,
    }),
    /not the approved exact endpoint/,
  );
});

test("requires the active provider to remain backend PostgreSQL primary", async () => {
  await assert.rejects(
    verifyBackendReleaseSchemaContract({
      rawAppEnvironment: appEnvironment({ NUTSNEWS_DATABASE_PROVIDER_MODE: "supabase_primary" }),
      migrationHead,
      schemaVersion,
    }),
    /must remain backend_postgres_primary/,
  );
});

test("fails closed for migration head and fingerprint mismatches", async () => {
  for (const row of [
    { migration_head: "20260717113000" },
    { actual_schema_fingerprint: "b".repeat(32) },
  ]) {
    await assert.rejects(
      verifyBackendReleaseSchemaContract({
        rawAppEnvironment: appEnvironment(),
        migrationHead,
        schemaVersion,
        fetchImpl: async () => schemaResponse(row),
      }),
      /backend-postgres-migration\.yml/,
    );
  }
});

test("redirect and request failures do not disclose the protected token", async () => {
  let message = "";
  try {
    await verifyBackendReleaseSchemaContract({
      rawAppEnvironment: appEnvironment(),
      migrationHead,
      schemaVersion,
      fetchImpl: async () => {
        throw new TypeError(`redirect refused for Bearer ${token}`);
      },
    });
  } catch (error) {
    message = error.message;
  }

  assert.match(message, /schema contract request failed/);
  assert.doesNotMatch(message, new RegExp(token));
});
