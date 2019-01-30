import queue
import random
import hashlib
import threading
from collections import OrderedDict


class UnrecognizedTokenError(Exception):
    pass


class BaseDecoderEncoder:

    LIST = b'l'
    INT = b'i'
    STR = b'0123456789'
    DICT = b'd'
    END = b'e'
    SEP = b':'

    tokens = {
        'list': lambda x: x == BaseDecoderEncoder.LIST,
        'int': lambda x: x == BaseDecoderEncoder.INT,
        'str': lambda x: x in BaseDecoderEncoder.STR,
        'dict': lambda x: x == BaseDecoderEncoder.DICT,
        'end': lambda x: x == BaseDecoderEncoder.END,
        'sep': lambda x: x == BaseDecoderEncoder.SEP
    }

    encode_info = {
        list: (b'l', b'e'),
        bytes: (b'', b''),
        OrderedDict: (b'd', b'e'),
    }

    def __init__(self, data):
        self._data = data
        self.index = 0

    @property
    def data(self):
        return self._data


class OrderedDecoder(BaseDecoderEncoder):

    def __init__(self, data):
        if not isinstance(data, (bytes, bytearray)):
            raise RuntimeError('Input data must be bytes.')
        super().__init__(data)
        self.decoded_data = OrderedDict()

    def get_token_type(self, token):
        for tk in self.tokens:
            if self.tokens[tk](token):
                return tk

    def decode_list(self, res_list=None):
        """Method for list decoding"""
        if not res_list:
            res_list = []
            self.move()
        el = self.decode_current_token()
        if el is not None and el != self.END:
            res_list.append(el)
            self.decode_list(res_list)
        return res_list

    def decode_int(self):
        """Method for int decoding"""
        self.move()
        num = b''
        while self.data[self.index: self.index + 1] != b'e':
            num += self.data[self.index: self.index + 1]
            self.move()
        self.move()
        return num

    def get_str_digits_len(self):
        """Method for finding length of forthcoming string"""
        str_dig_len = 1
        while self.data[self.index + str_dig_len:
                        self.index + str_dig_len + 1] in self.STR:
            str_dig_len += 1
        return str_dig_len

    def decode_str(self):
        """Method for string decoding"""
        str_dig_len = self.get_str_digits_len()
        str_len = int(self.data[self.index: self.index + str_dig_len].
                      decode('utf-8'))
        self.move(str_dig_len + 1)
        string = self.data[self.index: self.index + str_len]
        self.move(str_len)
        return string

    def move(self, dist=1):
        """Shortcut for index incrementation"""
        self.index += dist

    def decode_dict(self, res_dict=None):
        """Method for dict decoding"""
        if not res_dict:
            res_dict = OrderedDict()
            self.move()
        key = self.decode_current_token()
        if key == self.END:
            return res_dict
        value = self.decode_current_token()
        if not any(el is None for el in (key, value)):
            res_dict[key] = value
            self.decode_dict(res_dict)
        return res_dict

    def decode_end(self):
        """Move index by 1 on end match, return end symbol"""
        self.move()
        return self.END

    def decode_sep(self):
        """Move index by 1 on separator match"""
        self.move()

    def decode_current_token(self):
        """The real decoding deal"""
        element = self.data[self.index: self.index + 1]
        token = self.get_token_type(element)
        if token:
            if token == BaseDecoderEncoder.SEP:
                self.decode_sep()
                return self.decode_current_token()
            return getattr(self, 'decode_{}'.format(token))()
        raise UnrecognizedTokenError

    def decode(self):
        """Decoding symbolic start method"""
        return self.decode_current_token()


class OrderedEncoder(BaseDecoderEncoder):

    def encode(self):
        """Encoding symbolic start method"""
        return self.encode_entity(self.data)

    def encode_entity(self, ent):
        """The real encoding deal"""
        if type(ent) is bytes and all(el in b'0123456789' for el in ent):
            return b'i' + ent + b'e'
        else:
            start_b, end_b = self.encode_info[type(ent)]
            data = b''
            if type(ent) is OrderedDict:
                for key, val in ent.items():
                    data += self.encode_entity(key)
                    data += self.encode_entity(val)
            elif type(ent) is list:
                for el in ent:
                    data += self.encode_entity(el)
            else:
                return str(len(ent)).encode('utf-8') + b':' + ent
            return start_b + data + end_b


class Block:
    """Integral part of the piece as a whole"""

    MISSING = 1
    PROCESSING = 2
    COMPLETE = 3

    def __init__(self, length, offset):
        self.length = length
        self.offset = offset
        self.state = self.MISSING
        self.data = bytearray()

    def fill_block_with_data(self, data: bytes):
        if len(self.data) == self.length:
            self.data.extend(data)
            self.state = self.COMPLETE
        else:
            self.state = self.MISSING


class Piece:
    """A sum of a number of blocks"""

    REQ_SIZE = 2**14  # 16KB block (stated as optimal in wiki)

    def __init__(self, length, sha1):
        self.length = length
        self.sha1 = sha1
        if self.length % self.REQ_SIZE:
            self.nr_blocks = self.length // self.REQ_SIZE + 1
            modify_last_block = True
        else:
            self.nr_blocks = self.length // self.REQ_SIZE
            modify_last_block = False
        self.blocks = [Block(self.REQ_SIZE, self.REQ_SIZE * i) for i in range(self.nr_blocks)]
        if modify_last_block:
            self.blocks[-1].length -= self.length - (self.length % self.REQ_SIZE)
        self.left = self.length
        self.is_complete = False

    def missing_blocks(self):
        return {block for block in self.blocks if block.state == block.MISSING}

    def pending_blocks(self):
        return {block for block in self.blocks if block.state == block.PROCESSING}

    def is_complete(self):
        return all(block.state == block.COMPLETE for block in self.blocks)

    def complete_raw_data(self):
        data = bytearray()
        for block in self.blocks:
            data.extend(block.data)
        return data

    def check_integrity(self):
        return hashlib.sha1(self.complete_raw_data()).digest() == self.sha1


