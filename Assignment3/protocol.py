from __future__ import generator_stop
import Crypto.Hash.HMAC
from Crypto.Cipher import AES
from Crypto.Hash import SHA256
import time
import os

# generator and modulus chosen from rfc3526, specifically the smallest secure (see submission doc) MODP Group - group 14
# https://www.ietf.org/rfc/rfc3526.txt
generator = 2
modulus = 0xFFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7EDEE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3BE39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA051015728E5A8AACAA68FFFFFFFFFFFFFFFF

class Protocol:

    # Initializer (Called from app.py)
    def __init__(self):
        self._skey = None
        self._ikey = None
        self.expE = None
        self.expI = None
        self.isClient = False
        self.sharedSecret = None
        self.timestamp = 0

    def setSharedSecret(self,ss):
        h = SHA256.new()
        h.update(ss.encode('utf-8'))
        self.sharedSecret = h.digest()

    # Creating the initial message of your protocol (to be send to the other party to bootstrap the protocol)
    #Only called by clients
    def GetProtocolInitiationMessage(self):
        assert self.sharedSecret is not None

        cipher = AES.new(self.sharedSecret, AES.MODE_CCM)
        # the crypto library suggests using the os random number generator and generally considers it to be a cryptographically secure random number generator:
        # link here: https://cryptography.io/en/latest/random-numbers/

        self.expE = int.from_bytes(os.urandom(180),byteorder='big')
        self.expI = int.from_bytes(os.urandom(180),byteorder='big')
        partialEncKey = pow(generator,self.expE,modulus)
        partialIntKey = pow(generator,self.expI,modulus)
        self.timestamp = int(time.time())
        timestamp = str(self.timestamp)
        data = "CLNT"  + timestamp + "|"+ str(partialEncKey) + "|" + str(partialIntKey)

        ciphertext, MAC_tag = cipher.encrypt_and_digest(data.encode('utf-8'))

        # realized that the mac_tag that is acquired from the encrypt_and_digest function is actually a second level mac.
        # effectively, the encrypt function (to my understanding) replicates precisely what the entire thing does, aka what AES-CCM does in this case, and then encrypt_and_digest adds a mac to that scheme
        # simply due to the fact that every single mode has an encrypt_and_digest function that can be used,
        return cipher.nonce + ciphertext + MAC_tag


    # Checking if a received message is part of your protocol (called from app.py)
    def areSessionKeysNeeded(self):
        return self._skey is None or self._ikey is None

    def int_to_bytes(self,i):
        return i.to_bytes(length=((i.bit_length() + 7) // 8), byteorder='big')

    # Processing protocol message
    # THROW EXCEPTION IF AUTHENTICATION FAILS
    def ProcessReceivedProtocolMessage(self, message, isClient):
        assert self.sharedSecret is not None
        assert len(message)>16
        mac = message[-16:]
        nonce = message[:11]
        ciphertext = message[11:-16]  # I'm Alice represents first 11 byte nonce needed for AES, 16 byte MAC
        cipher = AES.new(self.sharedSecret, AES.MODE_CCM,nonce=nonce)
        plaintext = cipher.decrypt_and_verify(ciphertext,mac).decode('utf-8')

        # get the timestamp
        first, second = plaintext.split('|', 1)
        timestamp = int(first[4:])
        if isClient:
            # set the new information provided by the server accordingly
            if first[:4] != "SRVR":
                raise Exception("SRVR tag not found, could not complete key establishment")
            # verify timestamp
            if self.timestamp + 1 != timestamp:
                raise Exception("Timestamp does not match, could not complete key establishment")
            BEncPKey, BIntPKey = second.split('|')
            h = SHA256.new()
            h.update(self.int_to_bytes(pow(int(BEncPKey),self.expE,modulus)))
            h2 = SHA256.new()
            h2.update(self.int_to_bytes(pow(int(BIntPKey),self.expI, modulus)))
            self.SetSessionKey(h.digest(),h2.digest())
            return None
        else:
            if first[:4] != "CLNT":
                raise Exception("CLNT tag not found, could not complete key establishment")
            if timestamp < int(time.time()) - (60 * 5):
                raise Exception("Received timestamp is too old, could not complete key establishment")

            cipher = AES.new(self.sharedSecret, AES.MODE_CCM)
            # the crypto library suggests using the os random number generator and generally considers it to be a cryptographically secure random number generator:
            # link here: https://cryptography.io/en/latest/random-numbers/
            b = int.from_bytes(os.urandom(180),byteorder='big')
            bP = int.from_bytes(os.urandom(180),byteorder='big')
            partialEncKey = pow(generator, b, modulus)
            partialIntKey = pow(generator, bP, modulus)
            AEncPKey,AIntPKey = second.split('|')
            h = SHA256.new()
            h.update(self.int_to_bytes(pow(int(AEncPKey),b,modulus)))
            h2 = SHA256.new()
            h2.update(self.int_to_bytes(pow(int(AIntPKey),bP,modulus)))
            self.SetSessionKey(h.digest(),h2.digest())
            data = "SRVR" + str(timestamp+1) + "|" + str(partialEncKey) + "|" + str(partialIntKey)

            ciphertext, MAC_tag = cipher.encrypt_and_digest(data.encode('utf-8'))
            return cipher.nonce+ciphertext+MAC_tag

    # Setting the key for the current session
    def SetSessionKey(self, skey, ikey):
        self.expE = 0
        self.expI = 0
        self._skey = skey
        self._ikey = ikey
        pass


    # Encrypting messages
    # RETURN AN ERROR MESSAGE IF INTEGRITY VERITIFCATION OR AUTHENTICATION FAILS
    def EncryptAndProtectMessage(self, plain_text):
        if self._skey is None or self._ikey is None:
            raise Exception("Incorrect session or integrity key. Please restart your application")
        ctrcipher = AES.new(self._skey, AES.MODE_CTR)
        hmac = Crypto.Hash.HMAC.new(self._ikey)

        cipher_text = ctrcipher.nonce + ctrcipher.encrypt(plain_text.encode('utf-8'))

        hmac.update(cipher_text)
        cipher_text += hmac.digest()

        return cipher_text


    # Decrypting and verifying messages
    # RETURN AN ERROR MESSAGE IF INTEGRITY VERITIFCATION OR AUTHENTICATION FAILS
    def DecryptAndVerifyMessage(self, cipher_text):
        if self._skey is None or self._ikey is None:
            raise Exception("Bad session or integrity key - Key establishment failed")
        ctrcipher = AES.new(self._skey, AES.MODE_CTR, nonce=cipher_text[:8])
        hmac = Crypto.Hash.HMAC.new(self._ikey)

        hmac.update(cipher_text[:-16])
        hmac.verify(cipher_text[-16:])

        plain_bytes = ctrcipher.decrypt(cipher_text[8:-16])

        return plain_bytes.decode('utf-8')