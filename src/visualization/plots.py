"""Plotting helpers — interactive Plotly HTML + static matplotlib PNG.

Interactive HTML is for the dashboard/notebooks; PNG is for the SIH
presentation deck (offline-safe).
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import plotly.graph_objects as go


def time_series(df: pd.DataFrame, columns: list[str], title: str, ylabel: str,
                out_html: str | Path, out_png: str | Path | None = None,
                labels: dict | None = None, colors: list[str] | None = None):
    """Multi-line time-series plot (interactive + optional PNG)."""
    out_html = Path(out_html)
    out_html.parent.mkdir(parents=True, exist_ok=True)

    fig = go.Figure()
    default_colors = ["#d62728", "#1f77b4", "#2ca02c", "#9467bd", "#8c564b", "#e377c2"]
    for i, col in enumerate(columns):
        fig.add_trace(go.Scatter(
            x=df.index, y=df[col],
            name=(labels or {}).get(col, col),
            line=dict(color=(colors or default_colors)[i % len(default_colors)]),
        ))
    fig.update_layout(
        title=title, yaxis_title=ylabel, xaxis_title="",
        template="plotly_white", hovermode="x unified", height=500,
        legend=dict(orientation="h", yanchor="bottom", y=1.02))
    fig.write_html(out_html)
    print(f"  plotly -> {out_html}")

    if out_png:
        out_png = Path(out_png)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        plt.figure(figsize=(12, 5))
        for i, col in enumerate(columns):
            plt.plot(df.index, df[col], label=(labels or {}).get(col, col),
                     color=(colors or default_colors)[i % len(default_colors)])
        plt.title(title)
        plt.ylabel(ylabel)
        plt.grid(alpha=0.3)
        plt.legend(ncol=len(columns), fontsize=9)
        plt.tight_layout()
        plt.savefig(out_png, dpi=140)
        plt.close()
        print(f"  png    -> {out_png}")


def bar_compare(df: pd.DataFrame, x_col: str, y_col: str, title: str,
                out_html: str | Path, out_png: str | Path | None = None,
                ylabel: str = ""):
    """Bar chart for design comparisons (material / orientation sweeps)."""
    out_html = Path(out_html)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    fig = go.Figure(go.Bar(x=df[x_col].astype(str), y=df[y_col],
                           marker_color="#1f77b4"))
    fig.update_layout(title=title, yaxis_title=ylabel or y_col, xaxis_title=x_col,
                      template="plotly_white", height=450)
    fig.write_html(out_html)
    print(f"  plotly -> {out_html}")

    if out_png:
        out_png = Path(out_png)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        plt.figure(figsize=(10, 4.5))
        plt.bar(df[x_col].astype(str), df[y_col], color="#1f77b4")
        plt.title(title)
        plt.ylabel(ylabel or y_col)
        plt.grid(alpha=0.3, axis="y")
        plt.xticks(rotation=45)
        plt.tight_layout()
        plt.savefig(out_png, dpi=140)
        plt.close()
        print(f"  png    -> {out_png}")
