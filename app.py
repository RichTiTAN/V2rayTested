import os
import sys
import time
import json
import base64
import urllib.parse
import subprocess
import threading
import atexit
import glob
import requests
import asyncio
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from github import Github, Auth

# ==========================================
# CONFIGURATION
# ==========================================
GITHUB_TOKEN = "INSERT-YOUR-GITHUB-TOKEN"
REPO_NAME = "USERNAME/REPO" 
FILE_PATH_IN_REPO = "working_configs.txt"

XRAY_EXE = "xray.exe" if os.name == 'nt' else "./xray"

SUBSCRIPTIONS = [
    "SUB01",
    "SUB02",
    "SUB03",
    "SUB04"
]

TCP_PRECHECK_CONCURRENCY = 1500 
XRAY_THREADS = 250                
TEST_TIMEOUT = 6                
LOOP_INTERVAL = 7200            

# ==========================================
# LEAK PREVENTION
# ==========================================
active_processes = []
process_lock = threading.Lock()

def cleanup_processes():
    print("\n[!] Shutting down safely... Cleaning up processes and files.")
    
    os.system("taskkill /f /im xray.exe >nul 2>&1" if os.name == 'nt' else "pkill -f xray >/dev/null 2>&1")
    
    with process_lock:
        for p in active_processes:
            try:
                p.terminate()
                p.wait(timeout=1)
            except:
                p.kill()
        active_processes.clear()
    for temp_file in glob.glob("temp_*.json"):
        try: os.remove(temp_file)
        except: pass

atexit.register(cleanup_processes)

# ==========================================
# PARSING LOGIC
# ==========================================
def rename_link(link, new_name):
    """Safely renames VLESS configs by replacing the URI fragment."""
    safe_name = urllib.parse.quote(new_name)
    
    if '#' in link:
        base_link = link.split('#', 1)[0]
        return f"{base_link}#{safe_name}"
    else:
        return f"{link}#{safe_name}"

def parse_link_details(link):
    """Extracts host and port for Phase 1 TCP pre-check."""
    try:
        if link.startswith("vless://"):
            parsed = urllib.parse.urlparse(link)
            return parsed.hostname, (parsed.port or 443)
    except:
        return None, None
    return None, None

def parse_to_outbound(link):
    """Converts verified working links into Xray format (VLESS Reality)."""
    try:
        if link.startswith("vless://"):
            parsed = urllib.parse.urlparse(link)
            query_params = urllib.parse.parse_qs(parsed.query)
            params = {k: v[0] for k, v in query_params.items()}
            
            user_entry = {"id": parsed.username, "encryption": "none"}
            if "flow" in params: user_entry["flow"] = params["flow"]

            outbound = {
                "protocol": "vless",
                "settings": {"vnext": [{"address": parsed.hostname, "port": int(parsed.port or 443), "users": [user_entry]}]},
                "streamSettings": {"network": params.get("type", "tcp")}
            }

            network_type = params.get("type", "tcp")
            if network_type == "ws": outbound["streamSettings"]["wsSettings"] = {"path": params.get("path", "/")}
            elif network_type == "grpc": outbound["streamSettings"]["grpcSettings"] = {"serviceName": params.get("serviceName", "")}

            security = params.get("security", "none")
            if security == "reality":
                outbound["streamSettings"]["security"] = "reality"
                outbound["streamSettings"]["realitySettings"] = {
                    "show": False, "fingerprint": params.get("fp", "chrome"),
                    "serverName": params.get("sni", ""), "publicKey": params.get("pbk", ""),
                    "shortId": params.get("sid", ""), "spiderX": params.get("spx", "/")
                }
            elif security == "tls":
                outbound["streamSettings"]["security"] = "tls"
                outbound["streamSettings"]["tlsSettings"] = {"serverName": params.get("sni", ""), "fingerprint": params.get("fp", "chrome")}
            return outbound
    except:
        return None
    return None

