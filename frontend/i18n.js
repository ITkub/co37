/*
 * CO-37 - Sprachen.
 *
 * Nur die Oberflaeche uebersetzt. Backend und Agent liefern Schluessel und
 * Werte, niemals fertige Saetze - sonst braeuchte es drei Woerterbuecher an
 * drei Orten und drei Stellen, an denen ein Text fehlen kann.
 *
 * Englisch ist die Vorgabe und zugleich der Rueckfall: fehlt ein Schluessel
 * in der eingestellten Sprache, wird der englische genommen; fehlt er auch
 * dort, erscheint der Schluessel selbst. Sichtbar falsch ist besser als
 * unsichtbar leer - eine leere Beschriftung faellt niemandem auf.
 *
 * Im Markup steht der englische Text ausgeschrieben, nicht nur der
 * Schluessel. Zwei Gruende: die Seite zeigt schon vor dem ersten Skriptlauf
 * etwas Lesbares, und die Pruefung, ob die Reiter in eine Zeile passen,
 * kann weiterhin am Markup rechnen.
 *
 * Wird vor app.js geladen und stellt seine Namen global bereit - die
 * Oberflaeche kommt ohne Module aus, und script-src bleibt bei 'self'.
 */

const SPRACHEN = ["en", "de"];
const SPRACHE_VORGABE = "en";
const SPRACH_COOKIE = "co37_lang";

/* Reihenfolge im Woerterbuch: Schluessel alphabetisch, damit Luecken beim
   Lesen auffallen. Die Pruefreihe vergleicht beide Sprachen ohnehin. */
