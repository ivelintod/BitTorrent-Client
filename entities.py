import sys
import time
import socket
import struct
import threading
from collections import OrderedDict
import requests

import decoder


class Peer:
    def __init__(self, ip, port):
        self.ip = ip
        self.port = port
        self._is_valid = None
        self.sock = self.create_socket()

    def create_socket(self):
        try:
            info = socket.getaddrinfo(self.ip, self.port)
        except socket.gaierror as e:
            print('Error while connecting to socket: %s' % str(s))
        else:
            sock_args = info[0][:3]
            return socket.socket(*sock_args)

    def connect(self, timeout=None):
        if timeout:
            self.sock.settimeout(timeout)
        try:
            self.sock.connect((self.ip, self.port))
        except socket.error as e:
            print('Network failure: ', str(e))
            self.sock.close()
            self._is_valid = False
        else:
            self._is_valid = True

    @property
    def is_valid(self):
        return self._is_valid


class Tracker:
    pass


class Client:

    def __init__(self, torrent_path):
        self.torrent = decoder.Torrent(torrent_path)
        self.session = requests.Session()
        self._downloaded = 0
        self._uploaded = 0
        self._data_left = self.torrent.get_files_length()
        self.peers = []

    @property
    def tracker_header(self):
        tracker_hdr = self.torrent.tracker_info_header
        additional_info = {
            'uploaded': self.uploaded,
            'downloaded': self.downloaded,
            'left': self.left,
            'port': self.port,
            'compact': self.compact
        }

        tracker_hdr.update(additional_info)
        return tracker_hdr

    @property
    def downloaded(self):
        return self._downloaded

    @property
    def uploaded(self):
        return self._uploaded

    @property
    def left(self):
        return self._data_left

    @property
    def port(self):
        return 6889

    @property
    def compact(self):
        return 1

    def connect(self):
        hdr = self.tracker_header
        hdr['event'] = 'started'
        if b'announce-list' in self.torrent.data:
            announces = self.torrent.data[b'announce-list']
            for announce in announces:
                res = self.tracker_request(announce[0], hdr)
                if b'failure' not in res:
                    return res
            return res
        else:
            return self.tracker_request(self.torrent.announce, hdr)

    def tracker_request(self, announce, hdr):
        url_prep = requests.Request('GET', announce.decode('utf-8'),
                                    params=hdr).prepare()
        res = self.session.send(url_prep)
        return res

    def parse_peers(self, resp):
        # following few lines are for eliminating the
        # string length and the semi colons
        resp = resp[5:]
        item = resp[:1]
        while item in b'0123456789':
            resp = resp[1:]
            item = resp[:1]
        print(resp)
        resp = resp[1:]

        peers = {}
        print(resp)
        try:
            # 4 bytes for ip addr and 2 for port nr
            offset = 6
            ind = 0
            peer_nr = 0
            while True:
                peer_info = resp[ind: ind + offset]
                peer_ip = socket.inet_ntoa(peer_info[:4])
                print(peer_info[4:6])
                peer_port = struct.unpack('!H', peer_info[4:6])
                peers[peer_nr] = (peer_ip, peer_port)

                peer_nr += 1
                ind += offset
        except Exception:
            pass

        return peers

    def parse_interval(self, resp):
        resp = resp[9:]
        interval = bytearray()
        while resp[:1] in b'0123456789':
            interval += resp[:1]
            resp = resp[1:]
        return int(interval)

    def parse_binary_response(self, resp):
        possible_items = (b'warning message', b'interval', b'min interval',
                          b'tracker id', b'complete', b'incomplete', b'peers')

        result = {}
        for item in possible_items:
            found = resp.find(item)
            if found != -1:
                str_item = item.decode('utf-8')
                try:
                    result[str_item] = getattr(
                        self, 'parse_{}'.format(str_item))(resp[found:]
                    )
                except AttributeError as e:
                    print('No such parsing method\n', str(e))

        return result

    def parse_tracker_response(self, response):
        try:
            # normal response
            decoded_resp = self.torrent.decode_chunks(response)
            return decoded_resp
        except decoder.UnrecognizedTokenError:
            # binary model response
            return self.parse_binary_response(response)

    def create_handshake(self):
        pstrlen = struct.pack('!b', 19)
        pstr = b'BitTorrent protocol'
        reserved = struct.pack('!II', 0, 0)
        peer_id = self.torrent.tracker_info_header['peer_id'].encode('utf-8')
        info_hash = self.torrent.tracker_info_header['info_hash']
        handshake = b''.join(
            [pstrlen, pstr, reserved, info_hash, peer_id]
        )
        return handshake

    def connect_to_peers(self, peer_list):
        for peer_idx, peer in enumerate(peer_list):
            peer_thr = threading.Thread(target=peer.connect,
                                        kwargs={'timeout': 120})
            peer_thr.start()
            print(Peer, '{} thread started'.format(peer_idx))

        while any(peer.is_valid is None for peer in peer_list):
            time.sleep(1)

        self.peers = [peer for peer in peer_list if peer.is_valid]

    def request_piece(self, peer: Peer):
        pass


if __name__ == '__main__':
    c = Client(sys.argv[1])
    resp = c.connect()
    parsed = c.parse_tracker_response(resp.text.encode('utf-8'))
    peers = [Peer(ip, port[0]) for _, (ip, port) in parsed['peers'].items()]
    handshake = c.create_handshake()
    c.connect_to_peers(peers)
