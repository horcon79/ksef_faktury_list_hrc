# KSeF Faktury List — filtr samofakturowania

Rozszerzenie projektu [Pafkaja/ksef_faktury_list](https://github.com/Pafkaja/ksef_faktury_list)
o możliwość pobierania wyłącznie faktur wystawionych w trybie **samofakturowania**.
Zachowuje oryginalny skrypt, w tym generowanie PDF, pobieranie XML, kody QR,
uwierzytelnianie tokenem lub certyfikatem oraz wysyłkę faktur przez SMTP.

Autorem projektu źródłowego jest **Pafkaja**. Niniejsze rozszerzenie stworzone przez **horcon79** jest
udostępniane na tej samej licencji **MIT**, z zachowaniem oryginalnej noty
licencyjnej. Nie jest to oficjalny klient Ministerstwa Finansów.

## Po co ta zmiana?

W procesie samofakturowania nabywca wystawia fakturę w imieniu sprzedawcy.
Przy pobieraniu dokumentów z KSeF przydatne jest ograniczenie wyników do tego
trybu, a także **zawężenie do konkretnego kontrahenta (NIP nabywcy)**, gdy prawo
do samofakturowania w imieniu spółki posiada wielu partnerów handlowych.
Następnie wykorzystywane są istniejące funkcje: zapisu XML, wygenerowania PDF
i przekazania dokumentów do księgowości pocztą elektroniczną.

API KSeF udostępnia filtr metadanych `isSelfInvoicing`. Rozszerzenie przekazuje
ten filtr w zapytaniu do API, a także umożliwia selekcję faktur konkretnego nabywcy
(`--buyer-nip`), zanim uruchomione zostaną pobieranie plików, generowanie PDF
i wysyłka. Pozostałe etapy korzystają z otrzymanej listy faktur.

W strukturze FA oznaczenie samofakturowania znajduje się pod ścieżką
`Faktura/Fa/Adnotacje/P_17` i ma wartość `1`. Skrypt używa filtra API;
nie dodaje odrębnego sprawdzania tego znacznika w pobranym XML.

## Dodane funkcje

| Element | Zmiana |
|---|---|
| Argument CLI | `--isSelfInvoicing` oraz alias `--is-self-invoicing` |
| Wartość `true` | Pobieranie tylko faktur w trybie samofakturowania |
| Wartość `false` | Pobieranie faktur z wyłączeniem samofakturowania |
| Flaga bez wartości | Odpowiada `true` |
| Brak flagi | Brak filtra, tak jak w oryginalnym skrypcie |
| Filtr nabywcy | `--buyer-nip` oraz alias `--buyerNip` (10 cyfr, myślniki, prefiks PL) |
| Metody Python | Opcjonalne argumenty `is_self_invoicing` i `buyer_nip` w `query_invoices()` |
| Walidacja | Niepoprawna wartość CLI kończy działanie przed połączeniem z KSeF |
| Tabela faktur | Dodana kolumna `NIP nabywcy` dla szybkiej weryfikacji kontrahenta |
| Obsługa limitów (429) | Automatyczne wznawianie (do 3 prób, 2s z nagłówkiem `Retry-After` z KSeF) |
| Throttling zapytań | Pauza 0.3s po pobraniu XML z sieci przy masowym przetwarzaniu |
| Cache z dysku (XML) | Wczytywanie istniejącego pliku XML z dysku bez odpytywania API KSeF |
| Testy | Filtr, zgodność CLI, NIP nabywcy, PDF/XML, SMTP, retry 429, disk cache i throttling |

Pole w JSON wysyłanym do `POST /invoices/query/metadata` jest typu boolean,
np. `"isSelfInvoicing": true`. Przy braku flagi pole nie jest wysyłane.

Parametr `--buyer-nip` (alias `--buyerNip`) filtruje listę faktur na podstawie danych
identyfikacyjnych nabywcy (`Podmiot2` / `buyer`) zawartych w metadanych KSeF.
W trybie samofakturowania podmiotem autoryzowanym jest Sprzedawca (`Subject1`),
a faktury wystawiają różni nabywcy — parametr ten pozwala przetwarzać dokumenty
tylko od wybranego partnera. Przed zapytaniem NIP jest normalizowany do 10 cyfr
(obsługiwane są myślniki, spacje oraz opcjonalny prefiks `PL`). Niepoprawny format
(zła liczba cyfr lub litery) powoduje przerwanie działania przed nawiązaniem połączenia.

Nowe argumenty metody Python (`is_self_invoicing`, `buyer_nip`) dodano na końcu
sygnatury `query_invoices()`, aby zachować zgodność dotychczasowych wywołań.

## Odporność na limity KSeF (Rate Limiting) i masowe przetwarzanie

Podczas masowego pobierania faktur lub jednoczesnego generowania PDF i zapisu XML serwery produkcyjne KSeF nakładają restrykcyjne limity zapytań w oknach minutowych (**HTTP 429 Too Many Requests**). W skrypcie wdrożono mechanizmy zapewniające stabilną pracę bez przerywania przetwarzania:

1. **Automatyczne wznawianie przy błędzie 429 (Retry-After):**
   - Przy odebraniu kodu 429 klient analizuje nagłówek `Retry-After` zwracany przez Ministerstwo Finansów (np. czas pozostały do zresetowania okna minutowego limitu) lub domyślnie odczekuje 2.0s.
   - Wykonywane są do 3 prób ponowienia zapytania przed zgłoszeniem błędu.
   - Oczekiwanie i kolejne próby są rejestrowane w logach (`logger.warning`).

2. **Throttling zapytań (0.3s):**
   - Po każdym pobraniu pliku XML z sieci dodano mikropauzę 0.3 sekundy.
   - Zapobiega to wysyłaniu żądań serią (burst requests) i minimalizuje ryzyko nałożenia blokady przez KSeF.

3. **Odczyt z dysku w cache (Disk Cache):**
   - Generator PDF, zapis XML oraz moduł wysyłki e-mail sprawdzają przed zapytaniem sieciowym, czy plik `{ksefNumber}.xml` znajduje się już lokalnie w katalogu `--xml-output-dir`.
   - Jeśli plik już istnieje, jest wczytywany bezpośrednio z dysku – eliminuje to zbędne ponowne zapytania przy kolejnych uruchomieniach oraz przy równoczesnym użyciu `--download-xml` i `--download-pdf`.

## Funkcje oryginału

- Uwierzytelnianie tokenem KSeF lub podpisem XAdES.
- Środowiska `prod`, `demo` i `test`.
- Wybór NIP-u, zakresu dat i kontekstu sprzedawcy/nabywcy.
- Prezentacja listy faktur jako tabela lub JSON.
- Pobieranie oryginalnych XML-i.
- Generowanie PDF z kodem QR i obsługą polskich znaków.
- Konwersja XML → PDF offline.
- Wysyłka PDF i XML przez SMTP: osobno lub w jednej wiadomości zbiorczej.
- Wspólny cache XML/PDF podczas pojedynczego uruchomienia.

Pełną dokumentację źródłowego projektu zachowano w
[docs/README.upstream.md](docs/README.upstream.md).

## Instalacja

Pobierz repozytorium przyciskiem **Code → Download ZIP** albo sklonuj adres
wyświetlony w menu **Code**, a następnie przejdź do jego katalogu.
Zalecany jest Python 3.10 lub nowszy.

```sh
python -m venv .venv
```

Aktywacja środowiska na Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Aktywacja na Linux/macOS:

```sh
source .venv/bin/activate
```

Instalacja zależności i lista opcji:

```sh
python -m pip install -r requirements.txt
python ksef_faktury_list.py --help
```

Dla polskich znaków w PDF zainstaluj font **DejaVu Sans**, zgodnie
z instrukcją projektu źródłowego. Dockerfile i zależności pozostały bez zmian.

## Podstawowe użycie

Tylko samofakturowanie, produkcyjny KSeF:

```sh
python ksef_faktury_list.py --nip "TWOJ_NIP" --token-file "token.txt" --env prod --subject-type Subject1 --isSelfInvoicing true
```

Równoważne formy filtra:

```text
--isSelfInvoicing true
--isSelfInvoicing
--is-self-invoicing true
--is-self-invoicing
```

Aby wyłączyć samofakturowanie z wyników, podaj `--isSelfInvoicing false`.
Aby pobrać wyniki bez tego ograniczenia, pomiń parametr.

### Filtrowanie po NIP nabywcy (samofakturowanie dla wybranego kontrahenta)

Gdy w trybie samofakturowania (`Subject1`) faktury w Twoim imieniu wystawia wielu różnych nabywców, możesz zawęzić przetwarzanie do jednego podmiotu:

```sh
python ksef_faktury_list.py --nip "TWOJ_NIP" --token-file "token.txt" --env prod --subject-type Subject1 --isSelfInvoicing true --buyer-nip 9876543210
```

Równoważne formy parametru:

```text
--buyer-nip 9876543210
--buyer-nip 987-654-32-10
--buyer-nip PL9876543210
--buyerNip 9876543210
```

- **Dlaczego ten parametr jest przydatny:** W procesie samofakturowania podmiotem autoryzowanym w KSeF jest Sprzedawca (NIP podany w `--nip`). Jeśli prawo do samofakturowania ma wielu kontrahentów, zapytanie KSeF zwraca wszystkie dokumenty wystawione przez wszystkich uprawnionych partnerów. Flaga `--buyer-nip` zawęża wynik wyłącznie do wybranego nabywcy, ograniczając wyświetlaną tabelę, zapisywane pliki XML, generowane PDF-y oraz załączniki wiadomości e-mail.
- **Normalizacja i walidacja:** Wartość parametru jest automatycznie oczyszczana (usuwane są myślniki, spacje oraz prefiks `PL`). Podanie ciągu innego niż 10 cyfr powoduje zakończenie działania ze stosownym komunikatem przed nawiązaniem połączenia z KSeF.

### Który `subject-type` wybrać?

| Opcja | Rola podanego NIP-u | Zastosowanie |
|---|---|---|
| `Subject1` | Sprzedawca | Faktury wystawione przez nabywców w Twoim imieniu |
| `Subject2` | Nabywca | Faktury zakupowe oznaczone jako samofakturowanie |

Domyślną opcją oryginału pozostaje **`Subject2`**. W przykładach dla sprzedawcy
ustawiamy jawnie `--subject-type Subject1`.

### XML i PDF z wybranego okresu

Przykład obejmujący 26–28 września 2026:

```sh
python ksef_faktury_list.py --nip "TWOJ_NIP" --token "TWOJ_TOKEN_KSEF" --env prod --subject-type Subject1 --isSelfInvoicing true --buyer-nip 9876543210 --date-from 2026-09-26 --date-to 2026-09-28 --download-xml --xml-output-dir "./output/xml" --download-pdf --pdf-output-dir "./output/pdf"
```

`--output` nadal wybiera format listy (`table` lub `json`). Katalogi plików
ustawia się przez `--xml-output-dir` i `--pdf-output-dir`.

### PDF + XML wysyłane e-mailem

```sh
python ksef_faktury_list.py --nip "TWOJ_NIP" --token-file "token.txt" --env prod --subject-type Subject1 --isSelfInvoicing true --buyer-nip 9876543210 --date-from 2026-09-26 --date-to 2026-09-28 --download-xml --xml-output-dir "./output/xml" --download-pdf --pdf-output-dir "./output/pdf" --send-email --smtp-host "smtp.example.com" --smtp-port 587 --smtp-user "sender@example.com" --smtp-password-file "smtp_haslo.txt" --email-from "sender@example.com" --email-to "recipient@example.com" --email-group all
```

- `--email-group all`: jedna wiadomość z kompletem załączników z przetworzonej listy.
- `--email-group single`: osobna wiadomość dla każdej faktury; wariant domyślny.
- `--email-to` można powtórzyć dla kilku odbiorców.
- Oryginalna obsługa SMTP korzysta z STARTTLS; przykład używa portu 587.
- `--download-pdf` zachowuje pliki na dysku. Bez tej opcji wysyłka nadal
  może korzystać z PDF-ów wygenerowanych tymczasowo.

### Ostatnie trzy dni w PowerShell

Dziś i dwa poprzednie dni według lokalnej daty komputera:

```powershell
$dateFrom = (Get-Date).AddDays(-2).ToString('yyyy-MM-dd')
$dateTo = (Get-Date).ToString('yyyy-MM-dd')
python .\ksef_faktury_list.py --nip "TWOJ_NIP" --token-file "token.txt" --env prod --subject-type Subject1 --isSelfInvoicing true --buyer-nip 9876543210 --date-from $dateFrom --date-to $dateTo --download-pdf --pdf-output-dir "./output/pdf"
```

Można dopisać argumenty SMTP z poprzedniego przykładu.
Bez jawnego zakresu oryginał przyjmuje początek 30 dni temu i koniec dzisiaj.
Daty dotyczą **przyjęcia do KSeF (`Invoicing`)**, zgodnie z domyślnym
zachowaniem klienta, a nie daty wystawienia zapisanej na fakturze.

## Użycie z kodu Python

```python
from ksef_faktury_list import KSeFClient

client = KSeFClient.from_token("TWOJ_TOKEN_KSEF", environment="prod")
client.init_session_token("TWOJ_NIP")
try:
    result = client.query_invoices(
        subject_type="Subject1",
        is_self_invoicing=True,
        buyer_nip="9876543210",
    )
    invoices = result.get("invoices", [])
finally:
    client.terminate_session()
```

`is_self_invoicing=False` wyklucza samofakturowanie; `None` pomija filtr.
`buyer_nip` przyjmuje 10-cyfrowy NIP nabywcy (jako ciąg znaków) lub `None`.

## Testy i zakres weryfikacji

```sh
python -m unittest -v test_self_invoicing.py
```

Testy działają offline i sprawdzają:

1. Wysyłanie do API prawdziwych wartości boolean i brak pola przy `None`.
2. Oba aliasy CLI, flagę bez wartości, `false` oraz odrzucenie błędnego argumentu.
3. Przejście przefiltrowanej listy przez zapis XML i rzeczywisty generator PDF.
4. Przygotowanie załączników PDF/XML dla wiadomości pojedynczych i zbiorczych.
5. Automatyczne ponawianie zapytań po kodzie 429 z uwzględnieniem `Retry-After` oraz rzucenie błędu po wyczerpaniu 3 prób.
6. Pomijanie zapytań do API KSeF w przypadku obecności pliku XML na dysku (disk cache).
7. Weryfikację throttlingu (0.3s) po pobraniu dokumentu z API.
8. Filtrowanie faktur po NIP nabywcy (dla `buyer_nip` w kodzie i CLI, różne formaty zapisu, wczesna walidacja).

KSeF i SMTP są w testach zastąpione atrapami. Żaden test nie wysyła prawdziwego
e-maila. Testy nie potwierdzają działania konkretnego tokenu produkcyjnego
ani konfiguracji serwera pocztowego.

## Zachowane ograniczenia i propozycje dalszych zmian

Zakres tej wersji jest celowo niewielki: dodaje filtr, zachowując dotychczasowe
działanie oryginalnego skryptu. Poniższe rozszerzenia **nie są jeszcze wdrożone**:

| Obszar | Obecne działanie | Propozycja kolejnego kroku |
|---|---|---|
| Stronicowanie CLI | Jedna strona wyników, domyślnie do 100 faktur | Pobieranie kolejnych stron i obsługa `isTruncated` |
| Długi zakres dat | Oryginalny klient skraca zakres większy niż 90 dni | Podział okresu na mniejsze okna |
| Ponowna wysyłka | Brak trwałej historii wysłanych e-maili | Rejestr numerów KSeF i statusów wysyłki |
| Synchronizacja | Wyszukiwanie według przekazanych dat | Kursor `PermanentStorage` i obsługa przerw w harmonogramie |
| Odporność API | Wdrożony retry 429 z `Retry-After` i throttling 0.3s | Odświeżanie accessToken przy bardzo długich sesjach |
| Kontrola XML | Filtr po stronie API | Opcjonalne potwierdzenie `P_17=1` po pobraniu |

Ponowne uruchomienie dla nakładających się okresów może ponownie wysłać te same
faktury. Filtr nie dotyczy lokalnej konwersji `--xml-to-pdf`.

Tokeny i hasła przechowuj poza repozytorium. Pliki `token.txt`, `smtp_haslo.txt`,
klucze prywatne i katalog `output/` są objęte dołączonym `.gitignore`.
Zachowany tryb `--verbose` może wypisywać poufne dane z odpowiedzi API;
przed udostępnieniem logów usuń sekrety i dane faktur.

## Propozycja włączenia zmiany do projektu źródłowego

Zmiana jest przygotowana jako niewielkie rozszerzenie możliwe do przeniesienia
do upstream. Obejmuje opcjonalny parametr metody, argument CLI, przekazanie
filtra do API i testy. Brak parametru zachowuje dotychczasowy JSON zapytania;
funkcje uwierzytelniania, PDF i SMTP nie zostały zmodyfikowane.

Nie wysłano automatycznie pull requesta do autora projektu. Zgłoszenia błędów
i propozycje zmian powinny zawierać zanonimizowany opis, bez tokenów i faktur
z rzeczywistymi danymi klientów.

## Autorstwo i licencja

- Projekt źródłowy: [Pafkaja/ksef_faktury_list](https://github.com/Pafkaja/ksef_faktury_list).
- Rozszerzenie filtra samofakturowania: fork przygotowany dla
  [horcon79](https://github.com/horcon79).
- Bazowa wersja: commit `82904db7d5dbda21a28a06265d17d3ecb6c6252b`;
  zawartość skryptu sprawdzona 28.09.2026.
- Licencja: [MIT](LICENSE). Oryginalną notę zachowano w skrypcie i skopiowano
  do pliku LICENSE bez zmiany jej treści.
- Dokumentacja API: [KSeF API 2.0](https://api.ksef.mf.gov.pl/docs/v2/index.html).
