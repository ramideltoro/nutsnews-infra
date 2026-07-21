import { DurableObject } from "cloudflare:workers";
import {
  DNS_STATE,
  applyDnsUpdateSuccess,
  classifyDnsRecords,
  evaluateFailover,
  normalizeState,
  publicStatus,
  readConfig,
  sanitizeSummary,
} from "./core.mjs";

const API_BASE = "https://api.cloudflare.com/client/v4";
const STATE_KEY = "failover-state";

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload, null, 2), {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "application/json; charset=utf-8",
    },
  });
}

function textResponse(message, status = 200) {
  return new Response(message, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "text/plain; charset=utf-8",
    },
  });
}

function adminAuthorized(request, env) {
  const token = String(env.ADMIN_TOKEN || "");
  const header = request.headers.get("Authorization") || "";
  return token.length >= 24 && header === `Bearer ${token}`;
}

function controllerStub(env) {
  const config = readConfig(env);
  const id = env.DNS_FAILOVER.idFromName(config.controllerName);
  return env.DNS_FAILOVER.get(id);
}

async function requestJson(request) {
  const text = await request.text();
  if (!text.trim()) {
    return {};
  }
  try {
    return JSON.parse(text);
  } catch {
    throw new Error("Request body must be valid JSON.");
  }
}

function withCacheBust(url) {
  const parsed = new URL(url);
  parsed.searchParams.set("dns_failover_check", String(Date.now()));
  return parsed.toString();
}

async function cloudflareApi(env, method, path, payload) {
  const token = String(env.CLOUDFLARE_DNS_API_TOKEN || "");
  if (!token) {
    throw new Error("Cloudflare DNS API token is not configured.");
  }
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  let body = {};
  try {
    body = await response.json();
  } catch {
    body = {};
  }
  if (!response.ok || body.success !== true) {
    throw new Error(`Cloudflare DNS API ${method} ${path} failed with HTTP ${response.status}.`);
  }
  return body.result;
}

async function fetchManagedDnsRecords(env, config) {
  if (!config.zoneId || !config.dnsRecords.length) {
    return [];
  }
  const records = [];
  for (const record of config.dnsRecords) {
    const result = await cloudflareApi(
      env,
      "GET",
      `/zones/${config.zoneId}/dns_records/${record.id}`,
    );
    records.push(result);
  }
  return records;
}

async function updateManagedDnsRecords(env, config, action) {
  if (!config.zoneId || !config.dnsRecords.length) {
    throw new Error("Cloudflare zone and DNS record configuration is required for DNS updates.");
  }
  for (const record of config.dnsRecords) {
    await cloudflareApi(env, "PATCH", `/zones/${config.zoneId}/dns_records/${record.id}`, {
      comment: `Managed by NutsNews DNS failover controller (${action.reason})`,
      content: action.targetContent,
      name: record.name,
      proxied: config.dnsProxied,
      ttl: config.dnsTtl,
      type: record.type,
    });
  }
}

async function checkVpsHealth(config) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort("timeout"), config.healthTimeoutMs);
  try {
    const response = await fetch(withCacheBust(config.healthCheckUrl), {
      cache: "no-store",
      headers: { Accept: "application/json,text/plain;q=0.9,*/*;q=0.1" },
      signal: controller.signal,
    });
    if (!response.ok) {
      return { ok: false, error: `VPS readiness returned HTTP ${response.status}.` };
    }
    const contentType = response.headers.get("Content-Type") || "";
    if (contentType.includes("application/json")) {
      const payload = await response.json();
      const deploymentTarget = payload.deployment_target || payload.deploymentTarget || "";
      if (config.expectedReadinessTarget && deploymentTarget && deploymentTarget !== config.expectedReadinessTarget) {
        return {
          ok: false,
          error: `VPS readiness target was ${deploymentTarget}, expected ${config.expectedReadinessTarget}.`,
        };
      }
    }
    return { ok: true };
  } catch (error) {
    return { ok: false, error: sanitizeSummary(error?.message || error, "VPS readiness request failed.") };
  } finally {
    clearTimeout(timeout);
  }
}

function logEvent(event) {
  console.log(JSON.stringify({ service: "nutsnews-dns-failover", ...event }));
}