const I18N = {
  en: {
    "act.agent": "Agent",
    "act.approve": "Approve",
    "act.history": "History",
    "act.live": "Live",
    "act.patch": "Patch",
    "act.reboot": "Restart",
    "act.reject": "Reject",
    "act.scan": "Check",
    "app.filter": "Filter by host or tag",
    "app.logout": "Sign out",
    "app.new_version": "CO-37 was updated to {neu} — this page is still running {alt}.",
    "app.offline": "Server is not responding — still trying.",
    "app.refresh": "Refresh",
    "app.reload_now": "Reload now",
    "app.scan_all": "Check all",
    "app.settings": "Settings",
    "app.toggle_view": "Switch view",
    "ask.cancel_downtimes": "Cancel every downtime this host got from CO-37?",
    "ask.cancel_scheduled_reboot": "Cancel the scheduled restart?",
    "ask.downtime_first": "A downtime of {minuten} minutes is set first on:\\n{ziel}\\n\\nThe restart only happens once the downtime stands.",
    "ask.downtime_wanted_no_link": "WARNING: a downtime was requested but no Checkmk host is linked. NO downtime will be set — the monitoring will raise an alarm.",
    "ask.new_password": "New password for {name}:",
    "ask.no_downtime": "Without a downtime. The monitoring will raise an alarm.",
    "ask.no_pilot": "No pilot host chosen. Without a pilot every host is updated at the same time.\\n\\nSave anyway?",
    "ask.patch": "Install {anzahl} updates on {host}.",
    "ask.policy_after": "If a restart is required, it happens right afterwards.",
    "ask.policy_report_only": "If a restart is required, it is reported but not carried out.",
    "ask.policy_window": "If a restart is required, it happens in the window {fenster}.",
    "ask.reject_host": "Reject host? It receives no jobs and stays in the list.",
    "ask.remove_host": "Remove {host}?\\n\\nJobs and the update list are deleted. The agent can enrol again afterwards.",
    "ask.remove_user": "Remove the account {name}?",
    "ask.revoke_token": "Revoke the agent token of {host}?",
    "ask.run_update": "Run the update from {von} to {nach}?\\n\\nThe service restarts briefly.",
    "ask.schedule_reboot": "Schedule a restart of {host} at {zeit}?",
    "ask.update_agents": "Update {anzahl} agents to {version}?\\n\\nEach agent restarts afterwards. Running jobs are not interrupted.",
    "ask.will_reboot": "Some of these updates require a restart (a kernel, for example).",
    "common.cancel": "Cancel",
    "common.close": "Close",
    "common.close2": "Close",
    "common.close3": "Close",
    "common.close4": "Close",
    "common.close5": "Close",
    "common.close6": "Close",
    "common.close7": "Close",
    "common.close8": "Close",
    "common.loading": "Loading…",
    "common.save": "Save",
    "dt.all_cmk_hosts": "every host configured in Checkmk",
    "dt.cancel": "Cancel",
    "dt.comment": "Comment",
    "dt.comment_example": "CO-37: manual maintenance",
    "dt.from": "From",
    "dt.hint": "Applies to every linked Checkmk host. Cancelling only affects downtimes that were set by CO-37.",
    "dt.minutes_from_now": "Minutes from now",
    "dt.or_fixed": "Or a fixed period:",
    "dt.set": "Set downtime",
    "dt.show_active": "Show active",
    "dt.to": "To",
    "filter.all": "All states",
    "filter.offline": "Offline",
    "filter.pending_approval": "Pending approval",
    "filter.reboot": "Restart required",
    "filter.updates": "Updates pending",
    "filter.uptodate": "Up to date",
    "history.title": "History",
    "host.agent_token": "Agent token",
    "host.auto_reboot": "Restart on its own when required",
    "host.carrier_hint": "Only for hosts that carry the monitoring themselves — the hypervisor running the Checkmk machine, for example. When such a host restarts, every monitored host reports as down without being affected itself.<br><br>The downtime then applies to every host configured in Checkmk, not just the ones selected above. With this option set, that is enough on its own — a link above is no longer needed.",
    "host.cmk_hint": "More than one host can be selected. When this host goes down, other objects are often affected too — cluster resources, services, dependent applications. All selected ones get the same downtime.",
    "host.cmk_search": "Search Checkmk hosts",
    "host.display_name": "Display name",
    "host.downtime_all": "Set downtime on all Checkmk hosts",
    "host.downtime_minutes": "Downtime for a restart (minutes)",
    "host.maintenance_window": "Maintenance window",
    "host.monitoring_carrier": "Monitoring carrier",
    "host.name_example": "e.g. domain controller",
    "host.reboot_needed": "Restart required",
    "host.reboot_needed_allowed": "Restart required, allowed",
    "host.reboot_policy": "Restart policy after updates",
    "host.remove": "Remove host",
    "host.revoke_token": "Revoke token",
    "host.system": "System",
    "host.tab.downtime": "Downtime",
    "host.tab.general": "General",
    "host.tab.schedule_reboot": "Schedule restart",
    "host.tab.schedule_update": "Schedule update",
    "host.title": "Host",
    "host.token_hint": "The token does not expire. Revoke it if it may have leaked — for instance after the host was handed on or rebuilt.<br><br>The agent notices at its next contact and enrols again by itself. Schedule, Checkmk link and history are kept. Until it has enrolled again the name is unclaimed, so the host returns to pending approval.",
    "host.window_example": "SAT,SUN 02:00-05:00",
    "host.window_hint": "This is <b>not a schedule</b>, it is a gate: it decides whether a restart that became due after patching may happen right away. Nothing is ever started on its own. An empty field means any time.<br><br>Format: <code>SAT,SUN 02:00-05:00</code> — spanning midnight works too: <code>MON-FRI 22:00-06:00</code>",
    "language.default": "Default for this installation",
    "language.default.hint": "Applies to users who have not chosen a language themselves, and to the sign-in page.",
    "language.default.saved": "Default language saved.",
    "language.hint": "The language applies to this interface only. Messages from older agents keep the wording they were recorded with.",
    "language.name.de": "Deutsch",
    "language.name.en": "English",
    "language.own": "My language",
    "language.own.saved": "Language saved.",
    "list.all_hosts": "all hosts",
    "list.drag": "Drag to change the order",
    "list.drag_blocked": "Reordering only works without an active filter",
    "list.empty": "No host has enrolled yet.<br><br>Install the agent on a target system — see Settings → Agents.",
    "list.no_match": "No host matches the filter.",
    "list.not_linked": "not linked",
    "live.copy": "Copy log",
    "live.follow": "Scroll along automatically",
    "live.title": "Live output",
    "login.failed": "Sign-in failed.",
    "login.fill_both": "Please fill in both.",
    "login.password": "Password",
    "login.session_expired": "Session expired. Please sign in again.",
    "login.submit": "Sign in",
    "login.subtitle": "Core Operations Manager",
    "login.title": "Sign in",
    "login.unreachable": "Server is not reachable.",
    "login.user": "Username",
    "msg.agent_update_many": "Update scheduled for {anzahl} agents",
    "msg.agent_update_scheduled": "Agent update scheduled",
    "msg.agents_current": "All agents are up to date",
    "msg.build_requested": "Build requested — running on the server",
    "msg.cancelled": "Cancelled",
    "msg.cmk_connected": "Checkmk connected",
    "msg.copied": "{was} copied",
    "msg.copy_failed": "Cannot copy — please select the text and press Ctrl+C",
    "msg.copy_failed_short": "Cannot copy — please select the text",
    "msg.created": "{name} created",
    "msg.downtime_cancelled": "Downtimes cancelled",
    "msg.downtime_set": "Downtime set",
    "msg.host_approved": "Host approved",
    "msg.host_rejected": "Host rejected",
    "msg.host_removed": "Host removed",
    "msg.key_applied": "License key applied",
    "msg.key_rejected": "Key rejected — the previous state stands",
    "msg.key_removed": "License key removed",
    "msg.need_datetime": "Please give a date and time",
    "msg.need_day_time": "Give a weekday and a time for the update schedule",
    "msg.need_minutes": "Please give minutes or a period",
    "msg.need_name_pw": "Fill in a name and a password",
    "msg.need_zip": "Please choose the ZIP file of the package",
    "msg.no_key": "No key entered",
    "msg.nothing_to_copy": "Nothing to copy",
    "msg.order_not_saved": "The order could not be saved",
    "msg.package_discarded": "Package discarded",
    "msg.package_staged": "Package checked and staged",
    "msg.patch_scheduled": "Patch job scheduled",
    "msg.pw_changed": "Password changed. Please sign in again.",
    "msg.pw_mismatch": "The two new passwords do not match",
    "msg.pw_set": "Password set for {name}, running sessions ended",
    "msg.pw_short": "The password must have at least 6 characters",
    "msg.reboot_scheduled": "Restart scheduled",
    "msg.reboot_triggered": "Restart triggered — expires after {minuten} minutes without contact",
    "msg.refreshed": "Refreshed",
    "msg.removed": "{name} removed",
    "msg.scan_many": "Check scheduled for {anzahl} hosts",
    "msg.scan_scheduled": "Check scheduled — runs at the agent's next contact",
    "msg.time_in_past": "That point in time is in the past",
    "msg.token_failed": "The token could not be created",
    "msg.token_revoked": "Token revoked — the host is waiting to enrol again",
    "msg.unreachable_code": "Server is not reachable ({code})",
    "msg.url_scheme": "The address must start with http:// or https://",
    "note.agent_outdated": "Agent {version} outdated",
    "note.followup": "Follow-up scan queued ({anzahl})",
    "note.job_running": "{auftrag} running",
    "note.never_reported": "never reported",
    "note.pending_approval": "pending approval",
    "note.reboot_planned": "Restart planned: {zeit}",
    "note.rejected": "rejected",
    "note.silent": "Host is not reporting",
    "note.updates_at": "Updates: {zeit}",
    "note.updates_need_reboot": "Updates require a restart",
    "plan.auto_reboot": "Restart automatically when required",
    "plan.expiry_hint": "If the host does not report in before this deadline, the restart is <b>not caught up on</b> but discarded as expired. Otherwise a server that was off over the weekend would restart on Monday morning in the middle of the working day.",
    "plan.grace_hint": "The grace period keeps a host that was switched off at the scheduled time from patching later during working hours. Once it has passed, the appointment is skipped.",
    "plan.grace_hours": "Grace period (hours)",
    "plan.grace_minutes": "Grace period (minutes)",
    "plan.in_2h": "In 2 hours",
    "plan.job.patch": "Install updates",
    "plan.job.patch_reboot": "Install updates and restart if required",
    "plan.job.reboot": "Restart only",
    "plan.job.scan": "Check for updates only",
    "plan.last": "Last run",
    "plan.next": "Next appointment",
    "plan.once_hint": "Places a job at a fixed point in time. It is handed to the agent only then. Policy and maintenance window are bypassed — the time was chosen deliberately, after all.",
    "plan.saturday_0300": "Saturday 03:00",
    "plan.scheduled": "Scheduled",
    "plan.set_downtime": "Set a Checkmk downtime first",
    "plan.submit": "Schedule",
    "plan.time": "Time",
    "plan.today_2200": "Today 22:00",
    "plan.update_enabled": "Install updates on a schedule",
    "plan.update_hint": "A recurring appointment for installing updates. It is evaluated when the agent makes contact — if the host is not running at that time, the grace period applies.",
    "plan.weekdays": "Weekdays",
    "plan.what": "What should happen",
    "plan.when": "Date and time",
    "rack.actions": "Actions",
    "rack.checkmk": "Checkmk",
    "rack.host": "Host",
    "rack.no": "No",
    "rack.security": "Security",
    "rack.system": "System",
    "rack.updates": "Updates",
    "reason.cbs": "Component store",
    "reason.kernel": "new kernel",
    "reason.netlogon": "domain join",
    "reason.package_manager": "package manager",
    "reason.pending_rename": "leftover files",
    "reason.windows_update": "Windows Update",
    "reboot.cancel": "Cancel",
    "reboot.dialog_info": "Restarts <b>{host}</b>. No updates are installed.",
    "reboot.dialog_title": "Restart {host}",
    "reboot.downtime_minutes": "Downtime (minutes)",
    "reboot.downtime_target": "A downtime is set on: <b>{ziel}</b>. If it fails, the restart is skipped.",
    "reboot.grace_hint": "The <b>grace period</b> limits how long the job stays valid. If the host is unreachable now and only reports in afterwards, the restart is <b>no longer carried out</b> but discarded as expired. Without that limit a server that is currently off would restart in the middle of the working day the next time it is switched on.",
    "reboot.grace_minutes": "Grace period (minutes)",
    "reboot.no_link": "No Checkmk host linked — no downtime can be set. The monitoring will raise an alarm during the restart.",
    "reboot.now": "Restart now",
    "reboot.set_downtime": "Set a Checkmk downtime first",
    "reboot.title": "Restart",
    "settings.account.change": "Change password",
    "settings.account.current_pw": "Current password",
    "settings.account.hint": "Change your own password. Afterwards every session of this account is ended, including this one.",
    "settings.account.new_pw": "New password",
    "settings.account.new_pw2": "Repeat new password",
    "settings.account.role": "Role",
    "settings.account.signed_in_as": "Signed in as",
    "settings.agents.copy_cmd": "Copy command",
    "settings.agents.copy_cmd2": "Copy command",
    "settings.agents.download": "Download agent",
    "settings.agents.download_deb": "Download",
    "settings.agents.download_msi": "Download",
    "settings.agents.hint": "Agents enrol by themselves after installation and appear in the overview as <b>pending approval</b>. Without approval they receive no jobs.",
    "settings.agents.linux": "Set up Linux",
    "settings.agents.linux_hint": "Run on the target system as root. Downloads the package and sets the agent up.<br> The command carries a token that expires shortly — whatever is left behind in the shell history on the target system is worthless by then. Generate it only just before setting up.",
    "settings.agents.linux_pkg": "<b>Linux</b> · .deb for Debian and Ubuntu<br>",
    "settings.agents.make_cmd": "Generate command",
    "settings.agents.rebuild": "Rebuild packages",
    "settings.agents.windows": "Set up Windows",
    "settings.agents.windows_hint": "Download the MSI, copy it to the target system, then in a command prompt as Administrator:",
    "settings.agents.windows_pkg": "<b>Windows</b> · .msi, Python included<br>",
    "settings.audit.hint": "Who did what and when. Append-only — entries can neither be changed nor deleted.",
    "settings.cmk.connect": "Connect and save",
    "settings.cmk.hint": "An automation user with permission for the host list and downtimes. Checkmk 2.4 no longer creates an <code>automation</code> user automatically.",
    "settings.cmk.secret": "Automation secret",
    "settings.cmk.secret_hint": "Checked before saving and stored encrypted. Never retrievable again.",
    "settings.cmk.site": "Site",
    "settings.cmk.url": "Server URL",
    "settings.cmk.user": "User",
    "settings.cmk.verify": "Verify TLS certificate",
    "settings.license.apply": "Apply",
    "settings.license.hint": "Up to 10 hosts work without a key, commercially as well and with no time limit. Approved hosts are counted; pending, rejected and deleted ones are not.<br><br>Beyond that a key is required — available from <b>sales@itkub.de</b>.<br><br>When a key expires, hosts that are already approved <b>keep being patched</b>. Only new approvals above 10 are no longer possible.",
    "settings.license.key": "Key",
    "settings.license.key_title": "License key",
    "settings.license.remove": "Remove",
    "settings.license.scope": "Scope",
    "settings.proxy.example": "e.g. 192.168.1.20",
    "settings.proxy.hint": "Address of the proxy in front. Only from there are the <code>X-Forwarded-For</code> and <code>X-Forwarded-Proto</code> headers evaluated — separate several with commas.<br><br><b>Encrypted requests are accepted from this address only.</b> If this is empty or holds the wrong address, every request claiming HTTPS is rejected — CO-37 does not terminate TLS itself, so the claim would be unproven. Unencrypted access stays available until the box below is ticked.<br><br>An entry that is too broad is a risk: anyone who can then set the header themselves bypasses the sign-in rate limit with invented senders.<br><br>The way back from a wrong entry is always unencrypted access straight to the internal port.",
    "settings.proxy.https_hint": "With this option on, every request that did not arrive encrypted through the configured proxy is rejected. Direct access to the internal port then answers with an error message and nothing else.<br><br>The port itself stays open — it is only truly closed by a firewall rule. That belongs to the machine, not to this application.<br><br><b>The switch affects every agent as well.</b> Any that still speak unencrypted drop out immediately — and silently, because the agent writes the error only to its own log file.<br><br><b>If the proxy fails for good,</b> you can no longer get in through the browser — not even to clear this box. The way back is then through the machine itself:<br><code>/opt/co37/venv/bin/python -c 'import sqlite3;c=sqlite3.connect(\"/opt/co37/data/co37.db\");c.execute(\"UPDATE setting SET value=? WHERE key=?\",(\"false\",\"https_only\"));c.commit()'</code><br>then <code>systemctl restart co37-backend</code>. In full in <code>REVERSE-PROXY.md</code>.",
    "settings.proxy.https_only": "Restrict access to HTTPS",
    "settings.proxy.https_title": "HTTPS only",
    "settings.proxy.public": "Public address",
    "settings.proxy.public_example": "e.g. https://co37.example.com",
    "settings.proxy.public_hint": "The address at which CO-37 is reachable <b>from the target systems</b>. It ends up in the installation commands and therefore in the <code>agent.conf</code> of every newly set up host.<br><br>If the field is left empty, the address you are currently viewing the dashboard at is used. That makes it depend on how you open the page — set up a host over the internal port after switching, and its agent would talk unencrypted for good.<br><br>Existing agents are not affected. Their server address sits in <code>agent.conf</code> on the host and is not changed by any update.",
    "settings.proxy.public_save": "Save",
    "settings.proxy.public_title": "Public address",
    "settings.proxy.save": "Save",
    "settings.proxy.title": "Reverse proxy",
    "settings.proxy.trusted": "Trusted proxy",
    "settings.rollout.all": "All at once",
    "settings.rollout.auto": "Roll out automatically after a system update",
    "settings.rollout.current": "Current version",
    "settings.rollout.done": "Agents rolled out",
    "settings.rollout.hint": "Agent and system carry the same version number. Where it differs on a host, the overview shows a note there and an <b>Agent</b> button to update it.",
    "settings.rollout.mode": "Procedure",
    "settings.rollout.none": "— none selected —",
    "settings.rollout.pilot": "Pilot host",
    "settings.rollout.save": "Save",
    "settings.rollout.staged": "Staged — one host first, then the rest",
    "settings.rollout.start": "Roll out now",
    "settings.rollout.state": "State",
    "settings.rollout.title": "Agent updates",
    "settings.rollout.update_all": "Update all outdated agents",
    "settings.tab.access": "Access",
    "settings.tab.account": "Account",
    "settings.tab.agents": "Agents",
    "settings.tab.audit": "Log",
    "settings.tab.checkmk": "Checkmk",
    "settings.tab.language": "Language",
    "settings.tab.license": "License",
    "settings.tab.update": "Update",
    "settings.tab.users": "Users",
    "settings.title": "Settings",
    "settings.update.discard": "Discard",
    "settings.update.drop": "Drop the <b>ZIP package</b> and the <b>.sig file</b> here, or click<br>",
    "settings.update.files": "co37_vX_Y_Z.zip and co37_vX_Y_Z.zip.sig, select both · max. 50 MB",
    "settings.update.installed": "Installed version",
    "settings.update.reset": "Reset",
    "settings.update.run": "Run update",
    "settings.update.run_hint": "The current state is backed up, replaced, and the service restarted. If the health check fails, it is rolled back.",
    "settings.update.sig_hint": "The signature proves that the package comes from the publisher and was not altered on the way. It is checked <b>before</b> unpacking — the watcher unpacks as root, so a substituted package would amount to code execution as root.<br><br>A checksum alone is not enough for this: anyone who can swap the file can swap the checksum file next to it as well.",
    "settings.update.staged": "Staged",
    "settings.update.state": "State",
    "settings.update.watcher": "Update watcher",
    "settings.users.create": "Create",
    "settings.users.existing": "Existing users",
    "settings.users.hint": "Users may do anything in patch management but cannot manage users. Only administrators can.",
    "settings.users.name": "Username",
    "settings.users.new": "Create a new user",
    "settings.users.password": "Password",
    "settings.users.role": "Role",
    "settings.users.role_admin": "Administrator",
    "settings.users.role_user": "User",
    "stats.hosts": "Hosts",
    "stats.online": "online",
    "stats.reboot": "Restart required",
    "stats.scheduled": "scheduled",
    "stats.updates": "Updates pending",
    "stats.waiting": "pending",
    "upd.error": "Error",
    "upd.idle": "Ready",
    "upd.rolled_back": "Rolled back",
    "upd.running": "Running",
    "upd.success": "Successful",
    "upd.triggered": "Requested",
    "upd.uploaded": "Package waiting",
  },
  de: {
    "act.agent": "Agent",
    "act.approve": "Freigeben",
    "act.history": "Verlauf",
    "act.live": "Live",
    "act.patch": "Patchen",
    "act.reboot": "Neustart",
    "act.reject": "Ablehnen",
    "act.scan": "Prüfen",
    "app.filter": "Filtern nach Host oder Tag",
    "app.logout": "Abmelden",
    "app.new_version": "CO-37 wurde auf {neu} aktualisiert — diese Seite läuft noch mit {alt}.",
    "app.offline": "Server antwortet nicht — es wird weiter versucht.",
    "app.refresh": "Neu laden",
    "app.reload_now": "Jetzt neu laden",
    "app.scan_all": "Alle prüfen",
    "app.settings": "Einstellungen",
    "app.toggle_view": "Ansicht wechseln",
    "ask.cancel_downtimes": "Alle von CO-37 gesetzten Downtimes dieses Hosts aufheben?",
    "ask.cancel_scheduled_reboot": "Eingeplanten Neustart abbrechen?",
    "ask.downtime_first": "Vorher wird eine Downtime von {minuten} Minuten gesetzt auf:\\n{ziel}\\n\\nDer Neustart erfolgt nur, wenn die Downtime steht.",
    "ask.downtime_wanted_no_link": "WARNUNG: Downtime gewünscht, aber kein Checkmk-Host verknüpft. Es wird KEINE Downtime gesetzt — das Monitoring wird Alarm schlagen.",
    "ask.new_password": "Neues Passwort für {name}:",
    "ask.no_downtime": "Ohne Downtime. Das Monitoring wird Alarm schlagen.",
    "ask.no_pilot": "Kein Pilot-Host gewählt. Ohne Pilot werden alle Hosts gleichzeitig aktualisiert.\\n\\nTrotzdem speichern?",
    "ask.patch": "{anzahl} Updates auf {host} installieren.",
    "ask.policy_after": "Ist ein Neustart nötig, erfolgt er anschließend.",
    "ask.policy_report_only": "Ist ein Neustart nötig, wird er gemeldet, aber nicht ausgeführt.",
    "ask.policy_window": "Ist ein Neustart nötig, erfolgt er im Fenster {fenster}.",
    "ask.reject_host": "Host ablehnen? Er bekommt keine Aufträge und bleibt in der Liste.",
    "ask.remove_host": "{host} entfernen?\\n\\nAufträge und Update-Liste werden gelöscht. Der Agent kann sich danach neu anmelden.",
    "ask.remove_user": "Zugang {name} entfernen?",
    "ask.revoke_token": "Agent-Token von {host} zurückziehen?",
    "ask.run_update": "Update von {von} auf {nach} ausführen?\\n\\nDer Dienst wird kurz neu gestartet.",
    "ask.schedule_reboot": "Neustart von {host} am {zeit} einplanen?",
    "ask.update_agents": "{anzahl} Agents auf {version} aktualisieren?\\n\\nJeder Agent startet danach neu. Laufende Aufträge werden nicht unterbrochen.",
    "ask.will_reboot": "Unter den Updates sind Pakete, die einen Neustart erfordern (z.B. Kernel).",
    "common.cancel": "Abbrechen",
    "common.close": "Schließen",
    "common.close2": "Schließen",
    "common.close3": "Schließen",
    "common.close4": "Schließen",
    "common.close5": "Schließen",
    "common.close6": "Schließen",
    "common.close7": "Schließen",
    "common.close8": "Schließen",
    "common.loading": "Wird geladen…",
    "common.save": "Speichern",
    "dt.all_cmk_hosts": "alle in Checkmk konfigurierten Hosts",
    "dt.cancel": "Aufheben",
    "dt.comment": "Kommentar",
    "dt.comment_example": "CO-37: Manuelle Wartung",
    "dt.from": "Von",
    "dt.hint": "Wirkt auf alle verknüpften Checkmk-Hosts. Aufheben trifft nur Downtimes, die von CO-37 gesetzt wurden.",
    "dt.minutes_from_now": "Minuten ab jetzt",
    "dt.or_fixed": "Oder eine feste Zeitspanne:",
    "dt.set": "Downtime setzen",
    "dt.show_active": "Aktive anzeigen",
    "dt.to": "Bis",
    "filter.all": "Alle Zustände",
    "filter.offline": "Offline",
    "filter.pending_approval": "Wartet auf Freigabe",
    "filter.reboot": "Neustart nötig",
    "filter.updates": "Updates offen",
    "filter.uptodate": "Aktuell",
    "history.title": "Verlauf",
    "host.agent_token": "Agent-Token",
    "host.auto_reboot": "Neustart eigenständig ausführen, wenn nötig",
    "host.carrier_hint": "Nur für Hosts, die das Monitoring selbst tragen — etwa den Virtualisierer, auf dem die Checkmk-Maschine läuft. Startet ein solcher Host neu, meldet jeder überwachte Host aus, ohne selbst betroffen zu sein.<br><br>Die Downtime greift dann auf allen in Checkmk konfigurierten Hosts, nicht nur auf den oben ausgewählten. Ist die Option gesetzt, genügt sie allein — eine Verknüpfung oben ist dann nicht mehr nötig.",
    "host.cmk_hint": "Mehrere Hosts auswählbar. Fällt dieser Host aus, sind oft weitere Objekte mit betroffen — Cluster-Ressourcen, Dienste, abhängige Anwendungen. Alle ausgewählten bekommen dieselbe Downtime.",
    "host.cmk_search": "Checkmk-Hosts durchsuchen",
    "host.display_name": "Anzeigename",
    "host.downtime_all": "Downtime auf alle Checkmk-Hosts setzen",
    "host.downtime_minutes": "Downtime-Dauer bei Neustart (Minuten)",
    "host.maintenance_window": "Wartungsfenster",
    "host.monitoring_carrier": "Monitoring-Träger",
    "host.name_example": "z.B. Domaincontroller",
    "host.reboot_needed": "Neustart nötig",
    "host.reboot_needed_allowed": "Neustart nötig, freigegeben",
    "host.reboot_policy": "Neustart-Richtlinie nach Updates",
    "host.remove": "Host entfernen",
    "host.revoke_token": "Token zurückziehen",
    "host.system": "System",
    "host.tab.downtime": "Downtime",
    "host.tab.general": "Allgemein",
    "host.tab.schedule_reboot": "Neustart planen",
    "host.tab.schedule_update": "Update planen",
    "host.title": "Host",
    "host.token_hint": "Das Token gilt unbegrenzt. Zurückziehen, wenn es abgeflossen sein könnte — etwa nachdem der Host weitergegeben oder neu aufgesetzt wurde.<br><br>Der Agent bemerkt es beim nächsten Kontakt und meldet sich von selbst neu an. Zeitplan, Checkmk-Verknüpfung und Verlauf bleiben erhalten. Bis zur erneuten Anmeldung steht der Name offen, deshalb landet der Host wieder in der Freigabe.",
    "host.window_example": "SA,SO 02:00-05:00",
    "host.window_hint": "Das ist <b>kein Zeitplan</b>, sondern eine Schranke: sie entscheidet, ob ein nach dem Patchen fälliger Neustart sofort erfolgen darf. Es wird nichts von selbst gestartet. Leeres Feld bedeutet jederzeit.<br><br>Format: <code>SA,SO 02:00-05:00</code> — auch über Mitternacht: <code>MO-FR 22:00-06:00</code>",
    "language.default": "Vorgabe für diese Installation",
    "language.default.hint": "Gilt für Benutzer, die selbst keine Sprache gewählt haben, und für die Anmeldeseite.",
    "language.default.saved": "Vorgabesprache gespeichert.",
    "language.hint": "Die Sprache gilt nur für diese Oberfläche. Meldungen älterer Agenten behalten den Wortlaut, mit dem sie aufgezeichnet wurden.",
    "language.name.de": "Deutsch",
    "language.name.en": "English",
    "language.own": "Meine Sprache",
    "language.own.saved": "Sprache gespeichert.",
    "list.all_hosts": "alle Hosts",
    "list.drag": "Ziehen, um die Reihenfolge zu ändern",
    "list.drag_blocked": "Reihenfolge ändern nur ohne aktiven Filter",
    "list.empty": "Noch kein Host angemeldet.<br><br>Agent auf einem Zielsystem installieren — siehe Einstellungen → Agents.",
    "list.no_match": "Kein Host passt zum Filter.",
    "list.not_linked": "nicht verknüpft",
    "live.copy": "Protokoll kopieren",
    "live.follow": "Automatisch mitscrollen",
    "live.title": "Live-Ausgabe",
    "login.failed": "Anmeldung fehlgeschlagen.",
    "login.fill_both": "Bitte beides ausfüllen.",
    "login.password": "Passwort",
    "login.session_expired": "Sitzung abgelaufen. Bitte erneut anmelden.",
    "login.submit": "Anmelden",
    "login.subtitle": "Core Operations Manager",
    "login.title": "Anmeldung",
    "login.unreachable": "Server nicht erreichbar.",
    "login.user": "Benutzername",
    "msg.agent_update_many": "Aktualisierung für {anzahl} Agents eingeplant",
    "msg.agent_update_scheduled": "Agent-Aktualisierung eingeplant",
    "msg.agents_current": "Alle Agents sind aktuell",
    "msg.build_requested": "Paketbau angefordert — läuft auf dem Server",
    "msg.cancelled": "Abgebrochen",
    "msg.cmk_connected": "Checkmk verbunden",
    "msg.copied": "{was} kopiert",
    "msg.copy_failed": "Kopieren nicht möglich — bitte markieren und Strg+C",
    "msg.copy_failed_short": "Kopieren nicht möglich — bitte markieren",
    "msg.created": "{name} angelegt",
    "msg.downtime_cancelled": "Downtimes aufgehoben",
    "msg.downtime_set": "Downtime gesetzt",
    "msg.host_approved": "Host freigegeben",
    "msg.host_rejected": "Host abgelehnt",
    "msg.host_removed": "Host entfernt",
    "msg.key_applied": "Lizenzschlüssel eingetragen",
    "msg.key_rejected": "Schlüssel abgelehnt — bisheriger Stand bleibt",
    "msg.key_removed": "Lizenzschlüssel entfernt",
    "msg.need_datetime": "Bitte Datum und Uhrzeit angeben",
    "msg.need_day_time": "Für den Update-Zeitplan Wochentag und Uhrzeit angeben",
    "msg.need_minutes": "Bitte Minuten oder eine Zeitspanne angeben",
    "msg.need_name_pw": "Name und Passwort ausfüllen",
    "msg.need_zip": "Bitte die ZIP-Datei des Pakets wählen",
    "msg.no_key": "Kein Schlüssel eingegeben",
    "msg.nothing_to_copy": "Nichts zu kopieren",
    "msg.order_not_saved": "Reihenfolge konnte nicht gespeichert werden",
    "msg.package_discarded": "Paket verworfen",
    "msg.package_staged": "Paket geprüft und bereitgestellt",
    "msg.patch_scheduled": "Patch-Auftrag eingeplant",
    "msg.pw_changed": "Passwort geändert. Bitte neu anmelden.",
    "msg.pw_mismatch": "Die beiden neuen Passwörter stimmen nicht überein",
    "msg.pw_set": "Passwort für {name} gesetzt, laufende Sitzungen beendet",
    "msg.pw_short": "Passwort muss mindestens 6 Zeichen haben",
    "msg.reboot_scheduled": "Neustart eingeplant",
    "msg.reboot_triggered": "Neustart ausgelöst — verfällt nach {minuten} Minuten ohne Kontakt",
    "msg.refreshed": "Aktualisiert",
    "msg.removed": "{name} entfernt",
    "msg.scan_many": "Prüfung für {anzahl} Hosts eingeplant",
    "msg.scan_scheduled": "Prüfung eingeplant — wird beim nächsten Agent-Kontakt ausgeführt",
    "msg.time_in_past": "Der Zeitpunkt liegt in der Vergangenheit",
    "msg.token_failed": "Token konnte nicht erzeugt werden",
    "msg.token_revoked": "Token zurückgezogen — Host wartet auf erneute Anmeldung",
    "msg.unreachable_code": "Server nicht erreichbar ({code})",
    "msg.url_scheme": "Die Adresse muss mit http:// oder https:// beginnen",
    "note.agent_outdated": "Agent {version} veraltet",
    "note.followup": "Nachschlag vorgemerkt ({anzahl})",
    "note.job_running": "{auftrag} läuft",
    "note.never_reported": "noch nie gemeldet",
    "note.pending_approval": "wartet auf Freigabe",
    "note.reboot_planned": "Neustart geplant: {zeit}",
    "note.rejected": "abgelehnt",
    "note.silent": "Host meldet sich nicht",
    "note.updates_at": "Updates: {zeit}",
    "note.updates_need_reboot": "Updates erfordern Neustart",
    "plan.auto_reboot": "Bei Bedarf automatisch neu starten",
    "plan.expiry_hint": "Meldet sich der Host bis zum Ablauf dieser Frist nicht, wird der Neustart <b>nicht nachgeholt</b>, sondern als abgelaufen verworfen. Sonst würde ein Server, der übers Wochenende aus war, Montagfrüh im laufenden Betrieb neu starten.",
    "plan.grace_hint": "Der Nachlauf verhindert, dass ein Host, der zum Termin ausgeschaltet war, später im Betrieb patcht. Nach Ablauf wird der Termin übersprungen.",
    "plan.grace_hours": "Nachlauf (Stunden)",
    "plan.grace_minutes": "Nachlaufzeit (Minuten)",
    "plan.in_2h": "In 2 Stunden",
    "plan.job.patch": "Updates installieren",
    "plan.job.patch_reboot": "Updates installieren und neu starten, falls nötig",
    "plan.job.reboot": "Nur neu starten",
    "plan.job.scan": "Nur auf Updates prüfen",
    "plan.last": "Zuletzt gelaufen",
    "plan.next": "Nächster Termin",
    "plan.once_hint": "Legt einen Auftrag auf einen festen Zeitpunkt. Er wird erst dann an den Agent übergeben. Richtlinie und Wartungsfenster werden übergangen — der Termin ist ja ausdrücklich gewählt.",
    "plan.saturday_0300": "Samstag 03:00",
    "plan.scheduled": "Eingeplant",
    "plan.set_downtime": "Vorher Downtime in Checkmk setzen",
    "plan.submit": "Einplanen",
    "plan.time": "Uhrzeit",
    "plan.today_2200": "Heute 22:00",
    "plan.update_enabled": "Updates nach Zeitplan installieren",
    "plan.update_hint": "Wiederkehrender Termin zum Installieren von Updates. Wird beim Kontakt des Agents ausgewertet — läuft der Host zum Termin nicht, greift der Nachlauf.",
    "plan.weekdays": "Wochentage",
    "plan.what": "Was soll passieren",
    "plan.when": "Datum und Uhrzeit",
    "rack.actions": "Aktionen",
    "rack.checkmk": "Checkmk",
    "rack.host": "Host",
    "rack.no": "Nr",
    "rack.security": "Sicherheit",
    "rack.system": "System",
    "rack.updates": "Updates",
    "reason.cbs": "Komponentenspeicher",
    "reason.kernel": "neuer Kernel",
    "reason.netlogon": "Domänenbeitritt",
    "reason.package_manager": "Paketverwaltung",
    "reason.pending_rename": "Dateireste",
    "reason.windows_update": "Windows Update",
    "reboot.cancel": "Abbrechen",
    "reboot.dialog_info": "Startet <b>{host}</b> neu. Es werden keine Updates installiert.",
    "reboot.dialog_title": "{host} neu starten",
    "reboot.downtime_minutes": "Downtime-Dauer (Minuten)",
    "reboot.downtime_target": "Downtime wird gesetzt auf: <b>{ziel}</b>. Scheitert sie, unterbleibt der Neustart.",
    "reboot.grace_hint": "Die <b>Nachlaufzeit</b> begrenzt, wie lange der Auftrag gültig bleibt. Ist der Host jetzt nicht erreichbar und meldet sich erst danach, wird der Neustart <b>nicht mehr ausgeführt</b>, sondern als abgelaufen verworfen. Ohne diese Grenze würde ein Server, der gerade aus ist, beim nächsten Einschalten mitten im Betrieb neu starten.",
    "reboot.grace_minutes": "Nachlaufzeit (Minuten)",
    "reboot.no_link": "Kein Checkmk-Host verknüpft — es kann keine Downtime gesetzt werden. Das Monitoring wird beim Neustart Alarm schlagen.",
    "reboot.now": "Jetzt neu starten",
    "reboot.set_downtime": "Checkmk-Downtime vorher setzen",
    "reboot.title": "Neustart",
    "settings.account.change": "Passwort ändern",
    "settings.account.current_pw": "Aktuelles Passwort",
    "settings.account.hint": "Eigenes Passwort ändern. Nach der Änderung werden alle Sitzungen dieses Zugangs beendet, auch diese.",
    "settings.account.new_pw": "Neues Passwort",
    "settings.account.new_pw2": "Neues Passwort wiederholen",
    "settings.account.role": "Rolle",
    "settings.account.signed_in_as": "Angemeldet als",
    "settings.agents.copy_cmd": "Befehl kopieren",
    "settings.agents.copy_cmd2": "Befehl kopieren",
    "settings.agents.download": "Agent herunterladen",
    "settings.agents.download_deb": "Herunterladen",
    "settings.agents.download_msi": "Herunterladen",
    "settings.agents.hint": "Agents melden sich nach der Installation selbst an und erscheinen in der Übersicht als <b>wartet auf Freigabe</b>. Ohne Freigabe bekommen sie keine Aufträge.",
    "settings.agents.linux": "Linux einrichten",
    "settings.agents.linux_hint": "Auf dem Zielsystem als root ausführen. Lädt das Paket und richtet den Agent ein.<br>\n          Der Befehl enthält ein Token, das nach kurzer Zeit abläuft — was auf dem Zielsystem in der\n          Verlaufsdatei zurückbleibt, ist dann wertlos. Erst kurz vor der Einrichtung erzeugen.",
    "settings.agents.linux_pkg": "<b>Linux</b> · .deb für Debian und Ubuntu<br>",
    "settings.agents.make_cmd": "Befehl erzeugen",
    "settings.agents.rebuild": "Pakete neu bauen",
    "settings.agents.windows": "Windows einrichten",
    "settings.agents.windows_hint": "MSI herunterladen, auf das Zielsystem kopieren, dann in einer Eingabeaufforderung als Administrator:",
    "settings.agents.windows_pkg": "<b>Windows</b> · .msi, Python ist enthalten<br>",
    "settings.audit.hint": "Wer wann was getan hat. Wird nur angehängt — Einträge lassen sich weder ändern noch löschen.",
    "settings.cmk.connect": "Verbinden und speichern",
    "settings.cmk.hint": "Automation-Benutzer mit Recht auf Hostliste und Downtimes. In Checkmk 2.4 wird kein Benutzer <code>automation</code> mehr automatisch angelegt.",
    "settings.cmk.secret": "Automation-Secret",
    "settings.cmk.secret_hint": "Wird vor dem Speichern geprüft und verschlüsselt abgelegt. Nie wieder abrufbar.",
    "settings.cmk.site": "Site",
    "settings.cmk.url": "Server-URL",
    "settings.cmk.user": "Benutzer",
    "settings.cmk.verify": "TLS-Zertifikat prüfen",
    "settings.license.apply": "Eintragen",
    "settings.license.hint": "Bis zu 10 Hosts sind ohne Schlüssel möglich, auch geschäftlich und ohne zeitliche Begrenzung. Gezählt werden freigegebene Hosts; wartende, abgelehnte und gelöschte zählen nicht.<br><br>Für mehr Hosts wird ein Schlüssel benötigt — erhältlich über <b>sales@itkub.de</b>.<br><br>Läuft ein Schlüssel ab, werden bereits freigegebene Hosts <b>weiterhin gepatcht</b>. Lediglich neue Freigaben oberhalb von 10 sind dann nicht mehr möglich.",
    "settings.license.key": "Schlüssel",
    "settings.license.key_title": "Lizenzschlüssel",
    "settings.license.remove": "Entfernen",
    "settings.license.scope": "Umfang",
    "settings.proxy.example": "z.B. 192.168.1.20",
    "settings.proxy.hint": "Adresse des vorgeschalteten Proxy. Nur von dort werden die Kopfzeilen <code>X-Forwarded-For</code> und <code>X-Forwarded-Proto</code> ausgewertet — mehrere mit Komma trennen.<br><br><b>Verschlüsselte Anfragen werden ausschließlich von dieser Adresse angenommen.</b> Steht hier nichts oder die falsche Adresse, wird jede Anfrage abgewiesen, die HTTPS behauptet — CO-37 nimmt selbst kein TLS entgegen, die Behauptung wäre unbelegt. Unverschlüsselt bleibt erreichbar, bis unten der Haken gesetzt wird.<br><br>Ein zu weit gefasster Eintrag ist ein Risiko: wer dann die Kopfzeile selbst setzt, umgeht die Drosselung der Anmeldung mit erfundenen Absendern.<br><br>Der Weg zurück bei einem falschen Eintrag ist immer der unverschlüsselte Zugang direkt auf dem internen Port.",
    "settings.proxy.https_hint": "Ist die Option an, werden alle Anfragen abgewiesen, die nicht verschlüsselt über den eingetragenen Proxy kamen. Der direkte Zugriff auf den internen Port beantwortet dann nur noch mit einer Fehlermeldung.<br><br>Der Port selbst bleibt offen — wirklich geschlossen ist er erst mit einer Firewallregel. Die gehört zum Rechner und nicht in diese Anwendung.<br><br><b>Die Umschaltung trifft auch alle Agents.</b> Wer noch unverschlüsselt spricht, fällt sofort aus — und still, weil der Agent den Fehler nur in seine eigene Protokolldatei schreibt.<br><br><b>Fällt der Proxy dauerhaft aus,</b> kommst du über den Browser nicht mehr herein — auch nicht, um diesen Haken zu entfernen. Der Weg zurück geht dann über die Maschine selbst:<br><code>/opt/co37/venv/bin/python -c 'import sqlite3;c=sqlite3.connect(\"/opt/co37/data/co37.db\");c.execute(\"UPDATE setting SET value=? WHERE key=?\",(\"false\",\"https_only\"));c.commit()'</code><br>danach <code>systemctl restart co37-backend</code>. Ausführlich in <code>REVERSE-PROXY.md</code>.",
    "settings.proxy.https_only": "Zugang auf HTTPS beschränken",
    "settings.proxy.https_title": "Nur über HTTPS",
    "settings.proxy.public": "Öffentliche Adresse",
    "settings.proxy.public_example": "z.B. https://co37.beispiel.com",
    "settings.proxy.public_hint": "Adresse, unter der CO-37 <b>von den Zielsystemen aus</b> erreichbar ist. Sie landet in den Installationsbefehlen und damit in der <code>agent.conf</code> jedes neu eingerichteten Hosts.<br><br>Bleibt das Feld leer, wird die Adresse verwendet, unter der du das Dashboard gerade aufgerufen hast. Dann hängt es davon ab, wie du die Seite öffnest — richtest du nach der Umstellung einen Host über den internen Port ein, spräche dessen Agent dauerhaft unverschlüsselt.<br><br>Bestehende Agents sind davon nicht betroffen. Deren Server steht in der <code>agent.conf</code> auf dem Host und wird durch kein Update geändert.",
    "settings.proxy.public_save": "Speichern",
    "settings.proxy.public_title": "Öffentliche Adresse",
    "settings.proxy.save": "Speichern",
    "settings.proxy.title": "Reverse Proxy",
    "settings.proxy.trusted": "Vertrauenswürdiger Proxy",
    "settings.rollout.all": "Alle auf einmal",
    "settings.rollout.auto": "Nach einem Systemupdate automatisch ausrollen",
    "settings.rollout.current": "Aktuelle Version",
    "settings.rollout.done": "Ausgerollte Agents",
    "settings.rollout.hint": "Agent und System tragen dieselbe Versionsnummer. Weicht sie auf einem Host ab, erscheint dort in der Übersicht ein Hinweis und ein Knopf <b>Agent</b> zum Aktualisieren.",
    "settings.rollout.mode": "Vorgehen",
    "settings.rollout.none": "— keiner gewählt —",
    "settings.rollout.pilot": "Pilot-Host",
    "settings.rollout.save": "Speichern",
    "settings.rollout.staged": "Gestaffelt — erst ein Host, dann der Rest",
    "settings.rollout.start": "Jetzt ausrollen",
    "settings.rollout.state": "Zustand",
    "settings.rollout.title": "Agent-Updates",
    "settings.rollout.update_all": "Alle veralteten Agents aktualisieren",
    "settings.tab.access": "Zugang",
    "settings.tab.account": "Konto",
    "settings.tab.agents": "Agents",
    "settings.tab.audit": "Protokoll",
    "settings.tab.checkmk": "Checkmk",
    "settings.tab.language": "Sprache",
    "settings.tab.license": "Lizenz",
    "settings.tab.update": "Update",
    "settings.tab.users": "Benutzer",
    "settings.title": "Einstellungen",
    "settings.update.discard": "Verwerfen",
    "settings.update.drop": "<b>ZIP-Paket</b> und <b>.sig-Datei</b> hier ablegen oder klicken<br>",
    "settings.update.files": "co37_vX_Y_Z.zip und co37_vX_Y_Z.zip.sig, zusammen auswählen · max. 50 MB",
    "settings.update.installed": "Installierte Version",
    "settings.update.reset": "Zurücksetzen",
    "settings.update.run": "Update ausführen",
    "settings.update.run_hint": "Der aktuelle Stand wird gesichert, ausgetauscht und der Dienst neu gestartet. Schlägt der Health-Check fehl, wird zurückgerollt.",
    "settings.update.sig_hint": "Die Signatur weist nach, dass das Paket vom Herausgeber stammt und unterwegs nicht verändert wurde. Sie wird <b>vor</b> dem Auspacken geprüft — der Watcher entpackt als root, ein untergeschobenes Paket wäre damit Codeausführung als root.<br><br>Eine Prüfsumme allein genügt dafür nicht: wer die Datei austauschen kann, kann auch die Prüfsummendatei daneben austauschen.",
    "settings.update.staged": "Bereitgestellt",
    "settings.update.state": "Zustand",
    "settings.update.watcher": "Update-Watcher",
    "settings.users.create": "Anlegen",
    "settings.users.existing": "Vorhandene Benutzer",
    "settings.users.hint": "Benutzer dürfen alles am Patch-Management, aber keine Benutzer verwalten. Das können nur Administratoren.",
    "settings.users.name": "Benutzername",
    "settings.users.new": "Neuen Benutzer anlegen",
    "settings.users.password": "Passwort",
    "settings.users.role": "Rolle",
    "settings.users.role_admin": "Administrator",
    "settings.users.role_user": "Benutzer",
    "stats.hosts": "Hosts",
    "stats.online": "online",
    "stats.reboot": "Neustart nötig",
    "stats.scheduled": "geplant",
    "stats.updates": "Updates offen",
    "stats.waiting": "wartet",
    "upd.error": "Fehler",
    "upd.idle": "Bereit",
    "upd.rolled_back": "Zurückgerollt",
    "upd.running": "Wird ausgeführt",
    "upd.success": "Erfolgreich",
    "upd.triggered": "Angefordert",
    "upd.uploaded": "Paket wartet",
  },
};

