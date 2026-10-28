#!/usr/bin/env python3
"""
Turns experiment results into report-ready graphs and a summary table.

    python3 analyze.py               # reads results/      (Mininet runs)
    python3 analyze.py results_sim   # reads results_sim/  (simulator runs)

Writes <dir>/figures/*.png and <dir>/summary.md
"""

import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.ticker
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import aidc_config as cfg

MODES = [("ecmp", "ECMP (static hash)"), ("adaptive", "AIDC-Net (adaptive)")]
COLORS = {"ecmp": "#2a78d6", "adaptive": "#eb6834"}
SPINE_COLORS = ["#1baf7a", "#4a3aa7", "#e87ba4", "#008300"]
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"

plt.rcParams.update({
    "figure.dpi": 150, "savefig.bbox": "tight", "font.size": 10,
    "axes.edgecolor": INK2, "axes.labelcolor": INK, "text.color": INK,
    "xtick.color": INK2, "ytick.color": INK2,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.axisbelow": True, "lines.linewidth": 2, "legend.frameon": False,
})


def load(base, mode, name):
    path = os.path.join(base, mode, name)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    df = pd.read_csv(path)
    return df if len(df) else None


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "results"
    data = {}
    for mode, _ in MODES:
        it = load(base, mode, "iterations.csv")
        if it is None:
            continue
        t0 = it["start"].min()
        d = {"iter": it}
        for name in ("flows", "links", "events", "rtt"):
            df = load(base, mode, f"{name}.csv")
            if df is not None and "time" in df:
                df = df[df["time"] >= t0 - 1].copy()
                df["t"] = df["time"] - t0
            d[name] = df
        d["t_end"] = it["end"].max() - t0
        data[mode] = d
    if not data:
        sys.exit(f"no results in {base}/ - run run_experiment.py or simulate.py first")

    t_max = max(d["t_end"] for d in data.values())
    fig_dir = os.path.join(base, "figures")
    os.makedirs(fig_dir, exist_ok=True)
    labels = dict(MODES)
    ideal = None
    flows0 = next((d["flows"] for d in data.values() if d.get("flows") is not None), None)

    # 1. All-reduce (training step) completion time per iteration
    fig, ax = plt.subplots(figsize=(7, 3.6))
    for mode, d in data.items():
        it = d["iter"]
        ax.plot(it["iteration"], it["duration_s"], marker="o", ms=5, color=COLORS[mode],
                label=f"{labels[mode]}  - mean {it['duration_s'].mean():.1f}s")
    if flows0 is not None:
        # one transfer at full line rate = the best any routing could do
        ideal = flows0["mbytes"].iloc[0] * 8 / cfg.LINK_BW_MBPS
        ax.axhline(ideal, color=INK2, lw=1, ls="--")
        ax.text(0.01, ideal, f"line-rate ideal {ideal:.1f}s",
                transform=ax.get_yaxis_transform(), ha="left", va="top",
                color=INK2, fontsize=9)
    ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    ax.set_xlabel("Training iteration")
    ax.set_ylabel("All-reduce completion time (s)")
    ax.set_ylim(0, None)
    ymax = ax.get_ylim()[1]
    ax.set_ylim(0, ymax * 1.25)
    ax.set_title("Time GPUs wait on the network each training step", loc="left")
    ax.legend(loc="upper left", ncol=1)
    fig.savefig(os.path.join(fig_dir, "1_allreduce_time.png"))
    plt.close(fig)

    # 2. CDF of per-flow throughput
    fig, ax = plt.subplots(figsize=(6, 3.6))
    for mode, d in data.items():
        fl = d.get("flows")
        if fl is None:
            continue
        v = np.sort(fl["mbps"].dropna().values)
        ax.step(v, np.arange(1, len(v) + 1) / len(v), where="post",
                color=COLORS[mode], label=labels[mode])
    ax.set_xlabel("Per-flow throughput (Mbit/s)")
    ax.set_ylabel("Fraction of flows")
    ax.set_xlim(0, cfg.LINK_BW_MBPS * 1.05)
    ax.set_title("Flow throughput distribution (CDF)", loc="left")
    ax.legend(loc="upper left")
    fig.savefig(os.path.join(fig_dir, "2_throughput_cdf.png"))
    plt.close(fig)

    # 3. Traffic carried by each spine over time (load balance)
    modes_with_links = [m for m in data if data[m].get("links") is not None]
    if modes_with_links:
        fig, axes = plt.subplots(len(modes_with_links), 1, sharex=True, sharey=True,
                                 figsize=(8, 2.4 * len(modes_with_links) + 0.6),
                                 squeeze=False)
        for ax, mode in zip(axes[:, 0], modes_with_links):
            ln = data[mode]["links"]
            ln = ln[ln["link"].str.startswith("spine")].copy()
            ln["spine"] = ln["link"].str.extract(r"spine(\d+)")[0].astype(int)
            per = ln.groupby(["t", "spine"])["mbps"].sum().unstack(fill_value=0)
            for s in per.columns:
                ax.plot(per.index, per[s], lw=1.5, color=SPINE_COLORS[(s - 1) % 4],
                        label=f"spine{s}")
            ax.set_title(labels[mode], loc="left", fontsize=10)
            ax.set_ylabel("Mbit/s")
            ax.set_xlim(0, t_max)
        axes[-1, 0].set_xlabel("Time since first iteration (s)")
        fig.suptitle("Traffic carried by each spine switch", x=0.02, ha="left")
        fig.legend(*axes[0, 0].get_legend_handles_labels(), loc="upper right",
                   ncol=cfg.NUM_SPINES, bbox_to_anchor=(0.98, 1.0))
        fig.savefig(os.path.join(fig_dir, "3_spine_load.png"))
        plt.close(fig)

    # 4. Utilisation heatmap: every fabric link over time
    if modes_with_links:
        links = cfg.fabric_links()
        fig, axes = plt.subplots(len(modes_with_links), 1, sharex=True,
                                 figsize=(8, 0.28 * len(links) * len(modes_with_links) + 1.2),
                                 squeeze=False)
        for ax, mode in zip(axes[:, 0], modes_with_links):
            ln = data[mode]["links"]
            grid = ln.pivot_table(index="link", columns="t", values="util").reindex(links)
            im = ax.imshow(grid.values * 100, aspect="auto", cmap="Blues", vmin=0, vmax=100,
                           interpolation="nearest",
                           extent=[grid.columns.min(), grid.columns.max(), len(links) - 0.5, -0.5])
            ax.set_yticks(range(len(links)))
            ax.set_yticklabels(links, fontsize=7)
            ax.grid(False)
            ax.set_title(labels[mode], loc="left", fontsize=10)
            ax.set_xlim(0, t_max)
        axes[-1, 0].set_xlabel("Time since first iteration (s)")
        cb = fig.colorbar(im, ax=axes[:, 0], shrink=0.6, pad=0.02)
        cb.set_label("Link utilisation (%)")
        cb.outline.set_visible(False)
        fig.suptitle("Fabric link utilisation (white = idle link, dark = saturated)",
                     x=0.02, ha="left")
        fig.savefig(os.path.join(fig_dir, "4_link_heatmap.png"))
        plt.close(fig)

    # 5. RTT (Mininet only)
    modes_with_rtt = [m for m in data if data[m].get("rtt") is not None]
    if modes_with_rtt:
        fig, ax = plt.subplots(figsize=(6, 3.6))
        for mode in modes_with_rtt:
            v = np.sort(data[mode]["rtt"]["rtt_ms"].values)
            ax.step(v, np.arange(1, len(v) + 1) / len(v), where="post",
                    color=COLORS[mode], label=labels[mode])
        ax.set_xscale("log")
        ax.set_xlabel("Ping RTT h11 -> h21 during training (ms, log scale)")
        ax.set_ylabel("Fraction of pings")
        ax.set_title("Cross-rack latency (CDF)", loc="left")
        ax.legend(loc="lower right")
        fig.savefig(os.path.join(fig_dir, "5_rtt_cdf.png"))
        plt.close(fig)

    # Summary table
    rows = []
    for mode, d in data.items():
        it, fl, ln, ev, rtt = (d["iter"], d.get("flows"), d.get("links"),
                               d.get("events"), d.get("rtt"))
        r = {"Mode": labels[mode],
             "Mean step time (s)": it["duration_s"].mean(),
             "p95 step time (s)": it["duration_s"].quantile(0.95),
             "Mean flow Mbit/s": fl["mbps"].mean() if fl is not None else np.nan}
        if ideal:
            r["Network efficiency (%)"] = 100 * ideal / it["duration_s"].mean()
        if ln is not None:
            # Jain's fairness index of load across all fabric links (1 = perfectly
            # even), averaged over polls where the fabric is carrying traffic.
            per_t = ln.groupby("t")["util"]
            busy_t = per_t.sum()[per_t.sum() > 0.5].index
            jain = per_t.apply(lambda u: u.sum() ** 2 / (len(u) * (u ** 2).sum())
                               if (u ** 2).sum() > 0 else np.nan)
            r["Link-load fairness (Jain)"] = jain.loc[busy_t].mean()
            # Links sitting idle while the fabric is busy = wasted capacity
            idle = ln[ln["t"].isin(busy_t)].groupby("t")["util"].apply(
                lambda u: (u < 0.1).mean())
            r["Idle fabric links while busy (%)"] = 100 * idle.mean()
        if ev is not None:
            r["Reroutes"] = int((ev["event"] == "reroute").sum())
        if rtt is not None:
            r["Median RTT (ms)"] = rtt["rtt_ms"].median()
            r["p99 RTT (ms)"] = rtt["rtt_ms"].quantile(0.99)
        rows.append(r)
    summary = pd.DataFrame(rows).set_index("Mode").T
    if "ecmp" in data and "adaptive" in data:
        e = data["ecmp"]["iter"]["duration_s"].mean()
        a = data["adaptive"]["iter"]["duration_s"].mean()
        speedup = f"\nAIDC-Net cuts mean all-reduce time by {100 * (e - a) / e:.1f}% " \
                  f"({e:.2f}s -> {a:.2f}s), i.e. {e / a:.2f}x faster training steps.\n"
    else:
        speedup = ""
    text = summary.to_markdown(floatfmt=".2f") if _has_tabulate() else summary.round(2).to_string()
    with open(os.path.join(base, "summary.md"), "w") as f:
        f.write(f"# AIDC-Net results ({base})\n\n{text}\n{speedup}")
    print(summary.round(2).to_string())
    print(speedup)
    print(f"figures -> {fig_dir}/   summary -> {base}/summary.md")


def _has_tabulate():
    try:
        import tabulate  # noqa: F401
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    main()
