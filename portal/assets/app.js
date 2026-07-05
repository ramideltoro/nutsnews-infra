const DATA_URL = "/data/status.json";

const $ = (id) => document.getElementById(id);

function text(value, fallback = "unknown") {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  return String(value);
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

function shortCommit(value) {
  const commit = text(value);
  return commit.length > 12 ? commit.slice(0, 12) : commit;
}

function levelClass(level) {
  if (["critical", "failed", "inactive", "exited", "unhealthy"].includes(String(level).toLowerCase())) {
    return "pill--danger";
  }
  if (["warning", "degraded", "unknown"].includes(String(level).toLowerCase())) {
    return "pill--warn";
  }
  if (["ok", "active", "running", "healthy", "enabled"].includes(String(level).toLowerCase())) {
    return "pill--ok";
  }
  return "pill--muted";
}

function pill(value) {
  const label = text(value);
  return `<span class="pill ${levelClass(label)}">${escapeHtml(label)}</span>`;
}

function escapeHtml(value) {
  return text(value, "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
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

function renderMetrics(id, items) {
  $(id).innerHTML = items.map((item) => metric(item.label, item.value, item.hint)).join("");
}

function renderTable(id, rows, emptyMessage, colspan = 7) {
  $(id).innerHTML = rows.length
    ? rows.join("")
    : `<tr><td colspan="${colspan}">${escapeHtml(emptyMessage)}</td></tr>`;
}

function renderLog(id, lines) {
  $(id).textContent = Array.isArray(lines) && lines.length ? lines.join("\n") : "No recent lines.";
}

function renderLinks(id, links) {
  $(id).innerHTML = (links || [])
    .map((link) => `<li><a href="${escapeHtml(link.url)}" rel="noreferrer">${escapeHtml(link.name)}</a></li>`)
    .join("");
}

function renderOverview(data) {
  const host = data.host || {};
  const gitops = data.gitops || {};
  const lastApply = gitops.last_apply || {};
  renderMetrics("overview-grid", [
    { label: "Hostname", value: text(host.hostname), hint: text(host.fqdn) },
    { label: "Uptime", value: duration(host.uptime_seconds), hint: text(host.os) },
    { label: "Public IPv4", value: text(host.public_ipv4), hint: `IPv6 ${text(host.public_ipv6)}` },
    { label: "Kernel", value: text(host.kernel), hint: text(host.architecture) },
    { label: "Infra Commit", value: shortCommit(gitops.deployed_commit), hint: text(gitops.repository) },
    { label: "Last Apply", value: text(lastApply.status), hint: text(lastApply.run_url || lastApply.recorded_at) },
  ]);
  $("portal-policy").textContent = text(data.portal?.management_policy, "Read-only. Production changes still go through GitOps.");
}

function renderResources(data) {
  const resources = data.resources || {};
  const memory = resources.memory || {};
  const swap = resources.swap || {};
  const disk = resources.disk || {};
  const nutsnewsDisk = resources.nutsnews_disk || {};
  const load = resources.load_average || {};
  const network = resources.network || {};
  renderMetrics("resource-grid", [
    { label: "CPU", value: resources.cpu_percent === null ? "unknown" : percent(resources.cpu_percent), hint: "Short sample" },
    { label: "Load", value: `${text(load.one)} / ${text(load.five)} / ${text(load.fifteen)}`, hint: "1m / 5m / 15m" },
    { label: "RAM", value: percent(memory.used_percent), hint: `${bytes(memory.used_bytes)} used of ${bytes(memory.total_bytes)}` },
    { label: "Swap", value: percent(swap.used_percent), hint: `${bytes(swap.used_bytes)} used of ${bytes(swap.total_bytes)}` },
    { label: "Root Disk", value: percent(disk.used_percent), hint: `${bytes(disk.used_bytes)} used of ${bytes(disk.total_bytes)}` },
    { label: "Root Inodes", value: percent(disk.inode_used_percent), hint: `${text(disk.inode_used)} used of ${text(disk.inode_total)}` },
    { label: "NutsNews Disk", value: percent(nutsnewsDisk.used_percent), hint: text(nutsnewsDisk.path) },
    { label: "Network", value: `${bytes(network.rx_bytes)} rx`, hint: `${bytes(network.tx_bytes)} tx since boot` },
  ]);
}

function processRows(processes, primaryMetric) {
  return (processes || []).map((process) => {
    const command = text(process.command || process.name);
    const app = text(process.name);
    const primary = primaryMetric === "cpu" ? percent(process.cpu_percent) : bytes(process.memory_bytes);
    const secondary = primaryMetric === "cpu" ? bytes(process.memory_bytes) : percent(process.cpu_percent);
    return `
      <tr>
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

function renderProcessVisibility(data) {
  const processes = data.processes || {};
  $("process-method").textContent = text(
    processes.method,
    "CPU percent is best-effort process attribution from the local collector.",
  );
  renderTable("process-memory-table", processRows(processes.top_memory, "memory"), "No process data found.", 9);
  renderTable("process-cpu-table", processRows(processes.top_cpu, "cpu"), "No process data found.", 9);
}

function renderDiskAndNetwork(data) {
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
    { label: "Telemetry Note", value: "honest", hint: text(processNetwork.note) },
  ]);
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

function renderLogs(data) {
  const logs = data.logs || {};
  $("log-redaction").textContent = text(logs.redaction, "Recent logs with basic redaction.");
  renderLog("logs-caddy", logs.caddy);
  renderLog("logs-journal", logs.journal_warnings);
  renderLog("logs-auth", logs.auth);
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
    {
      label: "Failed Logins",
      value: text(failed.recent_failed_login_lines, "0"),
      hint: `${text(failed.invalid_user_lines, "0")} invalid user lines`,
    },
    {
      label: "SSH Password Auth",
      value: text(ssh.password_authentication),
      hint: `Root login ${text(ssh.permit_root_login)}`,
    },
    {
      label: "SSH Forwarding",
      value: text(ssh.allow_tcp_forwarding),
      hint: `Keyboard interactive ${text(ssh.kbd_interactive_authentication)}`,
    },
  ]);
  $("open-ports").textContent = Array.isArray(security.open_ports) && security.open_ports.length
    ? security.open_ports.join("\n")
    : "No open port data found.";
}

function renderBackupsAndAlerts(data) {
  const backups = data.backups || {};
  const latest = backups.latest || {};
  const reporting = data.email_reporting || {};
  renderMetrics("backup-grid", [
    { label: "Backup Directory", value: text(backups.directory), hint: `${bytes(backups.size_bytes)} used` },
    { label: "Latest Backup", value: text(latest.path, "placeholder"), hint: text(latest.updated_at || backups.latest_status) },
    { label: "Snapshot Reminder", value: "planned", hint: text(backups.snapshot_reminder) },
  ]);
  renderMetrics("email-reporting-grid", [
    { label: "Email Alerts", value: reporting.enabled ? "enabled" : "disabled", hint: text(reporting.status) },
    { label: "Configured", value: reporting.configured ? "yes" : "no", hint: `${text(reporting.recipients_count, "0")} recipient(s)` },
    { label: "Last Alert Check", value: text(reporting.last_alert_check_at), hint: `${text(reporting.pending_alerts, "0")} pending alert(s)` },
    { label: "Last Report", value: text(reporting.last_report_sent_at), hint: `cooldown ${duration(reporting.cooldown_seconds)}` },
    { label: "Suppressed", value: text(reporting.suppressed_alerts, "0"), hint: "duplicate-alert cooldown" },
    { label: "Last Error", value: text(reporting.last_error, "none"), hint: text(reporting.email_config_source) },
  ]);

  const alerts = data.alerts?.items || [];
  $("alerts-list").innerHTML = alerts
    .map((alert) => `<li class="alert--${escapeHtml(alert.level)}">${pill(alert.level)} ${escapeHtml(alert.message)}</li>`)
    .join("");
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

function overallState(data) {
  const alerts = data.alerts?.items || [];
  if (alerts.some((alert) => alert.level === "critical")) {
    return "critical";
  }
  if (alerts.some((alert) => alert.level === "warning")) {
    return "warning";
  }
  return "ok";
}

function render(data) {
  const state = overallState(data);
  $("overall-state").className = `pill ${levelClass(state)}`;
  $("overall-state").textContent = state;
  $("generated-at").textContent = `Snapshot ${text(data.generated_at)}`;
  renderOverview(data);
  renderResources(data);
  renderProcessVisibility(data);
  renderDiskAndNetwork(data);
  renderDocker(data);
  renderServices(data);
  renderLogs(data);
  renderSecurity(data);
  renderBackupsAndAlerts(data);
  renderGitopsAndRunbooks(data);
}

async function load() {
  try {
    const response = await fetch(`${DATA_URL}?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`status ${response.status}`);
    }
    render(await response.json());
  } catch (error) {
    $("overall-state").className = "pill pill--danger";
    $("overall-state").textContent = "unavailable";
    $("generated-at").textContent = `Collector data failed to load: ${error.message}`;
  }
}

load();
window.setInterval(load, 60000);