export class DnsFailoverController extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
  }

  async storedState() {
    return normalizeState((await this.ctx.storage.get(STATE_KEY)) || {});
  }

  async saveState(state) {
    await this.ctx.storage.put(STATE_KEY, normalizeState(state));
  }

  async scheduleNext(delayMs = null) {
    const config = readConfig(this.env);
    const nextAlarm = Date.now() + (delayMs ?? config.checkIntervalMs);
    await this.ctx.storage.setAlarm(nextAlarm);
    return nextAlarm;
  }

  async bootstrapAlarm() {
    const config = readConfig(this.env);
    const currentAlarm = await this.ctx.storage.getAlarm();
    const now = Date.now();
    if (!currentAlarm || currentAlarm < now || currentAlarm > now + config.checkIntervalMs * 4) {
      return this.scheduleNext(1000);
    }
    return currentAlarm;
  }

  async runCheck(source) {
    const config = readConfig(this.env);
    let state = await this.storedState();
    let dnsState = DNS_STATE.UNKNOWN;
    let health = { ok: false, error: "Health check did not run." };

    try {
      health = await checkVpsHealth(config);
    } catch (error) {
      health = {
        ok: false,
        error: sanitizeSummary(error?.message || error, "VPS readiness request failed."),
      };
    }

    try {
      const dnsRecords = await fetchManagedDnsRecords(this.env, config);
      dnsState = classifyDnsRecords(dnsRecords, config);
    } catch (error) {
      dnsState = DNS_STATE.UNKNOWN;
      if (health.ok) {
        health = {
          ok: true,
          warning: sanitizeSummary(error?.message || error, "Cloudflare DNS state read failed."),
        };
      }
    }

    const decision = evaluateFailover({
      previousState: state,
      health,
      observedDnsState: dnsState,
      config,
    });
    state = decision.state;

    if (decision.action) {
      try {
        await updateManagedDnsRecords(this.env, config, decision.action);
        state = applyDnsUpdateSuccess(state, decision.action);
      } catch (error) {
        state.lastDnsAction = `failed:${decision.action.reason}:${decision.action.target}`;
        state.lastErrorSummary = sanitizeSummary(error?.message || error, "Cloudflare DNS update failed.");
      }
    }

    await this.saveState(state);
    logEvent({
      source,
      health: state.lastHealthStatus,
      dns_state: state.activeDnsTarget,
      dns_action: state.lastDnsAction,
      failure_count: state.consecutiveFailureCount,
      recovery_count: state.consecutiveRecoveryCount,
      manual_lock: state.manualLock,
    });
    return { state, config };
  }

  async alarm() {
    try {
      await this.runCheck("alarm");
    } catch (error) {
      const state = await this.storedState();
      state.lastErrorSummary = sanitizeSummary(error?.message || error, "Durable Object alarm failed.");
      state.lastDnsAction = "failed:alarm";
      await this.saveState(state);
      logEvent({ source: "alarm", dns_action: state.lastDnsAction, error: state.lastErrorSummary });
    } finally {
      await this.scheduleNext();
    }
  }

  async fetch(request) {
    const url = new URL(request.url);
    const config = readConfig(this.env);

    if (request.method === "GET" && url.pathname === "/status") {
      return jsonResponse(publicStatus(await this.storedState(), config, await this.ctx.storage.getAlarm()));
    }

    if (request.method === "POST" && url.pathname === "/watchdog") {
      const alarmTimestamp = await this.bootstrapAlarm();
      return jsonResponse({ ok: true, alarmScheduledAt: new Date(alarmTimestamp).toISOString() });
    }

    if (request.method === "POST" && url.pathname === "/check-now") {
      const result = await this.runCheck("manual-check");
      await this.scheduleNext();
      return jsonResponse(publicStatus(result.state, result.config, await this.ctx.storage.getAlarm()));
    }

    if (request.method === "POST" && url.pathname === "/manual-lock") {
      const body = await requestJson(request);
      if (typeof body.locked !== "boolean") {
        return jsonResponse({ ok: false, error: "manual-lock requires boolean locked." }, 400);
      }
      const state = await this.storedState();
      state.manualLock = body.locked;
      state.manualLockReason = sanitizeSummary(body.reason || (body.locked ? "manual lock" : "manual unlock"));
      state.lastDnsAction = body.locked ? "manual:locked" : "manual:unlocked";
      await this.saveState(state);
      await this.scheduleNext();
      return jsonResponse(publicStatus(state, config, await this.ctx.storage.getAlarm()));
    }

    if (request.method === "POST" && (url.pathname === "/manual-failover" || url.pathname === "/manual-failback")) {
      const body = await requestJson(request);
      const failover = url.pathname === "/manual-failover";
      const expectedConfirm = failover ? "failover-to-vercel" : "failback-to-vps";
      if (body.confirm !== expectedConfirm) {
        return jsonResponse({ ok: false, error: `Manual DNS update requires confirm=${expectedConfirm}.` }, 400);
      }
      const action = {
        reason: failover ? "manual_failover" : "manual_failback",
        target: failover ? DNS_STATE.SECONDARY : DNS_STATE.PRIMARY,
        targetContent: failover ? config.secondaryDnsTarget : config.primaryDnsTarget,
      };
      let state = await this.storedState();
      await updateManagedDnsRecords(this.env, config, action);
      state = applyDnsUpdateSuccess(state, action);
      await this.saveState(state);
      await this.scheduleNext();
      return jsonResponse(publicStatus(state, config, await this.ctx.storage.getAlarm()));
    }

    return jsonResponse({ ok: false, error: "Not found." }, 404);
  }
}

export default {
  async scheduled(_controller, env, ctx) {
    const stub = controllerStub(env);
    ctx.waitUntil(stub.fetch("https://dns-failover.internal/watchdog", { method: "POST" }));
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/healthz") {
      return textResponse("ok\n");
    }
    if (!adminAuthorized(request, env)) {
      return jsonResponse({ ok: false, error: "Unauthorized." }, 401);
    }
    return controllerStub(env).fetch(request);
  },
};
