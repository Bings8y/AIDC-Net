#!/usr/bin/env python3
"""
Flow-level simulator of the AIDC-Net fabric, which runs anywhere (no Mininet,
no root, no Linux).

It replays the same GPU ring all-reduce workload as run_experiment.py on the
same leaf-spine topology, shares link bandwidth max-min fairly (a fluid
approximation of TCP), and routes with the *same* aidc_policy code the Ryu
controller uses, including the telemetry delay (the policy only sees link
rates averaged over the last poll interval).

    python3 simulate.py                 # both modes, writes results_sim/
    python3 analyze.py results_sim

Emulation (Mininet) is the real experiment; use this to sanity-check the
algorithm, sweep parameters, or produce graphs when you have no Linux VM.
"""

import argparse
import csv
import os
import random
from collections import defaultdict

import aidc_config as cfg
from aidc_policy import AdaptivePolicy, FlowInfo, ecmp_spine

DT = 0.005                      # simulation step (s)
CTRL_BYTES = 20_000             # iperf3-style control connection before each transfer
CTRL_LEAD_S = 0.005             # ... opened this long before the data connection


class SimFlow(FlowInfo):
    def __init__(self, fid, src, dst, size_bytes, start, spine, elephant):
        super().__init__(fid, src[0], dst[0], spine)
        self.src, self.dst = src, dst
        self.remaining = size_bytes * 8.0   # bits
        self.start = start
        self.end = None
        self.sent_bits = 0.0                # since last telemetry poll
        self.elephant = elephant
        self.cur_rate = 0.0

    def links(self):
        (sl, sk), (dl, dk) = self.src, self.dst
        return [f"h{sl}{sk}->leaf{sl}",
                *cfg.path_links(sl, dl, self.spine),
                f"leaf{dl}->h{dl}{dk}"]


def max_min_rates(flows, capacity):
    """Progressive filling: every flow gets its max-min fair share."""
    rates = {}
    remaining_cap = defaultdict(lambda: capacity)
    active = {f: f.links() for f in flows}
    while active:
        count = defaultdict(int)
        for ls in active.values():
            for l in ls:
                count[l] += 1
        share = min(remaining_cap[l] / n for l, n in count.items())
        bottlenecks = {l for l, n in count.items()
                       if remaining_cap[l] / n <= share + 1e-9}
        for f, ls in list(active.items()):
            if any(l in bottlenecks for l in ls):
                rates[f] = share
                for l in ls:
                    remaining_cap[l] -= share
                del active[f]
    return rates


