#!/usr/bin/env python3
"""
Bitcoin Sync Dashboard

A real-time web dashboard for monitoring Bitcoin Core initial block download (IBD) progress.
Features realistic ETA calculation that accounts for increasing block sizes over time.

No external dependencies - uses only Python standard library.

Usage:
    python3 bitcoin-sync-dashboard.py [options]

Options:
    --host HOST         Bitcoin node hostname/IP (default: localhost)
    --ssh-user USER     SSH username for remote nodes (default: current user)
    --port PORT         Dashboard web port (default: 8888)
    --rpc-host HOST     Bitcoin RPC host (default: 127.0.0.1)
    --rpc-port PORT     Bitcoin RPC port (default: 8332)
    --docker CONTAINER  Docker container name if bitcoind runs in Docker
    --help              Show this help message

Examples:
    # Local bitcoind
    python3 bitcoin-sync-dashboard.py

    # Remote node via SSH
    python3 bitcoin-sync-dashboard.py --host 192.168.1.100 --ssh-user ralf

    # Docker container
    python3 bitcoin-sync-dashboard.py --docker bitcoind

    # Remote Docker via SSH
    python3 bitcoin-sync-dashboard.py --host 192.168.1.100 --ssh-user ralf --docker bitcoind
"""

import subprocess
import json
import time
import http.server
import socketserver
import argparse
import os
from urllib.parse import urlparse
from collections import deque

# Configuration (set via command line args)
CONFIG = {
    'host': 'localhost',
    'ssh_user': os.environ.get('USER', 'root'),
    'port': 8888,
    'rpc_host': '127.0.0.1',
    'rpc_port': 8332,
    'docker_container': None,
}

# Store historical data for speed calculation
history = deque(maxlen=60)


def run_command(cmd, timeout=15):
    """Run a command locally or via SSH depending on config"""
    try:
        if CONFIG['host'] == 'localhost' or CONFIG['host'] == '127.0.0.1':
            # Local execution
            result = subprocess.run(
                cmd, shell=True,
                capture_output=True, text=True, timeout=timeout
            )
        else:
            # Remote execution via SSH
            ssh_cmd = [
                "ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
                f"{CONFIG['ssh_user']}@{CONFIG['host']}",
                cmd
            ]
            result = subprocess.run(
                ssh_cmd,
                capture_output=True, text=True, timeout=timeout
            )

        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except Exception:
        return None


def run_bitcoin_cli(rpc_cmd):
    """Run bitcoin-cli command"""
    if CONFIG['docker_container']:
        cmd = f"docker exec {CONFIG['docker_container']} bitcoin-cli {rpc_cmd}"
    else:
        cmd = f"bitcoin-cli -rpcconnect={CONFIG['rpc_host']} -rpcport={CONFIG['rpc_port']} {rpc_cmd}"

    return run_command(cmd)


def get_system_stats():
    """Fetch system stats from the node"""
    stats = {}

    # Get CPU info
    cpu_info = run_command("cat /proc/cpuinfo | grep 'model name' | head -1 | cut -d: -f2")
    if cpu_info:
        stats['cpu_model'] = cpu_info.strip()

    # Get CPU cores
    nproc = run_command("nproc")
    if nproc:
        try:
            stats['cpu_cores'] = int(nproc)
        except ValueError:
            pass

    # Get load average
    load_info = run_command("cat /proc/loadavg")
    if load_info:
        parts = load_info.split()
        if len(parts) >= 3:
            try:
                stats['load_1m'] = float(parts[0])
                stats['load_5m'] = float(parts[1])
                stats['load_15m'] = float(parts[2])
            except ValueError:
                pass

    # Get memory info
    mem_info = run_command("free -b | grep Mem")
    if mem_info:
        parts = mem_info.split()
        if len(parts) >= 7:
            try:
                stats['mem_total'] = int(parts[1])
                stats['mem_used'] = int(parts[2])
                stats['mem_free'] = int(parts[3])
                stats['mem_available'] = int(parts[6])
                stats['mem_percent'] = (stats['mem_used'] / stats['mem_total']) * 100
            except (ValueError, ZeroDivisionError):
                pass

    # Get uptime
    uptime_info = run_command("cat /proc/uptime")
    if uptime_info:
        try:
            stats['uptime_seconds'] = float(uptime_info.split()[0])
            stats['uptime_human'] = format_duration(stats['uptime_seconds'])
        except (ValueError, IndexError):
            pass

    return stats


