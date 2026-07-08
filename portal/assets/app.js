const DATA_URL = "/data/status.json";

const $ = (id) => document.getElementById(id);

let currentData = null;

function text(value, fallback = "unknown") {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  return String(value);
}

function escapeHtml(value) {
  return text(value, "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function clamp(value, min = 0, max = 100) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return min;
  }
  return Math.min(Math.max(number, min), max);
}

function bytes(value) {
  const number = Number(value || 0);
  if (number < 1024) {
    return `${number} B`;
  }
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let size = number;
  let unit = "B";
  for (const nextUnit of units) {
    size /= 1024;
    unit = nextUnit;
    if (size < 1024) {
      break;
    }
  }
  return `${size.toFixed(size >= 10 ? 1 : 2)} ${unit}`;
}

function duration(seconds) {
  const total = Number(seconds || 0);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (total < 60) {
    return `${Math.round(total)}s`;
  }
  if (days > 0) {
    return `${days}d ${hours}h`;
  }
  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  return `${minutes}m`;
}

function percent(value) {
  const number = Number(value || 0);
  return `${number.toFixed(1)}%`;
}

function compactTimestamp(value) {
  const raw = text(value);
  if (raw === "unknown") {
    return raw;
  }
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) {
    return raw;
  }
  const month = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][date.getUTCMonth()];
  const day = String(date.getUTCDate()).padStart(2, "0");
  const hour = String(date.getUTCHours()).padStart(2, "0");
  const minute = String(date.getUTCMinutes()).padStart(2, "0");
  return `${month} ${day} ${date.getUTCFullYear()} ${hour}:${minute} UTC`;
}

function shortCommit(value) {
  const commit = text(value);
  return commit.length > 12 ? commit.slice(0, 12) : commit;
}

function levelClass(level) {
  const normalized = String(level).toLowerCase();
  if (
    [
      "critical",
      "over limit",
      "over_limit",
      "failed",
      "inactive",
      "exited",
      "unhealthy",
      "send failed",
      "stale",
      "unavailable",
    ].includes(normalized)
  ) {
    return "pill--danger";
  }
  if (["warning", "degraded", "unknown", "misconfigured", "disabled", "never", "busy", "cached"].includes(normalized)) {
    return "pill--warn";
  }
  if (["ok", "safe", "active", "running", "healthy", "enabled", "configured", "sent", "success", "fresh", "live"].includes(normalized)) {
    return "pill--ok";
  }
  if (["not configured", "not_configured"].includes(normalized)) {
    return "pill--muted";
  }
  return "pill--muted";
}

function stateFromPercent(value, warn = 70, danger = 85) {
  const number = Number(value || 0);
  if (number >= danger) {
    return "danger";
  }
  if (number >= warn) {
    return "warn";
  }
  return "ok";
}

function pill(value) {
  const label = text(value);
  return `<span class="pill ${levelClass(label)}">${escapeHtml(label)}</span>`;
}

function metric(label, value, hint = "") {
  return `
    <article class="metric">
      <div class="metric__label">${escapeHtml(label)}</div>
      <div class="metric__value">${escapeHtml(value)}</div>
      ${hint ? `<div class="metric__hint">${escapeHtml(hint)}</div>` : ""}
    </article>
  `;
}

function stat(label, value, hint = "") {
  return `
    <article class="stat-card">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      ${hint ? `<small>${escapeHtml(hint)}</small>` : ""}
    </article>
  `;
}

function gauge(label, rawValue, hint = "", options = {}) {
  const value = clamp(rawValue);
  const state = options.state || stateFromPercent(value, options.warn ?? 70, options.danger ?? 85);
  const display = options.display || percent(value);
  return `
    <article class="gauge-card gauge-card--${state}" style="--gauge-value: ${value}">
      <div class="gauge-ring" aria-hidden="true">
        <div class="gauge-ring__inner">${escapeHtml(display)}</div>
      </div>
      <div class="gauge-card__body">
        <div class="metric__label">${escapeHtml(label)}</div>
        ${hint ? `<div class="metric__hint">${escapeHtml(hint)}</div>` : ""}
      </div>
    </article>
  `;
}