def simulate(mode, iterations, mbytes, compute_s, seed):
    rng = random.Random(seed)
    policy = AdaptivePolicy(seed=seed) if mode == "adaptive" else None
    cap = cfg.LINK_BW_BPS
    ring = cfg.allreduce_ring()

    t = 0.0
    next_poll = cfg.POLL_INTERVAL
    link_bits = defaultdict(float)      # bits sent per link since last poll
    link_bps = {}                       # what telemetry reports to the policy
    active, pending = [], []            # pending = not yet started
    flow_rows, iter_rows, link_rows, event_rows = [], [], [], []
    fid = 0
    rates, dirty = {}, True

    def new_flow(src, dst, size, start, elephant):
        nonlocal fid
        fid += 1
        # Random source port -> random ECMP hash, as with real TCP.
        key = (cfg.host_ip(*src), cfg.host_ip(*dst), 6, rng.randint(32768, 60999), 5201)
        return SimFlow(key, src, dst, size, start, None, elephant)

    for it in range(1, iterations + 1):
        it_start = t
        data_flows = []
        for i, src in enumerate(ring):
            dst = ring[(i + 1) % len(ring)]
            jitter = rng.uniform(0, 0.02)
            pending.append(new_flow(src, dst, CTRL_BYTES, t + jitter, False))
            f = new_flow(src, dst, mbytes * 1_000_000, t + jitter + CTRL_LEAD_S, True)
            pending.append(f)
            data_flows.append(f)

        while any(f.end is None for f in data_flows):
            # start flows whose time has come (routing decision = first packet)
            for f in [f for f in pending if f.start <= t]:
                pending.remove(f)
                if mode == "ecmp":
                    f.spine = ecmp_spine(f.fid)
                else:
                    f.spine = policy.place(f.src_leaf, f.dst_leaf, t, link_bps)
                event_rows.append([f"{t:.3f}", "place", fid_str(f), f.src_leaf,
                                   f.dst_leaf, "", f.spine, ""])
                active.append(f)
                dirty = True

            if dirty:
                rates = max_min_rates(active, cap)
                dirty = False

            for f in active:
                bits = min(rates[f] * DT, f.remaining)
                f.remaining -= bits
                f.sent_bits += bits
                for l in f.links():
                    link_bits[l] += bits
            t += DT
            for f in [f for f in active if f.remaining <= 1e-6]:
                f.end = t
                active.remove(f)
                dirty = True
                if f.elephant:
                    dur = f.end - f.start
                    flow_rows.append([it, f"h{f.src[0]}{f.src[1]}", f"h{f.dst[0]}{f.dst[1]}", mbytes,
                                      f"{f.start:.3f}", f"{f.end:.3f}", f"{dur:.3f}",
                                      f"{mbytes * 8 / dur:.3f}"])

            if t >= next_poll:          # telemetry poll + control loop
                for l in cfg.fabric_links():
                    bps = link_bits[l] / cfg.POLL_INTERVAL
                    link_bps[l] = bps
                    link_rows.append([f"{t:.3f}", l, f"{bps / 1e6:.3f}", f"{bps / cap:.4f}"])
                link_bits.clear()
                for f in active:
                    f.rate_bps = f.sent_bits / cfg.POLL_INTERVAL
                    f.sent_bits = 0.0
                if mode == "adaptive":
                    for f, old, new in policy.rebalance(active, t):
                        event_rows.append([f"{t:.3f}", "reroute", fid_str(f), f.src_leaf,
                                           f.dst_leaf, old, new, f"{f.rate_bps / 1e6:.2f}"])
                        dirty = True
                next_poll += cfg.POLL_INTERVAL

        it_end = max(f.end for f in data_flows)
        iter_rows.append([it, f"{it_start:.3f}", f"{it_end:.3f}", f"{it_end - it_start:.3f}"])
        # compute phase: idle fabric (keep emitting telemetry)
        t_resume = t + compute_s
        while t < t_resume:
            t += DT
            if t >= next_poll:
                for l in cfg.fabric_links():
                    link_rows.append([f"{t:.3f}", l, "0.000", "0.0000"])
                link_bps = {}
                next_poll += cfg.POLL_INTERVAL

    return flow_rows, iter_rows, link_rows, event_rows


def fid_str(f):
    src, dst, proto, sport, dport = f.fid
    return f"{src}:{sport}->{dst}:{dport}/{proto}"


def write_csv(path, header, rows):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["ecmp", "adaptive", "both"], default="both")
    ap.add_argument("--iterations", type=int, default=20)
    ap.add_argument("--mbytes", type=int, default=8)
    ap.add_argument("--compute", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="results_sim")
    args = ap.parse_args()

    for mode in (["ecmp", "adaptive"] if args.mode == "both" else [args.mode]):
        flows, iters, links, events = simulate(mode, args.iterations, args.mbytes,
                                               args.compute, args.seed)
        d = os.path.join(args.out, mode)
        os.makedirs(d, exist_ok=True)
        write_csv(os.path.join(d, "flows.csv"),
                  ["iteration", "src", "dst", "mbytes", "start", "end", "duration_s", "mbps"], flows)
        write_csv(os.path.join(d, "iterations.csv"),
                  ["iteration", "start", "end", "duration_s"], iters)
        write_csv(os.path.join(d, "links.csv"), ["time", "link", "mbps", "util"], links)
        write_csv(os.path.join(d, "events.csv"),
                  ["time", "event", "flow", "src_leaf", "dst_leaf", "from_spine",
                   "to_spine", "mbps"], events)
        durs = [float(r[3]) for r in iters]
        print(f"{mode:9s} mean all-reduce time {sum(durs) / len(durs):6.2f}s  "
              f"worst {max(durs):6.2f}s  reroutes "
              f"{sum(1 for e in events if e[1] == 'reroute')}")
    print(f"results in {args.out}/  ->  python3 analyze.py {args.out}")


if __name__ == "__main__":
    main()
