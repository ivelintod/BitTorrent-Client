import sys
import requests
from collections import OrderedDict
from decoder import Torrent


class Peer:
    pass


class Tracker:
    pass


class Client:

    def __init__(self, torrent_path):
        self.torrent = Torrent(torrent_path)
        self.session = requests.Session()

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
        return 0

    @property
    def uploaded(self):
        return 0

    @property
    def left(self):
        return 1620068363 + 11425562 + 1690

    @property
    def port(self):
        return 6889

    @property
    def compact(self):
        return 1

    def connect(self):
        hdr = self.tracker_header
        hdr.update({'event': 'started'})
        url_prep = requests.Request('GET', self.torrent.announce,
                                    params=hdr).prepare()
        res = self.session.send(url_prep)
        return res

    def connect_to_tracker(self):
        conn = requests.post(self.torrent.announce,
                             data=self.tracker_header)

        return conn

    def request_piece(self, peer: Peer):
        pass


if __name__ == '__main__':
    c = Client(sys.argv[1])
    c.connect()