let SPRACHE = SPRACHE_VORGABE;

/**
 * Uebersetzt einen Schluessel.
 *
 * werte fuellt Platzhalter der Form {name}. Ersetzt wird mit split/join
 * statt mit einem regulaeren Ausdruck: der Name koennte Sonderzeichen
 * enthalten, und eine unmaskierte Ersetzung traefe dann das Falsche.
 */
function t(schluessel, werte) {
  let text = (I18N[SPRACHE] || {})[schluessel];
  if (text === undefined) text = I18N[SPRACHE_VORGABE][schluessel];
  if (text === undefined) return schluessel;
  if (werte) {
    for (const name of Object.keys(werte)) {
      text = text.split("{" + name + "}").join(String(werte[name]));
    }
  }
  return text;
}

/** Gueltige Sprache oder null. Kein Rateschluss auf Teilzeichenketten. */
function pruefeSprache(code) {
  return SPRACHEN.includes(code) ? code : null;
}

/**
 * Liest die Sprache aus dem Cookie.
 *
 * roh ist nur fuer die Pruefreihe da: dort gibt es keinen echten
 * Cookie-Speicher, und ohne diesen Weg liesse sich das Auslesen nicht
 * pruefen.
 */
function spracheAusCookie(roh) {
  const text = roh === undefined ? document.cookie : roh;
  for (const teil of String(text || "").split(";")) {
    const [name, ...rest] = teil.trim().split("=");
    // Genauer Vergleich: ein Cookie 'x_co37_lang' darf nicht treffen.
    if (name === SPRACH_COOKIE) return pruefeSprache(rest.join("="));
  }
  return null;
}