function temperature(label, rawValue, state, hint = "", displayValue = null) {
  const value = clamp(rawValue);
  const display = displayValue || percent(value);
  return `
    <article class="temperature-card temperature-card--${state}" style="--temperature-value: ${value}">
      <div class="temperature-card__scale" aria-hidden="true">
        <span></span>
      </div>
      <div>
        <div class="metric__label">${escapeHtml(label)}</div>
        <div class="temperature-card__value">${escapeHtml(display)}</div>
        ${hint ? `<div class="metric__hint">${escapeHtml(hint)}</div>` : ""}
      </div>
    </article>
  `;
}

function sparkline(values) {
  const numbers = values.map((item) => Number(item || 0));
  const max = Math.max(...numbers, 1);
  return `
    <span class="sparkline" aria-hidden="true">
      ${numbers
        .map((item) => `<i style="--bar-height: ${Math.max(8, (item / max) * 100)}%"></i>`)
        .join("")}
    </span>
  `;
}

function renderMetrics(id, items) {
  $(id).innerHTML = items.map((item) => metric(item.label, item.value, item.hint)).join("");
}

function renderStats(id, items) {
  $(id).innerHTML = items.map((item) => stat(item.label, item.value, item.hint)).join("");
}

function renderTable(id, rows, emptyMessage, colspan = 7) {
  $(id).innerHTML = rows.length
    ? rows.join("")
    : `<tr><td colspan="${colspan}">${escapeHtml(emptyMessage)}</td></tr>`;
}

function renderLinks(id, links) {
  $(id).innerHTML = (links || [])
    .map((link) => `<li><a href="${escapeHtml(link.url)}" rel="noreferrer">${escapeHtml(link.name)}</a></li>`)
    .join("");
}

function nonOkAlerts(data) {
  return (data.alerts?.items || []).filter((alert) => String(alert.level).toLowerCase() !== "ok");
}

function healthScore(data) {
  const resources = data.resources || {};
  const memory = resources.memory || {};
  const swap = resources.swap || {};
  const disk = resources.disk || {};
  const services = data.services || [];
  const alerts = nonOkAlerts(data);
  let score = 100;

  for (const alert of alerts) {
    score -= String(alert.level).toLowerCase() === "critical" ? 30 : 12;
  }

  score -= Math.max(0, Number(memory.used_percent || 0) - 70) * 0.35;
  score -= Math.max(0, Number(disk.used_percent || 0) - 70) * 0.35;
  score -= Math.max(0, Number(disk.inode_used_percent || 0) - 70) * 0.25;
  score -= Math.max(0, Number(swap.used_percent || 0) - 25) * 0.2;

  const importantInactive = services.filter(
    (service) =>
      ["ssh.service", "docker.service", "fail2ban.service"].includes(service.name) &&
      !["active", "activating"].includes(service.active),
  );
  score -= importantInactive.length * 20;

  return Math.round(clamp(score));
}

function overallState(data) {
  const alerts = data.alerts?.items || [];
  if (alerts.some((alert) => alert.level === "critical")) {
    return "critical";
  }
  if (alerts.some((alert) => alert.level === "warning")) {
    return "warning";
  }
  return healthScore(data) >= 80 ? "ok" : "warning";
}

function serviceHealthPercent(data) {
  const services = data.services || [];
  if (!services.length) {
    return 0;
  }
  const active = services.filter((service) => ["active", "activating"].includes(service.active)).length;
  return Math.round((active / services.length) * 100);
}

function alertPressure(data) {
  const alerts = nonOkAlerts(data);
  if (alerts.some((alert) => alert.level === "critical")) {
    return 100;
  }
  if (alerts.some((alert) => alert.level === "warning")) {
    return 62;
  }
  return 5;
}