def get_bitcoin_stats():
    """Fetch all Bitcoin stats"""
    stats = {}

    # Get blockchain info
    info = run_bitcoin_cli("getblockchaininfo")
    if info:
        try:
            data = json.loads(info)
            stats['height'] = data.get('blocks', 0)
            stats['headers'] = data.get('headers', 0)
            stats['progress'] = data.get('verificationprogress', 0) * 100
            stats['pruned'] = data.get('pruned', False)
            stats['size_on_disk'] = data.get('size_on_disk', 0)
            stats['chain'] = data.get('chain', 'unknown')
            stats['difficulty'] = data.get('difficulty', 0)
            stats['ibd'] = data.get('initialblockdownload', True)
        except json.JSONDecodeError:
            pass

    # Get network info
    netinfo = run_bitcoin_cli("getnetworkinfo")
    if netinfo:
        try:
            data = json.loads(netinfo)
            stats['version'] = data.get('subversion', 'unknown')
            stats['connections'] = data.get('connections', 0)
            stats['connections_in'] = data.get('connections_in', 0)
            stats['connections_out'] = data.get('connections_out', 0)
        except json.JSONDecodeError:
            pass

    stats['timestamp'] = time.time()
    return stats


def calculate_realistic_eta(current_height, target_height, current_speed):
    """
    Calculate realistic ETA based on block eras.

    Block processing speed varies dramatically by era due to block size:
    - Era 1 (0-200k, 2009-2012): Tiny blocks, ~1KB avg - baseline speed
    - Era 2 (200k-400k, 2012-2016): Small blocks, ~200KB avg - 2x slower
    - Era 3 (400k-500k, 2016-2018): Growing blocks, ~800KB avg - 5x slower
    - Era 4 (500k-700k, 2018-2021): Full blocks, ~1.2MB avg - 8x slower
    - Era 5 (700k-850k, 2021-2023): SegWit full, ~1.5MB avg - 12x slower
    - Era 6 (850k+, 2023+): Inscriptions/Ordinals, ~2MB avg - 20x slower
    """

    # Define eras: (start_height, end_height, slowdown_factor relative to era 1)
    eras = [
        (0,       200000,  1.0),    # 2009-2012: tiny blocks
        (200000,  400000,  2.0),    # 2012-2016: small blocks
        (400000,  500000,  5.0),    # 2016-2018: growing blocks
        (500000,  700000,  8.0),    # 2018-2021: full blocks
        (700000,  850000,  12.0),   # 2021-2023: SegWit era
        (850000,  9999999, 20.0),   # 2023+: Inscriptions era
    ]

    # Determine which era we're currently in to get base speed
    current_era_factor = 1.0
    for start, end, factor in eras:
        if start <= current_height < end:
            current_era_factor = factor
            break

    # Base speed normalized to era 1
    base_speed = current_speed * current_era_factor

    # Calculate time for each remaining era segment
    total_seconds = 0

    for start, end, factor in eras:
        # Calculate overlap with remaining blocks
        segment_start = max(start, current_height)
        segment_end = min(end, target_height)

        if segment_start < segment_end:
            blocks_in_segment = segment_end - segment_start
            # Speed in this era = base_speed / factor
            era_speed = base_speed / factor
            if era_speed > 0:
                segment_time = blocks_in_segment / era_speed
                total_seconds += segment_time

    return total_seconds


