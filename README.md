# AIDC-Net: An SDN-Based Adaptive Network Architecture for Efficient Traffic Management in AI Data Centers

Computer Networks IA3 mini-project, built in **Cisco Packet Tracer**.

GPU clusters train models with **synchronised, bursty elephant flows**: every
training step ends in an *all-reduce*, where each GPU sends a large chunk to its
neighbour at the same moment, and the step can only finish when the **slowest**
transfer finishes. Standard data-center routing (ECMP) hashes each flow onto a
path without looking at load, so two elephants often collide on one link while
a parallel link sits idle. That collision stalls every GPU.

AIDC-Net replaces static ECMP with a central **orchestrator** that knows where
every flow is and places each elephant flow on the least-loaded path.

## Architecture

```
 Orchestrator   pt_orchestrator.py  ->  aidc_policy.py (ECMP hash | adaptive place + rebalance)
                pushes one /32 route per flow to the source leaf
 Controller     "controller" router in the NOC, serial link to each spine;
                while it is on, the leaves follow its placement
 ------------------------------------------------------------------------------
 Fabric                 spine1            spine2          (Cisco 2911, OSPF area 0)
                        /  |  \          /  |  \
                    leaf1   leaf2   leaf3                  (Cisco 2911)
                      |       |       |
                    tor1    tor2    tor3                   (Cisco 2960)
 ------------------------------------------------------------------------------
 Compute            h11 h12  h21 h22  h31 h32              (Server-PT = GPU servers)
                    ring all-reduce: h11->h21->h31->h12->h22->h32->h11
```

### Addressing

| What | Addresses |
|---|---|
| Rack `l` hosts | `10.0.l.0/24`, host `h{l}{k}` = `10.0.l.k`, gateway `10.0.l.254` (leaf Gi0/0) |
| leaf `l` <-> spine `s` | `172.16.s.(4(l-1))/30`, spine end `.1`, leaf end `.2` (spine Gi0/(l-1), leaf Gi0/s) |
| controller <-> spine `s` | `10.99.s.0/30`, spine end `.1` (Serial0/0/0), controller end `.2` (Serial0/0/(s-1)) |
| OSPF router IDs | spines `10.255.0.s`, leaves `10.255.1.l`, controller `10.255.9.9` |

### How routing works

* **Baseline (ECMP):** OSPF gives every leaf two equal-cost routes to every other
  rack, one through each spine (`show ip route ospf` on any leaf). A commodity
  switch picks between them by hashing the flow's 5-tuple, blind to load.
* **AIDC-Net (adaptive):** the orchestrator runs the placement algorithm in
  `aidc_policy.py`: each new flow goes on the spine whose path has the lowest
  bottleneck load, then elephants that still share a link are rebalanced
  (the Hedera idea, NSDI 2010).