function renderOverview(data) {
  const host = data.host || {};
  const gitops = data.gitops || {};
  const lastApply = gitops.last_apply || {};
  const resources = data.resources || {};
  const memory = resources.memory || {};
  const disk = resources.disk || {};
  const score = healthScore(data);
  const state = overallState(data);

  $("health-gauge-grid").innerHTML = gauge("Health Score", score, `Overall state ${state}`, {
    state: score >= 85 ? "ok" : score >= 65 ? "warn" : "danger",
    display: String(score),
  });

  renderMetrics("overview-grid", [
    { label: "Hostname", value: text(host.hostname), hint: text(host.fqdn) },
    { label: "Uptime", value: duration(host.uptime_seconds), hint: text(host.os) },
    { label: "Public IPv4", value: text(host.public_ipv4), hint: `IPv6 ${text(host.public_ipv6)}` },
    { label: "Kernel", value: text(host.kernel), hint: text(host.architecture) },
    { label: "Infra Commit", value: shortCommit(gitops.deployed_commit), hint: text(gitops.repository) },
    { label: "Last Apply", value: text(lastApply.status), hint: text(lastApply.run_url || lastApply.recorded_at) },
  ]);

  const services = serviceHealthPercent(data);
  const alerts = alertPressure(data);
  $("hotspot-grid").innerHTML = [
    temperature("Memory Pressure", memory.used_percent, stateFromPercent(memory.used_percent), `${bytes(memory.used_bytes)} used`),
    temperature("Disk Pressure", disk.used_percent, stateFromPercent(disk.used_percent), `${bytes(disk.free_bytes)} free`),
    temperature("Service Health", services, services >= 90 ? "ok" : services >= 70 ? "warn" : "danger", `${services}% active`),
    temperature("Alert Level", alerts, alerts >= 90 ? "danger" : alerts >= 40 ? "warn" : "ok", `${nonOkAlerts(data).length} active issue(s)`),
  ].join("");

  $("portal-policy").textContent = text(data.portal?.management_policy, "Read-only. Production changes still go through GitOps.");
}

function renderEmailReporting(data) {
  const reporting = data.email_reporting || {};
  const alerts = data.alerts?.items || [];
  const enabledLabel = reporting.enabled ? "enabled" : "disabled";
  const configuredLabel = reporting.configured ? "configured" : "misconfigured";
  const lastSuccess = reporting.last_report_success_at || reporting.last_report_sent_at || "never";
  const lastRun = reporting.last_report_run_at || reporting.updated_at || "never";

  $("email-state").innerHTML = `${pill(enabledLabel)} ${pill(configuredLabel)} ${pill(reporting.status)}`;
  renderMetrics("email-status-grid", [
    { label: "Email Reporting", value: enabledLabel, hint: text(reporting.status) },
    { label: "SMTP Configured", value: reporting.smtp_host_configured ? "yes" : "no", hint: `${text(reporting.recipients_count, "0")} recipient(s)` },
    { label: "Next Report", value: text(reporting.next_report_run_at), hint: text(reporting.timer || "health report timer") },
    { label: "Last Run", value: text(lastRun), hint: `mode ${text(reporting.mode)}` },
    { label: "Last Success", value: text(lastSuccess), hint: "health report email" },
    { label: "Last Error", value: text(reporting.last_error, "none"), hint: text(reporting.email_config_source) },
  ]);
  renderStats("reporting-strip", [
    { label: "Pending Alerts", value: text(reporting.pending_alerts, "0"), hint: text(reporting.last_alert_check_at) },
    { label: "Suppressed", value: text(reporting.suppressed_alerts, "0"), hint: `cooldown ${duration(reporting.cooldown_seconds)}` },
    { label: "Timer", value: text(reporting.timer_active), hint: text(reporting.timer_sub_state) },
    { label: "Updated", value: text(reporting.updated_at), hint: "reporting snapshot" },
  ]);
  $("alerts-list").innerHTML = alerts
    .map((alert) => `<li class="alert--${escapeHtml(alert.level)}">${pill(alert.level)} <span>${escapeHtml(alert.message)}</span></li>`)
    .join("");
}

function freeTierState(provider) {
  const risk = String(provider.risk_status || provider.health || "").toLowerCase();
  if (risk === "critical" || risk === "over_limit") {
    return "danger";
  }
  if (risk === "warning") {
    return "warn";
  }
  if (risk === "safe" || risk === "healthy") {
    return "ok";
  }
  return "unknown";
}

