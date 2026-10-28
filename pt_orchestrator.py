#!/usr/bin/env python3
"""
AIDC-Net orchestrator for the Cisco Packet Tracer version of the fabric.

Packet Tracer has no OpenFlow, so the controller cannot install 5-tuple rules.
In a leaf-spine fabric a flow's path is fully decided by the spine it crosses,
and every ring hop of the all-reduce goes to a different destination host, so
a /32 static route on the source leaf (AD 1, beats OSPF's 110) pins exactly one
ring flow to one spine. That is the PT equivalent of the controller's rule.

    python3 pt_orchestrator.py plan                 # writes packet_tracer/aidc_plan.json
    python3 pt_orchestrator.py combined --seed 7     # both placements, switched by the controller
    python3 pt_orchestrator.py ecmp     --seed 7     # hash placement (the baseline)
    python3 pt_orchestrator.py adaptive --seed 7     # AIDC-Net placement + rebalance
    python3 pt_orchestrator.py clear                 # back to plain OSPF ECMP

Every mode prints the placement and writes one IOS snippet per device to
packet_tracer/configs/<mode>_<device>.txt, to paste into that device's CLI
(enable -> configure terminal -> paste).

combined is what AIDC-Net.pkt runs. The controller router in the NOC has a
serial link to each spine. Each leaf gets two routes per ring flow:
  * the controller's choice, with next hop 10.99.s.1 (spine s's end of its
    controller link). Recursive lookup resolves that via spine s, so the flow
    crosses spine s. The route exists only while the controller link is up.
  * the ECMP hash choice as a floating route (AD 5), used when the controller
    is off, exactly what a leaf hashing on its own would do.
Power the controller off in Packet Tracer and the fabric falls back to ECMP.

The placement logic is the unchanged aidc_policy code from the SDN controller.
"""

import argparse
import json
import os
import random
from collections import Counter

import aidc_config as cfg
from aidc_policy import AdaptivePolicy, FlowInfo, ecmp_spine

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "packet_tracer")
# Device positions on the isometric data-centre background (make_background.py).
LAYOUT = os.path.join(OUT, "visual_layout.json")
IPERF_PORT = 5201
FALLBACK_AD = 5         # ECMP-hash routes sit behind the controller's (AD 1)
CTRL_OSPF_COST = 1000   # controller's side of its links, so 10.99.s.0/30 is reached via spine s


# ------------------------------------------------------------ PT addressing
def p2p_ip(s, l, end):
    """leaf l <-> spine s link is 172.16.s.(4(l-1))/30; end 1 = spine, 2 = leaf."""
    return f"172.16.{s}.{(l - 1) * 4 + end}"


def gateway_ip(l):
    return f"10.0.{l}.254"


def spine_port(l):
    return f"GigabitEthernet0/{l - 1}"


def leaf_uplink(s):
    return f"GigabitEthernet0/{s}"


def ctrl_ip(s, end):
    """controller <-> spine s link is 10.99.s.0/30; end 1 = spine, 2 = controller."""
    return f"10.99.{s}.{end}"


def ctrl_port(s):
    return f"Serial0/0/{s - 1}"


