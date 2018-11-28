import sys
import time
import queue
import select
import socket
import struct
import threading
import traceback
from collections import OrderedDict
import requests

import decoder


TORRENTS = []
TORRENT_TO_MESSAGES = {}
TORRENT_TO_PEER_QUEUE = {}


def register_torrent(torrent):
    """Register torrents in chronological order"""
    TORRENTS.append(torrent)
    peer_id = torrent.tracker_info_header['peer_id'].encode('utf-8')
    info_hash = torrent.tracker_info_header['info_hash']
    TORRENT_TO_MESSAGES[torrent] = PeerMessage(peer_id, info_hash)
    TORRENT_TO_PEER_QUEUE[torrent] = queue.Queue()


def get_torrent_msg_rel():
    """
    Use latest torrent from here instead of
    passing it every time to PeerMessages
    as a parameter wherever needed in
    current torrent processing
    """
    return TORRENT_TO_MESSAGES[TORRENTS[-1]]


def get_torrent_queue_rel():
    """Same as above but for peer queue"""
    return TORRENT_TO_PEER_QUEUE[TORRENTS[-1]]


def get_current_torrent():
    """Get latest registered torrent"""
    return TORRENTS[-1]


class Peer:

    MAX_CONN_ATTEMPTS = 3

    def __init__(self, ip, port, sock=None):
        self.ip = ip
        self.port = port
        self._is_valid = None
        self.connection_attempts = 0
        if not sock:
            self.sock = self.create_client_socket()
        else:
            self.sock = sock
            self._is_valid = True

    def create_client_socket(self):
        """Create socket ready to CONNECT to peers from tracker response"""
        try:
            info = socket.getaddrinfo(self.ip, self.port)
        except socket.gaierror as err:
            print('Error while connecting to socket: %s' % str(err))
        else:
            sock_args = info[0][:3]
            return socket.socket(*sock_args)

    def reset(self):
        self._is_valid = None
        self.connection_attempts = 0

    def connect(self, timeout=None):
        if timeout:
            self.sock.settimeout(timeout)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            self.sock.bind(("0.0.0.0", 6889))
            self.connection_attempts += 1
            self.sock.connect((self.ip, self.port))
        except socket.error as e:
            # print('Network failure: ', str(e))
            if self.connection_attempts == self.MAX_CONN_ATTEMPTS:
                self.sock.close()
                self._is_valid = False
            else:
                # print('Attempting once more...')
                self.sock.close()
                self.sock = self.create_client_socket()
                time.sleep(2)
                self.connect(timeout)
        else:
            self._is_valid = True

    def recv(self, msg_len=5):
        # data = b''
        # while True:
        #     print('receiving')
        #     more = self.sock.recv(0xff)
        #     print('done')
        #     if not more:
        #         return data
        #     data += more
        return self.sock.recv(msg_len)

    def send(self, msg):
        self.sock.sendall(msg)

    @property
    def is_valid(self):
        return self._is_valid


from requests.adapters import HTTPAdapter
from requests.packages.urllib3.poolmanager import PoolManager