function quotaCard(provider) {
  const state = freeTierState(provider);
  const stale = provider.stale ? " " + pill("stale") : "";
  const usedPercent = clamp(provider.percent_used);
  const sourceStatus = provider.source_status || provider.status;
  const riskLabel = provider.risk_label || provider.risk_status || provider.health;
  return `
    <article class="quota-card quota-card--${state}">
      <div class="quota-card__header">
        <div>
          <h3>${escapeHtml(provider.platform)}</h3>
          <small>${escapeHtml(text(provider.plan, "Free"))}</small>
        </div>
        <span>${pill(riskLabel)} ${pill(sourceStatus)}${stale}</span>
      </div>
      <div class="quota-card__usage">
        <strong>${escapeHtml(text(provider.current_usage))}</strong>
        <span>of ${escapeHtml(text(provider.quota))}</span>
      </div>
      <div class="quota-card__bar" aria-hidden="true">
        <span style="width: ${usedPercent}%"></span>
      </div>
      <dl class="quota-card__details">
        <div><dt>Remaining</dt><dd>${escapeHtml(text(provider.remaining))}</dd></div>
        <div><dt>Used</dt><dd>${escapeHtml(text(provider.percent_used_display))}</dd></div>
        <div><dt>Free</dt><dd>${escapeHtml(text(provider.percent_remaining_display))}</dd></div>
        <div><dt>Checked</dt><dd>${escapeHtml(compactTimestamp(provider.last_checked_at))}</dd></div>
      </dl>
      <p>${escapeHtml(text(provider.source_detail, "Usage source unknown."))}</p>
    </article>
  `;
}

function renderFreeTierUsage(data) {
  const freeTier = data.free_tier_usage || {};
  const providers = freeTier.providers || [];
  const summary = freeTier.summary || {};
  $("free-tier-updated").textContent = providers.length
    ? `Quota config verified in provider docs; snapshot ${text(freeTier.generated_at)}`
    : "No free-tier provider quota configuration found.";
  renderStats("free-tier-summary", [
    { label: "Services", value: text(summary.total_services, providers.length), hint: "tracked usage-limited services" },
    { label: "Safe", value: text(summary.safe, "0"), hint: "below warning threshold" },
    { label: "Warning", value: text(summary.warning, "0"), hint: "70% or higher" },
    { label: "Critical", value: text(summary.critical, "0"), hint: "85% or higher" },
    { label: "Over Limit", value: text(summary.over_limit, "0"), hint: "100% or higher" },
    { label: "Unknown", value: text(summary.unknown_or_not_configured, "0"), hint: "missing or unavailable source" },
  ]);

  $("free-tier-gauges").innerHTML = providers
    .map((provider) => {
      const remaining = Number(provider.percent_remaining);
      const value = Number.isFinite(remaining) ? remaining : 0;
      const display = provider.percent_remaining_display || "unknown";
      return temperature(
        provider.platform,
        value,
        freeTierState(provider),
        `${text(provider.remaining)} remaining • ${text(provider.status)}`,
        display,
      );
    })
    .join("");

  $("free-tier-cards").innerHTML = providers.map(quotaCard).join("");

  const rows = providers.flatMap((provider) =>
    (provider.metrics || []).map(
      (metric) => `
        <tr>
          <td>${escapeHtml(provider.platform)}</td>
          <td>${escapeHtml(metric.label)}</td>
          <td>${escapeHtml(text(metric.usage_display))}</td>
          <td>${escapeHtml(text(metric.limit_display))}</td>
          <td>${escapeHtml(text(metric.remaining_display))}</td>
          <td>${escapeHtml(text(metric.percent_used_display))}</td>
          <td>${escapeHtml(text(metric.percent_remaining_display))}</td>
          <td>${escapeHtml(text(metric.period))}</td>
          <td>${escapeHtml(text(metric.reset_at))}</td>
          <td>
            ${pill(provider.risk_label || provider.risk_status || metric.risk_status)}
            ${pill(provider.source_status || provider.status)}
          </td>
        </tr>
      `,
    ),
  );
  renderTable("free-tier-table", rows, "No free-tier usage metrics found.", 10);
}

