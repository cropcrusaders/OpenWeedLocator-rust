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
