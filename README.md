# Quadro Monitor

Natywna aplikacja GTK4 przygotowana dla Linux/PikaOS i konkretnego zestawu:
Gigabyte X870 EAGLE WIFI7, Ryzen 7 9800X3D, Radeon RX 7900 XTX oraz
Aquacomputer Quadro.

Monitorowane parametry:

- temperatury i obciążenia AMD Ryzen 7 9800X3D,
- pompy CPU podłączonej jako IT8696 Fan 1,
- wentylatora CPU podłączonego jako IT8696 Fan 5,
- temperatury cieczy układu GPU z Aquacomputer Quadro Sensor 1,
- temperatur RX 7900 XTX zgodnych z LACT: edge, junction/hotspot oraz mem/VRAM,
- obciążenia RX 7900 XTX oraz średniej mocy PPT w watach,
- wentylatora GPU podłączonego jako Quadro Fan 1,
- pompy GPU podłączonej jako Quadro Fan 2,
- telemetrii elektrycznej obu używanych kanałów,
- czujnika przepływu Quadro,
- dwuminutowej historii temperatur i prędkości GPU Fan 1.

Obroty płyty głównej są udostępniane przez zewnętrzny moduł `it87` DKMS.
Mapowanie zostało potwierdzone na tej maszynie: IT8696 Fan 1 to pompa CPU,
a IT8696 Fan 5 to wentylator CPU.

Aplikacja czyta standardowy interfejs Linux `hwmon`; nie potrzebuje konta root
ani ciągłego uruchamiania `liquidctl`.

Uruchomienie z terminala:

```bash
python3 quadro_monitor.py
```

## Instalacja użytkownika

Wymagane pakiety systemowe: Python 3, GTK4, libadwaita, PyGObject oraz
`python3-psutil`. Odczyty płyty X870 wymagają modułu
[`frankcrawford/it87`](https://github.com/frankcrawford/it87) z obsługą
IT8696E.

```bash
chmod +x install.sh
./install.sh
```

Instalator kopiuje program do `~/.local/share/quadro-monitor`, dodaje polecenie
`quadro-monitor`, wpis do menu aplikacji oraz uruchamia usługę loggera.

## Log telemetrii

Usługa użytkownika `quadro-telemetry-logger.service` zapisuje próbkę co sekundę
do `logs/hardware-telemetry.csv`. Log obejmuje temperaturę i użycie CPU,
wszystkie tachometry oraz PWM IT8696, tachometry i ciecz Quadro, a także
temperatury dedykowanego GPU zgodne z LACT: `edge`, `junction` i `mem`.

Plik jest obracany po osiągnięciu 50 MiB; zachowywanych jest pięć poprzednich
plików (`.1`–`.5`). Podgląd na żywo:

```bash
tail -f ~/.local/share/quadro-monitor/logs/hardware-telemetry.csv
```

## Dopasowanie sprzętu

Dedykowany GPU jest identyfikowany w `quadro_monitor.py` i
`telemetry_logger.py` przez adres PCI `0000:03:00.0`. Mapowanie płyty:

- IT8696 Fan 1 — pompa CPU,
- IT8696 Fan 5 — wentylator CPU,
- Quadro Fan 1 — wentylator układu GPU,
- Quadro Fan 2 — pompa układu GPU.

Przed użyciem na innym komputerze należy dostosować adres PCI i mapowanie
kanałów do własnego sprzętu.