def calculate_speed_and_eta(stats):
    """Calculate sync speed and ETA based on history with realistic slowdown model"""
    if 'height' not in stats:
        return stats

    current_height = stats['height']
    current_time = stats['timestamp']

    history.append((current_time, current_height))

    if len(history) >= 2:
        # Short-term speed (last 10 samples)
        short_window = list(history)[-10:]
        if len(short_window) >= 2:
            time_diff = short_window[-1][0] - short_window[0][0]
            block_diff = short_window[-1][1] - short_window[0][1]
            if time_diff > 0:
                stats['speed_short'] = block_diff / time_diff
            else:
                stats['speed_short'] = 0

        # Long-term speed
        time_diff = history[-1][0] - history[0][0]
        block_diff = history[-1][1] - history[0][1]
        if time_diff > 0:
            stats['speed_long'] = block_diff / time_diff
        else:
            stats['speed_long'] = 0

        # Realistic ETA calculation accounting for block size growth
        target_height = stats.get('headers', 0)
        blocks_remaining = target_height - current_height

        if blocks_remaining > 0 and stats.get('speed_short', 0) > 0:
            eta_seconds = calculate_realistic_eta(
                current_height,
                target_height,
                stats['speed_short']
            )
            stats['eta_seconds'] = eta_seconds
            stats['eta_human'] = format_duration(eta_seconds)
        else:
            stats['eta_human'] = 'Calculating...'
    else:
        stats['speed_short'] = 0
        stats['speed_long'] = 0
        stats['eta_human'] = 'Calculating...'

    stats['blocks_remaining'] = stats.get('headers', 0) - current_height
    return stats


def format_duration(seconds):
    """Format seconds into human readable duration"""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds/60)}m {int(seconds%60)}s"
    elif seconds < 86400:
        hours = int(seconds / 3600)
        mins = int((seconds % 3600) / 60)
        return f"{hours}h {mins}m"
    else:
        days = int(seconds / 86400)
        hours = int((seconds % 86400) / 3600)
        return f"{days}d {hours}h"