# --------------------------------------------------------------- PT plan
def build_plan():
    devices, links, ospf = [], [], []
    pos = {}
    if os.path.exists(LAYOUT):
        with open(LAYOUT) as fh:
            pos = json.load(fh)["devices"]

    def dev(name, model, cat, role, x, y, ifs=None, gw=""):
        x, y = pos.get(name, (x, y))
        devices.append(dict(name=name, model=model, category=cat, role=role, x=x, y=y,
                            interfaces=ifs or {}, gateway=gw, interfaces_v6={},
                            gateway_v6="", vlan=0, wireless=False))

    fabric = dict(network="172.16.0.0", wildcard="0.0.255.255", area=0)
    for s in range(1, cfg.NUM_SPINES + 1):
        dev(f"spine{s}", "2911", "router", "core_router", 250 + 300 * (s - 1), 80,
            {spine_port(l): p2p_ip(s, l, 1) + "/30" for l in range(1, cfg.NUM_LEAVES + 1)})
        ospf.append(dict(router=f"spine{s}", process_id=1, router_id=f"10.255.0.{s}",
                         networks=[fabric]))

    for l in range(1, cfg.NUM_LEAVES + 1):
        x = 100 + 300 * (l - 1)
        ifs = {"GigabitEthernet0/0": gateway_ip(l) + "/24"}
        for s in range(1, cfg.NUM_SPINES + 1):
            ifs[leaf_uplink(s)] = p2p_ip(s, l, 2) + "/30"
            links.append(dict(device_a=f"spine{s}", port_a=spine_port(l),
                              device_b=f"leaf{l}", port_b=leaf_uplink(s), cable="cross"))
        dev(f"leaf{l}", "2911", "router", "edge_router", x, 230, ifs)
        ospf.append(dict(router=f"leaf{l}", process_id=1, router_id=f"10.255.1.{l}",
                         networks=[fabric, dict(network=f"10.0.{l}.0",
                                                wildcard="0.0.0.255", area=0)]))
        dev(f"tor{l}", "2960-24TT", "switch", "access_switch", x, 360)
        links.append(dict(device_a=f"leaf{l}", port_a="GigabitEthernet0/0",
                          device_b=f"tor{l}", port_b="GigabitEthernet0/1", cable="straight"))
        for k in range(1, cfg.HOSTS_PER_LEAF + 1):
            h = cfg.host_name(l, k)
            dev(h, "Server-PT", "server", "end_host", x - 60 + 120 * (k - 1), 490,
                {"FastEthernet0": cfg.host_ip(l, k) + "/24"}, gateway_ip(l))
            links.append(dict(device_a=f"tor{l}", port_a=f"FastEthernet0/{k}",
                              device_b=h, port_b="FastEthernet0", cable="straight"))

    # AIDC-Net controller in the NOC, one serial link to each spine (HWIC-2T).
    dev("controller", "2911", "router", "core_router", 300, 470,
        {ctrl_port(s): ctrl_ip(s, 2) + "/30" for s in range(1, cfg.NUM_SPINES + 1)})
    modules = [dict(device="controller", slot="0/0", module="HWIC-2T")]
    ctrl_nets = []
    for s in range(1, cfg.NUM_SPINES + 1):
        modules.append(dict(device=f"spine{s}", slot="0/0", module="HWIC-2T"))
        devices[s - 1]["interfaces"]["Serial0/0/0"] = ctrl_ip(s, 1) + "/30"
        links.append(dict(device_a=f"spine{s}", port_a="Serial0/0/0",
                          device_b="controller", port_b=ctrl_port(s), cable="serial"))
        net = dict(network=f"10.99.{s}.0", wildcard="0.0.0.3", area=0)
        ospf[s - 1]["networks"].append(net)
        ctrl_nets.append(net)
    ospf.append(dict(router="controller", process_id=1, router_id="10.255.9.9", networks=ctrl_nets))

    validations = [dict(check_type="ping", from_device=cfg.host_name(*a),
                        to_target=cfg.host_name(*b), expected="Reply")
                   for a, b in ring_hops()]
    return dict(name="aidc_leaf_spine", devices=devices, modules=modules, links=links,
                dhcp_pools=[], static_routes=[], ospf_configs=ospf, rip_configs=[],
                eigrp_configs=[], vlans=[], access_ports=[], trunks=[],
                subinterfaces=[], validations=validations, dual_stack=False,
                errors=[], warnings=[])


# ------------------------------------------------------------ placement
def ring_hops():
    ring = cfg.allreduce_ring()
    return [(ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring))]


def ring_flows(seed):
    """One all-reduce step: every ring hop is an iperf3 transfer from an ephemeral port."""
    rng = random.Random(seed)
    flows = []
    for (sl, sk), (dl, dk) in ring_hops():
        key = (cfg.host_ip(sl, sk), cfg.host_ip(dl, dk), 6,
               rng.randint(32768, 60999), IPERF_PORT)
        f = FlowInfo(key, sl, dl, spine=None)
        f.src, f.dst = (sl, sk), (dl, dk)
        flows.append(f)
    return flows


def place(mode, seed):
    flows = ring_flows(seed)
    if mode == "ecmp":
        for f in flows:
            f.spine = ecmp_spine(f.fid)
        return flows, []

    # All six transfers start together, before telemetry has measured any of
    # them, so place() spreads them using its estimate for just-placed flows.
    policy = AdaptivePolicy(seed=seed)
    for f in flows:
        f.spine = policy.place(f.src_leaf, f.dst_leaf, 0.0, {})
    # One poll later every ring hop shows up as an elephant.
    for f in flows:
        f.rate_bps = cfg.LINK_BW_BPS
    moves = policy.rebalance(flows, now=cfg.POLL_INTERVAL)
    return flows, moves


def link_load(flows):
    load = Counter({l: 0 for l in cfg.fabric_links()})
    for f in flows:
        load.update(cfg.path_links(f.src_leaf, f.dst_leaf, f.spine))
    return load


