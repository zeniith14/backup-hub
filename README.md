# Backup Hub

Dashboard web per i backup `rclone` di un homelab (pensata per Proxmox + Google Drive cifrato).
Gira in un container separato, esegue i job, ti mostra cosa è successo e **quando sarà il prossimo**.

- **Prossimo backup** con conto alla rovescia e durata stimata (mediana delle ultime esecuzioni riuscite)
- **Progresso in tempo reale**: percentuale, dati copiati, velocità, file, tempo rimanente, file in trasferimento
- **Pulsante «Esegui ora»** (tutti i job o uno solo), **dry-run** per provare senza toccare niente, **Annulla**
- **Storico** con esito, durata, quantità di dati, errori e **log completo** di ogni esecuzione
- **Orario modificabile dalla dashboard** (cron, con anteprima delle prossime esecuzioni)
- **Notifiche** quando un backup fallisce (ntfy, Telegram o webhook) — opzionali
- Un solo processo Python (FastAPI + SQLite), nessuna dipendenza esterna a runtime, UI chiara/scura e utilizzabile da telefono

## Come funziona

Backup Hub lancia `rclone sync` (o `copy`) per ogni job definito nella config e legge l'output JSON di rclone
(`--use-json-log`) per mostrare l'avanzamento. Le sorgenti possono essere remote SFTP verso le tue VM,
quindi il container di backup non deve montare dischi né dipende dalle USB.

Protezioni utili per un backup affidabile:
- `max_delete` per job: se la sorgente sparisce (disco staccato) rclone si ferma invece di svuotare la destinazione
- un solo backup alla volta; un backup automatico che parte mentre ne gira uno manuale viene saltato
- un backup è «riuscito» solo se rclone esce con codice 0; gli errori finiscono nello storico e nelle notifiche
- le esecuzioni rimaste a metà per un riavvio compaiono come «Interrotto»

## Installazione (container Debian/Ubuntu, come root)

```bash
git clone https://github.com/zeniith14/backup-hub.git
cd backup-hub
./deploy/install.sh
```

Lo script installa Python, crea l'ambiente virtuale, genera `/etc/backup-hub/config.yml` con una **password casuale**
(la stampa alla fine) e avvia il servizio `backup-hub` sulla porta **8080**.

Prerequisito: `rclone` installato e con i remote già configurati per l'utente root del container
(`rclone listremotes`). I remote SFTP usano la chiave in `/root/.ssh`.

Poi apri `http://<ip-del-container>:8080` (utente qualsiasi, password dal config).

### Configurazione

Modifica `/etc/backup-hub/config.yml` (vedi `config.example.yml`, commentato) e riavvia:

```bash
systemctl restart backup-hub
```

L'orario del backup si cambia anche dalla dashboard («Modifica orario»): quel valore ha la precedenza su quello del file.

### Aggiornare

```bash
backup-hub-update      # git pull + dipendenze + restart
```

### Se avevi un vecchio script con cron

Disattivalo, altrimenti due backup scriverebbero sulla stessa destinazione:

```bash
rm -f /etc/cron.d/backup
pgrep -a rclone        # nessun rclone deve essere in corso
```

## Sicurezza

- La dashboard è pensata per la **rete locale**. Autenticazione HTTP Basic con una sola password: se la esponi fuori casa,
  mettila dietro HTTPS (es. Cloudflare Tunnel + Access) e non esporla direttamente.
- Senza `auth.password` la dashboard è aperta a tutti: l'interfaccia lo segnala con un avviso.
- Il servizio gira come root perché usa la config rclone e le chiavi SSH di root. Il container deve contenere solo questo.

## Sviluppo

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest httpx
.venv/bin/python -m pytest -q          # i test usano un rclone finto (tests/fake_rclone.py)
BACKUP_HUB_CONFIG=./config.yml .venv/bin/python -m app
```

## API (per integrazioni)

| Metodo | Percorso | Cosa fa |
|---|---|---|
| GET | `/api/status` | stato completo (scheduler, job, backup in corso) |
| GET | `/api/runs?limit=50&job=` | storico |
| GET | `/api/runs/{id}/log` | log di un'esecuzione |
| POST | `/api/run` `{"jobs": null, "dry_run": false}` | avvia i backup |
| POST | `/api/cancel` | annulla il backup in corso |
| POST | `/api/schedule` `{"cron": "30 3 * * *", "enabled": true}` | cambia l'orario |
