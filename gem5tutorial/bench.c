// Runs one OpenSSL algorithm (chosen with -DALG="...") on a fixed buffer and
// prints a Python script that checks the result. Exit code 0 means correct.
#include <openssl/evp.h>
#include <stdio.h>
#include <stdlib.h>

// gem5 doesn't advertise Zvk* to Linux programs, so tell OpenSSL to use its
// vector-crypto code anyway. Runs before OpenSSL's own startup code.
__attribute__((constructor(101))) static void force_vector_crypto(void)
{
    setenv("OPENSSL_riscvcap", "rv64gc_v_zbb_zvbb_zvbc_zvkb_zvkg_zvkned_zvknhb_zvksed_zvksh", 0);
}

int main(void)
{
    static unsigned char msg[4096], out[sizeof msg + 16], key[32], iv[16];
    int n = 0, len;

    for (int i = 0; i < (int)sizeof msg; i++) msg[i] = i * 7;
    for (int i = 0; i < (int)sizeof key; i++) key[i] = i;

    const EVP_MD *md = EVP_get_digestbyname(ALG);
    const EVP_CIPHER *c = EVP_get_cipherbyname(ALG);
    if (md) {
        EVP_Digest(msg, sizeof msg, out, (unsigned *)&n, md, NULL);
    } else {
        EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
        EVP_EncryptInit_ex(ctx, c, NULL, key, iv);
        EVP_EncryptUpdate(ctx, out, &n, msg, sizeof msg);
        EVP_EncryptFinal_ex(ctx, out + n, &len);
        n += len;
        if (EVP_CIPHER_get_mode(c) == EVP_CIPH_GCM_MODE) {
            EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_GET_TAG, 16, out + n);
            n += 16;
        }
    }

    printf("import hashlib, sys\n"
           "from Crypto.Cipher import AES, ChaCha20\n"
           "msg, key, iv = bytes(i * 7 & 255 for i in range(4096)), bytes(range(32)), bytes(16)\n"
           "ref = {\n"
           "  'sha256':      lambda: hashlib.sha256(msg).digest(),\n"
           "  'sha512':      lambda: hashlib.sha512(msg).digest(),\n"
           "  'aes-128-ctr': lambda: AES.new(key[:16], AES.MODE_CTR, nonce=b'', initial_value=iv).encrypt(msg),\n"
           "  'aes-128-gcm': lambda: b''.join(AES.new(key[:16], AES.MODE_GCM, nonce=iv[:12]).encrypt_and_digest(msg)),\n"
           "  'chacha20':    lambda: ChaCha20.new(key=key, nonce=iv[4:]).encrypt(msg),\n"
           "}['%s']()\n"
           "out = bytes.fromhex('", ALG);
    for (int i = 0; i < n; i++) printf("%02x", out[i]);
    printf("')\nsys.exit(out != ref)\n");
    return 0;
}
