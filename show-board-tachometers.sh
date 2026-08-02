#!/usr/bin/env bash

set -u

while true; do
    board_dir=""
    for candidate in /sys/class/hwmon/hwmon*; do
        if [[ -r "$candidate/name" ]] && [[ "$(<"$candidate/name")" == "it8696" ]]; then
            board_dir="$candidate"
            break
        fi
    done

    printf '\033[2J\033[H'
    printf 'X870 EAGLE WIFI7 — tachometry płyty IT8696\n'
    printf 'Aktualizacja: %(%Y-%m-%d %H:%M:%S)T    Odświeżanie: 1 s\n' -1
    printf 'Ctrl+C zamyka podgląd\n\n'

    if [[ -z "$board_dir" ]]; then
        printf 'Sterownik IT8696 nie jest dostępny.\n'
        sleep 1
        continue
    fi

    printf '%-9s  %-24s  %10s  %7s  %s\n' 'KANAŁ' 'OPIS' 'OBROTY' 'PWM' 'STAN'
    printf '%-9s  %-24s  %10s  %7s  %s\n' '--------' '------------------------' '----------' '-------' '--------'

    for channel in 1 2 3 4 5 6; do
        rpm_path="$board_dir/fan${channel}_input"
        pwm_path="$board_dir/pwm${channel}"
        rpm=0
        pwm_text='—'

        [[ -r "$rpm_path" ]] && rpm="$(<"$rpm_path")"
        if [[ -r "$pwm_path" ]]; then
            pwm_raw="$(<"$pwm_path")"
            pwm_text="$((pwm_raw * 100 / 255))%"
        fi

        case "$channel" in
            1) description='Pompa CPU' ;;
            5) description='Wentylator CPU' ;;
            *) description="Fan $channel" ;;
        esac

        if (( rpm > 0 )); then
            state='AKTYWNY'
        else
            state='brak RPM'
        fi

        printf 'fan%-5d  %-24s  %7d RPM  %7s  %s\n' \
            "$channel" "$description" "$rpm" "$pwm_text" "$state"
    done

    sleep 1
done
