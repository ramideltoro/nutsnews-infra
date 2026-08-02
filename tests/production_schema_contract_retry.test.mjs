import assert from "node:assert/strict";
import test from "node:test";

import {
  fetchWithRetry,
  verifyProductionSchemaContract,
} from "../scripts/verify_production_schema_contract.mjs";

const sourceCommit = "a".repeat(40);
const migrationHead = "20260717113000";
const schemaVersion = "20260712170000";
const supabaseProjectRef = "mpqfulvvagyzqneiaqky";
const fingerprint = "b".repeat(32);

function response(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: { cancel: async () => {} },
    json: async () => payload,
  };
}

function runtimeConfigResponse() {
  return response(200, {
    runtimeEnv: "production",
    sideEffectsMode: "live",
    supabaseUrl: `https://${supabaseProjectRef}.supabase.co`,
    supabaseAnonKey: "public-test-key",
  });
}

function schemaResponse(overrides = {}) {
  return response(200, {
    migration_head: migrationHead,
    legacy_schema_version: schemaVersion,
    expected_schema_fingerprint: fingerprint,
    actual_schema_fingerprint: fingerprint,
    ...overrides,
  });
}

test("schema verification retries transient 504 and 429 responses before succeeding", async () => {
  const rpcResponses = [response(504, {}), response(429, {}), schemaResponse()];
  const delays = [];
  let rpcCalls = 0;
  const fetchImpl = async (url) => {
    if (new URL(url).pathname === "/api/runtime-config") return runtimeConfigResponse();
    rpcCalls += 1;
    return rpcResponses.shift();
  };

  const result = await verifyProductionSchemaContract({
    sourceCommit,
    migrationHead,
    schemaVersion,
    fetchImpl,
    sleepImpl: async (delay) => delays.push(delay),
    requestTimeoutMs: 100,
  });

  assert.deepEqual(result, { supabaseProjectRef });
  assert.equal(rpcCalls, 3);
  assert.deepEqual(delays, [5_000, 10_000]);
});

test("retry exhaustion reports an upstream outage without claiming a migration is required", async () => {
  await assert.rejects(
    fetchWithRetry(
      "Production Supabase schema contract RPC",
      new URL(`https://${supabaseProjectRef}.supabase.co/rest/v1/rpc/nutsnews_migration_schema_contract`),
      {},
      {
        fetchImpl: async () => response(504, {}),
        sleepImpl: async () => {},
        maxAttempts: 3,
        requestTimeoutMs: 100,
      },
    ),
    (error) => {
      assert.match(error.message, /remained unavailable after 3 attempts \(HTTP 504\)/);
      assert.doesNotMatch(error.message, /production-supabase-migration/);
      return true;
    },
  );
});

test("non-retryable RPC responses remain fail-closed migration errors", async () => {
  let rpcCalls = 0;
  await assert.rejects(
    verifyProductionSchemaContract({
      sourceCommit,
      migrationHead,
      schemaVersion,
      fetchImpl: async (url) => {
        if (new URL(url).pathname === "/api/runtime-config") return runtimeConfigResponse();
        rpcCalls += 1;
        return response(404, {});
      },
      sleepImpl: async () => {},
      requestTimeoutMs: 100,
    }),
    /schema contract RPC returned HTTP 404.*production-supabase-migration/s,
  );
  assert.equal(rpcCalls, 1);
});

test("a real schema mismatch still requires migration", async () => {
  await assert.rejects(
    verifyProductionSchemaContract({
      sourceCommit,
      migrationHead,
      schemaVersion,
      fetchImpl: async (url) => {
        if (new URL(url).pathname === "/api/runtime-config") return runtimeConfigResponse();
        return schemaResponse({ actual_schema_fingerprint: "c".repeat(32) });
      },
      sleepImpl: async () => {},
      requestTimeoutMs: 100,
    }),
    /Production Supabase is not compatible.*production-supabase-migration/s,
  );
});
