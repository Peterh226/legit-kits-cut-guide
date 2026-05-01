# Quilt Tracker App

Flask web app for tracking quilt progress. Runs on the Raspberry Pi.

## Setup (Pi)

```bash
pm2 start ~/legit-kits-cut-guide/quilt-tracker-app/app.py --name quilttracker --interpreter python3
pm2 save
```

Access at `http://<pi-ip>:3001` from any browser on the network.

## Update workflow

```bash
cd ~/legit-kits-cut-guide && git pull && pm2 restart quilttracker
```

Then shift-refresh in the browser for CSS/JS changes.

## Add a new quilt

On the development machine, after running `extract.py`:
```bash
git add quilts/<quilt-id>/ && git commit -m "Add <quilt-name> data" && git push
```

On Pi:
```bash
cd ~/legit-kits-cut-guide && git pull && pm2 restart quilttracker
```

The app auto-discovers the new quilt from the `quilts/` folder on startup.