function renderResources(data) {
  const resources = data.resources || {};
  const memory = resources.memory || {};
  const swap = resources.swap || {};
  const disk = resources.disk || {};
  const nutsnewsDisk = resources.nutsnews_disk || {};
  const load = resources.load_average || {};
  const network = resources.network || {};

  $("resource-gauges").innerHTML = [
    gauge("CPU", resources.cpu_percent ?? 0, "Short sample"),
    gauge("RAM", memory.used_percent, `${bytes(memory.used_bytes)} of ${bytes(memory.total_bytes)}`),
    gauge("Root Disk", disk.used_percent, `${bytes(disk.free_bytes)} free`),
    gauge("Swap", swap.used_percent, `${bytes(swap.used_bytes)} of ${bytes(swap.total_bytes)}`, { warn: 50, danger: 75 }),
    gauge("Root Inodes", disk.inode_used_percent, `${text(disk.inode_used)} of ${text(disk.inode_total)}`),
  ].join("");

  renderMetrics("resource-grid", [
    {
      label: "Load",
      value: `${text(load.one)} / ${text(load.five)} / ${text(load.fifteen)}`,
      hint: "1m / 5m / 15m",
    },
    { label: "Load Shape", value: `${text(load.one)}`, hint: `${text(load.five)} five-minute ${text(load.fifteen)} fifteen-minute` },
    { label: "NutsNews Disk", value: percent(nutsnewsDisk.used_percent), hint: text(nutsnewsDisk.path) },
    { label: "Host Received", value: bytes(network.rx_bytes), hint: "Interface counters since boot" },
    { label: "Host Sent", value: bytes(network.tx_bytes), hint: "Interface counters since boot" },
    { label: "Load Trend", value: "compact", hint: `${load.one ?? 0} ${load.five ?? 0} ${load.fifteen ?? 0}` },
  ]);
  $("load-sparkline").innerHTML = sparkline([load.one, load.five, load.fifteen]);
}

function processRows(processes, primaryMetric) {
  return (processes || []).map((process) => {
    const command = text(process.command || process.name);
    const app = text(process.name);
    const primary = primaryMetric === "cpu" ? percent(process.cpu_percent) : bytes(process.memory_bytes);
    const secondary = primaryMetric === "cpu" ? bytes(process.memory_bytes) : percent(process.cpu_percent);
    const search = `${app} ${process.pid} ${process.user} ${command}`.toLowerCase();
    return `
      <tr data-search="${escapeHtml(search)}">
        <td title="${escapeHtml(command)}">${escapeHtml(app)}</td>
        <td>${escapeHtml(process.pid)}</td>
        <td>${escapeHtml(process.user)}</td>
        <td>${escapeHtml(primary)}</td>
        <td>${escapeHtml(secondary)}</td>
        <td>${escapeHtml(process.threads)}</td>
        <td>${escapeHtml(duration(process.cpu_time_seconds))}</td>
        <td>${escapeHtml(duration(process.elapsed_seconds))}</td>
        <td>${escapeHtml(duration(process.idle_seconds))}</td>
      </tr>
    `;
  });
}

function applyProcessFilter() {
  const query = $("process-filter").value.trim().toLowerCase();
  for (const tableId of ["process-memory-table", "process-cpu-table"]) {
    for (const row of $(tableId).querySelectorAll("tr[data-search]")) {
      row.hidden = Boolean(query) && !row.dataset.search.includes(query);
    }
  }
}

function renderProcessVisibility(data) {
  const processes = data.processes || {};
  $("process-method").textContent = text(
    processes.method,
    "CPU percent is best-effort process attribution from the local collector.",
  );
  renderTable("process-memory-table", processRows(processes.top_memory, "memory"), "No process data found.", 9);
  renderTable("process-cpu-table", processRows(processes.top_cpu, "cpu"), "No process data found.", 9);
  applyProcessFilter();
}