# ==========================================
# PHASE 1: ASYNC TCP PRE-CHECK
# ==========================================
async def check_tcp_port(semaphore, link):
    host, port = parse_link_details(link)
    if not host or not port:
        return None
    
    async with semaphore:
        try:
            fut = asyncio.open_connection(host, port)
            reader, writer = await asyncio.wait_for(fut, timeout=2.5)
            writer.close()
            await writer.wait_closed()
            return link
        except:
            return None

async def run_tcp_precheck(links):
    print(f"[*] Phase 1: Filtering {len(links)} configs via Async TCP Pre-Check...")
    semaphore = asyncio.BoundedSemaphore(TCP_PRECHECK_CONCURRENCY)
    tasks = [check_tcp_port(semaphore, link) for link in links]
    
    results = await asyncio.gather(*tasks)
    survived = [r for r in results if r is not None]
    print(f"[+] Phase 1 Complete! Discarded dead links. {len(survived)} configs survived to Phase 2.")
    return survived

# ==========================================
# PHASE 2: DETAILED XRAY TESTING
# ==========================================
def test_single_config(link, port):
    time.sleep(random.uniform(0.0, 2.0))

    outbound = parse_to_outbound(link)
    if not outbound: return None

    config = {
        "log": {"loglevel": "none"},
        "inbounds": [{"port": port, "listen": "127.0.0.1", "protocol": "socks"}],
        "outbounds": [outbound]
    }

    config_file = f"temp_{port}.json"
    with open(config_file, 'w') as f:
        json.dump(config, f)

    proc = subprocess.Popen([XRAY_EXE, "-c", config_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with process_lock: active_processes.append(proc)

    time.sleep(1) 

    is_working = False
    try:
        proxies = {"http": f"socks5h://127.0.0.1:{port}", "https": f"socks5h://127.0.0.1:{port}"}
        r = requests.get("http://www.google.com/generate_204", proxies=proxies, timeout=TEST_TIMEOUT)
        if r.status_code == 204:
            is_working = True
    except:
        pass

    with process_lock:
        if proc in active_processes: active_processes.remove(proc)
    proc.terminate()
    proc.wait()
    try: os.remove(config_file)
    except: pass

    if is_working:
        return link
    return None

# ==========================================
# PHASE 3: GEOIP LOCATION FETCHING
# ==========================================
def geoip_single_config(link, port):
    """Routes a request through the working proxy to get the real exit-node location."""
    
    # STAGGER THE STARTUP: Prevent Phase 3 fork bomb
    time.sleep(random.uniform(0.0, 2.0))

    outbound = parse_to_outbound(link)
    if not outbound: return rename_link(link, "V2rayTested-UNK")

    config = {
        "log": {"loglevel": "none"},
        "inbounds": [{"port": port, "listen": "127.0.0.1", "protocol": "socks"}],
        "outbounds": [outbound]
    }

    config_file = f"temp_geo_{port}.json"
    with open(config_file, 'w') as f:
        json.dump(config, f)

    proc = subprocess.Popen([XRAY_EXE, "-c", config_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with process_lock: active_processes.append(proc)

    time.sleep(1) 

    location = "UNK"
    try:
        proxies = {"http": f"socks5h://127.0.0.1:{port}", "https": f"socks5h://127.0.0.1:{port}"}
        geo_r = requests.get("http://ip-api.com/json/", proxies=proxies, timeout=TEST_TIMEOUT)
        if geo_r.status_code == 200:
            location = geo_r.json().get("countryCode", "UNK")
    except:
        pass

    with process_lock:
        if proc in active_processes: active_processes.remove(proc)
    proc.terminate()
    proc.wait()
    try: os.remove(config_file)
    except: pass

    # Rename with the final location tag
    return rename_link(link, f"V2rayTested-{location}")

# ==========================================
# GITHUB & MAIN LOOP
# ==========================================
def fetch_configs():
    print("[*] Downloading configs from subscriptions...")
    all_links = set()
    for sub in SUBSCRIPTIONS:
        try:
            r = requests.get(sub, timeout=15)
            text = r.text.strip()
            
            if not text.startswith("vmess://") and not text.startswith("vless://"):
                try: text = base64.b64decode(text + "=" * (-len(text) % 4)).decode('utf-8')
                except: pass
                
            for line in text.splitlines():
                if line.startswith("vless://"):
                    all_links.add(line)
        except Exception as e:
            print(f"[!] Failed fetching {sub}: {e}")
            
    print(f"[*] Filtered out old protocols. Kept {len(all_links)} VLESS configs.")
    return list(all_links)

def upload_to_github(working_links):
    print("\n[*] Uploading verified list to GitHub...")
    try:
        auth = Auth.Token(GITHUB_TOKEN)
        g = Github(auth=auth)
        
        repo = g.get_repo(REPO_NAME)
        raw_text = "\n".join(working_links)
        b64_content = base64.b64encode(raw_text.encode('utf-8')).decode('utf-8')
        try:
            file = repo.get_contents(FILE_PATH_IN_REPO)
            repo.update_file(FILE_PATH_IN_REPO, "Auto-Update Live Proxies", b64_content, file.sha)
            print("[+] GitHub file updated successfully.")
        except:
            repo.create_file(FILE_PATH_IN_REPO, "Initial Live Proxies Commit", b64_content)
            print("[+] New GitHub file created successfully.")
    except Exception as e:
        print(f"[!] GitHub Upload Failed: {e}")

def main():
    cleanup_processes()
    while True:
        print("\n" + "="*50)
        print(f"[*] Verification Loop Started: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*50) 
        
        raw_links = fetch_configs()
        print(f"[+] Downloaded {len(raw_links)} raw unique configs.")
        
        # Phase 1: Async TCP check
        survived_links = asyncio.run(run_tcp_precheck(raw_links))
        
        # Phase 2: Deep Xray check on survivors
        working_raw_configs = []
        if survived_links:
            print(f"\n[*] Phase 2: Deep-testing {len(survived_links)} configs with Xray Core...")
            with ThreadPoolExecutor(max_workers=XRAY_THREADS) as executor:
                futures = {executor.submit(test_single_config, link, 10000 + (i % 5000)): link for i, link in enumerate(survived_links)}
                for i, future in enumerate(as_completed(futures)):
                    result = future.result()
                    if result: working_raw_configs.append(result)
                    if (i + 1) % 100 == 0:
                        print(f"    Xray checked {i + 1}/{len(survived_links)}... (Found {len(working_raw_configs)} authenticated)")

        # Phase 3: GeoIP Test on Working Configs
        final_configs = []
        if working_raw_configs:
            print(f"\n[*] Phase 3: Fetching GeoIP locations for {len(working_raw_configs)} working configs...")
            with ThreadPoolExecutor(max_workers=XRAY_THREADS) as executor:
                futures = {executor.submit(geoip_single_config, link, 20000 + (i % 5000)): link for i, link in enumerate(working_raw_configs)}
                for i, future in enumerate(as_completed(futures)):
                    result = future.result()
                    if result: final_configs.append(result)
                    if (i + 1) % 50 == 0:
                        print(f"    GeoIP fetched {i + 1}/{len(working_raw_configs)}...")
        
        # ==========================================
        # DUPLICATION CHECK & UPLOAD
        # ==========================================
        unique_configs = list(set(final_configs))
        duplicates_removed = len(final_configs) - len(unique_configs)
        
        print(f"\n[+] Cycle Complete. Out of {len(raw_links)} inputs, {len(final_configs)} passed the full suite.")
        if duplicates_removed > 0:
            print(f"[*] Removed {duplicates_removed} duplicate configs. Final count: {len(unique_configs)} strictly unique configs.")
        
        if unique_configs:
            upload_to_github(unique_configs)
            
        print(f"[*] Cycle finished. Sleeping for {LOOP_INTERVAL} seconds...")
        time.sleep(LOOP_INTERVAL)

if __name__ == "__main__":
    if not os.path.exists(XRAY_EXE):
        print(f"[!] Error: Place {XRAY_EXE} in this folder before running.")
        sys.exit(1)
    main()