class SourcePortAdapter(HTTPAdapter):
    """"Transport adapter" that allows us to set the source port."""
    def __init__(self, port, *args, **kwargs):
        self._source_port = port
        super(SourcePortAdapter, self).__init__(*args, **kwargs)

    def init_poolmanager(self, connections, maxsize, block=False):
        self.poolmanager = PoolManager(
            num_pools=connections, maxsize=maxsize,
            block=block, source_address=('0.0.0.0', self._source_port),
            socket_options=[(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1), (socket.SOL_SOCKET, socket.SO_REUSEPORT, 1), (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
        )


class Tracker:

    def __init__(self, port, compact):
        self.torrent = get_current_torrent()
        self.port = port
        self.compact = compact
        self.session = requests.Session()
        self.session.mount('http://', SourcePortAdapter(self.port))

    @property
    def tracker_header(self):
        """
        Tracker header from bdecoded torrent extended
        """
        tracker_hdr = self.torrent.tracker_info_header
        additional_info = {
            'port': self.port,
            'compact': self.compact
        }

        tracker_hdr.update(additional_info)
        return tracker_hdr

    def connect(self):
        """
        Prepare and send request to the tracker
        """
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
        """
        Actual request sending
        """
        url_prep = requests.Request('GET', announce.decode('utf-8'),
                                    params=hdr).prepare()
        res = self.session.send(url_prep)
        return res


class Client:

    def __init__(self, torrent_path):
        self.torrent = decoder.Torrent(torrent_path)

        register_torrent(self.torrent)

        self.tracker = Tracker(self.port, self.compact)
        self.peers = []
        self.peers_queue = None
        self._terminate = False

        self.server_socket_thread = threading.Thread(
            target=self.create_server_socket
        )

        self.peer_loop = PeerLoop()


    @property
    def port(self):
        """
        TCP port on which client operates (hardcoded for now)
        """
        return 6889

    @property
    def compact(self):
        """
        Format in which we would like tracker response
        """
        return 1

    def terminate(self):
        self._terminate = True

    def start(self):
        """
        Method for initiating all torrent downloading processes
        """
        self.peers_queue = get_torrent_queue_rel()
        self.server_socket_thread.start()
        self.peer_loop.start()

        tracker_resp = self.tracker.connect()
        parsed = self.parse_tracker_response(tracker_resp.text.encode('utf-8'))
        peers = [Peer(ip, port[0]) for _, (ip, port)
                 in parsed[b'peers'].items()]
        threading.Thread(target=self.connect_to_peers, args=(peers,)).start()

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
                    result[item] = getattr(
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

    def create_server_socket(self):
        external_ip = requests.get('https://ipinfo.io/ip').text.strip()
        serv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        serv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        serv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        try:
            serv_sock.bind((external_ip, self.port))
        except OSError:
            # in case we are behind a NAT
            serv_sock.bind(('0.0.0.0', self.port))
        serv_sock.listen(100)
        while not self._terminate:
            print('*******************************************************************************************************')
            print('Waiting for other peers...')
            client_sock, client_addr = serv_sock.accept()
            client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            self.peers_queue.put(Peer(client_addr[0], client_addr[1],
                                      sock=client_sock))

    def connect_to_peers(self, peer_list):
        while not self._terminate:
            for peer_idx, peer in enumerate(peer_list):
                peer.reset()
                peer_thr = PeerThread(peer,
                                      self.peers_queue,
                                      timeout=20)
                peer_thr.start()
                # print(Peer, '{} thread started'.format(peer_idx))

            while any(peer.is_valid is None for peer in peer_list):
                time.sleep(1)

            print('cycle done')
            print(len(peer_list))
            for peer in peer_list:
                if peer.is_valid:
                    self.peers.append(peer)

            peer_list = [peer for peer in peer_list if not peer.is_valid]

            time.sleep(2)


class PeerThread(threading.Thread):

    def __init__(self, peer, queue, **kwargs):
        self.peer = peer
        self.peer_queue = queue
        super().__init__(kwargs=kwargs)

    def run(self):
        self.peer.connect(**self._kwargs)
        if self.peer.is_valid:
            self.peer_queue.put(self.peer)


class PeerLoop(threading.Thread):

    def __init__(self):
        self.peer_queue = get_torrent_queue_rel()
        self.peer_messages = get_torrent_msg_rel()
        self.processed_peers = {}
        self.message_queues = {}
        self.peer_buffers = {}
        self.peer_msg_rel = {}
        self._terminate = False
        super().__init__()

    def run(self):
        threading.Thread(target=self.peer_communication_handler).start()
        self._reader_loop()

    def terminate(self):
        self._terminate = True

    def check_handshake_response(self, resp, handshake):
        # checking if hash_info matches
        return resp[28: 48] == handshake[28:48]

    def runtime_removal(self, peer_sock, *sock_lists):
        if peer_sock in self.processed_peers:
            print('TRIENIEEEEEEEEEEEEEEEEEEEEEE')
            del self.processed_peers[peer_sock]

        for sock_list in sock_lists:
            if peer_sock in sock_list:
                sock_list.remove(peer_sock)

    def peer_communication_handler(self):
        handshake = Handshake(self.peer_messages.peer_id,
                              self.peer_messages.info_hash)
        interested = Interested()
        request = Request()
        handshake_sent_peers = set()
        print('starteeeed')

        while not self._terminate:
            read, write, err = select.select(self.processed_peers.keys(),
                                             self.processed_peers.keys(),
                                             [], 0.1)
            for peer_sock in read:
                peer = self.processed_peers[peer_sock]

                msg = self.peer_msg_rel[peer]

                try:
                    if msg and msg.msg_len:
                        print(msg.msg_len)
                        resp = peer.recv(msg.msg_len)
                        first = False
                    else:
                        resp = peer.recv()
                        first = True
                except socket.error as e:
                    print('Erroneous client', str(e))
                    self.runtime_removal(peer_sock, write, err)
                    continue

                time.sleep(0.5)
                print(peer_sock, 'delegating')
                print('first', msg.msg_buffer if msg else None)
                print('resp', resp)

                if not msg or not msg.msg_buffer:
                    try:
                        msg_instance = self.peer_messages.delegate(resp)
                        if not msg_instance:
                            self.runtime_removal(peer_sock, write, err)
                            continue
                        self.peer_msg_rel[peer] = msg_instance
                        print('MSG LEN', msg_instance.msg_len)
                    except Exception:
                        print('Exception occurred', traceback.format_exc())

                msg.msg_buffer += resp

                if len(msg.msg_buffer) == msg.initial_len + 4:
                    self.message_queues[peer].put(msg)
                    print('second', msg.msg_buffer)
                    msg.msg_buffer = bytearray()
                    print('SUCCESS')

                if msg:
                    if first:
                        msg.msg_len -= 1
                    else:
                        msg.msg_len -= len(resp)

            for peer_sock in write:
                peer = self.processed_peers[peer_sock]
                msg_queue = self.message_queues[peer]
                if not msg_queue.empty():
                    msg = msg_queue.get_nowait()
                else:
                    continue

                is_valid, info = msg.decode(msg.msg_buffer)
                if not is_valid:
                    self.runtime_removal(peer_sock, err)
                    continue

                try:
                    if peer not in handshake_sent_peers:
                        peer.send(handshake.encode())
                        handshake_sent_peers.add(peer)
                        continue
                    peer.send(interested.encode())
                    peer.send(request.encode())
                except socket.error as e:
                    print('There was an exception:', str(e))
                    self.runtime_removal(peer_sock)
                time.sleep(2)

            for peer in err:
                print('Erroneous peer', peer)


    def peer_message_handler(self):
        while not self._terminate:
            msg = self.message_queues



        # peer.send(handshake.encode())
        # inp, _, _ = select.select([peer.sock], [], [], 10)
        # print('Inp1: ', inp)
        # if not inp:
        #     return

        # resp = peer.recvall()
        # print(resp)
        # if not handshake.decode(resp):
        #     print('Peer({} {}) is fishy!'.format(peer.ip, peer.port))
        #     return

        # while True:
        #     interested = Interested(self.peer_messages.peer_id,
        #                             self.peer_messages.info_hash)
        #     peer.send(interested.encode())
        #     inp, _, _ = select.select([peer.sock], [], [], 10)
        #     print('Inp2: ', inp)
        #     # if not inp:
        #         # return

        #     resp = peer.recvall()
        #     print('Resp', resp)
        #     if not resp:
        #         print('Peer connection broken')
        #         break


        #     request = Request(self.peer_messages.peer_id,
        #                       self.peer_messages.info_hash)
        #     peer.send(request.encode())

        #     inp, _, _ = select.select([peer.sock], [], [], 10)
        #     print('Inp3: ', inp)
        #     resp = peer.recvall()
        #     print('Data: ', resp)
        #     if not resp:
        #         print('Peer connection broken')
        #         break

    def _reader_loop(self):
        while not self._terminate:
            try:
                new_peer = self.peer_queue.get()
            except queue.Empty:
                continue

            if new_peer not in self.processed_peers:
                print('new peer')
                self.processed_peers[new_peer.sock] = new_peer
                self.message_queues[new_peer] = queue.Queue()
                self.peer_msg_rel[new_peer] = None


class PeerMessage:

    msg_ids = {
        b'\x00': 'Choke',
        b'\x01': 'Unchoke',
        b'\x02': 'Interested',
        b'\x03': 'NotInterested',
        b'\x04': 'Have',
        b'\x05': 'Bitfield',
        b'\x06': 'Request',
        b'\x07': 'Piece',
        b'\x08': 'Cancel',
        b'\x09': 'Port',
        b'\x13': 'Handshake'
    }

    def __init__(self, peer_id=None, info_hash=None):
        self.peer_id = peer_id
        self.info_hash = info_hash
        self.initial_len = None
        self.msg_len = None
        self.msg_buffer = bytearray()

    @staticmethod
    def get_len(msg):
        return struct.unpack('!I', msg[:4])[0]

    def delegate(self, msg):
        """Delegate to correct message class"""
        if not msg:
            return
        if PeerMessage.msg_ids.get(msg[:1], '') == 'Handshake':
            msg_id = msg[:1]
        # elif len(msg) > 4:
        else:
            msg_id = msg[4:5]
        # else:
            #Probably keep-alive msg, nothing to do
            # return None

        print(msg)

        clz = globals().get(PeerMessage.msg_ids.get(msg_id, ''), '')
        if not clz:
            return None

        clz_instance = clz(self.peer_id, self.info_hash)
        clz_instance.msg_len = clz.get_len(msg)
        clz_instance.initial_len = clz.get_len(msg)
        return clz_instance


    def decode(self, msg):
        """Deal with incoming msg response"""
        raise NotImplementedError


class Handshake(PeerMessage):

    def encode(self):
        pstrlen = struct.pack('!B', 19)
        pstr = b'BitTorrent protocol'
        reserved = struct.pack('!II', 0, 0)
        handshake = b''.join(
            [pstrlen, pstr, reserved, self.info_hash, self.peer_id]
        )
        return handshake

    def decode(self, msg):
        if not msg[28: 48] == self.encode()[28: 48]:
            return False, None
        return True, None

    @staticmethod
    def get_len(msg):
        return 64


class KeepAlive(PeerMessage):

    def encode(self):
        return struct.pack('!I', 0)

    def decode(self, msg):
        if msg[3:4] == b'\x00':
            return True, msg[1:]
        return False, None


class Choke(PeerMessage):

    def encode(self):
        return struct.pack('!IB', 1, 0)

    def decode(self, msg):
        if msg[:2] == b'\x01\x00':
            return True, msg[2:]
        return False, None


class Unchoke(PeerMessage):

    def encode(self):
        return struct.pack('!IB', 1, 1)

    def decode(self, msg):
        if msg[:2] == b'\x01\x01':
            return True, msg[2:]
        return False, None


class Interested(PeerMessage):

    def encode(self):
        return struct.pack('!IB', 1, 2)

    def decode(self, msg):
        if msg[:2] == b'\x01\x02':
            return True, msg[2:]
        return False, None


class NotInterested(PeerMessage):

    def encode(self):
        return struct.pack('!IB', 1, 3)

    def decode(self, msg):
        if msg[:2] == b'\x01\x03':
            return True, msg[2:]
        return False, None


class Have(PeerMessage):

    def encode(self, piece_index):
        len_id = struct.pack('!IB', 5, 4)
        payload = struct.pack('!I', piece_index)
        return len_id + payload

    def decode(self, msg):
        _, _, index = struct.unpack('!IBI', msg)


class Bitfield(PeerMessage):

    def encode(self, bitfield, index, begin, length):
        len_id = struct.pack('!IB', len(bitfield) + 1, 5)
        return len_id

    def decode(self, msg):
        pass


class Request(PeerMessage):

    def encode(self, index=0, begin=0, length=2**14):
        len_id = struct.pack('!IB', 13, 6)
        payload = struct.pack('!III', index, begin, length)
        return len_id + payload

    def decode(self, msg):
        _, _, index, begin, length = struct.unpack('!IBIII', msg)


class Piece(PeerMessage):

    def encode(self, index, begin, block):
        len_id = struct.pack('!IB', len(block) + 9, 7)
        payload = struct.pack('II', index, begin) + block
        return len_id + payload

    def decode(self, msg):
        _, _, index, begin = struct.unpack('!IBIII', msg[:13])
        block = msg[13:]


if __name__ == '__main__':
    c = Client(sys.argv[1])
    c.start()