function renderDisk(data) {
  const diskUsage = data.disk_usage || {};
  $("disk-hotspots-method").textContent = `${text(diskUsage.method, "Cached folder scan.")} ${
    diskUsage.from_cache ? "Serving cached data." : "Fresh scan."
  }`;
  const rows = (diskUsage.top_folders || []).map(
    (folder) => `
      <tr>
        <td>${escapeHtml(folder.path)}</td>
        <td>${escapeHtml(bytes(folder.size_bytes))}</td>
      </tr>
    `,
  );
  renderTable("disk-hotspots-table", rows, diskUsage.errors?.join(" | ") || "No disk scan data found.", 2);
  renderStats("disk-stats", [
    { label: "Scan Cache", value: diskUsage.from_cache ? "cached" : "fresh", hint: `${duration(diskUsage.cache_seconds)} TTL` },
    { label: "Scan Roots", value: text((diskUsage.scan_roots || []).length, "0"), hint: (diskUsage.scan_roots || []).join(", ") },
    { label: "Largest Entry", value: bytes(diskUsage.top_folders?.[0]?.size_bytes), hint: text(diskUsage.top_folders?.[0]?.path) },
  ]);
}

function renderNetwork(data) {
  const network = data.resources?.network || {};
  const processNetwork = data.process_network || {};
  renderMetrics("network-grid", [
    { label: "Host Received", value: bytes(network.rx_bytes), hint: "Interface counters since boot" },
    { label: "Host Sent", value: bytes(network.tx_bytes), hint: "Interface counters since boot" },
    {
      label: "Per-App Network",
      value: processNetwork.available ? "available" : "not available",
      hint: text(processNetwork.method),
    },
    { label: "Telemetry Label", value: "honest", hint: text(processNetwork.note) },
  ]);

  const rows = (network.interfaces || []).map(
    (iface) => `
      <tr>
        <td>${escapeHtml(iface.name)}</td>
        <td>${escapeHtml(bytes(iface.rx_bytes))}</td>
        <td>${escapeHtml(bytes(iface.tx_bytes))}</td>
      </tr>
    `,
  );
  renderTable("network-interface-table", rows, "No interface counter data found.", 3);
}

function renderDocker(data) {
  const containers = data.docker?.containers || [];
  const rows = containers.map(
    (container) => `
      <tr>
        <td>${escapeHtml(container.name)}</td>
        <td>${pill(container.state)}</td>
        <td>${pill(container.health)}</td>
        <td>${escapeHtml(container.restart_count)}</td>
        <td>${escapeHtml(container.image)}</td>
        <td>${escapeHtml(container.ports || "none")}</td>
        <td>${escapeHtml(container.compose_project || "none")}</td>
      </tr>
    `,
  );
  renderTable("docker-table", rows, data.docker?.error || "No containers found.");
}

function renderServices(data) {
  const services = data.services || [];
  const rows = services.map(
    (service) => `
      <tr>
        <td>${escapeHtml(service.name)}</td>
        <td>${pill(service.active)}</td>
        <td>${pill(service.enabled)}</td>
      </tr>
    `,
  );
  renderTable("services-table", rows, "No service status found.", 3);
}

function filterLogLines(lines) {
  const query = $("log-filter").value.trim().toLowerCase();
  if (!Array.isArray(lines) || !lines.length) {
    return ["No recent lines."];
  }
  if (!query) {
    return lines;
  }
  const filtered = lines.filter((line) => line.toLowerCase().includes(query));
  return filtered.length ? filtered : ["No matching lines."];
}

function renderLogs(data) {
  const logs = data.logs || {};
  $("log-redaction").textContent = text(logs.redaction, "Recent logs with secret redaction.");
  $("logs-caddy").textContent = filterLogLines(logs.caddy).join("\n");
  $("logs-journal").textContent = filterLogLines(logs.journal_warnings).join("\n");
  $("logs-auth").textContent = filterLogLines(logs.auth).join("\n");
}

