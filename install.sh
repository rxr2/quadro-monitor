#!/usr/bin/env bash

set -euo pipefail

source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
install_dir="${XDG_DATA_HOME:-$HOME/.local/share}/quadro-monitor"
bin_dir="$HOME/.local/bin"
applications_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
icons_dir="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
systemd_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

mkdir -p "$install_dir" "$bin_dir" "$applications_dir" "$icons_dir" "$systemd_dir"

install -m 0755 "$source_dir/quadro_monitor.py" "$install_dir/quadro_monitor.py"
install -m 0755 "$source_dir/telemetry_logger.py" "$install_dir/telemetry_logger.py"
install -m 0755 "$source_dir/show-board-tachometers.sh" "$install_dir/show-board-tachometers.sh"
install -m 0644 "$source_dir/style.css" "$install_dir/style.css"
install -m 0644 "$source_dir/README.md" "$install_dir/README.md"
install -m 0644 "$source_dir/quadro-monitor.svg" "$icons_dir/quadro-monitor.svg"

sed "s|@APP_DIR@|$install_dir|g" "$source_dir/quadro-monitor.desktop.in" \
    > "$applications_dir/quadro-monitor.desktop"
chmod 0644 "$applications_dir/quadro-monitor.desktop"

sed "s|@APP_DIR@|$install_dir|g" "$source_dir/quadro-telemetry-logger.service.in" \
    > "$systemd_dir/quadro-telemetry-logger.service"
chmod 0644 "$systemd_dir/quadro-telemetry-logger.service"

launcher="$bin_dir/quadro-monitor"
printf '#!/bin/sh\nexec /usr/bin/python3 "%s/quadro_monitor.py" "$@"\n' "$install_dir" > "$launcher"
chmod 0755 "$launcher"

command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "$applications_dir" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 \
    && gtk-update-icon-cache -f -t "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" || true

systemctl --user daemon-reload
systemctl --user enable --now quadro-telemetry-logger.service

printf '\nQuadro Monitor został zainstalowany.\n'
printf 'Uruchom: %s\n' "$launcher"
printf 'Log CSV: %s/logs/hardware-telemetry.csv\n' "$install_dir"
