import queue
import threading


TORRENTS = []
TORRENT_TO_MESSAGES = {}
TORRENT_TO_PEER_QUEUE = {}
TORRENT_TO_PIECES_DATA_QUEUE = {}


def register_torrent(torrent, msg_class):
    """Register torrents in chronological order"""
    TORRENTS.append(torrent)
    peer_id = torrent.tracker_info_header['peer_id'].encode('utf-8')
    info_hash = torrent.tracker_info_header['info_hash']
    TORRENT_TO_PEER_QUEUE[torrent] = queue.Queue()
    TORRENT_TO_PIECES_DATA_QUEUE[torrent] = queue.Queue()
    TORRENT_TO_MESSAGES[torrent] = msg_class(peer_id, info_hash)


def get_current_torrent():
    """Get latest registered torrent"""
    return TORRENTS[-1]


def get_current_pieces_manager():
    """Get pieces_manager instance from latest registered torrent"""
    return get_current_torrent().pieces_manager


def get_torrent_msg_rel():
    """
    Use latest torrent from here instead of
    passing it every time to PeerMessages
    as a parameter wherever needed in
    current torrent processing
    """
    return TORRENT_TO_MESSAGES[get_current_torrent()]


def get_torrent_peers_queue_rel():
    """Same as above but for peer queue"""
    return TORRENT_TO_PEER_QUEUE[get_current_torrent()]


def get_torrent_pieces_data_queue_rel():
    """Same as above but for pieces queue"""
    return TORRENT_TO_PIECES_DATA_QUEUE[get_current_torrent()]


class PiecesPeersTransport:

    def __init__(self):
        self.lock = threading.Lock()
        self.peers_pieces_queues = {}

    def register(self, peer):
        with self.lock:
            self.peers_pieces_queues[peer] = queue.Queue(maxsize=5)

    def __getitem__(self, item):
        return self.peers_pieces_queues[item]

    def __iter__(self):
        return (peer for peer in self.peers_pieces_queues)

    # def __next__(self):
    #     return (peer for peer in self.peers_pieces_queues)


class PiecesPeersTransportFactory:

    torrent_mapping = {}

    @classmethod
    def produce(cls, torrent):
        if torrent in cls.torrent_mapping:
            return cls.torrent_mapping[torrent]
        new_transport_instance = PiecesPeersTransport()
        cls.torrent_mapping[torrent] = new_transport_instance
        return new_transport_instance