function renderSecurity(data) {
  const security = data.security || {};
  const updates = security.pending_updates || {};
  const failed = security.failed_logins || {};
  const ssh = security.ssh_hardening || {};
  const firewall = Array.isArray(security.firewall) ? security.firewall.join(" | ") : "unknown";
  renderMetrics("security-grid", [
    { label: "Firewall", value: firewall || "unknown", hint: "UFW verbose status" },
    { label: "Pending Updates", value: text(updates.count, "0"), hint: `${text(updates.security_count, "0")} security updates` },
    { label: "Last Reboot", value: text(security.last_reboot), hint: "UTC" },
    { label: "Failed Logins", value: text(failed.recent_failed_login_lines, "0"), hint: `${text(failed.invalid_user_lines, "0")} invalid user lines` },
    { label: "SSH Password Auth", value: text(ssh.password_authentication), hint: `Root login ${text(ssh.permit_root_login)}` },
    { label: "SSH Forwarding", value: text(ssh.allow_tcp_forwarding), hint: `Keyboard interactive ${text(ssh.kbd_interactive_authentication)}` },
  ]);
  $("open-ports").textContent = Array.isArray(security.open_ports) && security.open_ports.length
    ? security.open_ports.join("\n")
    : "No open port data found.";
}

function renderBackups(data) {
  const backups = data.backups || {};
  const latestSnapshot = backups.latest_snapshot || {};
  const lastBackup = backups.last_backup || {};
  const lastPrune = backups.last_prune || {};
  const lastCheck = backups.last_check || {};
  const retention = backups.retention || {};
  const enabledLabel = backups.enabled ? "enabled" : "disabled";
  const configuredLabel = backups.configured ? "configured" : "misconfigured";
  const snapshotId = latestSnapshot.short_id || latestSnapshot.id || "none";
  const age = backups.latest_snapshot_age_seconds;
  renderMetrics("backup-grid", [
    { label: "Backup State", value: `${enabledLabel} / ${configuredLabel}`, hint: text(backups.status || backups.security_model) },
    { label: "Repository", value: text(backups.repository_path), hint: text(backups.repository) },
    {
      label: "Latest Snapshot",
      value: snapshotId,
      hint: `${text(latestSnapshot.time || backups.latest_status)}${Number.isFinite(Number(age)) ? ` age ${duration(age)}` : ""}`,
    },
    { label: "Freshness", value: text(backups.latest_status), hint: `stale after ${duration(backups.stale_after_seconds)}` },
    { label: "Last Backup", value: text(lastBackup.status), hint: text(lastBackup.finished_at || lastBackup.error) },
    { label: "Last Prune", value: text(lastPrune.status), hint: `keep ${text(retention.keep_daily)}d ${text(retention.keep_weekly)}w ${text(retention.keep_monthly)}m` },
    { label: "Last Verify", value: text(lastCheck.status), hint: text(lastCheck.finished_at || lastCheck.error) },
    { label: "Next Run", value: text(backups.next_run_at), hint: `${text(backups.timer_active)} ${text(backups.timer_sub_state)}` },
    {
      label: "Protected Paths",
      value: text((backups.backup_paths || []).length, "0"),
      hint: (backups.missing_paths || []).length ? `missing ${backups.missing_paths.length}` : "all configured paths found",
    },
  ]);
}

