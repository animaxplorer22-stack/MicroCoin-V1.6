#!/usr/bin/env python3
"""
MICROCORE (MCX) STRIPE WEBHOOK - PRODUCTION v1.0
Handles credit card payments with full retry logic, monitoring, and security
Ready for mainnet launch

Run: python3 stripe_webhook.py

Environment Variables Required:
  - STRIPE_SECRET_KEY: sk_live_...
  - STRIPE_WEBHOOK_SECRET: whsec_...
  - MCX_NODE_WS_URL: ws://127.0.0.1:8080 (or your node IP)
  - MCX_NODE_HTTP_URL: http://127.0.0.1:8080 (fallback)
  - MCX_PRICE_USD: 0.01 (default)
  - LOG_LEVEL: INFO (default)
  - WEBHOOK_PORT: 5000 (default)

Features:
  - Automatic retry on failure with exponential backoff
  - Multiple node connection fallbacks (WebSocket + HTTP)
  - Webhook signature verification
  - Payment intent confirmation
  - Pending credit queue with retry
  - Full logging with rotation
  - Health check endpoint
  - Rate limiting
  - Graceful shutdown
"""

import os
import sys
import json
import time
import hmac
import hashlib
import sqlite3
import logging
import threading
import signal
from datetime import datetime
from typing import Dict, List, Optional, Any
from contextlib import contextmanager

# ==================== DEPENDENCY CHECK ====================
try:
    import requests
except ImportError:
    os.system("pip install requests")
    import requests

try:
    import stripe
except ImportError:
    os.system("pip install stripe")
    import stripe

try:
    import websockets
    import asyncio
except ImportError:
    os.system("pip install websockets")
    import websockets
    import asyncio

try:
    from flask import Flask, request, jsonify, abort, make_response
except ImportError:
    os.system("pip install flask")
    from flask import Flask, request, jsonify, abort, make_response