def format_bytes(bytes_val):
    """Format bytes into human readable size"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} PB"


DASHBOARD_HTML = '''<!DOCTYPE html>
<html>
<head>
    <title>Bitcoin Sync Dashboard</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #eee;
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 {
            text-align: center;
            margin-bottom: 30px;
            color: #f7931a;
            text-shadow: 0 0 20px rgba(247, 147, 26, 0.3);
        }
        h2 {
            color: #888;
            margin-bottom: 20px;
            font-weight: normal;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .card {
            background: rgba(255,255,255,0.05);
            border-radius: 15px;
            padding: 25px;
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.1);
            transition: transform 0.3s, box-shadow 0.3s;
        }
        .card:hover {
            transform: translateY(-5px);
            box-shadow: 0 10px 40px rgba(0,0,0,0.3);
        }
        .card-title {
            font-size: 0.85em;
            color: #888;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 10px;
        }
        .card-value {
            font-size: 2em;
            font-weight: bold;
            color: #fff;
        }
        .card-sub {
            font-size: 0.9em;
            color: #666;
            margin-top: 5px;
        }
        .progress-container {
            background: rgba(255,255,255,0.1);
            border-radius: 15px;
            padding: 30px;
            margin-bottom: 30px;
        }
        .progress-bar-bg {
            background: rgba(0,0,0,0.3);
            border-radius: 10px;
            height: 30px;
            overflow: hidden;
            margin: 20px 0;
        }
        .progress-bar {
            background: linear-gradient(90deg, #f7931a 0%, #ffcc00 100%);
            height: 100%;
            border-radius: 10px;
            transition: width 0.5s ease;
            box-shadow: 0 0 20px rgba(247, 147, 26, 0.5);
        }
        .progress-text {
            display: flex;
            justify-content: space-between;
            font-size: 1.2em;
            flex-wrap: wrap;
            gap: 10px;
        }
        .status-dot {
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            margin-right: 8px;
            animation: pulse 2s infinite;
        }
        .status-syncing { background: #f7931a; }
        .status-synced { background: #00ff00; }
        .status-error { background: #ff0000; }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .footer {
            text-align: center;
            color: #555;
            margin-top: 30px;
            font-size: 0.9em;
        }
        .footer a {
            color: #f7931a;
            text-decoration: none;
        }
        .highlight { color: #f7931a; }
        @media (max-width: 600px) {
            .card-value { font-size: 1.5em; }
            .progress-text { font-size: 1em; }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>&#8383; Bitcoin Sync Dashboard</h1>

        <div class="progress-container">
            <div class="progress-text">
                <span><span class="status-dot status-syncing" id="statusDot"></span><span id="status">Syncing...</span></span>
                <span id="progressPct">0%</span>
            </div>
            <div class="progress-bar-bg">
                <div class="progress-bar" id="progressBar" style="width: 0%"></div>
            </div>
            <div class="progress-text">
                <span>Block <span id="currentBlock" class="highlight">0</span> of <span id="targetBlock">0</span></span>
                <span>ETA: <span id="eta" class="highlight">Calculating...</span></span>
            </div>
        </div>

        <div class="grid">
            <div class="card">
                <div class="card-title">Blocks Remaining</div>
                <div class="card-value" id="blocksRemaining">-</div>
                <div class="card-sub">blocks to sync</div>
            </div>
            <div class="card">
                <div class="card-title">Sync Speed</div>
                <div class="card-value" id="syncSpeed">-</div>
                <div class="card-sub">blocks per second</div>
            </div>
            <div class="card">
                <div class="card-title">Disk Usage</div>
                <div class="card-value" id="diskUsage">-</div>
                <div class="card-sub" id="pruneStatus">-</div>
            </div>
            <div class="card">
                <div class="card-title">Connections</div>
                <div class="card-value" id="connections">-</div>
                <div class="card-sub" id="connectionDetails">in/out</div>
            </div>
            <div class="card">
                <div class="card-title">Network</div>
                <div class="card-value" id="network">-</div>
                <div class="card-sub" id="version">-</div>
            </div>
            <div class="card">
                <div class="card-title">Difficulty</div>
                <div class="card-value" id="difficulty">-</div>
                <div class="card-sub">current difficulty</div>
            </div>
        </div>

        <h2>System Resources</h2>
        <div class="grid">
            <div class="card">
                <div class="card-title">CPU</div>
                <div class="card-value" id="cpuLoad">-</div>
                <div class="card-sub" id="cpuModel">-</div>
            </div>
            <div class="card">
                <div class="card-title">Memory</div>
                <div class="card-value" id="memUsage">-</div>
                <div class="card-sub" id="memDetails">-</div>
            </div>
            <div class="card">
                <div class="card-title">Uptime</div>
                <div class="card-value" id="uptime">-</div>
                <div class="card-sub">node uptime</div>
            </div>
        </div>

        <div class="footer">
            <p>Auto-refreshes every 5 seconds | <span id="lastUpdate">-</span></p>
            <p style="margin-top: 10px;"><a href="https://github.com/ralfyang/bitcoin-sync-dashboard" target="_blank">GitHub</a></p>
        </div>
    </div>

    <script>
        function formatNumber(num) {
            return num.toLocaleString();
        }

        function formatCompact(num) {
            if (num >= 1e12) return (num / 1e12).toFixed(2) + 'T';
            if (num >= 1e9) return (num / 1e9).toFixed(2) + 'B';
            if (num >= 1e6) return (num / 1e6).toFixed(2) + 'M';
            if (num >= 1e3) return (num / 1e3).toFixed(1) + 'K';
            return num.toLocaleString();
        }

        function updateDashboard() {
            fetch('/api/stats')
                .then(r => r.json())
                .then(data => {
                    if (data.error) {
                        document.getElementById('status').textContent = 'Connection Error';
                        document.getElementById('statusDot').className = 'status-dot status-error';
                        return;
                    }

                    const progress = data.progress || 0;
                    document.getElementById('progressBar').style.width = Math.min(progress, 100) + '%';
                    document.getElementById('progressPct').textContent = progress.toFixed(4) + '%';

                    if (data.ibd) {
                        document.getElementById('status').textContent = 'Initial Block Download';
                        document.getElementById('statusDot').className = 'status-dot status-syncing';
                    } else {
                        document.getElementById('status').textContent = 'Synced';
                        document.getElementById('statusDot').className = 'status-dot status-synced';
                    }

                    document.getElementById('currentBlock').textContent = formatNumber(data.height || 0);
                    document.getElementById('targetBlock').textContent = formatNumber(data.headers || 0);
                    document.getElementById('blocksRemaining').textContent = formatNumber(data.blocks_remaining || 0);

                    const speed = data.speed_short || 0;
                    document.getElementById('syncSpeed').textContent = speed.toFixed(1);
                    document.getElementById('eta').textContent = data.eta_human || 'Calculating...';

                    document.getElementById('diskUsage').textContent = data.size_on_disk_human || '-';
                    document.getElementById('pruneStatus').textContent = data.pruned ? 'pruned mode' : 'full node';

                    document.getElementById('connections').textContent = data.connections || 0;
                    document.getElementById('connectionDetails').textContent =
                        (data.connections_in || 0) + ' in / ' + (data.connections_out || 0) + ' out';

                    document.getElementById('network').textContent = (data.chain || 'main').toUpperCase();
                    document.getElementById('version').textContent = data.version || '-';

                    document.getElementById('difficulty').textContent = formatCompact(data.difficulty || 0);

                    // System stats
                    if (data.load_1m !== undefined) {
                        var loadPct = ((data.load_1m / (data.cpu_cores || 1)) * 100).toFixed(0);
                        document.getElementById('cpuLoad').textContent = loadPct + '%';
                        document.getElementById('cpuModel').textContent = (data.cpu_model || 'Unknown') + ' (' + (data.cpu_cores || '?') + ' cores)';
                    }

                    if (data.mem_percent !== undefined) {
                        document.getElementById('memUsage').textContent = data.mem_percent.toFixed(0) + '%';
                        document.getElementById('memDetails').textContent = (data.mem_used_human || '-') + ' / ' + (data.mem_total_human || '-');
                    }

                    if (data.uptime_human) {
                        document.getElementById('uptime').textContent = data.uptime_human;
                    }

                    document.getElementById('lastUpdate').textContent = 'Last updated: ' + new Date().toLocaleTimeString();
                })
                .catch(err => {
                    document.getElementById('status').textContent = 'Error';
                    document.getElementById('statusDot').className = 'status-dot status-error';
                });
        }

        updateDashboard();
        setInterval(updateDashboard, 5000);
    </script>
</body>
</html>'''


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress logging

    def do_GET(self):
        path = urlparse(self.path).path

        if path == '/' or path == '/index.html':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode())

        elif path == '/api/stats':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()

            stats = get_bitcoin_stats()
            if stats and 'height' in stats:
                stats = calculate_speed_and_eta(stats)
                stats['size_on_disk_human'] = format_bytes(stats.get('size_on_disk', 0))
                # Add system stats
                sys_stats = get_system_stats()
                stats.update(sys_stats)
                if 'mem_total' in stats:
                    stats['mem_total_human'] = format_bytes(stats['mem_total'])
                    stats['mem_used_human'] = format_bytes(stats['mem_used'])
            else:
                stats = {'error': 'Could not fetch Bitcoin stats'}

            self.wfile.write(json.dumps(stats).encode())

        elif path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')

        else:
            self.send_response(404)
            self.end_headers()


def parse_args():
    parser = argparse.ArgumentParser(
        description='Bitcoin Sync Dashboard - Real-time monitoring for Bitcoin Core IBD',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s                                    # Local bitcoind
  %(prog)s --host 192.168.1.100 --ssh-user pi # Remote node via SSH
  %(prog)s --docker bitcoind                  # Local Docker container
  %(prog)s --host 10.0.0.5 --docker bitcoin   # Remote Docker via SSH
        '''
    )
    parser.add_argument('--host', default='localhost',
                        help='Bitcoin node hostname/IP (default: localhost)')
    parser.add_argument('--ssh-user', default=os.environ.get('USER', 'root'),
                        help='SSH username for remote nodes')
    parser.add_argument('--port', type=int, default=8888,
                        help='Dashboard web port (default: 8888)')
    parser.add_argument('--rpc-host', default='127.0.0.1',
                        help='Bitcoin RPC host (default: 127.0.0.1)')
    parser.add_argument('--rpc-port', type=int, default=8332,
                        help='Bitcoin RPC port (default: 8332)')
    parser.add_argument('--docker', metavar='CONTAINER',
                        help='Docker container name if bitcoind runs in Docker')
    return parser.parse_args()


def main():
    args = parse_args()

    CONFIG['host'] = args.host
    CONFIG['ssh_user'] = args.ssh_user
    CONFIG['port'] = args.port
    CONFIG['rpc_host'] = args.rpc_host
    CONFIG['rpc_port'] = args.rpc_port
    CONFIG['docker_container'] = args.docker

    print(f"Bitcoin Sync Dashboard")
    print(f"======================")
    print(f"Node: {CONFIG['host']}")
    if CONFIG['docker_container']:
        print(f"Docker: {CONFIG['docker_container']}")
    print(f"Dashboard: http://0.0.0.0:{CONFIG['port']}")
    print()

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", CONFIG['port']), DashboardHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down...")


if __name__ == '__main__':
    main()
