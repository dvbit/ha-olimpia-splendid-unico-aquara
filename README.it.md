# Olimpia Splendid Unico

[![HACS Validation](https://github.com/dvbit/ha-olimpia-splendid-unico-aquara/actions/workflows/validate.yml/badge.svg)](https://github.com/dvbit/ha-olimpia-splendid-unico-aquara/actions/workflows/validate.yml)
[![Hassfest](https://github.com/dvbit/ha-olimpia-splendid-unico-aquara/actions/workflows/hassfest.yml/badge.svg)](https://github.com/dvbit/ha-olimpia-splendid-unico-aquara/actions/workflows/hassfest.yml)

**[English version](README.md)**

Integrazione custom per [Home Assistant](https://www.home-assistant.io/) per i
condizionatori **Olimpia Splendid Unico**, con controllo **TCP locale** (nessun
cloud). Setup iniziale opzionale via BLE per pairing e configurazione WiFi.

> Fork di [Daneel87/ha-olimpia-splendid-unico](https://github.com/Daneel87/ha-olimpia-splendid-unico),
> esteso con il **controllo di posizione dell'aletta** tramite sensore di
> inclinazione Aqara DJT11LM.

> [!WARNING]
> **L'integrazione funziona solo con le unità dotate di scheda WiFi B1015** —
> quella gestita dall'app *Olimpia Splendid Unico*, che si annuncia via
> Bluetooth come **"OL01"**. Le unità con un modulo WiFi diverso o più recente
> usano un protocollo differente e **non verranno rilevate**. Vedi
> [Compatibilità](#compatibilità).

## Funzionalità

- **Modi HVAC**: Riscaldamento, Raffrescamento, Deumidificazione, Solo
  ventilazione, Auto
- **Velocità ventola**: Bassa, Media, Alta, Auto
- **Oscillazione**: pulsante toggle stateless (il firmware non riporta lo stato
  dell'aletta, quindi l'integrazione replica il pulsante dell'app ufficiale)
- **Posizionamento aletta** (opzionale, v0.2.0): porta l'aletta a qualsiasi
  angolo entro i limiti meccanici, usando un sensore di vibrazione/inclinazione
  [Aqara DJT11LM](https://www.zigbee2mqtt.io/devices/DJT11LM.html) incollato
  sull'aletta, con calibrazione automatica e ripetibile
- **Temperatura target** impostabile
- **Temperatura ambiente** letta dall'unità
- Entità **switch** per lo scheduler interno
- **Polling locale** (30 s) con riconnessione automatica
- **Setup BLE**: scansione, pairing ECDH, provisioning WiFi dall'interfaccia HA
- **IP manuale** con incolla credenziali o import da file

## Requisiti

- Home Assistant **2024.8.0** o superiore
- L'unità Unico deve essere sulla stessa LAN di HA (porta TCP 2000)
- Per il setup BLE: un adattatore Bluetooth accessibile a HA

## Installazione

### HACS (consigliata)

1. Apri HACS in Home Assistant
2. Menu tre puntini > **Repository personalizzati**
3. Aggiungi `https://github.com/dvbit/ha-olimpia-splendid-unico-aquara` con
   categoria **Integration**
4. Cerca "Olimpia Splendid Unico" e installa
5. Riavvia Home Assistant

### Manuale

1. Copia `custom_components/olimpia_splendid/` nella cartella
   `custom_components/` della tua istanza HA
2. Riavvia Home Assistant

## Configurazione

Vai su **Impostazioni > Dispositivi e servizi > Aggiungi integrazione** e cerca
**Olimpia Splendid Unico**. Sono disponibili due percorsi di configurazione.

### Opzione A: setup BLE (consigliato)

1. Scegli **"Nuovo dispositivo — setup BLE"**
2. L'integrazione cerca via Bluetooth le unità Unico nelle vicinanze
3. Seleziona il dispositivo dalla lista (compare come "OL01")
4. Inserisci **PIN dispositivo** (sull'etichetta dell'unità, default
   `12345678`), **SSID WiFi** e **password WiFi**
5. Attendi il completamento del pairing (fino a 60 secondi)
6. L'IP viene rilevato automaticamente

### Opzione B: pairing BLE esterno + IP manuale

Da usare quando HA non ha accesso al Bluetooth (VM, Docker senza passthrough,
macchina remota).

#### Passo 1 — esegui il tool di pairing BLE

Su una macchina con adattatore Bluetooth:

```bash
git clone https://github.com/dvbit/ha-olimpia-splendid-unico-aquara.git
cd ha-olimpia-splendid-unico-aquara/tools

pip install -r requirements.txt

python olimpia_ble.py scan
python olimpia_ble.py setup <MAC_ADDRESS> --pin <PIN> --ssid "TuaWiFi" --password "TuaPassword"
```

Al termine il tool salva le credenziali in `~/.olimpia/<IP>.json` e stampa l'IP
del dispositivo. Riferimento completo in [tools/README.md](tools/README.md).

#### Passo 2 — aggiungi l'integrazione in HA

1. **Impostazioni > Dispositivi e servizi > Aggiungi integrazione > Olimpia
   Splendid Unico**
2. Scegli **"Dispositivo configurato (inserisci IP)"**
3. Inserisci l'indirizzo IP
4. **Incolla il JSON delle credenziali** (consigliato): apri
   `~/.olimpia/<IP>.json` e copia l'intero contenuto nel campo "Credenziali JSON"
5. In alternativa lascia il campo vuoto se hai copiato il file sulla macchina HA

### Dopo la configurazione

- **Assegna un IP statico** al dispositivo tramite prenotazione DHCP del router.
- Verifica che l'entità climate mostri la temperatura ambiente.

## Controllo della posizione dell'aletta (opzionale)

L'Unico espone un unico **toggle stateless** per l'aletta: l'accensione avvia
un'oscillazione continua fra i due estremi meccanici, lo spegnimento ferma
l'aletta **esattamente dov'è**. L'unità non riporta mai l'angolo. Incollando un
sensore di vibrazione/inclinazione
[Aqara DJT11LM](https://www.zigbee2mqtt.io/devices/DJT11LM.html) sull'aletta,
l'integrazione può misurarne l'angolo e portarla in una posizione arbitraria.

### Come funziona — e perché è a tempo

Il DJT11LM **non trasmette in continuo**: i valori `angle_x` / `angle_y` /
`angle_z` si aggiornano solo alcuni secondi *dopo* un evento `tilt`, e le action
`vibration` non sono emesse più di una volta al minuto. Leggere l'angolo mentre
l'aletta è in movimento è quindi impossibile.

L'integrazione aggira il vincolo con un **modello a tempo, open-loop**:

1. La **calibrazione** misura i due angoli estremi e il tempo di corsa completa
   `T`, e campiona una curva `tempo → angolo` (la relazione in generale non è
   lineare).
2. Il **posizionamento** modella l'aletta come un'**onda triangolare** di
   periodo `2·T`. Note posizione e direzione correnti, la durata minima del
   movimento si calcola in forma chiusa; i rimbalzi agli estremi sono gestiti
   nativamente, quindi non esiste una "direzione sbagliata".
3. La **verifica**: dopo ogni fermata l'angolo viene riletto e il modello
   risincronizzato sul sensore, considerato la fonte autorevole. Se l'errore
   residuo supera ±3° viene tentata una correzione.

### Messa in servizio

1. Incolla il DJT11LM sull'aletta in modo che uno dei suoi assi segua la
   rotazione. Associa il sensore (Zigbee2MQTT o ZHA) **prima** di configurare
   questa integrazione.
2. Durante il config flow, lo step **"Sensore di inclinazione aletta"** chiede
   il dispositivo sensore e l'asse da monitorare. Lascia vuoto per saltare:
   tutto il resto continua a funzionare, resta disponibile solo il toggle cieco.
3. Apri **Impostazioni → Dispositivi e servizi → Olimpia Splendid Unico →
   Configura → Calibra l'aletta**.

> [!IMPORTANT]
> La calibrazione dura circa **3-5 minuti** e muove ripetutamente l'aletta. Il
> condizionatore deve essere **acceso**. Non usare l'unità nel frattempo. La
> calibrazione è ripetibile in qualsiasi momento, dalle opzioni o con l'entità
> pulsante `Calibrate flap`.

**Quale asse scegliere?** Osserva le entità del sensore in Strumenti per
sviluppatori muovendo l'aletta a mano: scegli l'asse con l'escursione maggiore.
Se la percentuale risulta invertita (100 % ad aletta chiusa), attiva
**"Inverti direzione"** nelle opzioni.

### Entità

| Entità | Tipo | Note |
| --- | --- | --- |
| `cover.<device>_flap` | Cover (damper) | Inclinazione 0-100 %, apri/chiudi/ferma/imposta |
| `sensor.<device>_flap_angle` | Sensore (°) | Rispecchia il sensore tilt; attributi: `position`, `source_entity` |
| `sensor.<device>_flap_calibration` | Sensore (diagnostico) | `uncalibrated` / `calibrating` / `calibrated` / `error` |
| `switch.<device>_continuous_swing` | Switch | Oscillazione continua |
| `button.<device>_calibrate_flap` | Pulsante (config) | Riesegue la calibrazione |
| `button.<device>_toggle_swing` | Pulsante | Toggle cieco originale, sempre disponibile |

Le entità dell'aletta vengono create **solo** se il sensore è configurato.

### Servizi

| Servizio | Campi | Descrizione |
| --- | --- | --- |
| `olimpia_splendid.set_flap_angle` | `angle` (°) | Porta l'aletta a un angolo assoluto, limitato agli estremi calibrati |
| `olimpia_splendid.calibrate_flap` | — | Esegue la calibrazione automatica |
| `olimpia_splendid.home_flap` | — | Risincronizza la posizione nota |

### Esempi di utilizzo

Aletta a metà apertura:

```yaml
action: cover.set_cover_tilt_position
target:
  entity_id: cover.olimpia_splendid_unico_flap
data:
  tilt_position: 50
```

Posizionamento a un angolo assoluto:

```yaml
action: olimpia_splendid.set_flap_angle
target:
  entity_id: cover.olimpia_splendid_unico_flap
data:
  angle: 12.5
```

Aletta verso l'alto in riscaldamento e verso il basso in raffrescamento:

```yaml
automation:
  - alias: Aletta segue il modo HVAC
    triggers:
      - trigger: state
        entity_id: climate.olimpia_splendid_unico
        attribute: hvac_action
    conditions:
      - condition: not
        conditions:
          - condition: state
            entity_id: climate.olimpia_splendid_unico
            state: "off"
    actions:
      - action: cover.set_cover_tilt_position
        target:
          entity_id: cover.olimpia_splendid_unico_flap
        data:
          tilt_position: >-
            {{ 20 if is_state_attr('climate.olimpia_splendid_unico',
                                   'hvac_action', 'heating') else 80 }}
    mode: single
```

Ricalibrazione semestrale notturna:

```yaml
automation:
  - alias: Calibrazione periodica aletta
    triggers:
      - trigger: time
        at: "03:30:00"
    conditions:
      - condition: template
        value_template: "{{ now().day == 1 and now().month in [1, 7] }}"
      - condition: state
        entity_id: climate.olimpia_splendid_unico
        state: fan_only
    actions:
      - action: olimpia_splendid.calibrate_flap
        target:
          entity_id: cover.olimpia_splendid_unico_flap
    mode: single
```

Notifica in caso di errore di calibrazione:

```yaml
automation:
  - alias: Errore calibrazione aletta
    triggers:
      - trigger: state
        entity_id: sensor.olimpia_splendid_unico_flap_calibration
        to: error
    actions:
      - action: notify.persistent_notification
        data:
          message: Calibrazione aletta fallita — controlla la batteria del sensore.
    mode: single
```

### Limiti noti

- **Il collo di bottiglia è il sensore.** Ogni fermata costa 5-12 s di attesa
  per il report dell'angolo: un singolo comando di posizionamento richiede
  quindi 10-30 s.
- **Nessun feedback durante il movimento**: l'angolo si aggiorna solo a fermata
  avvenuta.
- **Dopo un riavvio di Home Assistant** la posizione è ignota. Il primo comando
  innesca un homing automatico (~30-40 s): l'integrazione osserva il sensore per
  capire se l'aletta sta oscillando, la ferma se necessario, poi esegue una
  micro-corsa di 1 s per determinare la direzione di marcia.
- **Il posizionamento è bloccato a condizionatore spento**: in quello stato
  l'aletta non risponde ai comandi.
- L'attivazione dell'**oscillazione continua** invalida la posizione nota, che
  viene ristabilita al successivo comando di posizionamento.
- Precisione tipica **1-3°**. Gli scostamenti maggiori occasionali vengono
  rilevati da un controllo di deriva del modello e corretti al comando
  successivo.

## Logging

Log dettagliati in `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.olimpia_splendid: debug
```

- `DEBUG` — ogni toggle, tempo di corsa misurato, lettura angolo, step di
  calibrazione
- `INFO` — inizio/fine calibrazione e risultati, homing, posizionamento riuscito
- `WARNING` — report angolo mancante, sessione TCP degradata, posizione fuori
  tolleranza, deriva del modello
- `ERROR` — calibrazione interrotta

## Risoluzione dei problemi

### La scansione BLE non trova dispositivi

- Verifica che l'adattatore funzioni: `hcitool dev` deve elencarlo
- Avvicinati all'unità (portata BLE ~10 m)
- Il dispositivo compare come "OL01": prova `python olimpia_ble.py scan --name OL01`

### Il pairing BLE fallisce

- Controlla il PIN (etichetta dell'unità, default `12345678`)
- Usa `-v` per l'output dettagliato e capire quale passo fallisce

### "Nessuna credenziale trovata" con IP manuale

- Esegui prima il tool di pairing BLE
- Incolla il contenuto di `~/.olimpia/<IP>.json` nel campo "Credenziali JSON"

### La calibrazione dell'aletta fallisce

- Il condizionatore deve essere acceso
- Verifica che l'asse scelto sia quello con l'escursione maggiore
- Controlla la batteria del DJT11LM e il `linkquality` Zigbee (< 20 è debole)
- Nei log cerca `no travel limits found` (asse errato o aletta ferma) oppure
  `angle span too small` (asse errato)

### Il dispositivo risulta non disponibile

- Verifica raggiungibilità: `ping <IP>` e porta TCP 2000 aperta
- Dopo un riavvio del router l'IP può cambiare: usa una prenotazione DHCP

## Compatibilità

Testata su **Olimpia Splendid Unico Pro** con scheda WiFi **B1015**. Dovrebbe
funzionare con tutti i modelli Unico dotati della stessa scheda (stesso
protocollo e stessa app — Olimpia Splendid Unico v1.0.9).

**Come verificare la compatibilità:**

- L'unità è controllata dall'app **"Olimpia Splendid Unico"**
- In modalità pairing si annuncia via Bluetooth come **"OL01"**

## Specifica

Requisito consolidato da cui è stata sviluppata la funzione aletta (v0.2.0).

**R1 — Configurazione.** Nuovo step del config flow, saltabile, che chiede il
*dispositivo* sensore di inclinazione e l'*asse* (`x`/`y`/`z`) da monitorare;
l'entità `angle_<asse>` corrispondente viene risolta automaticamente. Se
saltato, l'integrazione si comporta come nella v0.1.x.

**R2 — Calibrazione automatica, ripetibile.** Due fasi, tutte le letture ad
aletta ferma:
*Fase A (ricerca)* — step a durata fissa con lettura dell'angolo dopo ogni
fermata, finché non si osservano due inversioni di corsa; produce `angle_min`,
`angle_max` e una stima grezza del tempo di corsa.
*Fase B (curva)* — parcheggio sull'estremo inferiore, poi 5 corse temporizzate
equispaziate registrando le coppie `(tempo, angolo)`; raffina estremi e tempo di
corsa e memorizza una curva lineare a tratti.
Avviabile da options flow, pulsante `Calibrate flap` e servizio
`calibrate_flap`. I risultati sono persistiti nelle opzioni del config entry.

**R3 — Modello di posizione.** Onda triangolare di periodo `2·T`,
risincronizzata sul sensore a ogni fermata. L'homing è eseguito pigramente al
primo comando dopo un riavvio.

**R4 — Entità.** Cover (`damper`, sola inclinazione), sensore angolo, sensore
diagnostico di calibrazione, switch oscillazione continua, pulsante di
calibrazione.

**R5 — Posizionamento.** Verifica post-stop con tolleranza **±3°** e **un**
ritentativo correttivo; bloccato a climate spento.

**R6 — Servizi.** `set_flap_angle`, `calibrate_flap`, `home_flap`.

**R7 — Logging.** Messaggi significativi su DEBUG / INFO / WARNING / ERROR.

**R8 — Rilascio.** Struttura compatibile HACS, traduzioni EN/IT/FR/ES/DE,
README in inglese e italiano.

## Documentazione del protocollo

Protocollo BLE e WiFi documentati in [PROTOCOL_BLE_WIFI.md](PROTOCOL_BLE_WIFI.md).

## Crediti

Integrazione originale di [@Daneel87](https://github.com/Daneel87). Controllo di
posizione dell'aletta e calibrazione con sensore di inclinazione a cura di
[@dvbit](https://github.com/dvbit).

## Licenza

MIT
