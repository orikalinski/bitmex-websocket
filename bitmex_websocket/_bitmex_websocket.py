from types import MethodType

from bitmex_websocket import constants
from bitmex_websocket.auth.APIKeyAuth import generate_nonce, generate_signature
from bitmex_websocket.settings import settings
from pyee import EventEmitter
from urllib.parse import urlparse
from websocket import WebSocketApp

import alog
import json
import ssl
import time

__all__ = ['BitMEXWebsocket']


class BitMEXWebsocketConnectionError(Exception):
    pass


class BitMEXWebsocket(EventEmitter, WebSocketApp):
    def __init__(self, should_auth=False, heartbeat=True, ping_interval=10,
                 ping_timeout=9):
        self.ping_timeout = ping_timeout
        self.ping_interval = ping_interval
        self.should_auth = should_auth
        self.heartbeat = heartbeat
        self.channels = []
        self.reconnect_count = 0

        EventEmitter.__init__(self)

        WebSocketApp.__init__(
            self,
            self._url,
            on_message=self.on_message,
            on_close=self.on_close,
            on_open=self.on_open,
            on_error=self.on_error,
            header=self.header(),
            on_pong=self.on_pong
        )

        self.on('subscribe', self.on_subscribe)

    @property
    def _url(self):
        base_url = settings.BASE_URL
        url_parts = list(urlparse(base_url))
        query_string = ''

        if self.heartbeat:
            query_string = '?heartbeat=true'

        url = "wss://{}/realtime{}".format(url_parts[1], query_string)
        return url

    def connect(self):
        """Connect to the websocket in a thread."""

        # setup websocket.run_forever arguments
        ws_run_args = {
            'sslopt': {"cert_reqs": ssl.CERT_NONE}
        }

        if self.heartbeat:
            ws_run_args['ping_timeout'] = self.ping_timeout
            ws_run_args['ping_interval'] = self.ping_interval

        alog.debug(ws_run_args)

        self.run_forever(**ws_run_args)

    def on_pong(self, ws, message):
        timestamp = float(time.time() * 1000)
        latency = timestamp - (self.last_ping_tm * 1000)
        alog.debug("message latency: %s" % (latency))
        self.emit('latency', latency)

    def subscribe_action(self, action, channel, instrument, action_handler):
        alog.info(locals())
        channelKey = "{}:{}".format(channel, instrument)
        alog.debug("Subscribe to action: %s" % (channelKey))
        subscriptionMsg = {"op": "subscribe", "args": [channelKey]}
        action_event_key = self.gen_action_event_key(action,
                                                     instrument,
                                                     channel)
        alog.debug("Subscribe to %s" % (action_event_key))
        self.on(action_event_key, action_handler)

        if channelKey not in self.channels:
            self.channels.append(channelKey)
            alog.debug(subscriptionMsg)
            self._send_message(subscriptionMsg)

    def subscribe(self, channel, handler):
        self._subscribe_to_channel(channel)
        self.on(channel, handler)
        if channel not in self.channels:
            self.channels.append(channel)

    def _subscribe_to_channel(self, channel):
        subscriptionMsg = {"op": "subscribe", "args": [channel]}
        self._send_message(subscriptionMsg)

    def _send_message(self, message):
        self.send(json.dumps(message))

    def is_connected(self):
        return self.sock.connected

    @staticmethod
    def on_subscribe(message):
        if message['success']:
            alog.debug("Subscribed to %s." % message['subscribe'])
        else:
            raise Exception('Unable to subsribe.')

    def on_message(self, ws, message):
        """Handler for parsing WS messages."""

        message = json.loads(message)
        alog.debug(alog.pformat(message))

        action = message['action'] if 'action' in message else None

        if action:
            table = message['table']
            event_name = ''
            if table in constants.CHANNELS:
                event_name = "%s:%s" % (action, table)
            else:
                if len(message['data']) > 0:
                    instrument = message['data'][0]['symbol']
                    event_name = self.gen_action_event_key(action,
                                                           instrument,
                                                           table)
            alog.debug(event_name)
            self.emit(event_name, message)
        elif 'subscribe' in message:
            self.emit('subscribe', message)
        elif 'status' in message:
            self.emit('status', message)

    def gen_action_event_key(self, event, instrument, table):
        return "%s:%s:%s" % (event, instrument, table)

    def header(self):
        """Return auth headers. Will use API Keys if present in settings."""
        alog.debug('shouldAuth: %s' % self.should_auth)

        if self.should_auth:
            alog.info("Authenticating with API Key.")
            # To auth to the WS using an API key, we generate a signature
            # of a nonce and the WS API endpoint.
            alog.debug(settings.BITMEX_API_KEY)
            nonce = generate_nonce()
            api_signature = generate_signature(
                settings.BITMEX_API_SECRET, 'GET', '/realtime', nonce, '')

            auth = [
                "api-nonce: " + str(nonce),
                "api-signature: " + api_signature,
                "api-key:" + settings.BITMEX_API_KEY
            ]
            alog.debug(auth)

            return auth
        else:
            return []

    def on_open(self, ws):
        alog.debug("Websocket Opened.")
        self.emit('open')

    def on_close(self, ws):
        alog.info('Websocket Closed')

    def on_error(self, ws, error):
        raise BitMEXWebsocketConnectionError(error)

