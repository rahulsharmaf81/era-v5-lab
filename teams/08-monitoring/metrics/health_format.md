# Node Health-Check JSON Format (v0)

**Author:** G8 Monitoring & Records (for Team 07 Infrastructure)  
**Target File:** `teams/08-monitoring/metrics/health_format.md`  
**Purpose:** Defines the standardized heartbeat and health-check JSON payload reported by cluster worker nodes (G7 Infrastructure) and ingested by G8 Monitoring for real-time alerting, node evictions, and run telemetry.

---

## 1. Overview & Transport Semantics

Each compute node runs a lightweight local health-check daemon (or cron runner) managed by G7.
* **Cadence:** Heartbeat emitted once every **10 seconds** per node.
* **Format:** Single-line JSON (or JSONL over local unix socket / HTTP POST / file drop).
* **Delivery:** Emitted to the local monitoring agent or ClickHouse collector.
* **Clock Sync:** Timestamps MUST be in UTC ISO-8601 format (`YYYY-MM-DDTHH:MM:SS.ffffffZ`).

---

## 2. Node State Machine

The top-level `status` field represents the node's operational readiness. It follows a strict 4-state lifecycle:

```
  +---------+   degraded condition   +------------+
  | HEALTHY | ---------------------> |  DEGRADED  |
  +---------+                        +------------+
       |                                   |
       | unrecoverable / threshold breach  |
       v                                   v
  +-----------------------------------------------+
  |                   UNHEALTHY                   |
  +-----------------------------------------------+
                         |
                         | missed >= 3 consecutive heartbeats (30s)
                         v
  +-----------------------------------------------+
  |                     DEAD                      |
  +-----------------------------------------------+
```

### State Definitions:
1. **`HEALTHY`**: All subsystem checks pass. Node is actively scheduling or training.
2. **`DEGRADED`**: Non-fatal anomaly detected (e.g., thermal throttle active, disk space low but above emergency cutoff, one non-critical GPU experiencing ECC correctable errors). Node can complete the current step/window but requires attention.
3. **`UNHEALTHY`**: Fatal hardware/software failure detected (e.g., NVLink failure, uncorrectable ECC error, CUDA Xid fatal event, disk read-only). Job scheduler should pause or cordon node and trigger checkpoint save.
4. **`DEAD`**: Node has stopped emitting heartbeats for $\ge 30\text{ s}$ ($3\times$ heartbeat interval). Inferred by G8 collector when heartbeats stall.

---

## 3. Schema Specification

### Top-Level JSON Fields

| Field | Type | Required | Description |
|---|---|:---:|---|
| `version` | `integer` | Yes | Schema version (currently `0` or `1`). |
| `timestamp` | `string` | Yes | ISO-8601 UTC timestamp of check generation. |
| `run_id` | `string` | Yes | Shared training or benchmark run identifier. |
| `node_id` | `string` | Yes | Unique host identifier (e.g., hostname or instance ID). |
| `role` | `string` | Yes | Role in cluster: `"worker"` \| `"coordinator"` \| `"storage"`. |
| `status` | `string` | Yes | Node state: `"HEALTHY"` \| `"DEGRADED"` \| `"UNHEALTHY"`. |
| `reasons` | `array[string]` | Yes | List of failure / warning codes (empty `[]` if `HEALTHY`). |
| `uptime_seconds` | `number` | Yes | Host system uptime in seconds. |
| `system` | `object` | Yes | Host CPU, memory, and disk telemetry. |
| `network` | `object` | Yes | Host networking and interconnect statistics. |
| `gpus` | `array[object]` | No | Per-GPU telemetry blocks (absent on CPU-only nodes). |

---

### Subsystem Objects

#### `system` Object
* `cpu_util_pct` (`float`): Host CPU utilization percentage `[0.0 - 100.0]`.
* `mem_total_gib` (`float`): Total system RAM in GiB.
* `mem_used_gib` (`float`): Used system RAM in GiB.
* `disk_avail_gib` (`float`): Free disk space on training/scratch storage mount.
* `disk_read_only` (`boolean`): `true` if root or scratch filesystem has flipped read-only.