* **How a decision reaches the network:** Packet Tracer has no OpenFlow, so the
  orchestrator installs a `/32` static route on the source leaf
  (administrative distance 1 beats OSPF's 110). Each ring hop goes to a
  different host, so one `/32` route pins exactly one flow to one spine,
  which is the same thing the SDN rule did.
* **One file, both modes:** every leaf holds two `/32` routes per ring flow.
  The controller's choice points at `10.99.s.1`, spine `s`'s end of its
  controller link; the leaf resolves that next hop through spine `s`
  (recursive route, AD 1). The controller's side of each link has OSPF cost
  1000, so `10.99.s.0/30` is only cheap through spine `s`. The ECMP hash
  choice is a floating route (AD 5). Power the controller off and its links
  go down, `10.99.s.0/30` leaves OSPF, the AD 1 routes cannot resolve, and
  every leaf falls back to the ECMP hash. Power it on and AIDC-Net is back.

### The Packet Tracer picture

The Logical workspace uses `packet_tracer/aidc_background.png` as its
background: an isometric data centre with a NOC (controller room), the spine
core, a power and cooling room and three GPU pods. Every device icon stands in
front of the rack it represents (spines in the spine core; leaf, ToR and two
GPU servers in each pod). `packet_tracer/make_background.py` draws the picture
and writes each device's canvas position to `packet_tracer/visual_layout.json`,
which `pt_orchestrator.py plan` reads.


## Files

| File | What it is |
|---|---|
| `packet_tracer/AIDC-Net.pkt` | The whole demo in one file: controller ON = AIDC-Net, controller OFF = ECMP hash |
| `packet_tracer/configs/` | IOS snippets per device for each mode (`combined_` is what `AIDC-Net.pkt` runs; `ecmp_`, `adaptive_`, `clear_` for pasting by hand) |
| `packet_tracer/aidc_plan.json` | Topology plan the `.pkt` was built from |
| `packet_tracer/aidc_background.png` | Isometric data-centre background for the Logical workspace |
| `packet_tracer/make_background.py` | Draws the background and writes `visual_layout.json` (device positions) |
| `pt_orchestrator.py` | The orchestrator: builds the plan, computes placements and writes the configs |
| `aidc_config.py` | Topology size, addressing, ring order |
| `aidc_policy.py` | ECMP hash and the adaptive algorithm |
| `simulate.py`, `analyze.py` | Flow-level simulator and graphs (step time, throughput, link load) |

## Running it

### 1. Open the network
Open `packet_tracer/AIDC-Net.pkt` in Packet Tracer 9.
Make sure the bottom-right toggle says **Realtime**, not Simulation.
If the data-centre background is missing (for example on another computer),
set it again with **Set Tiled Background** in the Logical workspace bar and
pick `packet_tracer/aidc_background.png`.

### 2. Show the fabric works
On any GPU server: *Desktop > Command Prompt*, e.g. on h11:
```
ping 10.0.2.1
tracert 10.0.2.1
```
On a leaf (*CLI* tab): `show ip ospf neighbor` (both spines FULL) and
`show ip route` (two equal-cost `O` routes per remote rack, plus the `S` /32
routes for the ring flows).

### 3. Show ECMP vs AIDC-Net in the same file
The box in the top-right corner of the canvas has the three steps:

1. **Controller ON** (how the file opens). On h31 run `tracert 10.0.1.2`:
   hop 2 is `172.16.2.9`, spine2. That is AIDC-Net's placement.
2. Click **controller > Physical** and press the power switch. Its link lights
   turn red. Run `tracert 10.0.1.2` on h31 again: hop 2 is now `172.16.1.9`,
   spine1. `tracert 10.0.1.1` on h32 also shows spine1, so the two elephants
   collide on `spine1->leaf1`. That is the ECMP hash.
3. Power the controller back on. After about a minute (boot + OSPF) h31's
   tracert is back on spine2.

On leaf3, `show ip route` shows the switch-over: `S 10.0.1.2/32 [1/0] via
10.99.2.1` with the controller on, `S 10.0.1.2/32 [5/0] via 172.16.1.9` with
it off.

| Controller | h31 `tracert 10.0.1.2` hop 2 | h32 `tracert 10.0.1.1` hop 2 | Busiest link |
|---|---|---|---|
| ON (AIDC-Net) | 172.16.2.9 (spine2) | 172.16.1.9 (spine1) | 1 elephant |
| OFF (ECMP hash) | 172.16.1.9 (spine1) | 172.16.1.9 (spine1) | 2 elephants, collision |

The placements come from the orchestrator:
```bash
python3 pt_orchestrator.py combined   # both placements + the configs AIDC-Net.pkt runs
python3 pt_orchestrator.py ecmp       # one mode at a time, configs to paste by hand
python3 pt_orchestrator.py adaptive
```
Output for one training step (`--seed 7`):

| Flow | ECMP spine | AIDC-Net spine |
|---|---|---|
| h11 -> h21 | 2 | 2 |
| h21 -> h31 | 1 | 1 |
| h31 -> h12 | **1** | **2** |
| h12 -> h22 | 1 | 1 |
| h22 -> h32 | 2 | 2 |
| h32 -> h11 | **1** | 1 |
| Busiest link | 2 elephants (`leaf3->spine1`, `spine1->leaf1`) | 1 elephant |
| Idle fabric links | 2 of 12 | 0 of 12 |

Under ECMP, h31->h12 and h32->h11 share `spine1->leaf1`, so each gets half a
link and the whole all-reduce step takes about twice as long. AIDC-Net moves
h31->h12 to spine2.

### 4. Graphs (optional)
Packet Tracer cannot measure throughput, so the timing comparison comes from
the flow-level simulator, which uses the same `aidc_policy.py` code:
```bash
pip3 install -r requirements.txt     # inside a venv on macOS
python3 simulate.py && python3 analyze.py results_sim
```

On **Windows** (PowerShell, Python 3.10+ from python.org):
```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
python simulate.py
python analyze.py results_sim
python pt_orchestrator.py combined
```

| Metric | ECMP (static hash) | AIDC-Net (adaptive) |
|---|---|---|
| Mean all-reduce step time (s) | 11.24 | **6.51** |
| p95 step time (s) | 12.85 | **6.58** |
| Network efficiency (%) | 57.0 | **98.3** |
| Link-load fairness (Jain) | 0.58 | **1.00** |
| Idle fabric links while busy (%) | 41.4 | **0.1** |

AIDC-Net cuts mean all-reduce time by 42% (1.73x faster training steps).
The earlier Mininet runs showed the same trend (13.19 s -> 8.86 s).

## Syllabus mapping

| Module | Where it appears |
|---|---|
| 1. Networks and the Internet: delay, throughput, topology design | Leaf-spine design; bottleneck sharing sets the step time |
| 3. Transport layer | Flows identified by the 5-tuple; colliding TCP flows split a bottleneck |
| 4. Data plane: IPv4 addressing, forwarding, SDN | /24 racks and /30 fabric links; per-flow forwarding decisions pushed by a central orchestrator |
| 5. Control plane: routing algorithms, SDN control plane | OSPF (link-state, ECMP) versus centralised load-aware placement; static routes with lower AD override OSPF |
| 6. Link layer: data-center networking | Leaf-spine Clos fabric with top-of-rack switches |

## Limitations

* Packet Tracer has no OpenFlow and no live link-rate telemetry, so the
  orchestrator plans placements offline from the known all-reduce pattern and
  pushes them as routes. An earlier measured, telemetry-driven version was
  built with Mininet + Ryu (not included in this repo).
* `/32` routes steer per destination, not per 5-tuple. That is exact here
  because every ring hop has a different destination host.
* ECMP can get lucky. When the hash spreads flows evenly, both schemes are
  equally fast. AIDC-Net's gain is in removing the bad cases.