function merkeSpracheImCookie(code) {
  if (!pruefeSprache(code)) return;
  const sicher = location.protocol === "https:" ? "; Secure" : "";
  // Ein Jahr. Kein HttpOnly - die Oberflaeche muss den Wert selbst lesen,
  // und eine Spracheinstellung ist kein Geheimnis.
  document.cookie = `${SPRACH_COOKIE}=${code}; path=/; max-age=31536000`
                  + `; SameSite=Lax${sicher}`;
}

/**
 * Setzt die Sprache und zeichnet die Oberflaeche neu.
 *
 * Gibt zurueck, ob etwas Gueltiges gesetzt wurde - der Aufrufer soll nicht
 * raten muessen, ob eine unbekannte Kennung stillschweigend verworfen wurde.
 */
function setzeSprache(code, { merken = true } = {}) {
  const gueltig = pruefeSprache(code);
  if (!gueltig) return false;
  SPRACHE = gueltig;
  if (merken) merkeSpracheImCookie(gueltig);
  if (typeof document !== "undefined" && document.documentElement) {
    document.documentElement.lang = gueltig;
  }
  uebersetzeDom();
  return true;
}

/**
 * Traegt alle Uebersetzungen ins Markup ein.
 *
 * Laeuft ueber das ganze Dokument, nicht nur ueber den sichtbaren Teil:
 * Dialoge sind geschlossen, ihr Inhalt existiert aber schon, und beim
 * Oeffnen soll nichts nachflackern.
 */
