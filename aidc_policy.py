"""
Path-selection policies for AIDC-Net.

Pure Python with no SDN dependencies, so the exact same code runs inside the
Ryu controller and inside the flow-level simulator (simulate.py).

In a leaf-spine fabric, a cross-rack flow's path is fully determined by the
spine it crosses, so "routing" a flow means choosing a spine.

  * ECMP          - static hash of the 5-tuple, like a commodity switch.
  * AdaptivePolicy - AIDC-Net's congestion-aware scheme:
      1. place()     : a new flow goes on the spine whose path has the lowest
                       bottleneck load (live telemetry + flows placed so recently
                       that telemetry can't see them yet).
      2. rebalance() : every poll, elephant flows (> ELEPHANT_FRACTION of a link)
                       are spread so no fabric link carries more elephants than
                       necessary (Hedera-style), moving a flow only when it
                       strictly lowers its bottleneck.
"""

import random
import zlib
from collections import Counter

import aidc_config as cfg


def ecmp_spine(flow_key):
    """Static ECMP: hash the 5-tuple onto one of the spines."""
    h = zlib.crc32(repr(flow_key).encode())
    return h % cfg.NUM_SPINES + 1


class FlowInfo:
    """What the policy needs to know about one cross-rack flow."""

    def __init__(self, fid, src_leaf, dst_leaf, spine):
        self.fid = fid
        self.src_leaf = src_leaf
        self.dst_leaf = dst_leaf
        self.spine = spine
        self.rate_bps = 0.0
        self.last_moved = float("-inf")   # new flows may be moved right away


class AdaptivePolicy:
    def __init__(self, capacity_bps=cfg.LINK_BW_BPS,
                 elephant_bps=cfg.ELEPHANT_FRACTION * cfg.LINK_BW_BPS,
                 hold_s=cfg.REROUTE_HOLD_S,
                 recent_window_s=2 * cfg.POLL_INTERVAL,
                 new_flow_estimate_bps=None,
                 seed=None):
        self.capacity = capacity_bps
        self.elephant_bps = elephant_bps
        self.hold_s = hold_s
        self.recent_window = recent_window_s
        # Until telemetry measures a new flow, assume it could fill half a link.
        self.new_flow_est = (new_flow_estimate_bps
                             if new_flow_estimate_bps is not None
                             else capacity_bps / 2)
        self.recent = []            # (time, [links]) of just-placed flows
        self.rng = random.Random(seed)

    # ------------------------------------------------------------ placement
    def place(self, src_leaf, dst_leaf, now, link_bps, may_be_elephant=True):
        """
        Choose a spine for a new flow.
        link_bps: {link_name: measured bits/s} from the latest telemetry poll.
        may_be_elephant=False (e.g. the ACK stream of a known TCP connection)
        places the flow without reserving capacity for it.
        """
        self.recent = [(t, ls) for t, ls in self.recent
                       if now - t < self.recent_window]
        pending = Counter()
        for _, ls in self.recent:
            pending.update(ls)

        def cost(s):
            return max(link_bps.get(l, 0.0) + pending[l] * self.new_flow_est
                       for l in cfg.path_links(src_leaf, dst_leaf, s))

        costs = {s: cost(s) for s in range(1, cfg.NUM_SPINES + 1)}
        best = min(costs.values())
        # Random tie-break so simultaneous arrivals don't all pile onto spine 1.
        choice = self.rng.choice([s for s, c in costs.items()
                                  if c <= best + 1e-6])
        if may_be_elephant:
            self.recent.append((now, cfg.path_links(src_leaf, dst_leaf, choice)))
        return choice

    # ---------------------------------------------------------- rebalancing
    def rebalance(self, flows, now):
        """
        flows: iterable of FlowInfo with up-to-date rate_bps.
        Mutates the FlowInfo.spine of moved flows and returns
        [(flow, old_spine, new_spine), ...] for the caller to apply.
        """
        elephants = sorted((f for f in flows if f.rate_bps >= self.elephant_bps),
                           key=lambda f: -f.rate_bps)
        load = Counter()
        for f in elephants:
            load.update(cfg.path_links(f.src_leaf, f.dst_leaf, f.spine))

        moves = []
        for f in elephants:
            if now - f.last_moved < self.hold_s:
                continue
            cur = f.spine
            load.subtract(cfg.path_links(f.src_leaf, f.dst_leaf, cur))

            def bottleneck(s):
                return max(load[l] for l in cfg.path_links(f.src_leaf, f.dst_leaf, s))

            # Prefer the current spine on ties -> no pointless reroutes.
            best = min(range(1, cfg.NUM_SPINES + 1),
                       key=lambda s: (bottleneck(s), s != cur, s))
            if bottleneck(best) < bottleneck(cur):
                f.spine = best
                f.last_moved = now
                moves.append((f, cur, best))
            load.update(cfg.path_links(f.src_leaf, f.dst_leaf, f.spine))
        return moves
