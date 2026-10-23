# SubscriptionAgent

Lokalny agent AI analizujący subskrypcje i proponujący oszczędności.
Uwzględnia ostatnie używanie, ceny, rodzaje umów i preferencje użytkownika.
Nie wymaga klucza API — Ollama i model pobierają się automatycznie do projektu.

## Technologie

Python 3.10+, requests, Ollama, llama3.2:3b, JSON i JSONL.

## Działanie

Model wybiera narzędzia do odczytu i sortowania danych, analizuje aktywność
oraz umowy, a następnie proponuje oszczędności. Osobna rozmowa modelu
weryfikuje propozycję. Program pokazuje, co zostawić, z czego zrezygnować
i ile można zaoszczędzić.

Zbieranie danych ma limit decyzji. Błąd lub osiągnięcie limitu zatrzymuje
pracę. Decyzje, wyniki narzędzi, tokeny, koszt API i opóźnienia są zapisywane
w `traces/`. Koszt zewnętrznego API wynosi 0 USD.

## Struktura

```text
main.py             uruchomienie
start.bat           przygotowanie środowiska
src/agent.py        pętla agenta i analizy
src/tools.py        odczyt, sortowanie i ocena danych
src/prompt_var.py   prompty
src/runtime.py      pobieranie i uruchamianie Ollama
data/               przykładowe subskrypcje i preferencje
runtime/            pobrany silnik i model
traces/             logi JSONL
```

## Uruchomienie

Windows x64, Python 3.10+ i internet przy pierwszym uruchomieniu.

W terminalu PowerShell:

```powershell
cd D:\Projekty\SubscriptionAgent
.\start.bat
```

Własne zadanie:

```powershell
.\start.bat "Zaproponuj oszczędności zgodne z moimi preferencjami."
```

## Przykład

Dla dołączonych danych: pozostaw Netflix, Spotify i Adobe CC.
Anuluj HBO Max, Disney+ i Xbox Game Pass — oszczędność 133 zł miesięcznie.

Agent tylko rekomenduje, nie anuluje usług. Wynik modelu może zawierać błędy.
