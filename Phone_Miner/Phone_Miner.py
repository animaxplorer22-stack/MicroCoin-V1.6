#!/usr/bin/env python3
"""
MICROCORE (MCX) PHONE MINER v6.0 - COMPLETE
Real ECDSA secp256k1 | Gossip Discovery | Peer Caching | No DNS Required
10 Levels (1,000 MCX per level) | Temporary + Permanent Towers
Remote Control | Uptime Tracking | Slashing Handling | Block Redistribution
Runs on iPhone (a-shell/iSH) and Android (Termux)

Run: python3 phone_miner.py
"""

import json
import time
import hashlib
import os
import sys
import asyncio
import secrets
from datetime import datetime
from typing import Optional, List, Dict, Any

# ==================== DEPENDENCY CHECK ====================
try:
    import websockets
except ImportError:
    print("[SETUP] Installing websockets...")
    os.system("pip install websockets")
    import websockets

try:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature
except ImportError:
    print("[SETUP] Installing cryptography...")
    os.system("pip install cryptography")
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature

# ==================== GOSSIP DISCOVERY (NO DNS) ====================
# HARDCODED BOOTNODES - CHANGE THIS TO YOUR NODE IP
BOOTSTRAP_NODES = [
    "YOUR_SERVER_IP:8080",  # ← CHANGE THIS TO YOUR NODE IP
]

PEER_CACHE_FILE = "phone_miner_peers.json"
NODE_PORT = 8080

def save_peers_to_cache(peers: List[str]) -> None:
    try:
        unique = list(set(peers))
        with open(PEER_CACHE_FILE, 'w') as f:
            json.dump(unique, f, indent=2)
        print(f"[CACHE] Saved {len(unique)} peers")
    except Exception as e:
        print(f"[CACHE] Save failed: {e}")

def load_peers_from_cache() -> List[str]:
    try:
        with open(PEER_CACHE_FILE, 'r') as f:
            peers = json.load(f)
        print(f"[CACHE] Loaded {len(peers)} peers from cache")
        return peers
    except:
        print(f"[CACHE] No cache file found")
        return []

def get_bootstrap_peers() -> List[str]:
    peers = BOOTSTRAP_NODES.copy()
    cached = load_peers_from_cache()
    for p in cached:
        if p not in peers:
            peers.append(p)
    return peers

# ==================== CONFIGURATION ====================
USERNAME = ""
WALLET_FILE = "microcore_phone_wallet.json"

INITIAL_STAKE = 1000  # 1,000 MCX per level
LEVEL_STAKE_RANGE = 1000
MAX_LEVEL = 10
SIGNING_WINDOW_MS = 2500
SLASH_RATE = 0.10
UPTIME_PING_INTERVAL = 30
STATUS_INTERVAL = 60
MAX_RECONNECT_ATTEMPTS = 10
RECONNECT_DELAY = 5
VERSION = "6.0"

# Level block intervals (seconds)
LEVEL_BLOCK_INTERVALS = {1:40, 2:35, 3:30, 4:25, 5:20, 6:15, 7:10, 8:9, 9:8, 10:7}

# ==================== REAL CRYPTO FUNCTIONS ====================
def generate_private_key() -> tuple:
    private_key = ec.generate_private_key(ec.SECP256K1())
    private_key_hex = private_key.private_numbers().private_value.to_bytes(32, 'big').hex()
    return private_key_hex, private_key

def get_public_key_pem(private_key_hex: str) -> str:
    private_value = int(private_key_hex, 16)
    private_key = ec.derive_private_key(private_value, ec.SECP256K1())
    public_key = private_key.public_key()
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()

def get_wallet_address(public_key_pem: str) -> str:
    return "MCR_" + hashlib.sha256(public_key_pem.encode()).hexdigest()[:32].upper()

def get_validator_id(username: str, public_key_pem: str) -> str:
    return hashlib.sha256(f"{username}{public_key_pem}".encode()).hexdigest()[:32]

