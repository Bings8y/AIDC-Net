# AIDC-Net – Commands

## 1. Python (Terminal, from the project folder)

```bash
# go to the project and activate the Python environment
cd ~/CNIA3
source .venv/bin/activate

# first time on a new computer only
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# ECMP vs AIDC-Net placement for one training step + write router configs
python3 pt_orchestrator.py combined

# one mode at a time
python3 pt_orchestrator.py ecmp
python3 pt_orchestrator.py adaptive

# other orchestrator modes
python3 pt_orchestrator.py plan        # regenerate the topology plan (aidc_plan.json)
python3 pt_orchestrator.py clear       # configs that remove all pins (plain OSPF ECMP)
python3 pt_orchestrator.py combined --seed 3   # a different training step

# timing simulation (20 training steps)
python3 simulate.py

# results table + graphs (graphs saved in results_sim/figures/)
python3 analyze.py results_sim

# keep old results: write to a new folder instead
python3 simulate.py --out results_new
python3 analyze.py results_new

# simulator options
python3 simulate.py --mode ecmp          # ecmp | adaptive | both
python3 simulate.py --iterations 50 --mbytes 16 --seed 7

# leave the Python environment
deactivate
```

## 2. Packet Tracer

Open `packet_tracer/AIDC-Net.pkt` in Packet Tracer 9 (Realtime mode).

### On a server (click server → Desktop → Command Prompt)

```
ipconfig                 show the server's IP and gateway
ping 10.0.2.1            h11 → h21 reachability
tracert 10.0.1.2         run on h31: hop 2 = 172.16.2.9 (spine2) controller ON
                                     hop 2 = 172.16.1.9 (spine1) controller OFF
tracert 10.0.1.1         run on h32: hop 2 = 172.16.1.9 (spine1)
```

### On a router (click leaf/spine → CLI tab)

```
enable
show ip ospf neighbor              both spines FULL
show ip route                      O = OSPF routes, S = AIDC-Net /32 routes
show ip route ospf                 only the OSPF (ECMP) routes
show ip route static               only the /32 pinned routes
show ip route | include /32|10.99  quick view of the pinned routes
show ip interface brief            interface IPs and up/down state
show running-config                full configuration
```

### Demo steps

1. Controller ON (file opens like this) → on h31: `tracert 10.0.1.2` → spine2 (AIDC-Net)
2. Click controller → Physical → power OFF → on h31: `tracert 10.0.1.2` → spine1;
   on h32: `tracert 10.0.1.1` → spine1 (collision = ECMP)
3. Power controller ON → wait ~1 minute → h31 back on spine2

## 3. Server IP addresses

| Server | IP | Gateway |
|---|---|---|
| h11 | 10.0.1.1 | 10.0.1.254 |
| h12 | 10.0.1.2 | 10.0.1.254 |
| h21 | 10.0.2.1 | 10.0.2.254 |
| h22 | 10.0.2.2 | 10.0.2.254 |
| h31 | 10.0.3.1 | 10.0.3.254 |
| h32 | 10.0.3.2 | 10.0.3.254 |

Ring (all-reduce): h11 → h21 → h31 → h12 → h22 → h32 → h11