#### `network` Object
* `interface` (`string`): Primary training interface (e.g., `"eth0"`, `"ib0"`).
* `carrier_up` (`boolean`): Physical link carrier status.
* `rx_drop_count` (`integer`): Dropped receive packets since daemon start.
* `tx_drop_count` (`integer`): Dropped transmit packets since daemon start.

#### `gpus[]` Array (Per-Device Telemetry)
* `index` (`integer`): Device logical ordinal (`0..N-1`).
* `uuid` (`string`): GPU UUID (preferred over PCI bus ID in cloud/virtualized setups like Modal).
* `pci_bus_id` (`string`): PCI bus ID (`"0000:00:1E.0"` or `"unknown"`).
* `healthy` (`boolean`): Individual GPU health flag.
* `temperature_gpu_c` (`integer`): Core temperature in Celsius.
* `temperature_mem_c` (`integer` | `null`): HBM/VRAM temperature in Celsius.
* `power_draw_w` (`float`): Current power draw in Watts.
* `power_limit_w` (`float`): Configured TDP power limit in Watts.
* `util_gpu_pct` (`float`): GPU compute utilization `[0.0 - 100.0]`.
* `mem_used_mib` (`integer`): VRAM used in MiB.
* `mem_total_mib` (`integer`): Total VRAM capacity in MiB.
* `ecc_errors` (`object`):
  * `correctable` (`integer`): Single-bit ECC error count.
  * `uncorrectable` (`integer`): Multi-bit uncorrectable ECC error count.
* `throttle_reasons` (`array[string]`): Active throttling flags (e.g., `"none"`, `"hw_slowdown"`, `"thermal"`, `"power_cap"`).
* `xid_errors` (`array[integer]`): Any NVIDIA driver Xid errors recorded in kernel log during window.

---

## 4. Standard Reason Codes

When `status` is `"DEGRADED"` or `"UNHEALTHY"`, `reasons` MUST contain one or more of the following standard tokens:

| Code | Severity | Description |
|---|---|---|
| `ECC_UNCORRECTABLE` | `UNHEALTHY` | Uncorrectable double-bit ECC error in VRAM. |
| `XID_FATAL` | `UNHEALTHY` | Fatal driver Xid error (e.g., Xid 31, 43, 45, 62, 79). |
| `NVLINK_DOWN` | `UNHEALTHY` | NVLink or InfiniBand interconnect lane failure. |
| `DISK_READ_ONLY` | `UNHEALTHY` | Storage volume mounted read-only due to filesystem errors. |
| `DISK_SPACE_CRITICAL` | `UNHEALTHY` | Scratch disk space $< 10\text{ GiB}$. |
| `THERMAL_THROTTLE` | `DEGRADED` | GPU clock throttled due to junction temperature. |
| `POWER_BRAKE` | `DEGRADED` | External power cap / supply throttling engaged. |
| `ECC_CORRECTABLE_HIGH` | `DEGRADED` | Correctable ECC error rate $> 50/\text{min}$. |
| `DISK_SPACE_LOW` | `DEGRADED` | Scratch disk space $< 50\text{ GiB}$. |

---

## 5. Example JSON Messages

### Example 1: `HEALTHY` Node (8x GPU Worker)
```json
{
  "version": 0,
  "timestamp": "2026-10-30T17:40:00.124590Z",
  "run_id": "era-v5-run-001",
  "node_id": "worker-us-central-04",
  "role": "worker",
  "status": "HEALTHY",
  "reasons": [],
  "uptime_seconds": 184200.5,
  "system": {
    "cpu_util_pct": 24.2,
    "mem_total_gib": 512.0,
    "mem_used_gib": 182.4,
    "disk_avail_gib": 850.2,
    "disk_read_only": false
  },
  "network": {
    "interface": "ib0",
    "carrier_up": true,
    "rx_drop_count": 0,
    "tx_drop_count": 0
  },
  "gpus": [
    {
      "index": 0,
      "uuid": "GPU-7b89d412-401f-2e33-9abc-f42145610001",
      "pci_bus_id": "0000:1B:00.0",
      "healthy": true,
      "temperature_gpu_c": 62,
      "temperature_mem_c": 71,
      "power_draw_w": 385.4,
      "power_limit_w": 700.0,
      "util_gpu_pct": 98.5,
      "mem_used_mib": 76540,
      "mem_total_mib": 81920,
      "ecc_errors": {
        "correctable": 0,
        "uncorrectable": 0
      },
      "throttle_reasons": ["none"],
      "xid_errors": []
    }
  ]
}
```

