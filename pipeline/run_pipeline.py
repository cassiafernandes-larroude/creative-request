"""
Entry point for the daily pipeline. Calls fetch_meta + fetch_shopify,
then runs the existing analysis (rebuild_v4) + dashboard generator (build_dashboard_v3).

Usage in GitHub Action:
  python pipeline/run_pipeline.py
  → Outputs: dashboard.html and analysis.json at repo root.
"""
import json, os, sys, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIPE = ROOT / "pipeline"
META_OUT = "/tmp/meta_data.json"
SHP_OUT = "/tmp/shopify_data.json"

def run(cmd, **kwargs):
    print(f"$ {' '.join(cmd)}", file=sys.stderr)
    r = subprocess.run(cmd, **kwargs)
    if r.returncode != 0:
        sys.exit(r.returncode)

def main():
    # 1. Fetch Meta
    run([sys.executable, str(PIPE / "fetch_meta.py"), META_OUT])
    # 2. Fetch Shopify
    run([sys.executable, str(PIPE / "fetch_shopify.py"), SHP_OUT])
    # 3. Run rebuild + build_dashboard (these scripts read META_OUT/SHP_OUT and write to ROOT)
    env = {**os.environ, "META_DATA_JSON": META_OUT, "SHOPIFY_DATA_JSON": SHP_OUT, "OUTPUT_DIR": str(ROOT)}
    run([sys.executable, str(PIPE / "rebuild_v5.py")], env=env)
    run([sys.executable, str(PIPE / "build_dashboard_v4.py")], env=env)
    print("[Pipeline] Done.", file=sys.stderr)

if __name__ == "__main__":
    main()
