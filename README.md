# Bitcoin Sync Dashboard

A real-time web dashboard for monitoring Bitcoin Core initial block download (IBD) progress with **realistic ETA calculation** that accounts for increasing block sizes over time.

![Dashboard Screenshot](screenshot.png)

## Features

- **Real-time sync progress** with visual progress bar
- **Realistic ETA calculation** - accounts for block size growth across different eras
- **System resource monitoring** - CPU load, memory usage, uptime
- **Network stats** - peer connections, difficulty, chain info
- **No external dependencies** - pure Python standard library
- **Remote monitoring** - monitor nodes via SSH
- **Docker support** - works with containerized Bitcoin Core
- **Mobile responsive** - works on phones and tablets

## Why Realistic ETA?

Most sync monitors assume constant block processing speed. In reality, Bitcoin blocks have grown dramatically over time:

| Era | Blocks | Years | Avg Block Size | Relative Speed |
|-----|--------|-------|----------------|----------------|
| 1 | 0-200K | 2009-2012 | ~1 KB | 1x (fastest) |
| 2 | 200K-400K | 2012-2016 | ~200 KB | 2x slower |
| 3 | 400K-500K | 2016-2018 | ~800 KB | 5x slower |
| 4 | 500K-700K | 2018-2021 | ~1.2 MB | 8x slower |
| 5 | 700K-850K | 2021-2023 | ~1.5 MB | 12x slower |
| 6 | 850K+ | 2023+ | ~2 MB | 20x slower |

A naive linear estimate might say "7 hours remaining" when the realistic estimate is "2+ days".

## Installation

```bash
# Clone the repository
git clone https://github.com/ralfyang/bitcoin-sync-dashboard.git
cd bitcoin-sync-dashboard

# Make executable (optional)
chmod +x bitcoin-sync-dashboard.py
```

**Requirements:** Python 3.6+ (no external packages needed)

## Usage

### Local Bitcoin Node

```bash
# If bitcoin-cli is in PATH and RPC is on default port
python3 bitcoin-sync-dashboard.py

# Custom RPC settings
python3 bitcoin-sync-dashboard.py --rpc-host 127.0.0.1 --rpc-port 8332
```

### Remote Node via SSH

```bash
# Monitor a remote node (requires SSH key authentication)
python3 bitcoin-sync-dashboard.py --host 192.168.1.100 --ssh-user pi
```

### Docker Container

```bash
# Local Docker container named 'bitcoind'
python3 bitcoin-sync-dashboard.py --docker bitcoind

# Remote Docker container via SSH
python3 bitcoin-sync-dashboard.py --host 192.168.1.100 --ssh-user ralf --docker bitcoind
```

### Custom Dashboard Port

```bash
python3 bitcoin-sync-dashboard.py --port 9000
```

## Command Line Options

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | localhost | Bitcoin node hostname/IP |
| `--ssh-user` | current user | SSH username for remote nodes |
| `--port` | 8888 | Dashboard web server port |
| `--rpc-host` | 127.0.0.1 | Bitcoin RPC host |
| `--rpc-port` | 8332 | Bitcoin RPC port |
| `--docker` | none | Docker container name |

## Running as a Service

### systemd (Linux)

Create `/etc/systemd/system/btc-dashboard.service`:

```ini
[Unit]
Description=Bitcoin Sync Dashboard
After=network.target

[Service]
Type=simple
User=bitcoin
ExecStart=/usr/bin/python3 /opt/bitcoin-sync-dashboard/bitcoin-sync-dashboard.py --docker bitcoind
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable btc-dashboard
sudo systemctl start btc-dashboard
```

### Docker

```dockerfile
FROM python:3.11-alpine
WORKDIR /app
COPY bitcoin-sync-dashboard.py .
EXPOSE 8888
CMD ["python3", "bitcoin-sync-dashboard.py", "--host", "bitcoind"]
```

## API Endpoints

The dashboard exposes a JSON API:

- `GET /` - Dashboard HTML page
- `GET /api/stats` - JSON stats (height, progress, ETA, system info)
- `GET /health` - Health check endpoint

### Example API Response

```json
{
  "height": 203094,
  "headers": 934491,
  "progress": 0.58,
  "pruned": true,
  "size_on_disk": 3905097013,
  "chain": "main",
  "difficulty": 3054627.53,
  "ibd": true,
  "version": "/Satoshi:27.0.0/",
  "connections": 10,
  "connections_in": 0,
  "connections_out": 10,
  "speed_short": 21.87,
  "eta_human": "1d 13h",
  "blocks_remaining": 731397,
  "cpu_model": "Intel Atom D510 @ 1.66GHz",
  "cpu_cores": 4,
  "load_1m": 2.13,
  "mem_percent": 52.26,
  "uptime_human": "49m 9s"
}
```

## Troubleshooting

### "Could not fetch Bitcoin stats"

- Ensure `bitcoin-cli` is accessible and RPC is enabled
- Check that bitcoind is running
- For Docker: ensure container name is correct

### SSH connection issues

- Ensure SSH key authentication is set up (no password prompts)
- Test with: `ssh user@host "bitcoin-cli getblockchaininfo"`

### Port already in use

- Use `--port` to specify a different port
- Or kill the existing process: `fuser -k 8888/tcp`

## License

MIT License - feel free to use, modify, and distribute.

## Contributing

Pull requests welcome! Please open an issue first to discuss major changes.

## Credits

Created for monitoring Bitcoin Core IBD on low-power hardware (Intel Atom, Raspberry Pi, etc.)
