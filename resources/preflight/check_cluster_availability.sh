#!/usr/bin/env bash
# Check genuinely idle and partially free compute-node capacity on SSH hosts
# configured in ~/.ssh/config.
# Usage: ./check_cluster_availability.sh [ssh-host ...]
# With no arguments, checks every concrete Host entry in ~/.ssh/config.
#
# Output status meanings:
#   FULLY_IDLE  no scheduler slots are assigned on the node
#   PARTIAL     some scheduler slots are assigned and some remain available
#   BUSY        no scheduler slots remain available
#   UNAVAILABLE node is down, offline, drained, reserved, or otherwise unusable
#   SUSPECT_LOAD scheduler says idle, but untracked CPU load is above threshold
#   SUSPECT_MOM  scheduler reports capacity, but MOM heartbeat evidence is
#                incomplete (for example, missing or unparseable)
#
# Optional environment variables:
#   MOM_MAX_AGE=300               maximum acceptable PBS MOM heartbeat age (s)
#   UNTRACKED_LOAD_THRESHOLD=1.0  load allowed on a scheduler-idle node
#   EXCLUDED_NODES="node06"        space/comma-separated policy exclusions

set -uo pipefail

SSH_CONFIG="${SSH_CONFIG:-$HOME/.ssh/config}"
CONNECT_TIMEOUT="${CONNECT_TIMEOUT:-8}"
MOM_MAX_AGE="${MOM_MAX_AGE:-300}"
UNTRACKED_LOAD_THRESHOLD="${UNTRACKED_LOAD_THRESHOLD:-1.0}"
EXCLUDED_NODES="${EXCLUDED_NODES:-}"

