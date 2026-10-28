r"""
Shared configuration for AIDC-Net.

Every other module (topology, controller, simulator, analysis) imports this
file, so the addressing and port numbering below is the single source of truth.

Leaf-spine fabric
-----------------
    spine1 ... spineS          (dpid 0x1000 + s)
      |  \   /  |
    leaf1 ... leafL            (dpid 0x2000 + l)
      |          |
    h11 h12 ... hL1 hL2        (GPU servers)

Port numbering (fixed, so the controller never needs topology discovery):
    leaf l : port k           -> host h{l}{k}          (k = 1..HOSTS_PER_LEAF)
             port H + s       -> spine s uplink        (H = HOSTS_PER_LEAF)
    spine s: port l           -> leaf l
"""

NUM_SPINES = 2
NUM_LEAVES = 3
HOSTS_PER_LEAF = 2

# Link capacity in Mbit/s. Kept small so Mininet's software switches can
# saturate links on a laptop VM; the *ratios* are what matter for the results.
LINK_BW_MBPS = 10
LINK_BW_BPS = LINK_BW_MBPS * 1_000_000

# Telemetry poll interval (seconds) used by controller and simulator.
POLL_INTERVAL = 0.5

# A flow is an "elephant" once its measured rate exceeds this fraction of a link.
ELEPHANT_FRACTION = 0.1

# A flow that was just rerouted is left alone for this long (prevents flapping).
REROUTE_HOLD_S = 1.5

OF_PORT = 6653


def spine_dpid(s):
    return 0x1000 + s


def leaf_dpid(l):
    return 0x2000 + l


def dpid_role(dpid):
    """Return ('spine', s) or ('leaf', l) for a datapath id."""
    if 0x1000 < dpid < 0x2000:
        return "spine", dpid - 0x1000
    if 0x2000 < dpid < 0x3000:
        return "leaf", dpid - 0x2000
    return None, None


def host_name(l, k):
    return f"h{l}{k}"


def host_ip(l, k):
    return f"10.0.{l}.{k}"


def host_mac(l, k):
    return f"00:00:00:00:{l:02x}:{k:02x}"


def leaf_host_port(k):
    return k


def leaf_uplink_port(s):
    return HOSTS_PER_LEAF + s


def spine_down_port(l):
    return l


def all_hosts():
    """List of (leaf, index, name) for every GPU host."""
    return [(l, k, host_name(l, k))
            for l in range(1, NUM_LEAVES + 1)
            for k in range(1, HOSTS_PER_LEAF + 1)]


def locate_ip(ip):
    """Map '10.0.l.k' -> (l, k), or None if it is not one of our hosts."""
    parts = ip.split(".")
    if len(parts) != 4 or parts[0] != "10" or parts[1] != "0":
        return None
    l, k = int(parts[2]), int(parts[3])
    if 1 <= l <= NUM_LEAVES and 1 <= k <= HOSTS_PER_LEAF:
        return l, k
    return None


def up_link(l, s):
    return f"leaf{l}->spine{s}"


def down_link(s, l):
    return f"spine{s}->leaf{l}"


def fabric_links():
    """All directional leaf<->spine links, the ones the controller balances."""
    links = []
    for l in range(1, NUM_LEAVES + 1):
        for s in range(1, NUM_SPINES + 1):
            links.append(up_link(l, s))
            links.append(down_link(s, l))
    return links


def path_links(src_leaf, dst_leaf, spine):
    return [up_link(src_leaf, spine), down_link(spine, dst_leaf)]


def allreduce_ring():
    """
    Ring order for the all-reduce workload, interleaved across leaves
    (h11, h21, h31, h12, h22, h32, ...) so every ring hop crosses the fabric,
    which is how a ring of GPUs spread over racks behaves.
    """
    return [(l, k) for k in range(1, HOSTS_PER_LEAF + 1)
            for l in range(1, NUM_LEAVES + 1)]