function uebersetzeDom(wurzel) {
  const w = wurzel || (typeof document !== "undefined" ? document : null);
  if (!w || !w.querySelectorAll) return;
  for (const el of w.querySelectorAll("[data-i18n]")) {
    setzeText(el, t(el.dataset.i18n));
  }
  for (const el of w.querySelectorAll("[data-i18n-html]")) {
    el.innerHTML = t(el.dataset.i18nHtml);
  }
  for (const el of w.querySelectorAll("[data-i18n-placeholder]")) {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  }
  for (const el of w.querySelectorAll("[data-i18n-title]")) {
    el.title = t(el.dataset.i18nTitle);
  }
}

/**
 * Ersetzt den Text eines Elements, ohne seine Kindelemente anzuruehren.
 *
 * Noetig, weil das Markup an vielen Stellen so aussieht:
 *
 *     <label>Anzeigename<input id="hName"></label>
 *     <label><input type="checkbox" id="hAuto"> Neustart ausfuehren</label>
 *
 * Ein schlichtes textContent haette dort das Eingabefeld beziehungsweise
 * das Kaestchen geloescht - und zwar erst beim Umschalten der Sprache,
 * also lange nach jeder Sichtpruefung.
 *
 * Darum gezielt der erste Textknoten mit Inhalt. Wo es keinen gibt, zaehlt
 * das Element als reiner Text und wird ganz ersetzt.
 */
