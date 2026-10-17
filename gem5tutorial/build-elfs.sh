
sudo apt update
sudo apt install -y git make perl curl xz-utils

cd ~/gem5tutorial

curl -L https://github.com/riscv-collab/riscv-gnu-toolchain/releases/download/2026.08.27/riscv64-glibc-ubuntu-24.04-gcc.tar.xz | tar -xJ   # -> ./riscv
export PATH="$PWD/riscv/bin:$PATH"

git clone --depth 1 --branch openssl-3.5.9 https://github.com/openssl/openssl.git
cd openssl
./Configure linux64-riscv64 no-shared no-dso no-sock -march=rv64gc -mabi=lp64d --cross-compile-prefix=riscv64-unknown-linux-gnu-
make -j"$(nproc)" build_libs
cd ..

mkdir -p riscv-crypto-elfs
for alg in sha256 sha512 chacha20; do
  riscv64-unknown-linux-gnu-gcc -O2 -march=rv64gc -mabi=lp64d -static -DALG=\"$alg\" bench.c \
    -Iopenssl/include openssl/libcrypto.a -o riscv-crypto-elfs/ossl_${alg//-/_}.elf
done