# ------------------------------------------------------------ IOS output
def leaf_config(l, flows, mode, fallback=None):
    """fallback: the ECMP placement, installed as floating routes (combined mode)."""
    lines = [f"! AIDC-Net {mode} placement for leaf{l}", "!"]
    # Remove whatever the previous run pinned, so modes can be switched freely.
    for dl, dk, _ in cfg.all_hosts():
        if dl == l:
            continue
        for s in range(1, cfg.NUM_SPINES + 1):
            dst = cfg.host_ip(dl, dk)
            lines.append(f"no ip route {dst} 255.255.255.255 {p2p_ip(s, l, 1)}")
            lines.append(f"no ip route {dst} 255.255.255.255 {p2p_ip(s, l, 1)} {FALLBACK_AD}")
            lines.append(f"no ip route {dst} 255.255.255.255 {ctrl_ip(s, 1)}")
    for f in flows:
        if f.src_leaf == l:
            hop = ctrl_ip(f.spine, 1) if fallback else p2p_ip(f.spine, l, 1)
            lines.append(f"ip route {cfg.host_ip(*f.dst)} 255.255.255.255 {hop}")
    for f in fallback or []:
        if f.src_leaf == l:
            lines.append(f"ip route {cfg.host_ip(*f.dst)} 255.255.255.255 "
                         f"{p2p_ip(f.spine, l, 1)} {FALLBACK_AD}")
    lines.append("end")
    return "\n".join(lines) + "\n"


def controller_configs():
    """Serial-link settings for the controller and spines (combined mode)."""
    ctrl = ["! AIDC-Net controller links", "!"]
    out = {}
    for s in range(1, cfg.NUM_SPINES + 1):
        ctrl += [f"interface {ctrl_port(s)}", f" ip ospf cost {CTRL_OSPF_COST}",
                 " clock rate 64000", " no shutdown", "exit"]
        out[f"spine{s}"] = "\n".join([f"! spine{s} link to the AIDC-Net controller", "!",
                                     "interface Serial0/0/0", " clock rate 64000",
                                     " no shutdown", "end"]) + "\n"
    out["controller"] = "\n".join(ctrl + ["end"]) + "\n"
    return out


def write_configs(mode, flows, fallback=None):
    d = os.path.join(OUT, "configs")
    os.makedirs(d, exist_ok=True)
    for l in range(1, cfg.NUM_LEAVES + 1):
        with open(os.path.join(d, f"{mode}_leaf{l}.txt"), "w") as fh:
            fh.write(leaf_config(l, flows, mode, fallback))
    if fallback:
        for name, text in controller_configs().items():
            with open(os.path.join(d, f"{mode}_{name}.txt"), "w") as fh:
                fh.write(text)
    return d


def report(mode, flows, moves):
    print(f"\n{mode.upper()} placement")
    print(f"{'flow':<22}{'spine':>6}   path")
    for f in flows:
        src = cfg.host_name(*f.src)
        dst = cfg.host_name(*f.dst)
        path = " , ".join(cfg.path_links(f.src_leaf, f.dst_leaf, f.spine))
        print(f"{src + ' -> ' + dst:<22}{f.spine:>6}   {path}")
    for f, old, new in moves:
        print(f"rebalance: {f.fid[0]} -> {f.fid[1]} moved spine{old} -> spine{new}")

    load = link_load(flows)
    worst = max(load.values())
    idle = sum(1 for v in load.values() if v == 0)
    print(f"\nelephants per fabric link: {dict(sorted(load.items()))}")
    print(f"busiest link carries {worst} elephant(s) -> slowest ring hop gets "
          f"1/{worst} of a link, so the all-reduce step takes ~{worst}x line-rate time")
    print(f"idle fabric links: {idle}/{len(load)}")
    return worst


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["plan", "combined", "ecmp", "adaptive", "clear"])
    ap.add_argument("--seed", type=int, default=7,
                    help="picks the ephemeral source ports, i.e. one training step")
    args = ap.parse_args()

    if args.mode == "plan":
        os.makedirs(OUT, exist_ok=True)
        path = os.path.join(OUT, "aidc_plan.json")
        with open(path, "w") as fh:
            json.dump(build_plan(), fh, indent=1)
        print(f"wrote {path}")
        return

    fallback = None
    if args.mode == "clear":
        flows = []
    elif args.mode == "combined":
        fallback, _ = place("ecmp", args.seed)
        report("ecmp (controller off)", fallback, [])
        flows, moves = place("adaptive", args.seed)
        report("adaptive (controller on)", flows, moves)
    else:
        flows, moves = place(args.mode, args.seed)
        report(args.mode, flows, moves)
    print(f"\nIOS snippets written to {write_configs(args.mode, flows, fallback)}/")


if __name__ == "__main__":
    main()
