#!/usr/bin/env python3
"""
RCC Debug Log Analyzer
Analyze debug logs from the RCC Server Emulator to understand protocol patterns
"""

import os
import re
from pathlib import Path
from datetime import datetime

def analyze_tcp_logs(log_file):
    """Analyze TCP communication logs"""
    print(f"\n📡 Analyzing TCP Log: {log_file}")
    print("-" * 60)
    
    connections = {}
    messages = []
    
    with open(log_file, 'r') as f:
        for line in f:
            if "NEW CONNECTION:" in line:
                client_match = re.search(r'(tcp_client_[\d\.]+_\d+)', line)
                if client_match:
                    client_id = client_match.group(1)
                    connections[client_id] = {"connected": True, "logged_in": False}
                    print(f"🔌 New connection: {client_id}")
            
            elif "LOGIN SUCCESS:" in line:
                client_match = re.search(r'(tcp_client_[\d\.]+_\d+)', line)
                if client_match:
                    client_id = client_match.group(1)
                    if client_id in connections:
                        connections[client_id]["logged_in"] = True
                    print(f"✅ Login successful: {client_id}")
            
            elif "MESSAGE PARSED:" in line:
                service_match = re.search(r'Service=(\d+), RPC=(\d+), Length=(\d+)', line)
                client_match = re.search(r'(tcp_client_[\d\.]+_\d+)', line)
                if service_match and client_match:
                    messages.append({
                        "client": client_match.group(1),
                        "service": int(service_match.group(1)),
                        "rpc": int(service_match.group(2)),
                        "length": int(service_match.group(3))
                    })
            
            elif "DISCONNECT:" in line or "CLEANUP:" in line:
                client_match = re.search(r'(tcp_client_[\d\.]+_\d+)', line)
                if client_match:
                    client_id = client_match.group(1)
                    print(f"🔌 Disconnected: {client_id}")
    
    print(f"\n📊 Summary:")
    print(f"Total connections: {len(connections)}")
    print(f"Successful logins: {sum(1 for c in connections.values() if c.get('logged_in', False))}")
    print(f"Total messages: {len(messages)}")
    
    # Service usage statistics
    service_stats = {}
    for msg in messages:
        service_id = msg["service"]
        if service_id not in service_stats:
            service_stats[service_id] = {"count": 0, "rpcs": set()}
        service_stats[service_id]["count"] += 1
        service_stats[service_id]["rpcs"].add(msg["rpc"])
    
    print(f"\n📈 Service Usage:")
    for service_id, stats in service_stats.items():
        service_name = {0: "Login", 1: "Unknown1", 2: "Unknown2"}.get(service_id, f"Unknown{service_id}")
        print(f"  Service {service_id} ({service_name}): {stats['count']} messages, RPCs: {sorted(stats['rpcs'])}")

def analyze_binary_logs(log_file):
    """Analyze binary data logs"""
    print(f"\n🔍 Analyzing Binary Log: {log_file}")
    print("-" * 60)
    
    incoming_count = 0
    outgoing_count = 0
    patterns = {}
    
    with open(log_file, 'r') as f:
        content = f.read()
        
        # Count incoming vs outgoing
        incoming_count = content.count("INCOMING -")
        outgoing_count = content.count("OUTGOING -")
        
        # Find common patterns in hex data
        hex_matches = re.findall(r'Hex: ([a-fA-F0-9]+)', content)
        
        print(f"📥 Incoming messages: {incoming_count}")
        print(f"📤 Outgoing messages: {outgoing_count}")
        print(f"🔢 Total hex patterns found: {len(hex_matches)}")
        
        # Analyze common starting bytes (protocol headers)
        if hex_matches:
            first_bytes = {}
            for hex_data in hex_matches:
                if len(hex_data) >= 2:
                    first_byte = hex_data[:2]
                    first_bytes[first_byte] = first_bytes.get(first_byte, 0) + 1
            
            print(f"\n📋 Common first bytes (service IDs):")
            for byte, count in sorted(first_bytes.items(), key=lambda x: x[1], reverse=True):
                print(f"  0x{byte}: {count} occurrences")

def analyze_http_logs(log_file):
    """Analyze HTTP communication logs"""
    print(f"\n🌐 Analyzing HTTP Log: {log_file}")
    print("-" * 60)
    
    requests = 0
    responses = 0
    
    with open(log_file, 'r') as f:
        content = f.read()
        requests = content.count("Request:")
        responses = content.count("Response:")
    
    print(f"📥 HTTP Requests: {requests}")
    print(f"📤 HTTP Responses: {responses}")

def main():
    """Main analysis function"""
    print("🔍 RCC Debug Log Analyzer")
    print("=" * 60)
    
    debug_dir = Path("./debug_logs")
    
    if not debug_dir.exists():
        print("❌ No debug_logs directory found. Run the server with debug mode first.")
        return
    
    # Find all log files
    tcp_logs = list(debug_dir.glob("tcp_communication_*.log"))
    http_logs = list(debug_dir.glob("http_communication_*.log"))
    binary_logs = list(debug_dir.glob("binary_data_*.log"))
    
    if not any([tcp_logs, http_logs, binary_logs]):
        print("❌ No log files found in debug_logs directory.")
        return
    
    # Analyze the most recent logs
    if tcp_logs:
        latest_tcp = max(tcp_logs, key=os.path.getctime)
        analyze_tcp_logs(latest_tcp)
    
    if binary_logs:
        latest_binary = max(binary_logs, key=os.path.getctime)
        analyze_binary_logs(latest_binary)
    
    if http_logs:
        latest_http = max(http_logs, key=os.path.getctime)
        analyze_http_logs(latest_http)
    
    print(f"\n✅ Analysis complete!")
    print(f"📁 All logs available in: {debug_dir}")

if __name__ == "__main__":
    main()
