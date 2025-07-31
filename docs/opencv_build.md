# Building OpenCV and Rust bindings

This project uses the Rust `opencv` crate for performance sensitive code. The crate expects OpenCV development headers to be available. In CI we install the `libopencv-dev` package so the build script can detect OpenCV via `pkg-config`.

For local development on Ubuntu run:

```bash
sudo apt-get update
sudo apt-get install libopencv-dev clang llvm-dev libclang-dev pkg-config
```

When OpenCV is not available system-wide you can fall back to a vendored build by setting:

```bash
export OPENCV_VENDORED=1
```

which compiles OpenCV from source, but increases build time significantly.

If these packages are missing the Rust build will exit with an error similar to:

```
error: OpenCV development libraries not found. Install 'libopencv-dev' or set OPENCV_VENDORED=1 to build from source.
```

This helps diagnose missing dependencies before a lengthy compile starts.

To use the latest clang toolchain on CI or GitHub Actions you can run:
```
wget https://apt.llvm.org/llvm.sh
chmod +x llvm.sh
sudo ./llvm.sh 18
sudo apt-get install -y clang-18 libclang-18-dev
sudo update-alternatives --install /usr/bin/clang clang /usr/bin/clang-18 100
sudo update-alternatives --install /usr/bin/clang++ clang++ /usr/bin/clang++-18 100
export LIBCLANG_PATH=/usr/lib/llvm-18/lib
```
This ensures the OpenCV bindings are compiled against a recent Clang and libclang.
