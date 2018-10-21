import random
import hashlib
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
        return self.encode_entity(self.data)

    def encode_entity(self, ent):
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


class Torrent:

    def __init__(self, torrent):
        self.torrent = torrent
        self.data = self.decode_torrent()
        self.bhash_info = self.encode_torrent(self.data[b'info'])
        # print(self.data)

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
        }