function setzeText(el, text) {
  const knoten = el.childNodes
    ? [...el.childNodes].find(n => n.nodeType === 3 && n.textContent.trim())
    : null;
  if (knoten) knoten.textContent = text;
  else el.textContent = text;
}

/**
 * Ermittelt die Sprache in der vereinbarten Reihenfolge:
 * Einstellung des Benutzers, Vorgabe der Installation, Englisch.
 *
 * Das Cookie steht vor der Vorgabe der Installation, aber hinter dem
 * Benutzer: wer bewusst umgeschaltet hat, soll das auf der Anmeldeseite
 * wiederfinden, ohne dass es eine Wahl am Konto ueberschreibt.
 */
/**
 * Gebietsschema fuer Datum und Uhrzeit.
 *
 * Nicht dieselbe Kennung wie die Sprache: 'en' allein waere fuer
 * toLocaleString nicht eindeutig, und en-US drehte Tag und Monat um -
 * '03.09.' wuerde zu '9/3/', was in Europa als 9. Maerz gelesen wird. Bei
 * Wartungsfenstern und geplanten Neustarts ist das kein Schoenheitsfehler.
 */
function zeitSprache() {
  return SPRACHE === "de" ? "de-DE" : "en-GB";
}

function ermittleSprache({ benutzer = null, vorgabe = null } = {}) {
  return pruefeSprache(benutzer)
      || spracheAusCookie()
      || pruefeSprache(vorgabe)
      || SPRACHE_VORGABE;
}