def sign_message(private_key_hex: str, message: str) -> str:
    private_value = int(private_key_hex, 16)
    private_key = ec.derive_private_key(private_value, ec.SECP256K1())
    signature = private_key.sign(message.encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(signature)
    return r.to_bytes(32, 'big').hex() + s.to_bytes(32, 'big').hex()

# ==================== WALLET MANAGEMENT ====================
class Wallet:
    def __init__(self, username: str, address: str, public_key_pem: str, private_key_hex: str):
        self.username = username
        self.address = address
        self.public_key_pem = public_key_pem
        self.private_key_hex = private_key_hex
        self._private_key = None
    
    def get_private_key(self):
        if self._private_key is None:
            self._private_key = ec.derive_private_key(int(self.private_key_hex, 16), ec.SECP256K1())
        return self._private_key
    
    def get_validator_id(self) -> str:
        return get_validator_id(self.username, self.public_key_pem)
    
    @classmethod
    def create_new(cls, username: str) -> 'Wallet':
        private_key_hex, _ = generate_private_key()
        public_key_pem = get_public_key_pem(private_key_hex)
        address = get_wallet_address(public_key_pem)
        return cls(username, address, public_key_pem, private_key_hex)
    
    @classmethod
    def load(cls, filename: str) -> Optional['Wallet']:
        if not os.path.exists(filename):
            return None
        with open(filename, 'r') as f:
            data = json.load(f)
        return cls(data.get('username', ''), data['address'], data['public_key_pem'], data['private_key_hex'])
    
    def save(self, filename: str):
        with open(filename, 'w') as f:
            json.dump({
                'username': self.username,
                'address': self.address,
                'public_key_pem': self.public_key_pem,
                'private_key_hex': self.private_key_hex,
                'created_at': time.time(),
                'version': VERSION
            }, f, indent=2)
# ==================== STATS MANAGEMENT ====================
class MinerStats:
    def __init__(self):
        self.stats_file = "phone_miner_stats.json"
        self.stats = self.load_stats()
    
    def load_stats(self) -> Dict:
        default_stats = {
            "stake": INITIAL_STAKE,
            "rewards": 0,
            "blocks": 0,
            "slashes": 0,
            "level": 1,
            "uptime": 0,
            "today_uptime": 0,
            "last_uptime_reset": time.time(),
            "consecutive_misses": 0,
            "current_peer_index": 0,
            "mining": True,
            "node_switches": 0,
            "version": VERSION
        }
        try:
            with open(self.stats_file, 'r') as f:
                loaded = json.load(f)
                default_stats.update(loaded)
        except:
            pass
        return default_stats
    
    def save(self):
        try:
            with open(self.stats_file, 'w') as f:
                json.dump(self.stats, f, indent=2)
        except Exception as e:
            print(f"[STORAGE] Save failed: {e}")
    
    def get(self, key: str, default=None):
        return self.stats.get(key, default)
    
    def set(self, key: str, value):
        self.stats[key] = value
        self.save()
    
    def update(self, **kwargs):
        self.stats.update(kwargs)
        self.save()
    
    def add_reward(self, amount: int, level: int = 1):
        self.stats["rewards"] += amount
        self.stats["stake"] += amount
        self.stats["blocks"] += 1
        self.stats["consecutive_misses"] = 0
        self.stats["level"] = self.calculate_level()
        self.save()
    
    def add_slash(self, amount: int):
        self.stats["stake"] -= amount
        if self.stats["stake"] < LEVEL_STAKE_RANGE:
            self.stats["stake"] = LEVEL_STAKE_RANGE
        self.stats["slashes"] += 1
        self.stats["consecutive_misses"] += 1
        self.stats["level"] = self.calculate_level()
        self.save()
    
    def add_uptime(self, seconds: int):
        self.stats["uptime"] += seconds
        self.stats["today_uptime"] += seconds
        if self.stats["today_uptime"] > 86400:
            self.stats["today_uptime"] = 86400
        self.save()
    
    def reset_daily_uptime(self) -> bool:
        now = time.time()
        if now - self.stats.get("last_uptime_reset", now) > 86400:
            self.stats["today_uptime"] = 0
            self.stats["last_uptime_reset"] = now
            self.save()
            return True
        return False
    
    def calculate_level(self) -> int:
        stake = self.stats["stake"]
        level = ((stake - 1) // LEVEL_STAKE_RANGE) + 1
        return max(1, min(level, MAX_LEVEL))
    
    def get_block_interval(self) -> int:
        level = self.calculate_level()
        return LEVEL_BLOCK_INTERVALS.get(level, 40)
    
    def record_node_switch(self):
        self.stats["node_switches"] = self.stats.get("node_switches", 0) + 1
        self.save()

# ==================== PHONE MINER ====================
class PhoneMiner:
    def __init__(self, wallet: Wallet):
        self.wallet = wallet
        self.validator_id = wallet.get_validator_id()
        
        # Gossip discovery
        self.peers = get_bootstrap_peers()
        self.current_peer_index = 0
        self.discovered_peers = set(self.peers)
        
        # WebSocket
        self.ws = None
        self.connected = False
        self.is_validator = False
        self.current_challenge = ""
        self.current_block_id = 0
        self.last_challenge_time = 0
        self.challenge_timeout_task = None
        
        # Timing
        self.start_time = time.time()
        self.last_uptime_ping = 0
        self.last_status_report = 0
        self.reconnect_attempts = 0
        self.last_uptime_add = 0
        
        # Mining state
        self.mining_enabled = True
        self.running = True
        
        # Stats
        self.stats = MinerStats()
        self.current_stake = self.stats.get("stake", INITIAL_STAKE)
        self.total_rewards = self.stats.get("rewards", 0)
        self.blocks_signed = self.stats.get("blocks", 0)
        self.slash_count = self.stats.get("slashes", 0)
        self.consecutive_misses = self.stats.get("consecutive_misses", 0)
        self.current_level = self.stats.calculate_level()
        self.node_switch_count = self.stats.get("node_switches", 0)
        
        # Uptime
        self.total_uptime = self.stats.get("uptime", 0)
        self.today_uptime = self.stats.get("today_uptime", 0)
        self.last_uptime_reset = self.stats.get("last_uptime_reset", time.time())
    
    def add_log(self, msg: str, msg_type: str = "info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        colors = {"success": "\033[92m", "error": "\033[91m", "info": "\033[94m"}
        print(f"[{timestamp}] {colors.get(msg_type, '')}{msg}\033[0m")
    
    def calculate_level(self) -> int:
        level = ((self.current_stake - 1) // LEVEL_STAKE_RANGE) + 1
        return max(1, min(level, MAX_LEVEL))
    
    def get_block_interval(self) -> int:
        return LEVEL_BLOCK_INTERVALS.get(self.current_level, 40)
    
    def update_level(self):
        self.current_level = self.calculate_level()
        self.stats.update(level=self.current_level)
    
    def update_uptime(self):
        now = time.time()
        if now - self.last_uptime_add >= UPTIME_PING_INTERVAL:
            self.stats.add_uptime(UPTIME_PING_INTERVAL)
            self.total_uptime = self.stats.get("uptime", 0)
            self.today_uptime = self.stats.get("today_uptime", 0)
            self.last_uptime_add = now
    
    def check_daily_reset(self):
        if self.stats.reset_daily_uptime():
            self.today_uptime = 0
            self.add_log("[DAILY] Uptime reset for new day", "info")
    
    def add_reward(self, reward: int, block_id: int = 0, level: int = 1):
        self.stats.add_reward(reward, level)
        self.current_stake = self.stats.get("stake", INITIAL_STAKE)
        self.total_rewards = self.stats.get("rewards", 0)
        self.blocks_signed = self.stats.get("blocks", 0)
        self.consecutive_misses = 0
        self.update_level()
        self.add_log(f"[REWARD] +{reward} MCX | Total: {self.total_rewards} | Stake: {self.current_stake} | Level: {self.current_level} | Block interval: {self.get_block_interval()}s", "success")
    
    def handle_slash(self, amount: int = 0, reason: str = "Missed signing") -> bool:
        if amount == 0:
            amount = max(int(self.current_stake * SLASH_RATE), LEVEL_STAKE_RANGE)
        
        self.stats.add_slash(amount)
        self.current_stake = self.stats.get("stake", INITIAL_STAKE)
        self.slash_count = self.stats.get("slashes", 0)
        self.consecutive_misses = self.stats.get("consecutive_misses", 0)
        self.update_level()
        
        self.add_log(f"[SLASH] -{amount} MCX | Stake: {self.current_stake} | Level: {self.current_level} | Slashes: {self.slash_count}/5", "error")
        
        if self.slash_count >= 5:
            self.add_log("[BAN] Too many slashes! Miner will stop mining.", "error")
            self.mining_enabled = False
            return False
        return True
    
    def record_miss(self, block_id: int, reason: str = "Timeout"):
        self.consecutive_misses += 1
        self.stats.update(consecutive_misses=self.consecutive_misses)
        self.add_log(f"[MISS] Block {block_id} missed | Consecutive misses: {self.consecutive_misses}", "error")
# ==================== GOSSIP DISCOVERY ====================
    def get_current_peer_url(self) -> Optional[str]:
        if not self.peers:
            return None
        peer = self.peers[self.current_peer_index]
        if "://" not in peer:
            peer = f"ws://{peer}"
        return peer
    
    def add_peer_from_gossip(self, peer: str):
        if peer not in self.discovered_peers:
            self.discovered_peers.add(peer)
            self.peers.append(peer)
            save_peers_to_cache(list(self.discovered_peers))
            self.add_log(f"[GOSSIP] Discovered new peer: {peer}", "success")
    
    def switch_to_next_peer(self):
        self.current_peer_index = (self.current_peer_index + 1) % len(self.peers) if self.peers else 0
        self.reconnect_attempts += 1
        if self.reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
            self.current_peer_index = 0
            self.reconnect_attempts = 0
            self.node_switch_count += 1
            self.stats.record_node_switch()
        self.add_log(f"[FAILOVER] Switching to peer #{self.current_peer_index}", "info")
    
    # ==================== WEBSOCKET COMMUNICATION ====================
    async def register(self):
        timestamp = time.time()
        reg_message = f"{self.validator_id}{self.wallet.username}{self.current_stake}{timestamp}"
        signature = sign_message(self.wallet.private_key_hex, reg_message)
        
        self.check_daily_reset()
        self.update_uptime()
        
        msg = {
            "type": "register",
            "validator_id": self.validator_id,
            "username": self.wallet.username,
            "public_key": self.wallet.public_key_pem,
            "wallet": self.wallet.address,
            "stake": self.current_stake,
            "level": self.current_level,
            "rewards": self.total_rewards,
            "blocks": self.blocks_signed,
            "uptime": self.total_uptime,
            "today_uptime": self.today_uptime,
            "miner_type": "phone",
            "version": VERSION,
            "timestamp": timestamp,
            "signature": signature
        }
        
        if self.ws:
            await self.ws.send(json.dumps(msg))
            self.add_log(f"[REG] Registered as '{self.wallet.username}' (Level {self.current_level})", "info")
    
    async def send_uptime_ping(self):
        self.update_uptime()
        msg = {
            "type": "uptime_ping",
            "validator_id": self.validator_id,
            "username": self.wallet.username,
            "uptime_seconds": self.total_uptime,
            "today_uptime": self.today_uptime,
            "stake": self.current_stake,
            "level": self.current_level
        }
        if self.ws:
            await self.ws.send(json.dumps(msg))
    
    async def sign_block(self):
        message = f"{self.current_challenge}{self.validator_id}{self.current_block_id}"
        signature = sign_message(self.wallet.private_key_hex, message)
        
        msg = {
            "type": "block_signature",
            "validator_id": self.validator_id,
            "username": self.wallet.username,
            "challenge": self.current_challenge,
            "signature": signature,
            "level": self.current_level,
            "stake": self.current_stake,
            "block_id": self.current_block_id,
            "timestamp": time.time()
        }
        
        if self.ws:
            await self.ws.send(json.dumps(msg))
            self.add_log(f"[SIGN] Signed block {self.current_block_id} (Level {self.current_level})", "success")
    
    async def send_status(self):
        msg = {
            "type": "miner_status",
            "validator_id": self.validator_id,
            "username": self.wallet.username,
            "stake": self.current_stake,
            "level": self.current_level,
            "blocks": self.blocks_signed,
            "rewards": self.total_rewards,
            "uptime": self.total_uptime,
            "today_uptime": self.today_uptime,
            "mining": self.mining_enabled
        }
        if self.ws:
            await self.ws.send(json.dumps(msg))
# ==================== MESSAGE HANDLING ====================
    async def handle_message(self, data: str):
        try:
            msg = json.loads(data)
            msg_type = msg.get("type")
            
            if msg_type == "registered":
                self.add_log(f"[NODE] Registration confirmed | Level: {msg.get('level')} | Reward: {msg.get('current_reward')} MCX/block", "success")
                self.reconnect_attempts = 0
            
            elif msg_type == "peers":
                for peer in msg.get("peers", []):
                    self.add_peer_from_gossip(peer)
                self.add_log(f"[GOSSIP] Received {len(msg.get('peers', []))} peers from node", "info")
            
            elif msg_type == "challenge":
                if not self.mining_enabled:
                    self.add_log("[MINING] Mining disabled, ignoring challenge", "info")
                    return
                
                self.current_challenge = msg.get("challenge", "")
                self.current_block_id = msg.get("block_id", 0)
                self.last_challenge_time = time.time()
                self.is_validator = True
                await self.sign_block()
                
                if self.challenge_timeout_task:
                    self.challenge_timeout_task.cancel()
                
                async def timeout_handler():
                    await asyncio.sleep(SIGNING_WINDOW_MS / 1000)
                    if self.is_validator:
                        self.record_miss(self.current_block_id, "Timeout")
                        self.handle_slash()
                        self.is_validator = False
                
                self.challenge_timeout_task = asyncio.create_task(timeout_handler())
            
            elif msg_type == "block_accepted":
                if self.challenge_timeout_task:
                    self.challenge_timeout_task.cancel()
                reward = msg.get("reward", 3)
                level = msg.get("level", 1)
                self.add_reward(reward, self.current_block_id, level)
                self.is_validator = False
                self.add_log(f"[NODE] Block {msg.get('block_id')} ACCEPTED! +{reward} MCX (Level {level})", "success")
            
            elif msg_type == "block_rejected":
                if self.challenge_timeout_task:
                    self.challenge_timeout_task.cancel()
                self.is_validator = False
                self.add_log(f"[NODE] Block {msg.get('block_id')} REJECTED", "error")
            
            elif msg_type == "slash":
                self.add_log("[NODE] Slash command received", "error")
                amount = msg.get("amount", 0)
                reason = msg.get("reason", "Node slashing")
                self.handle_slash(amount, reason)
                self.is_validator = False
            
            elif msg_type == "level_update":
                new_stake = msg.get("stake", self.current_stake)
                if new_stake != self.current_stake:
                    self.current_stake = new_stake
                    self.current_level = self.calculate_level()
                    self.stats.update(stake=self.current_stake, level=self.current_level)
                    self.add_log(f"[NODE] Level update: Level {self.current_level} (Stake: {self.current_stake} MCX, Block interval: {self.get_block_interval()}s)", "info")
            
            elif msg_type == "miner_control":
                action = msg.get("action")
                if action == "stop":
                    self.add_log("[CONTROL] Stop command received - stopping mining", "info")
                    self.mining_enabled = False
                    self.is_validator = False
                    self.stats.update(mining=False)
                elif action == "start":
                    self.add_log("[CONTROL] Start command received - resuming mining", "info")
                    self.mining_enabled = True
                    self.stats.update(mining=True)
                elif action == "restart":
                    self.add_log("[CONTROL] Restart command received", "info")
                    self.mining_enabled = False
                    self.is_validator = False
                    await asyncio.sleep(1)
                    self.mining_enabled = True
                    self.stats.update(mining=True)
                elif action == "status":
                    await self.send_status()
                
                ack = {"type": "control_response", "miner_id": self.validator_id, "action": action, "success": True}
                if self.ws:
                    await self.ws.send(json.dumps(ack))
            
            elif msg_type == "get_status":
                await self.send_status()
            
            elif msg_type == "balance":
                if msg.get("stake"):
                    self.current_stake = msg["stake"]
                    self.current_level = self.calculate_level()
                    self.stats.update(stake=self.current_stake, level=self.current_level)
            
            elif msg_type == "error":
                self.add_log(f"[NODE] Error: {msg.get('message', 'Unknown')}", "error")
        
        except Exception as e:
            self.add_log(f"[ERROR] Message handling: {e}", "error")

# ==================== CONNECTION LOOP ====================
    async def connect_and_run(self):
        self.reconnect_attempts = 0
        
        while self.running:
            peer_url = self.get_current_peer_url()
            if not peer_url:
                self.add_log("[ERROR] No peers available. Check BOOTSTRAP_NODES", "error")
                await asyncio.sleep(30)
                self.peers = get_bootstrap_peers()
                self.discovered_peers = set(self.peers)
                continue
            
            try:
                self.add_log(f"[CONN] Connecting to {peer_url}...", "info")
                
                async with websockets.connect(
                    peer_url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                    max_size=10_000_000
                ) as ws:
                    self.ws = ws
                    self.connected = True
                    self.reconnect_attempts = 0
                    self.add_log(f"[CONN] Connected to {peer_url}", "success")
                    
                    await ws.send(json.dumps({"type": "get_peers"}))
                    await self.register()
                    
                    while self.running and self.mining_enabled and self.connected:
                        if time.time() - self.last_uptime_ping > UPTIME_PING_INTERVAL:
                            await self.send_uptime_ping()
                            self.last_uptime_ping = time.time()
                        
                        if time.time() - self.last_status_report > STATUS_INTERVAL:
                            self.print_status()
                            self.last_status_report = time.time()
                        
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                            await self.handle_message(raw)
                        except asyncio.TimeoutError:
                            pass
                        
                        if self.is_validator and (time.time() - self.last_challenge_time) > (SIGNING_WINDOW_MS / 1000 + 0.5):
                            self.add_log(f"[TIMEOUT] Fallback timeout! Missed block {self.current_block_id}", "error")
                            self.record_miss(self.current_block_id, "Fallback timeout")
                            self.handle_slash()
                            self.is_validator = False
                        
                        await asyncio.sleep(0.05)
            
            except websockets.exceptions.ConnectionClosed as e:
                self.add_log(f"[CONN] Connection closed: {e}", "error")
                self.connected = False
            except Exception as e:
                self.add_log(f"[CONN] Connection error: {e}", "error")
                self.connected = False
            
            if not self.running:
                break
            
            self.switch_to_next_peer()
            delay = RECONNECT_DELAY * min(self.reconnect_attempts + 1, 10)
            self.add_log(f"[CONN] Reconnecting in {delay}s...", "info")
            await asyncio.sleep(delay)
        
        self.ws = None
    
    def print_status(self):
        uptime = int(time.time() - self.start_time)
        hours = uptime // 3600
        minutes = (uptime % 3600) // 60
        today_hours = self.today_uptime / 3600
        success_rate = 0
        total = self.blocks_signed + self.consecutive_misses
        if total > 0:
            success_rate = (self.blocks_signed / total) * 100
        
        print("\n" + "=" * 60)
        print("📱 MICROCORE PHONE MINER STATUS")
        print("=" * 60)
        print(f"Username: {self.wallet.username}")
        print(f"Wallet: {self.wallet.address[:24]}...")
        print(f"Validator ID: {self.validator_id[:20]}...")
        print("-" * 40)
        print(f"Level: {self.current_level} / {MAX_LEVEL}")
        print(f"Stake: {self.current_stake:,} MCX")
        print(f"Block Interval: {self.get_block_interval()} seconds")
        print(f"Rewards: {self.total_rewards:,} MCX")
        print(f"Blocks Signed: {self.blocks_signed}")
        print(f"Missed Blocks: {self.consecutive_misses}")
        print(f"Success Rate: {success_rate:.1f}%")
        print(f"Slash Count: {self.slash_count} / 5")
        print("-" * 40)
        print(f"Total Uptime: {hours}h {minutes}m")
        print(f"Today's Uptime: {today_hours:.1f}h / 24h")
        print(f"Peers in Cache: {len(self.discovered_peers)}")
        print(f"Node Switches: {self.node_switch_count}")
        print(f"Mining: {'🟢 ACTIVE' if self.mining_enabled else '🔴 STOPPED'}")
        print(f"Connected: {'✅ YES' if self.connected else '❌ NO'}")
        print("=" * 60 + "\n")
    
    async def run(self):
        print("\n" + "=" * 60)
        print("📱 MICROCORE PHONE MINER v6.0 📱")
        print("Real ECDSA | Gossip Discovery | Peer Caching | No DNS")
        print("10 Levels | 1,000 MCX/level | Permanent Towers")
        print("=" * 60)
        print(f"Username: {self.wallet.username}")
        print(f"Wallet: {self.wallet.address}")
        print(f"Validator ID: {self.validator_id[:20]}...")
        print("-" * 40)
        print(f"Initial Stake: {self.current_stake} MCX")
        print(f"Initial Level: {self.current_level}")
        print(f"Initial Block Interval: {self.get_block_interval()} seconds")
        print(f"Signing Window: {SIGNING_WINDOW_MS} ms")
        print(f"Slash Rate: {SLASH_RATE * 100}%")
        print("-" * 40)
        print(f"Bootnodes: {BOOTSTRAP_NODES}")
        print(f"Peers in cache: {len(self.discovered_peers)}")
        print("=" * 60)
        print("\n🚀 Miner starting... Press Ctrl+C to stop\n")
        
        await self.connect_and_run()

# ==================== MAIN ====================
async def main():
    print("\n" + "=" * 60)
    print("🔷 MICROCORE PHONE MINER - COMPLETE VERSION 🔷")
    print("=" * 60)
    
    wallet = Wallet.load(WALLET_FILE)
    if not wallet:
        print("\n[FIRST RUN] No wallet found.")
        if USERNAME:
            username = USERNAME
        else:
            username = input("Enter your username: ").strip()
            if not username:
                username = f"phone_miner_{int(time.time())}"
        
        wallet = Wallet.create_new(username)
        wallet.save(WALLET_FILE)
        print(f"\n✅ Wallet created!")
        print(f"   Username: {wallet.username}")
        print(f"   Address: {wallet.address}")
        print(f"   Private Key: {wallet.private_key_hex}")
        print(f"\n⚠️ SAVE THESE CREDENTIALS!")
        print(f"   Wallet file: {os.path.abspath(WALLET_FILE)}")
    else:
        print(f"\n✅ Wallet loaded: {wallet.username}")
        print(f"   Address: {wallet.address[:32]}...")
    
    miner = PhoneMiner(wallet)
    
    try:
        await miner.run()
    except KeyboardInterrupt:
        print("\n[STOP] Miner stopped")
        miner.stats.save()
        print(f"\n📊 FINAL STATS")
        print(f"   Rewards: {miner.total_rewards} MCX")
        print(f"   Blocks: {miner.blocks_signed}")
        print(f"   Slashes: {miner.slash_count}")
        print(f"   Node Switches: {miner.node_switch_count}")
        print(f"   Final Stake: {miner.current_stake} MCX")
        print(f"   Final Level: {miner.current_level}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[EXIT] Goodbye!")
        sys.exit(0)
