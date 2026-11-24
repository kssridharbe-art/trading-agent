"""
WebSocket Live Feed Module for Delta Exchange.
Maintains continuous WebSocket connection, subscribes to real-time ticker
and candle updates, sends periodic heartbeats, and reconnects automatically.
"""

import asyncio
import json
import logging
import threading
import time
from typing import Callable, Optional, Dict, Any
import websockets

logger = logging.getLogger("DeltaWebSocketFeed")


class DeltaWebSocketFeed:
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        ws_url: str = "wss://socket.delta.exchange",
        on_price_update: Optional[Callable[[float, int], None]] = None,
        on_candle_close: Optional[Callable[[Dict[str, Any]], None]] = None
    ):
        self.symbol = symbol
        self.ws_url = ws_url
        self.on_price_update = on_price_update
        self.on_candle_close = on_candle_close
        
        self.is_running = False
        self.thread: Optional[threading.Thread] = None
        self.current_price: Optional[float] = None
        self.last_heartbeat_time = 0
        self.connected = False

    def start(self):
        """Starts WebSocket client in a background daemon thread."""
        if self.is_running:
            return
        self.is_running = True
        self.thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self.thread.start()
        logger.info(f"WebSocket client thread started for {self.symbol}.")

    def stop(self):
        """Stops the WebSocket client."""
        self.is_running = False
        self.connected = False
        logger.info("WebSocket client stop requested.")

    def _run_event_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._connect_and_listen())

    async def _connect_and_listen(self):
        backoff = 1
        while self.is_running:
            try:
                logger.info(f"Connecting to Delta WebSocket at {self.ws_url}...")
                async with websockets.connect(self.ws_url, ping_interval=None) as ws:
                    self.connected = True
                    backoff = 1
                    logger.info("Connected to Delta WebSocket.")

                    # Subscribe to ticker and candlestick_5m
                    sub_message = {
                        "type": "subscribe",
                        "payload": {
                            "channels": [
                                {
                                    "name": "v2/ticker",
                                    "symbols": [self.symbol]
                                },
                                {
                                    "name": "candlestick_5m",
                                    "symbols": [self.symbol]
                                }
                            ]
                        }
                    }
                    await ws.send(json.dumps(sub_message))
                    logger.info(f"Subscribed to ticker and 5m candles for {self.symbol}.")

                    # Spawn heartbeat task
                    heartbeat_task = asyncio.create_task(self._heartbeat(ws))

                    try:
                        while self.is_running:
                            message = await ws.recv()
                            self._handle_message(message)
                    finally:
                        heartbeat_task.cancel()

            except Exception as e:
                self.connected = False
                logger.warning(f"WebSocket connection error: {e}. Reconnecting in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _heartbeat(self, ws):
        """Sends ping every 25 seconds to keep connection alive."""
        try:
            while self.is_running:
                await asyncio.sleep(25)
                ping_msg = {"type": "ping"}
                await ws.send(json.dumps(ping_msg))
                self.last_heartbeat_time = int(time.time())
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Heartbeat send failed: {e}")

    def _handle_message(self, message_str: str):
        try:
            msg = json.loads(message_str)
            msg_type = msg.get("type", "")

            # Ping response
            if msg_type == "pong":
                return

            channel = msg.get("channel", "")

            # 1. Ticker price update
            if "ticker" in channel:
                mark_price = msg.get("mark_price") or msg.get("close")
                if mark_price:
                    price = float(mark_price)
                    self.current_price = price
                    if self.on_price_update:
                        ts = int(msg.get("timestamp", time.time()))
                        self.on_price_update(price, ts)

            # 2. Candlestick update
            elif "candlestick" in channel:
                candle_data = msg.get("candle") or msg
                if self.on_candle_close:
                    self.on_candle_close(candle_data)

        except Exception as e:
            logger.error(f"Error parsing WebSocket message: {e}")