### Example 2: `DEGRADED` Node (Thermal Throttle on GPU 0)
```json
{
  "version": 0,
  "timestamp": "2026-10-30T17:40:10.134210Z",
  "run_id": "era-v5-run-001",
  "node_id": "worker-us-central-04",
  "role": "worker",
  "status": "DEGRADED",
  "reasons": ["THERMAL_THROTTLE"],
  "uptime_seconds": 184210.5,
  "system": {
    "cpu_util_pct": 26.1,
    "mem_total_gib": 512.0,
    "mem_used_gib": 182.5,
    "disk_avail_gib": 850.1,
    "disk_read_only": false
  },
  "network": {
    "interface": "ib0",
    "carrier_up": true,
    "rx_drop_count": 0,
    "tx_drop_count": 0
  },
  "gpus": [
    {
      "index": 0,
      "uuid": "GPU-7b89d412-401f-2e33-9abc-f42145610001",
      "pci_bus_id": "0000:1B:00.0",
      "healthy": true,
      "temperature_gpu_c": 89,
      "temperature_mem_c": 98,
      "power_draw_w": 520.0,
      "power_limit_w": 700.0,
      "util_gpu_pct": 74.0,
      "mem_used_mib": 76540,
      "mem_total_mib": 81920,
      "ecc_errors": {
        "correctable": 12,
        "uncorrectable": 0
      },
      "throttle_reasons": ["thermal", "hw_slowdown"],
      "xid_errors": []
    }
  ]
}
```

### Example 3: `UNHEALTHY` Node (Uncorrectable ECC Memory Failure)
```json
{
  "version": 0,
  "timestamp": "2026-10-30T17:40:20.145890Z",
  "run_id": "era-v5-run-001",
  "node_id": "worker-us-central-04",
  "role": "worker",
  "status": "UNHEALTHY",
  "reasons": ["ECC_UNCORRECTABLE", "XID_FATAL"],
  "uptime_seconds": 184220.5,
  "system": {
    "cpu_util_pct": 12.0,
    "mem_total_gib": 512.0,
    "mem_used_gib": 182.5,
    "disk_avail_gib": 850.0,
    "disk_read_only": false
  },
  "network": {
    "interface": "ib0",
    "carrier_up": true,
    "rx_drop_count": 0,
    "tx_drop_count": 0
  },
  "gpus": [
    {
      "index": 0,
      "uuid": "GPU-7b89d412-401f-2e33-9abc-f42145610001",
      "pci_bus_id": "0000:1B:00.0",
      "healthy": false,
      "temperature_gpu_c": 64,
      "temperature_mem_c": 72,
      "power_draw_w": 110.0,
      "power_limit_w": 700.0,
      "util_gpu_pct": 0.0,
      "mem_used_mib": 76540,
      "mem_total_mib": 81920,
      "ecc_errors": {
        "correctable": 12,
        "uncorrectable": 1
      },
      "throttle_reasons": ["none"],
      "xid_errors": [62]
    }
  ]
}
```

---

## 6. G7 Implementation Notes

1. **Failure Tolerance:** If query to `nvidia-smi` or NVML fails (e.g., driver hangs), the node daemon MUST catch the exception, set `status = "UNHEALTHY"`, add `"NVML_QUERY_FAILURE"` to `reasons`, and emit the heartbeat.
2. **Minimal Overhead:** Heartbeat queries should take $< 50\text{ ms}$ of wall-clock time and invoke NVML directly (or `nvidia-smi` in background thread) without triggering CUDA driver synchronization on active training streams.
3. **Modal / Cloud Compatibility:** Notice that virtualized GPU environments sometimes report PCI bus ID as `[Unknown Error]`. For identification, use GPU `uuid` as primary identifier.
