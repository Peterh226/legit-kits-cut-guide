# Quilt Tracker App

Flask web app for tracking quilt progress. Runs on the Raspberry Pi.

## Setup (Pi)

```bash
cd ~/legit-kits-cut-guide/quilt-tracker-app
python3 app.py
```

Access at `http://<pi-ip>:3001` from any browser on the network.

## Run on boot

Add to crontab (`crontab -e`):

```
@reboot sleep 10 && cd /home/peterh226/legit-kits-cut-guide/quilt-tracker-app && python3 app.py >> /home/peterh226/quilt-tracker.log 2>&1
```

## Update pattern data

On Windows, after running `extract.py`:
```
git add data/ && git commit -m "..." && git push
```

On Pi:
```
cd ~/legit-kits-cut-guide && git pull
```

The app picks up the new data on next restart.