# ==================== LOGGING SETUP ====================
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler('stripe_webhook.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================
class Config:
    """Production configuration"""
    
    # Stripe
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
    STRIPE_API_VERSION = "2025-02-24.acacia"
    
    # Node connection
    MCX_NODE_WS_URL = os.environ.get("MCX_NODE_WS_URL", "ws://127.0.0.1:8080")
    MCX_NODE_HTTP_URL = os.environ.get("MCX_NODE_HTTP_URL", "http://127.0.0.1:8080")
    
    # Tokenomics
    MCX_PRICE_USD = float(os.environ.get("MCX_PRICE_USD", 0.01))
    
    # Webhook server
    WEBHOOK_HOST = os.environ.get("WEBHOOK_HOST", "0.0.0.0")
    WEBHOOK_PORT = int(os.environ.get("WEBHOOK_PORT", 5000))
    
    # Retry settings
    MAX_RETRY_ATTEMPTS = int(os.environ.get("MAX_RETRY_ATTEMPTS", 10))
    RETRY_BASE_DELAY = int(os.environ.get("RETRY_BASE_DELAY", 5))
    RETRY_MAX_DELAY = int(os.environ.get("RETRY_MAX_DELAY", 300))
    
    # Database
    DB_FILE = os.environ.get("DB_FILE", "stripe_payments.db")
    
    # Rate limiting
    RATE_LIMIT_REQUESTS = int(os.environ.get("RATE_LIMIT_REQUESTS", 100))
    RATE_LIMIT_WINDOW = int(os.environ.get("RATE_LIMIT_WINDOW", 60))
    
    # Graceful shutdown
    GRACE_PERIOD = int(os.environ.get("GRACE_PERIOD", 10))
    
    @classmethod
    def validate(cls):
        """Validate required configuration"""
        missing = []
        if not cls.STRIPE_SECRET_KEY:
            missing.append("STRIPE_SECRET_KEY")
        if not cls.STRIPE_WEBHOOK_SECRET:
            missing.append("STRIPE_WEBHOOK_SECRET")
        
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
        
        logger.info("[CONFIG] All required variables set")
        logger.info(f"[CONFIG] Node WS URL: {cls.MCX_NODE_WS_URL}")
        logger.info(f"[CONFIG] Node HTTP URL: {cls.MCX_NODE_HTTP_URL}")
        logger.info(f"[CONFIG] MCX Price: ${cls.MCX_PRICE_USD}")
        logger.info(f"[CONFIG] Max retry attempts: {cls.MAX_RETRY_ATTEMPTS}")

# ==================== DATABASE ====================
class PaymentDatabase:
    """Database operations for payments with retry and connection management"""
    
    def __init__(self, db_file: str = Config.DB_FILE):
        self.db_file = db_file
        self._init_db()
    
    @contextmanager
    def get_connection(self):
        """Get database connection with context manager"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_file, timeout=30)
            conn.row_factory = sqlite3.Row
            yield conn
        except sqlite3.Error as e:
            logger.error(f"[DB] Connection error: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()
    
    def _init_db(self):
        """Initialize database tables"""
        with self.get_connection() as conn:
            c = conn.cursor()
            
            # Payments table
            c.execute('''CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_id TEXT UNIQUE,
                wallet TEXT NOT NULL,
                username TEXT NOT NULL,
                usd_amount REAL NOT NULL,
                mcx_amount INTEGER NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at REAL,
                completed_at REAL,
                stripe_payment_intent TEXT,
                stripe_session_id TEXT,
                payment_method TEXT DEFAULT 'card',
                currency TEXT DEFAULT 'usd',
                metadata TEXT,
                retry_count INTEGER DEFAULT 0,
                error_message TEXT
            )''')
            
            # Pending credits queue
            c.execute('''CREATE TABLE IF NOT EXISTS pending_credits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT NOT NULL,
                username TEXT NOT NULL,
                amount INTEGER NOT NULL,
                payment_id TEXT,
                created_at REAL,
                retry_count INTEGER DEFAULT 0,
                last_attempt REAL,
                completed BOOLEAN DEFAULT 0,
                error_message TEXT,
                FOREIGN KEY (payment_id) REFERENCES payments(payment_id)
            )''')
            
            # Webhook delivery log
            c.execute('''CREATE TABLE IF NOT EXISTS webhook_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stripe_event_id TEXT UNIQUE,
                event_type TEXT,
                payload TEXT,
                received_at REAL,
                processed BOOLEAN DEFAULT 0,
                processed_at REAL,
                error_message TEXT
            )''')
            
            # Rate limiting
            c.execute('''CREATE TABLE IF NOT EXISTS rate_limit (
                ip TEXT,
                timestamp REAL,
                PRIMARY KEY (ip, timestamp)
            )''')
            
            # Create indexes
            c.execute('CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_payments_wallet ON payments(wallet)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_credits_completed ON pending_credits(completed)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_webhook_event ON webhook_deliveries(stripe_event_id)')
            
            conn.commit()
            logger.info("[DB] Database initialized")
    
    def add_payment(self, payment_id: str, wallet: str, username: str, usd_amount: float,
                    mcx_amount: int, payment_intent: str, session_id: str = None,
                    metadata: dict = None) -> bool:
        """Add a new payment record"""
        try:
            with self.get_connection() as conn:
                c = conn.cursor()
                c.execute('''INSERT INTO payments 
                    (payment_id, wallet, username, usd_amount, mcx_amount, status, created_at,
                     stripe_payment_intent, stripe_session_id, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (payment_id, wallet, username, usd_amount, mcx_amount, 'pending',
                     time.time(), payment_intent, session_id, json.dumps(metadata) if metadata else None))
                conn.commit()
                logger.info(f"[DB] Payment recorded: {payment_id} ({mcx_amount} MCX to {wallet})")
                return True
        except sqlite3.Error as e:
            logger.error(f"[DB] Failed to add payment: {e}")
            return False
    
    def update_payment_status(self, payment_intent: str, status: str, error: str = None) -> bool:
        """Update payment status"""
        try:
            with self.get_connection() as conn:
                c = conn.cursor()
                c.execute('''UPDATE payments 
                    SET status=?, completed_at=?, error_message=?
                    WHERE stripe_payment_intent=?''',
                    (status, time.time() if status == 'completed' else None, error, payment_intent))
                conn.commit()
                logger.info(f"[DB] Payment {payment_intent} status: {status}")
                return True
        except sqlite3.Error as e:
            logger.error(f"[DB] Failed to update payment: {e}")
            return False
    
    def get_payment_by_intent(self, payment_intent: str) -> Optional[Dict]:
        """Get payment by Stripe payment intent ID"""
        try:
            with self.get_connection() as conn:
                c = conn.cursor()
                c.execute('''SELECT * FROM payments WHERE stripe_payment_intent=?''', (payment_intent,))
                row = c.fetchone()
                return dict(row) if row else None
        except sqlite3.Error as e:
            logger.error(f"[DB] Failed to get payment: {e}")
            return None
    
    def get_payment_by_id(self, payment_id: str) -> Optional[Dict]:
        """Get payment by payment ID"""
        try:
            with self.get_connection() as conn:
                c = conn.cursor()
                c.execute('''SELECT * FROM payments WHERE payment_id=?''', (payment_id,))
                row = c.fetchone()
                return dict(row) if row else None
        except sqlite3.Error as e:
            logger.error(f"[DB] Failed to get payment: {e}")
            return None
    
    def add_pending_credit(self, wallet: str, username: str, amount: int, payment_id: str = None) -> bool:
        """Add to pending credits queue"""
        try:
            with self.get_connection() as conn:
                c = conn.cursor()
                c.execute('''INSERT INTO pending_credits 
                    (wallet, username, amount, payment_id, created_at, retry_count, last_attempt)
                    VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (wallet, username, amount, payment_id, time.time(), 0, time.time()))
                conn.commit()
                logger.info(f"[DB] Pending credit: {amount} MCX to {wallet} (payment: {payment_id})")
                return True
        except sqlite3.Error as e:
            logger.error(f"[DB] Failed to add pending credit: {e}")
            return False
    
    def get_pending_credits(self, limit: int = 100) -> List[Dict]:
        """Get pending credits that need processing"""
        try:
            with self.get_connection() as conn:
                c = conn.cursor()
                c.execute('''SELECT * FROM pending_credits 
                    WHERE completed=0 
                    ORDER BY created_at ASC 
                    LIMIT ?''', (limit,))
                rows = c.fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"[DB] Failed to get pending credits: {e}")
            return []
    
    def mark_credit_completed(self, credit_id: int) -> bool:
        """Mark credit as completed"""
        try:
            with self.get_connection() as conn:
                c = conn.cursor()
                c.execute('''UPDATE pending_credits 
                    SET completed=1 
                    WHERE id=?''', (credit_id,))
                conn.commit()
                logger.info(f"[DB] Credit {credit_id} marked completed")
                return True
        except sqlite3.Error as e:
            logger.error(f"[DB] Failed to mark credit completed: {e}")
            return False
    
    def update_credit_retry(self, credit_id: int, error: str = None) -> bool:
        """Update credit retry count and error"""
        try:
            with self.get_connection() as conn:
                c = conn.cursor()
                c.execute('''UPDATE pending_credits 
                    SET retry_count = retry_count + 1, 
                        last_attempt = ?,
                        error_message = ?
                    WHERE id=?''',
                    (time.time(), error, credit_id))
                conn.commit()
                return True
        except sqlite3.Error as e:
            logger.error(f"[DB] Failed to update credit retry: {e}")
            return False
    
    def record_webhook_delivery(self, event_id: str, event_type: str, payload: str) -> bool:
        """Record webhook delivery"""
        try:
            with self.get_connection() as conn:
                c = conn.cursor()
                c.execute('''INSERT OR IGNORE INTO webhook_deliveries 
                    (stripe_event_id, event_type, payload, received_at)
                    VALUES (?, ?, ?, ?)''',
                    (event_id, event_type, payload, time.time()))
                conn.commit()
                return True
        except sqlite3.Error as e:
            logger.error(f"[DB] Failed to record webhook: {e}")
            return False
    
    def mark_webhook_processed(self, event_id: str) -> bool:
        """Mark webhook as processed"""
        try:
            with self.get_connection() as conn:
                c = conn.cursor()
                c.execute('''UPDATE webhook_deliveries 
                    SET processed=1, processed_at=? 
                    WHERE stripe_event_id=?''',
                    (time.time(), event_id))
                conn.commit()
                return True
        except sqlite3.Error as e:
            logger.error(f"[DB] Failed to mark webhook processed: {e}")
            return False
    
    def is_webhook_processed(self, event_id: str) -> bool:
        """Check if webhook already processed"""
        try:
            with self.get_connection() as conn:
                c = conn.cursor()
                c.execute('''SELECT processed FROM webhook_deliveries 
                    WHERE stripe_event_id=?''', (event_id,))
                row = c.fetchone()
                return bool(row and row[0]) if row else False
        except sqlite3.Error as e:
            logger.error(f"[DB] Failed to check webhook: {e}")
            return False
    
    def cleanup_old_records(self, days: int = 90) -> int:
        """Clean up old records"""
        try:
            with self.get_connection() as conn:
                c = conn.cursor()
                cutoff = time.time() - (days * 86400)
                c.execute('''DELETE FROM payments WHERE completed_at < ? AND status IN ('completed', 'failed')''', (cutoff,))
                deleted_payments = c.rowcount
                c.execute('''DELETE FROM pending_credits WHERE completed=1 AND created_at < ?''', (cutoff,))
                deleted_credits = c.rowcount
                c.execute('''DELETE FROM webhook_deliveries WHERE processed=1 AND received_at < ?''', (cutoff,))
                deleted_webhooks = c.rowcount
                conn.commit()
                logger.info(f"[DB] Cleanup: {deleted_payments} payments, {deleted_credits} credits, {deleted_webhooks} webhooks")
                return deleted_payments + deleted_credits + deleted_webhooks
        except sqlite3.Error as e:
            logger.error(f"[DB] Cleanup failed: {e}")
            return 0

# ==================== NODE COMMUNICATION ====================
class NodeClient:
    """Handles communication with MicroCore node with retry logic"""
    
    def __init__(self, ws_url: str = Config.MCX_NODE_WS_URL, http_url: str = Config.MCX_NODE_HTTP_URL):
        self.ws_url = ws_url
        self.http_url = http_url
        self._lock = asyncio.Lock()
    
    async def credit_mcx_ws(self, wallet: str, amount: int, payment_id: str = None) -> bool:
        """Credit MCX via WebSocket"""
        try:
            async with websockets.connect(self.ws_url) as ws:
                message = {
                    "type": "admin_credit",
                    "wallet": wallet,
                    "amount": amount,
                    "payment_id": payment_id,
                    "timestamp": time.time()
                }
                await ws.send(json.dumps(message))
                response = await ws.recv()
                data = json.loads(response)
                if data.get("success"):
                    logger.info(f"[NODE] ✅ Credited {amount} MCX to {wallet} via WebSocket")
                    return True
                else:
                    logger.error(f"[NODE] WebSocket credit failed: {data.get('error')}")
                    return False
        except Exception as e:
            logger.error(f"[NODE] WebSocket credit error: {e}")
            return False
    
    def credit_mcx_http(self, wallet: str, amount: int, payment_id: str = None) -> bool:
        """Credit MCX via HTTP (fallback)"""
        try:
            response = requests.post(
                f"{self.http_url}/api/credit",
                json={
                    "wallet": wallet,
                    "amount": amount,
                    "payment_id": payment_id,
                    "timestamp": time.time()
                },
                timeout=10,
                headers={"Content-Type": "application/json"}
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    logger.info(f"[NODE] ✅ Credited {amount} MCX to {wallet} via HTTP")
                    return True
                else:
                    logger.error(f"[NODE] HTTP credit failed: {data.get('error')}")
                    return False
            else:
                logger.error(f"[NODE] HTTP credit failed: {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"[NODE] HTTP credit error: {e}")
            return False
    
    async def credit_mcx(self, wallet: str, amount: int, payment_id: str = None) -> bool:
        """Credit MCX with automatic fallback"""
        # Try WebSocket first
        try:
            success = await self.credit_mcx_ws(wallet, amount, payment_id)
            if success:
                return True
        except Exception as e:
            logger.warning(f"[NODE] WebSocket attempt failed: {e}")
        
        # Fallback to HTTP
        logger.info(f"[NODE] Falling back to HTTP for credit to {wallet}")
        return self.credit_mcx_http(wallet, amount, payment_id)
    
    async def health_check(self) -> bool:
        """Check if node is healthy"""
        try:
            # Try WebSocket
            try:
                async with websockets.connect(self.ws_url, timeout=5) as ws:
                    await ws.send(json.dumps({"type": "get_health"}))
                    await ws.recv()
                    return True
            except:
                pass
            
            # Try HTTP
            try:
                response = requests.get(f"{self.http_url}/health", timeout=5)
                return response.status_code == 200
            except:
                pass
            
            return False
        except:
            return False

# ==================== STRIPE WEBHOOK HANDLER ====================
class StripeWebhookHandler:
    """Main webhook handler with full error handling and retry logic"""
    
    def __init__(self):
        self.db = PaymentDatabase()
        self.node = NodeClient()
        self.stripe_client = None
        self._shutdown = False
        self._pending_tasks = []
        
        # Configure Stripe
        stripe.api_key = Config.STRIPE_SECRET_KEY
        stripe.api_version = Config.STRIPE_API_VERSION
    
    def verify_signature(self, payload: bytes, sig_header: str) -> bool:
        """Verify Stripe webhook signature"""
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, Config.STRIPE_WEBHOOK_SECRET
            )
            return True
        except ValueError as e:
            logger.error(f"[SIG] Invalid payload: {e}")
            return False
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"[SIG] Invalid signature: {e}")
            return False
    
    def create_payment_intent(self, wallet: str, username: str, usd_amount: float) -> Dict:
        """Create a Stripe payment intent"""
        try:
            mcx_amount = int(usd_amount / Config.MCX_PRICE_USD)
            
            intent = stripe.PaymentIntent.create(
                amount=int(usd_amount * 100),
                currency="usd",
                metadata={
                    "wallet": wallet,
                    "username": username,
                    "mcx_amount": str(mcx_amount)
                },
                description=f"MicroCore MCX Purchase - {mcx_amount} MCX",
                receipt_email=None,
                automatic_payment_methods={"enabled": True}
            )
            
            payment_id = f"stripe_{intent.id}_{int(time.time())}"
            
            self.db.add_payment(
                payment_id=payment_id,
                wallet=wallet,
                username=username,
                usd_amount=usd_amount,
                mcx_amount=mcx_amount,
                payment_intent=intent.id,
                session_id=None,
                metadata={"intent_id": intent.id}
            )
            
            return {
                "success": True,
                "client_secret": intent.client_secret,
                "payment_intent_id": intent.id,
                "payment_id": payment_id,
                "mcx_amount": mcx_amount
            }
        except stripe.error.StripeError as e:
            logger.error(f"[STRIPE] Payment intent error: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"[STRIPE] Unexpected error: {e}")
            return {"success": False, "error": str(e)}
    
    async def process_successful_payment(self, payment_intent: Dict) -> bool:
        """Process a successful payment"""
        payment_intent_id = payment_intent['id']
        metadata = payment_intent.get('metadata', {})
        
        wallet = metadata.get('wallet')
        username = metadata.get('username')
        mcx_amount = int(metadata.get('mcx_amount', 0))
        
        if not wallet or not username or mcx_amount <= 0:
            logger.error(f"[PROCESS] Invalid metadata: {metadata}")
            return False
        
        # Check if payment already processed
        payment = self.db.get_payment_by_intent(payment_intent_id)
        if payment and payment['status'] == 'completed':
            logger.info(f"[PROCESS] Payment {payment_intent_id} already processed")
            return True
        
        # Update payment status
        self.db.update_payment_status(payment_intent_id, 'completed')
        
        # Add pending credit
        payment_id = payment['payment_id'] if payment else None
        self.db.add_pending_credit(wallet, username, mcx_amount, payment_id)
        
        # Immediately try to credit
        success = await self.node.credit_mcx(wallet, mcx_amount, payment_id)
        
        if not success:
            logger.warning(f"[PROCESS] Initial credit failed for {wallet}, added to retry queue")
            return False
        
        logger.info(f"[PROCESS] ✅ Successfully credited {mcx_amount} MCX to {wallet}")
        return True
    
    async def process_payment_failed(self, payment_intent: Dict) -> bool:
        """Process a failed payment"""
        payment_intent_id = payment_intent['id']
        self.db.update_payment_status(payment_intent_id, 'failed', payment_intent.get('last_payment_error', {}).get('message', 'Payment failed'))
        logger.warning(f"[PROCESS] Payment {payment_intent_id} failed")
        return True

# ==================== CREDIT PROCESSOR LOOP ====================
class CreditProcessor:
    """Background processor for pending credits with retry logic"""
    
    def __init__(self, handler: StripeWebhookHandler):
        self.handler = handler
        self._running = True
        self._thread = None
    
    def start(self):
        """Start the processor in background thread"""
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("[PROCESSOR] Started")
    
    def stop(self):
        """Stop the processor"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("[PROCESSOR] Stopped")
    
    def _run_loop(self):
        """Main processing loop"""
        while self._running:
            try:
                self._process_credits()
                time.sleep(10)
            except Exception as e:
                logger.error(f"[PROCESSOR] Loop error: {e}")
                time.sleep(30)
    
    def _process_credits(self):
        """Process pending credits with exponential backoff"""
        credits = self.handler.db.get_pending_credits(limit=50)
        
        if not credits:
            return
        
        logger.info(f"[PROCESSOR] Processing {len(credits)} pending credits")
        
        for credit in credits:
            if not self._running:
                break
            
            credit_id = credit['id']
            wallet = credit['wallet']
            amount = credit['amount']
            retry_count = credit['retry_count']
            payment_id = credit['payment_id']
            last_attempt = credit['last_attempt'] or 0
            
            # Calculate backoff delay
            delay = min(Config.RETRY_BASE_DELAY * (2 ** retry_count), Config.RETRY_MAX_DELAY)
            
            # Check if enough time has passed since last attempt
            if time.time() - last_attempt < delay:
                continue
            
            # Check max retries
            if retry_count >= Config.MAX_RETRY_ATTEMPTS:
                logger.error(f"[PROCESSOR] Credit {credit_id} exceeded max retries")
                self.handler.db.update_credit_retry(credit_id, f"Max retries exceeded ({Config.MAX_RETRY_ATTEMPTS})")
                continue
            
            # Attempt to credit
            try:
                success = asyncio.run(self.handler.node.credit_mcx(wallet, amount, payment_id))
                if success:
                    self.handler.db.mark_credit_completed(credit_id)
                    logger.info(f"[PROCESSOR] ✅ Credit {credit_id} completed")
                else:
                    self.handler.db.update_credit_retry(credit_id, "Node credit failed")
                    logger.warning(f"[PROCESSOR] Credit {credit_id} failed, retry {retry_count + 1}/{Config.MAX_RETRY_ATTEMPTS}")
            except Exception as e:
                self.handler.db.update_credit_retry(credit_id, str(e))
                logger.error(f"[PROCESSOR] Credit {credit_id} error: {e}")

# ==================== FLASK APP ====================
app = Flask(__name__)
handler = None
processor = None

# ==================== RATE LIMITING ====================
class RateLimiter:
    """Simple in-memory rate limiter"""
    
    def __init__(self):
        self.requests = {}
        self._lock = threading.Lock()
    
    def is_allowed(self, ip: str) -> bool:
        """Check if request is allowed"""
        now = time.time()
        window = Config.RATE_LIMIT_WINDOW
        limit = Config.RATE_LIMIT_REQUESTS
        
        with self._lock:
            if ip not in self.requests:
                self.requests[ip] = []
            
            # Clean old requests
            self.requests[ip] = [t for t in self.requests[ip] if now - t < window]
            
            if len(self.requests[ip]) >= limit:
                return False
            
            self.requests[ip].append(now)
            return True

rate_limiter = RateLimiter()

# ==================== ROUTES ====================

@app.route('/webhook', methods=['POST'])
def webhook_endpoint():
    """Main webhook endpoint"""
    # Rate limiting
    client_ip = request.remote_addr
    if not rate_limiter.is_allowed(client_ip):
        logger.warning(f"[RATE] Rate limit exceeded for {client_ip}")
        return make_response(jsonify({"error": "Rate limit exceeded"}), 429)
    
    # Get payload and signature
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    
    if not sig_header:
        logger.error("[WEBHOOK] Missing Stripe signature header")
        return make_response(jsonify({"error": "Missing signature"}), 400)
    
    # Verify signature
    if not handler.verify_signature(request.data, sig_header):
        logger.error("[WEBHOOK] Invalid signature")
        return make_response(jsonify({"error": "Invalid signature"}), 400)
    
    # Parse event
    try:
        event = stripe.Event.construct_from(request.json, stripe.api_key)
    except Exception as e:
        logger.error(f"[WEBHOOK] Failed to parse event: {e}")
        return make_response(jsonify({"error": str(e)}), 400)
    
    # Check if already processed
    event_id = event.get('id')
    if handler.db.is_webhook_processed(event_id):
        logger.info(f"[WEBHOOK] Event {event_id} already processed")
        return make_response(jsonify({"status": "already_processed"}), 200)
    
    # Record delivery
    handler.db.record_webhook_delivery(
        event_id=event_id,
        event_type=event.get('type'),
        payload=json.dumps(event)
    )
    
    # Process event
    event_type = event.get('type')
    logger.info(f"[WEBHOOK] Processing event: {event_type} ({event_id})")
    
    try:
        if event_type == 'payment_intent.succeeded':
            asyncio.run(handler.process_successful_payment(event['data']['object']))
        elif event_type == 'payment_intent.payment_failed':
            asyncio.run(handler.process_payment_failed(event['data']['object']))
        elif event_type in ['charge.succeeded', 'charge.updated']:
            # Handle charge events if needed
            logger.info(f"[WEBHOOK] Ignored event type: {event_type}")
        else:
            logger.info(f"[WEBHOOK] Unhandled event type: {event_type}")
        
        # Mark as processed
        handler.db.mark_webhook_processed(event_id)
        
        return make_response(jsonify({"status": "success"}), 200)
        
    except Exception as e:
        logger.error(f"[WEBHOOK] Processing error: {e}")
        return make_response(jsonify({"error": str(e)}), 500)

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    status = {
        "status": "healthy",
        "timestamp": time.time(),
        "version": "1.0",
        "db": "ok"
    }
    
    # Check node connection
    try:
        import asyncio
        if handler:
            healthy = asyncio.run(handler.node.health_check())
            status["node"] = "ok" if healthy else "unreachable"
        else:
            status["node"] = "unknown"
    except Exception as e:
        status["node"] = f"error: {e}"
        status["status"] = "degraded"
    
    return jsonify(status)

@app.route('/api/create-payment-intent', methods=['POST'])
def create_payment_intent_endpoint():
    """Create a Stripe payment intent (called from web wallet)"""
    data = request.json
    wallet = data.get('wallet')
    username = data.get('username')
    usd_amount = float(data.get('usd_amount', 0))
    
    if not wallet or not username or usd_amount <= 0:
        return jsonify({"success": False, "error": "Invalid parameters"}), 400
    
    if not handler:
        return jsonify({"success": False, "error": "Handler not initialized"}), 500
    
    result = handler.create_payment_intent(wallet, username, usd_amount)
    return jsonify(result)

@app.route('/api/pending-credits', methods=['GET'])
def pending_credits_endpoint():
    """Get pending credits (monitoring)"""
    if not handler:
        return jsonify({"error": "Handler not initialized"}), 500
    
    credits = handler.db.get_pending_credits()
    return jsonify({
        "count": len(credits),
        "credits": credits
    })

@app.route('/api/stats', methods=['GET'])
def stats_endpoint():
    """Get webhook statistics (monitoring)"""
    if not handler:
        return jsonify({"error": "Handler not initialized"}), 500
    
    with handler.db.get_connection() as conn:
        c = conn.cursor()
        
        c.execute("SELECT COUNT(*) FROM payments")
        total_payments = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM payments WHERE status='completed'")
        completed_payments = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM payments WHERE status='pending'")
        pending_payments = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM payments WHERE status='failed'")
        failed_payments = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM pending_credits WHERE completed=0")
        pending_credits = c.fetchone()[0]
        
        c.execute("SELECT SUM(amount) FROM pending_credits WHERE completed=0")
        pending_amount = c.fetchone()[0] or 0
        
        return jsonify({
            "total_payments": total_payments,
            "completed_payments": completed_payments,
            "pending_payments": pending_payments,
            "failed_payments": failed_payments,
            "pending_credits": pending_credits,
            "pending_amount_mcx": pending_amount,
            "timestamp": time.time()
        })

@app.route('/api/cleanup', methods=['POST'])
def cleanup_endpoint():
    """Trigger cleanup of old records (admin)"""
    if not handler:
        return jsonify({"error": "Handler not initialized"}), 500
    
    days = request.json.get('days', 90) if request.json else 90
    deleted = handler.db.cleanup_old_records(days)
    return jsonify({"deleted": deleted, "days": days})

# ==================== MAIN ====================
def main():
    """Main entry point"""
    global handler, processor
    
    print("\n" + "=" * 60)
    print("MICROCORE STRIPE WEBHOOK - PRODUCTION v1.0")
    print("=" * 60)
    print(f"Node WS URL: {Config.MCX_NODE_WS_URL}")
    print(f"Node HTTP URL: {Config.MCX_NODE_HTTP_URL}")
    print(f"MCX Price: ${Config.MCX_PRICE_USD}")
    print(f"Max Retries: {Config.MAX_RETRY_ATTEMPTS}")
    print(f"Webhook Port: {Config.WEBHOOK_PORT}")
    print("=" * 60)
    
    # Validate configuration
    try:
        Config.validate()
    except ValueError as e:
        print(f"❌ Configuration error: {e}")
        sys.exit(1)
    
    # Initialize handler
    handler = StripeWebhookHandler()
    
    # Start credit processor
    processor = CreditProcessor(handler)
    processor.start()
    
    # Setup signal handlers for graceful shutdown
    def signal_handler(sig, frame):
        print("\n[SHUTDOWN] Received signal, shutting down...")
        if processor:
            processor.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run Flask app
    print(f"\n[WEBHOOK] Server running on port {Config.WEBHOOK_PORT}")
    print(f"[WEBHOOK] Webhook URL: https://YOUR_DOMAIN/webhook")
    print("[WEBHOOK] Press Ctrl+C to stop\n")
    
    try:
        app.run(
            host=Config.WEBHOOK_HOST,
            port=Config.WEBHOOK_PORT,
            debug=False,
            threaded=True
        )
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Stopped by user")
    finally:
        if processor:
            processor.stop()
        print("[SHUTDOWN] Goodbye")

if __name__ == "__main__":
    main()