function renderAppLayer(data) {
  const app = data.app || {};
  const deployStatus = app.deploy_status || {};
  const routing = app.routing || {};
  const secrets = app.secrets || {};
  const marker = app.marker || {};
  const requiredKeys = secrets.required_secret_keys || [];
  const missingRequiredKeys = secrets.missing_required_secret_keys || [];
  const configuredKeys = secrets.configured_secret_keys || [];
  const missingCount = Number.isFinite(Number(missingRequiredKeys.length)) ? missingRequiredKeys.length : 0;
  const requiredCount = Number.isFinite(Number(requiredKeys.length)) ? requiredKeys.length : 0;
  const containerHealth = text(deployStatus.container_health);
  const containerState = text(deployStatus.container_state);
  const routeHealthStatus = text(routing.health_status, "unknown");

  const readinessLabel = app.enabled ? "enabled" : "not enabled";
  const readinessHint = app.enabled ? "App service layer is configured by Ansible vars or protected secrets." : "Staged setup is ready for explicit rollout.";
  const routeStatus = text(routing.status, "disabled");
  const routeHint = routing.health_url
    ? `Health check: ${routing.health_url}`
    : "Route is disabled; no public path is currently active.";
  const secretHint = requiredCount
    ? `${text(requiredCount)} required secret keys, ${text(configuredKeys.length)} configured, ${text(missingCount)} missing`
    : "No required app secrets are declared.";
  const envFile = secrets.env_file || "/etc/nutsnews/nutsnews-app.env";
  const envState = secrets.env_file_present === false ? "missing" : "present";
  const imageRepo = text(app.image_repo);
  const imageTag = text(app.image_tag);
  const image = text(app.image);
  const markerStatus = text(marker.status, "unknown");
  const markerUpdated = text(marker.recorded_at, "no marker");
  const container = `${text(app.container_name)}` + (app.container_port ? `:${text(app.container_port)}` : "");

  renderMetrics("app-grid", [
    { label: "App readiness", value: readinessLabel, hint: readinessHint },
    { label: "Deployment", value: text(deployStatus.status, "unknown"), hint: `${containerState} (${containerHealth})` },
    { label: "Route", value: routeStatus, hint: routeHint },
    { label: "Image", value: image, hint: text(app.image) },
    { label: "Image repository", value: imageRepo, hint: "Configured image repo" },
    { label: "Image tag", value: imageTag, hint: "Configured image tag" },
    { label: "Container", value: container, hint: `${text(deployStatus.container_ports, "no published ports")} • ${text(deployStatus.compose_project)}` },
    { label: "Secrets", value: `missing ${text(missingCount)}`, hint: secretHint },
    { label: "Env file", value: envState, hint: text(envFile) },
    { label: "Route health", value: routeHealthStatus, hint: routeStatus === "staged" ? "Route probe succeeded" : "No staged route probe yet" },
    { label: "App marker", value: markerStatus, hint: markerUpdated },
  ]);
  $("app-method").textContent = `Env file: ${envFile} • Route enabled: ${text(app.route_enabled, false)} • Marker: ${markerStatus}`;
  $("app-notes").textContent = app.route_enabled
    ? "Route checks target the configured staged path and will gate rollout until endpoint health is ready."
    : "Staged route is disabled. App service can be configured without changing public paths.";
  renderLinks("app-links", data.app_links);
}

function renderGitopsAndRunbooks(data) {
  const gitops = data.gitops || {};
  const lastApply = gitops.last_apply || {};
  renderMetrics("gitops-grid", [
    { label: "Repository", value: text(gitops.repository), hint: "Source of truth" },
    { label: "Commit", value: shortCommit(gitops.deployed_commit), hint: text(gitops.deployed_commit) },
    { label: "Apply Run", value: text(lastApply.status), hint: text(lastApply.run_url || "No protected apply marker yet") },
    { label: "Drift", value: "watch", hint: text(gitops.drift_warning) },
  ]);
  renderLinks("workflow-links", gitops.workflow_links);
  renderLinks("runbook-links", data.runbooks);
}

function render(data) {
  const state = overallState(data);
  $("overall-state").className = `pill ${levelClass(state)}`;
  $("overall-state").textContent = state;
  $("generated-at").textContent = `Snapshot ${text(data.generated_at)}`;
  renderOverview(data);
  renderEmailReporting(data);
  renderFreeTierUsage(data);
  renderResources(data);
  renderProcessVisibility(data);
  renderDisk(data);
  renderNetwork(data);
  renderDocker(data);
  renderServices(data);
  renderLogs(data);
  renderSecurity(data);
  renderBackups(data);
  renderAppLayer(data);
  renderGitopsAndRunbooks(data);
}

async function load() {
  try {
    const response = await fetch(`${DATA_URL}?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`status ${response.status}`);
    }
    currentData = await response.json();
    render(currentData);
  } catch (error) {
    $("overall-state").className = "pill pill--danger";
    $("overall-state").textContent = "unavailable";
    $("generated-at").textContent = `Collector data failed to load: ${error.message}`;
  }
}

$("process-filter").addEventListener("input", applyProcessFilter);
$("log-filter").addEventListener("input", () => {
  if (currentData) {
    renderLogs(currentData);
  }
});

load();
window.setInterval(load, 60000);