if [[ $# -gt 0 ]]; then
  hosts=("$@")
else
  if [[ ! -r "$SSH_CONFIG" ]]; then
    echo "Cannot read SSH config: $SSH_CONFIG" >&2
    exit 1
  fi
  # Keep only concrete aliases; wildcard and negated patterns are not hosts.
  while IFS= read -r host; do
    hosts+=("$host")
  done < <(awk '
      tolower($1) == "host" {
        for (i = 2; i <= NF; i++)
          if ($i !~ /[*?!]/) print $i
      }
    ' "$SSH_CONFIG" | awk '!seen[$0]++')
fi

if [[ ${#hosts[@]} -eq 0 ]]; then
  echo "No concrete SSH hosts found. Pass aliases explicitly, e.g. $0 jutai-1" >&2
  exit 1
fi

read -r -d '' remote_check <<'REMOTE_CHECK' || true
set -u
if command -v sinfo >/dev/null 2>&1; then
  echo "SCHEDULER=Slurm"
  echo "-- partition summary --"
  sinfo -h -o "%P AVAILABLE=%a NODES=%D STATE=%t" 2>&1 || true
  echo "-- node capacity (unique nodes) --"
  scontrol show nodes -o 2>&1 | awk -v load_threshold="$UNTRACKED_LOAD_THRESHOLD" '
    function value(prefix,    i, pair) {
      for (i = 1; i <= NF; i++) {
        split($i, pair, "=")
        if (pair[1] == prefix) return substr($i, length(prefix) + 2)
      }
      return ""
    }
    function gpu_count(tres,    entries, count, i, pair, total) {
      count = split(tres, entries, ",")
      total = -1
      for (i=1; i<=count; i++) {
        split(entries[i], pair, "=")
        if (pair[1] == "gres/gpu") return pair[2] + 0
        if (pair[1] ~ /^gres\/gpu:/) { if (total < 0) total=0; total += pair[2]+0 }
      }
      return total
    }
    {
      node = value("NodeName")
      state = value("State")
      total = value("CPUEfctv")
      if (total == "") total = value("CPUTot")
      alloc = value("CPUAlloc")
      load = value("CPULoad")
      mem = value("RealMemory")
      parts = value("Partitions")
      gres = value("CfgTRES")
      gpu_total = gpu_count(gres)
      gpu_alloc = gpu_count(value("AllocTRES"))
      if (gpu_total >= 0 && gpu_alloc < 0 && alloc == 0) gpu_alloc=0
      gpu_free = gpu_total >= 0 && gpu_alloc >= 0 ? gpu_total-gpu_alloc : "unknown"
      gpu_total_display = gpu_total >= 0 ? gpu_total : "unknown"
      gpu_alloc_display = gpu_alloc >= 0 ? gpu_alloc : "unknown"
      if (node == "" || total == "") next
      total += 0
      alloc += 0
      load += 0
      free = total - alloc
      if (free < 0) free = 0

      upper = toupper(state)
      unavailable = upper ~ /(DOWN|DRAIN|FAIL|MAINT|REBOOT|RESERVED|UNKNOWN|NO_RESPOND)/
      if (unavailable) {
        status = "UNAVAILABLE"
        free = 0
        unavailable_nodes++
      } else if (alloc == 0 && free == total && upper ~ /IDLE/ && load > load_threshold) {
        status = "SUSPECT_LOAD"
        free = 0
        suspect_nodes++
      } else if (alloc == 0 && free == total && upper ~ /IDLE/) {
        status = "FULLY_IDLE"
        full_nodes++
        full_cpus += free
      } else if (free > 0) {
        status = "PARTIAL"
        partial_nodes++
        partial_cpus += free
      } else {
        status = "BUSY"
        busy_nodes++
      }
      available_cpus += free
      printf "NODE=%s STATUS=%s STATE=%s CPUS_TOTAL=%d CPUS_ALLOC=%d CPUS_FREE=%d LOAD=%.2f MEM_MB=%s PARTITIONS=%s GRES=%s GPUS_TOTAL=%s GPUS_ALLOC=%s GPUS_FREE=%s\n", \
        node, status, state, total, alloc, free, load, mem, parts, gres, gpu_total_display, gpu_alloc_display, gpu_free
    }
    END {
      printf "SUMMARY FULLY_IDLE_NODES=%d PARTIAL_NODES=%d BUSY_NODES=%d UNAVAILABLE_NODES=%d SUSPECT_LOAD_NODES=%d FREE_CPUS=%d\n", \
        full_nodes, partial_nodes, busy_nodes, unavailable_nodes, suspect_nodes, available_cpus
    }
  ' || true
elif command -v pbsnodes >/dev/null 2>&1; then
  echo "SCHEDULER=PBS/Torque"
  echo "-- node capacity --"
  pbsnodes -a 2>&1 | awk -v now="$(date +%s)" -v max_mom_age="$MOM_MAX_AGE" -v load_threshold="$UNTRACKED_LOAD_THRESHOLD" -v excluded_nodes="$EXCLUDED_NODES" '
    BEGIN { RS=""; FS="\n" }
    function trim(text) {
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", text)
      return text
    }
    function rhs(text) {
      sub(/^[^=]*=[[:space:]]*/, "", text)
      return trim(text)
    }
    function count_job_slots(text, used,    entries, n, i, slot, range, ends, j, count) {
      delete used
      gsub(/[[:space:]]/, "", text)
      n = split(text, entries, ",")
      for (i = 1; i <= n; i++) {
        split(entries[i], range, "/")
        slot = range[1]
        if (slot ~ /^[0-9]+-[0-9]+$/) {
          split(slot, ends, "-")
          for (j = ends[1] + 0; j <= ends[2] + 0; j++) used[j] = 1
        } else if (slot ~ /^[0-9]+$/) {
          used[slot + 0] = 1
        }
      }
      count = 0
      for (j in used) count++
      return count
    }
    function is_policy_excluded(name,    normalized, entries, count, i) {
      normalized = excluded_nodes
      gsub(/,/, " ", normalized)
      count = split(normalized, entries, /[[:space:]]+/)
      for (i = 1; i <= count; i++)
        if (entries[i] == name) return 1
      return 0
    }
    {
      node = trim($1)
      state = "unknown"
      total = 0
      assigned = -1
      gpu_total = "unknown"
      gpu_assigned = "unknown"
      jobs = ""
      power = "unknown"
      mom_age = -1
      mom_note = ""
      mom_health = "UNKNOWN"
      mom_failure = 0
      load = 0
      for (line = 2; line <= NF; line++) {
        text = trim($line)
        if (text ~ /^state[[:space:]]*=/) state = rhs(text)
        else if (text ~ /^power_state[[:space:]]*=/) power = rhs(text)
        else if (text ~ /^np[[:space:]]*=/) total = rhs(text) + 0
        else if (text ~ /^resources_available.ncpus[[:space:]]*=/) total = rhs(text) + 0
        else if (text ~ /^resources_assigned.ncpus[[:space:]]*=/) assigned = rhs(text) + 0
        else if (text ~ /^(gpus|resources_available.ngpus)[[:space:]]*=/) gpu_total = rhs(text)
        else if (text ~ /^resources_assigned.ngpus[[:space:]]*=/) gpu_assigned = rhs(text)
        else if (text ~ /^jobs[[:space:]]*=/) jobs = rhs(text)
        else if (text ~ /^note[[:space:]]*=/) {
          note_text = text
          sub(/^[^=]*=[[:space:]]*/, "", note_text)
          mom_note = trim(note_text)
          note_upper = toupper(mom_note)
          if (note_upper ~ /(MOM[[:space:]_-]*(REJECT|FAIL)|SEND[[:space:]]+TO[[:space:]]+MOM|RC[[:space:]]*=[[:space:]]*-1|COMMUNICATION[[:space:]]+(FAIL|ERROR)|MOM[[:space:]]+UNREACH)/) {
            mom_failure = 1
          }
        }
        else if (text ~ /^status[[:space:]]*=/) {
          status_text = rhs(text)
          heartbeat = status_text
          sub(/^.*rectime=/, "", heartbeat)
          sub(/,.*$/, "", heartbeat)
          if (heartbeat ~ /^[0-9]+$/) mom_age = now - heartbeat
          load_text = status_text
          sub(/^.*loadave=/, "", load_text)
          sub(/,.*$/, "", load_text)
          if (load_text ~ /^[0-9.]+$/) load = load_text + 0

          # pbsnodes can retain state=free while the MOM is rejecting requests.
          # Keep explicit failure evidence separate from generic administrative
          # notes, which are retained for reporting but do not establish health.
          status_upper = toupper(status_text)
          if (status_upper ~ /(MOM[[:space:]_-]*(REJECT|FAIL)|SEND[[:space:]]+TO[[:space:]]+MOM|RC[[:space:]]*=[[:space:]]*-1|COMMUNICATION[[:space:]]+(FAIL|ERROR)|MOM[[:space:]]+UNREACH)/) {
            mom_failure = 1
          }
          note_text = status_text
          if (note_text ~ /(^|[ ,])note[[:space:]]*=/) {
            sub(/^.*note[[:space:]]*=[[:space:]]*/, "", note_text)
            sub(/,.*/, "", note_text)
            mom_note = trim(note_text)
          }
        }
      }
      if (node == "" || total <= 0) next
      if (assigned < 0) assigned = count_job_slots(jobs, used)
      free = total - assigned
      if (free < 0) free = 0

      lower = tolower(state)
      policy_excluded = is_policy_excluded(node)
      unavailable = lower ~ /(down|offline|unknown|reserve|stale|state-unknown)/
      if (policy_excluded) unavailable = 1
      if (tolower(power) !~ /^(running|unknown)$/) unavailable = 1
      scheduler_unavailable = unavailable
      suspect_mom = 0
      if (mom_failure) {
        unavailable = 1
        mom_health = "FAILED"
      } else if (mom_age > max_mom_age) {
        unavailable = 1
        mom_health = "STALE"
      } else if (mom_age < 0) {
        # A scheduler-free node without a parseable MOM heartbeat is not safe
        # to advertise as available. The resource selector rejects this
        # status, while leaving it distinguishable from a hard down node.
        suspect_mom = 1
        mom_health = "UNKNOWN"
      } else if (scheduler_unavailable) {
        mom_health = "SCHEDULER"
      } else {
        mom_health = "HEALTHY"
      }
      exclusive = lower ~ /(job-exclusive|busy)/
      if (unavailable) {
        status = "UNAVAILABLE"
        free = 0
        unavailable_nodes++
      } else if (exclusive) {
        status = "BUSY"
        free = 0
        busy_nodes++
      } else if (suspect_mom) {
        status = "SUSPECT_MOM"
        free = 0
        suspect_mom_nodes++
      } else if (assigned == 0 && lower ~ /free/ && load > load_threshold) {
        status = "SUSPECT_LOAD"
        free = 0
        suspect_nodes++
      } else if (assigned == 0 && lower ~ /free/) {
        status = "FULLY_IDLE"
        full_nodes++
        full_cpus += free
      } else if (free > 0 && lower ~ /free/) {
        status = "PARTIAL"
        partial_nodes++
        partial_cpus += free
      } else {
        status = "BUSY"
        free = 0
        busy_nodes++
      }
      available_cpus += free
      compact_jobs = jobs
      gsub(/[[:space:]]/, "", compact_jobs)
      if (compact_jobs == "") compact_jobs = "none"
      mom_display = mom_age < 0 ? "unknown" : mom_age
      note_display = mom_note
      gsub(/[[:space:]]+/, "_", note_display)
      if (note_display == "") note_display = "none"
      policy_display = policy_excluded ? "YES" : "NO"
      printf "NODE=%s STATUS=%s STATE=%s POWER=%s CPUS_TOTAL=%d CPUS_ALLOC=%d CPUS_FREE=%d LOAD=%.2f MOM_AGE_SEC=%s MOM_HEALTH=%s POLICY_EXCLUDED=%s MOM_NOTE=%s JOBS=%s GPUS_TOTAL=%s GPUS_ALLOC=%s\n", \
        node, status, state, power, total, assigned, free, load, mom_display, mom_health, policy_display, note_display, compact_jobs, gpu_total, gpu_assigned
      if (free > 0) {
        chunk = node ":ppn=" free
        exact_spec = exact_spec == "" ? chunk : exact_spec "+" chunk
      }
    }
    END {
      printf "SUMMARY FULLY_IDLE_NODES=%d PARTIAL_NODES=%d BUSY_NODES=%d UNAVAILABLE_NODES=%d SUSPECT_LOAD_NODES=%d SUSPECT_MOM_NODES=%d FREE_CPUS=%d\n", \
        full_nodes, partial_nodes, busy_nodes, unavailable_nodes, suspect_nodes, suspect_mom_nodes, available_cpus
      if (exact_spec != "") print "PBS_EXACT_AVAILABLE_SPEC=" exact_spec
      print "NOTE=PBS state=free may mean partially free; SUSPECT_MOM and UNAVAILABLE nodes are excluded from CPUS_FREE; recheck MOM health immediately before qsub."
    }
  ' || true
elif command -v bhosts >/dev/null 2>&1; then
  echo "SCHEDULER=LSF"
  echo "-- host summary --"
  bhosts -w 2>&1 || true
else
  echo "SCHEDULER=not detected"
  echo "No Slurm, PBS/Torque, or LSF command was found."
  echo "-- current load (login node only; not a scheduler allocation) --"
  uptime 2>&1 || true
fi
REMOTE_CHECK

for host in "${hosts[@]}"; do
  printf '\n===== %s =====\n' "$host"
  if ! ssh -o BatchMode=yes -o ConnectTimeout="$CONNECT_TIMEOUT" \
      -o StrictHostKeyChecking=accept-new "$host" \
      "env LC_ALL=C LANG=C MOM_MAX_AGE='$MOM_MAX_AGE' UNTRACKED_LOAD_THRESHOLD='$UNTRACKED_LOAD_THRESHOLD' EXCLUDED_NODES='$EXCLUDED_NODES' bash --noprofile --norc -s" <<<"$remote_check"; then
    echo "STATUS=UNREACHABLE_OR_AUTH_FAILED" >&2
  fi
done
