#!/usr/bin/env bash
# Checks that every tool the project needs is installed and working.
# Run from the project folder inside WSL:   bash scripts/check_setup.sh
# Each line prints PASS or FAIL. Send me the output if anything fails.

pass=0; fail=0
ok()  { printf '  PASS  %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL  %s\n        -> %s\n' "$1" "$2"; fail=$((fail+1)); }

echo "== Linux and build tools =="
if grep -qi microsoft /proc/version 2>/dev/null; then ok "running inside WSL"; else bad "running inside WSL" "open the Ubuntu app, not PowerShell"; fi
command -v g++   >/dev/null && ok "g++   $(g++ -dumpversion)"                         || bad "g++"   "sudo apt install -y build-essential"
command -v cmake >/dev/null && ok "cmake $(cmake --version | head -1 | awk '{print $3}')" || bad "cmake" "sudo apt install -y cmake"
command -v git   >/dev/null && ok "git   $(git --version | awk '{print $3}')"          || bad "git"   "sudo apt install -y git"
[ -n "$(git config --global user.name)" ] && ok "git user.name = $(git config --global user.name)" || bad "git user.name" "git config --global user.name \"Sunny Alex\""
command -v rlwrap >/dev/null && ok "rlwrap (arrow keys in q)" || bad "rlwrap" "sudo apt install -y rlwrap"

echo "== C++ compile test =="
tmp=$(mktemp -d)
printf '#include <cmath>\n#include <iostream>\nint main(){std::cout<<std::erfc(0.0)<<"\\n";}\n' > "$tmp/t.cpp"
if g++ -std=c++20 -O2 "$tmp/t.cpp" -o "$tmp/t" 2>/dev/null && [ "$("$tmp/t")" = "1" ]; then ok "C++20 compiles and runs"; else bad "C++20 compile" "check build-essential install"; fi
rm -rf "$tmp"

echo "== kdb+ (KDB-X) =="
if command -v q >/dev/null; then
  out=$(echo 'show 1+1;exit 0' | timeout 20 q -q 2>&1)
  if [ "$out" = "2" ]; then ok "q runs: 1+1 = 2"; else bad "q runs" "q printed: $out"; fi
else
  bad "q on PATH" "rerun the KX install command, then close and reopen Ubuntu"
fi

echo "== Python environment (oak) =="
if [ "$CONDA_DEFAULT_ENV" = "oak" ]; then ok "conda env 'oak' active"; else bad "conda env 'oak' active" "conda activate oak"; fi
PYKX_UNLICENSED=true python - <<'PY'
import importlib, sys
mods = ["numpy", "pandas", "scipy", "pybind11", "requests", "websockets", "streamlit", "plotly", "pytest", "pykx"]
for m in mods:
    try:
        mod = importlib.import_module(m)
        print(f"  PASS  {m:<10} {getattr(mod, '__version__', '')}")
    except Exception as e:
        print(f"  FAIL  {m}\n        -> {type(e).__name__}: {e}")
print(f"  INFO  python {sys.version.split()[0]}")
PY

echo "== Deribit public API =="
PYTHONWARNINGS=ignore python - <<'PY'
try:
    import requests
    r = requests.get("https://www.deribit.com/api/v2/public/get_index_price",
                     params={"index_name": "btc_usd"}, timeout=10)
    px = r.json()["result"]["index_price"]
    print(f"  PASS  Deribit reachable, BTC index = {px:,.2f} USD")
except Exception as e:
    print(f"  FAIL  Deribit API\n        -> {type(e).__name__}: {e}")
PY

echo
echo "Shell checks: $pass passed, $fail failed (Python checks listed above)."