class AutoFillQueue(queue.Queue):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cb = None

    def connect(self, callback):
        self.cb = callback

    def get(self, *args, **kwargs):
        if self.empty():
            self.put(self.cb())
        return super().get(*args, **kwargs)


class PieceManager(threading.Thread):

    def __init__(self, pieces: dict, *args,
                 pieces_data_queue=None,
                 pieces_info_queue=None,
                 **kwargs):

        super().__init__(*args, **kwargs)
        self.pieces = pieces
        self.pieces_data_queue = pieces_data_queue
        self.pieces_info_queue = pieces_info_queue
        if self.pieces_info_queue is not None:
            self.pieces_info_queue.connect(self.get_piece_info_for_request)
        self.pieces_lock = threading.Lock()
        self.current_piece_ind = 0
        self._terminate = False

    # def get_pieces_of_interest(self):
    #     return {piece for piece in self.pieces if not piece.is_complete}

    def set_queues(self, pieces_data_queue, pieces_info_queue):
        self.pieces_data_queue = pieces_data_queue
        self.pieces_info_queue = pieces_info_queue
        self.pieces_info_queue.connect(self.get_piece_info_for_request)

    def get_piece_info_for_request(self):
        with self.pieces_lock:
            piece_of_interest = self.pieces[self.current_piece_ind]
            for block in piece_of_interest.blocks:
                if block.MISSING:
                    return self.current_piece_ind, block.offset, block.length

    def run(self):
        if not self.pieces_info_queue or not self.pieces_data_queue:
            raise RuntimeError('Queues for piece management not set!')
        while not self._terminate:
            piece_ind, block_offset, block_data = self.pieces_data_queue.get()
            with self.pieces_lock:
                piece = self.pieces[piece_ind]
                for block in self.pieces[piece_ind]:
                    if block_offset == block.offset:
                        block.fill_block_with_data(block_data)
                        break
                    if piece.is_complete() and piece.check_integrity():
                        self.current_piece_ind += 1

    # def run(self):
    #     while not self._terminate:
    #         for piece in self.get_pieces_of_interest():
    #             pass


class Torrent:

    def __init__(self, torrent):
        self.torrent = torrent
        self.data = self.decode_torrent()
        self.bhash_info = self.encode_torrent(self.data[b'info'])

        self._downloaded = 0
        self._uploaded = 0
        self._data_left = self.get_files_length()
        self.sha_pieces = {
            ind: self.info[b'pieces'][i: i + 20]
            for ind, i in enumerate(
                range(0, len(self.info[b'pieces']), 20)
            )
        }
        self.actual_data = {
            piece_ind: Piece(int(self.info[b'piece length']),
                             self.sha_pieces[piece_ind]) for
            piece_ind in range(len(self.piece_length))
        }

        self.pieces_manager = PieceManager(self.actual_data)

    def decode_torrent(self):
        """Returns decoded torrent metainfo as python types"""
        with open(self.torrent, 'rb') as fd:
            return OrderedDecoder(fd.read()).decode()

    def decode_chunks(self, data):
        return OrderedDecoder(data).decode()

    def encode_torrent(self, data):
        return OrderedEncoder(data).encode()

    def get_files_length(self):
        return sum(int(x[b'length']) for x in self.data[b'info'][b'files'])

    @staticmethod
    def key_search(key, data):
        """Make fields with valid names as attributes
           available as class fields"""
        if key in data:
            return data[key]
        for k in data:
            if type(data[k]) == dict:
                return Torrent.key_search(key, data[k])

        return None

    def __getattr__(self, attr):
        try:
            return super().__getattr__(attr)
        except AttributeError:
            return Torrent.key_search(bytes(attr, encoding='utf-8'),
                                      self.data)

    @property
    def piece_length(self):
        """Returns 'piece length' field"""
        return self.data[b'info'][b'piece length']

    @property
    def created_by(self):
        """Returns 'created by' field if present"""
        return self.data.get('created by', None)

    @property
    def created_with(self):
        """Returns 'created with' field if present"""
        return self.data.get('created with', None)

    @property
    def tracker_info_header(self):
        """Returns torrent related info for tracker connection"""
        info_hash = hashlib.sha1(self.bhash_info).digest()
        peer_id = '-PC0001-' + ''.join(str(random.randint(0, 9))
                                       for _ in range(12))

        return {
            'info_hash': info_hash,
            'peer_id': peer_id,
            'uploaded': self.uploaded,
            'downloaded': self.downloaded,
            'left': self.left,
        }

    @property
    def downloaded(self):
        return self._downloaded

    @property
    def uploaded(self):
        return self._uploaded

    @property
    def left(self):
        return self._data_left

    # def verify_piece(self, piece, piece_ind):
    #     """Verify assembled piece integrity"""
    #     assert hashlib.sha1(piece).digest() == self.sha_pieces[piece_ind]

