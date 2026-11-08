#!/usr/bin/env bash
# Build / test / rebuild Decompress on Linux or macOS.
#   ./scripts/build.sh setup     create .venv and install the app in editable mode (+ dev tools, MediaPipe)
#   ./scripts/build.sh test      run the test suite
#   ./scripts/build.sh build     build the wheel + sdist + Blender add-on into dist/
#   ./scripts/build.sh verify    install the built wheel into a clean venv and smoke-test it
#   ./scripts/build.sh rebuild   clean -> setup -> test -> build -> verify   (the full cycle)
#   ./scripts/build.sh clean     remove build outputs (keeps .venv and your data)
#   ./scripts/build.sh exe       build the standalone app dist/Decompress/Decompress (no Python needed) + smoke test
#   ./scripts/build.sh shortcut  add a desktop/app-menu launcher (Linux .desktop file)
#   ./scripts/build.sh release   rebuild + exe
#   ./scripts/build.sh run       start the app      (http://127.0.0.1:8000)
#   ./scripts/build.sh hub       start the hub      (http://127.0.0.1:8765)
# Set EXTRAS to change optional extras (default "mediapipe,dev"; e.g. EXTRAS=all,dev).
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON=${PYTHON:-python3}
EXTRAS=${EXTRAS:-mediapipe,dev}
VENV=.venv
VPY=$VENV/bin/python

step() { printf '\n==> %s\n' "$*"; }

setup() {
  step "Creating $VENV and installing decompress[$EXTRAS] (editable)"
  [ -x "$VPY" ] || "$PYTHON" -m venv "$VENV"
  "$VPY" -m pip install -q --upgrade pip
  "$VPY" -m pip install -q -e ".[$EXTRAS]"
  "$VPY" -m decompress --version
}
test_() { step "Running tests"; "$VPY" -m pytest -q; }
build() {
  step "Building wheel + sdist"
  "$VPY" -m build --outdir dist .
  "$VPY" -m decompress blender-addon --out dist/decompress_blender_addon.zip
  ls -l dist
}
verify() {
  step "Installing the wheel into a clean venv and smoke-testing it"
  rm -rf build/verify-venv
  "$PYTHON" -m venv build/verify-venv
  build/verify-venv/bin/python -m pip install -q --upgrade pip
  build/verify-venv/bin/python -m pip install -q dist/decompress-*.whl
  build/verify-venv/bin/python scripts/smoke_test.py
}
exe() {
  step "Building standalone Decompress with PyInstaller"
  [ -x "$VPY" ] || setup
  "$VPY" -m pip install -q -e ".[$EXTRAS,exe]"
  "$VPY" packaging/make_icon.py
  "$VPY" -m PyInstaller --noconfirm --clean --log-level WARN --distpath dist --workpath build/pyinstaller packaging/decompress.spec
  "$VPY" scripts/smoke_test.py --exe dist/Decompress/Decompress
}
shortcut() {
  step "Creating a desktop launcher"
  local target="$PWD/dist/Decompress/Decompress"
  [ -x "$target" ] || { [ -x "$VPY" ] || setup; target="$PWD/$VENV/bin/decompress-launch"; }
  "$VPY" packaging/make_icon.py >/dev/null
  local dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"; mkdir -p "$dir"
  cat > "$dir/decompress.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Decompress
Comment=Camera vision and motion capture
Exec=$target
Icon=$PWD/packaging/decompress.png
Terminal=true
Categories=Graphics;Video;
DESK
  [ -d "$HOME/Desktop" ] && cp "$dir/decompress.desktop" "$HOME/Desktop/" && chmod +x "$HOME/Desktop/decompress.desktop"
  echo "  $dir/decompress.desktop -> $target"
}
clean() { step "Cleaning build outputs"; rm -rf build dist ./*.egg-info .pytest_cache; find . -name __pycache__ -not -path "./$VENV/*" -prune -exec rm -rf {} +; }

case "${1:-help}" in
  setup) setup ;;
  test) test_ ;;
  build) build ;;
  verify) verify ;;
  rebuild) clean; setup; test_; build; verify; step "Rebuild complete: dist/ is ready" ;;
  clean) clean ;;
  exe) exe ;;
  shortcut) shortcut ;;
  release) clean; setup; test_; build; verify; exe; step "Release ready in dist/" ;;
  run) exec "$VPY" -m decompress serve "${@:2}" ;;
  hub) exec "$VPY" -m decompress hub "${@:2}" ;;
  *) sed -n '2,15p' "$0"; exit 1 ;;
esac
